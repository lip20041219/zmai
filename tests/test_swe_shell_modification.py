"""P1-3：经 shell / git 产生的真实文件修改必须计入 modification 证据。

修复前，modification 证据只看工具名（write_file / edit）。任何经 shell_exec 的
真实改动（`python fix.py`、`sed -i`、`echo ... > app.py`）以及 `git checkout /
restore` 都被当成"没有修改"，导致：

  * eval 模式 ever_modified 恒为 False → EvalGuard 永久阻塞合法完成；
  * 修改前的 green state（completion.tests_passed / test_success_count）不被失效
    → 用"改之前的全绿"给"改之后的代码"背书（P0-2 的 stale green 同类）。

修复后判据是**工作区指纹**（真实状态证据），不是解析命令字符串：
判断"工作区实际有没有变"，而不是猜 `python fix.py` 是不是在写文件。

全部用例都走真实 Runtime + 真实工具执行（真的跑脚本改文件、真的跑 pytest、真的
跑 git），不通过直接改 metadata 来模拟结果。
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator
from pathlib import Path

from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

# eval 模式：未做任何代码修改时拦截完成判定（P1-3 场景 B 的触发条件）
_EVAL_CFG = {"eval.require_code_change": "true"}


# ── 测试项目 ────────────────────────────────────────────────────
def _write_project(tmp_path: Path) -> None:
    """初始 VALUE=1，test_v 断言 VALUE==1（初始全绿）。"""
    (tmp_path / "bug.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "test_v.py").write_text(
        "import bug\n\n\ndef test_v():\n    assert bug.VALUE == 1\n",
        encoding="utf-8",
    )
    # 用真实脚本改写源码：模拟 `python fix.py` 这类"命令意图无法从字符串判断"
    # 的 shell 修改（不解析命令，只看工作区实际变没变）。
    (tmp_path / "setter.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path('bug.py').write_text('VALUE = ' + sys.argv[1] + chr(10), encoding='utf-8')\n"
        "print('set VALUE =', sys.argv[1])\n",
        encoding="utf-8",
    )


def _write_dirty_repo_project(tmp_path: Path) -> None:
    """已提交 VALUE=1，然后把工作区改成脏的 VALUE=999。

    基线在 initialize 时建立（此时是 999），因此后续一条 `git checkout -- bug.py`
    就会让工作区真实变化 —— 用于隔离"git 作为修改来源"这一条路径。
    """
    _write_project(tmp_path)
    _init_repo(tmp_path)
    (tmp_path / "bug.py").write_text("VALUE = 999\n", encoding="utf-8")


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )


def _init_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")


# ── 工具调用 ────────────────────────────────────────────────────
def _sh(cmd: str) -> ToolCall:
    return ToolCall(id=cmd, name="shell_exec", params={"command": cmd})


def _pytest() -> ToolCall:
    return _sh("python -m pytest -q")


def _set(value: int) -> ToolCall:
    return _sh(f"python setter.py {value}")


def _read_only_shell() -> ToolCall:
    return _sh("python -c \"print(open('bug.py').read())\"")


def _git_call(args: str) -> ToolCall:
    return ToolCall(id=f"git_{args}", name="git", params={"args": args})


class _ScriptedBackend(Backend):
    name = "shell_mod"

    def __init__(self, script):
        self._script = script
        self._i = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        calls = None
        if self._i < len(self._script):
            calls = self._script[self._i]
        self._i += 1
        return BackendResponse(
            content="", tool_calls=calls,
            usage=TokenUsage(1, 1),
            stop_reason="tool_use" if calls else "end_turn",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _messages_text(ctx: AgentContext) -> str:
    return "\n".join(
        (m.get("content", "") if isinstance(m, dict) else str(m))
        for m in ctx.metadata.get("messages", [])
    )


def _run(tmp_path: Path, script, max_steps: int = 6, extra_config: dict | None = None,
         project=_write_project):
    project(tmp_path)
    backend = _ScriptedBackend(script)
    agent = SWEAgent("sm")
    ctx = AgentContext(
        agent_id="sm",
        task="修复 bug 使测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50, **(extra_config or {})},
        metadata={},
    )
    asyncio.run(agent.initialize(ctx))
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action.type)
        if action.type in ("complete", "fail"):
            break
    return ctx, actions


# ── Test 1：shell 实际修改源码 → 计入 modification ───────────────
def test_shell_source_change_is_a_modification(tmp_path):
    """`python setter.py 0` 真实改写了 bug.py → 必须被识别为修改证据。"""
    ctx, _ = _run(tmp_path, [[_set(0)], None])

    assert ctx.metadata.get("ever_modified") is True, (
        "shell 改写源码必须计入 ever_modified（否则 eval 模式永远无法完成）"
    )
    assert (tmp_path / "bug.py").read_text(encoding="utf-8") == "VALUE = 0\n", (
        "本用例必须真的改了文件，否则断言空洞"
    )


# ── Test 2：shell 只读 → 不产生 modification ────────────────────
def test_shell_read_only_is_not_a_modification(tmp_path):
    """只读 shell 与 pytest（会生成 __pycache__ / .pytest_cache）都不得算修改。"""
    script = [
        [_pytest()],          # 跑测试：产生 __pycache__ 与 .pytest_cache
        [_read_only_shell()],  # 纯读文件
        None,
    ]
    ctx, _ = _run(tmp_path, script)

    assert ctx.metadata.get("ever_modified") is not True, (
        "只读命令与测试运行不得被误判为代码修改"
    )
    # 确认缓存目录确实产生了 —— 否则本用例没有真正验证忽略规则
    cache_dirs = [p.name for p in tmp_path.rglob("*") if p.is_dir()]
    assert any(d == "__pycache__" for d in cache_dirs), (
        f"pytest 应当生成 __pycache__ 以验证忽略规则生效: {cache_dirs}"
    )


# ── Test 3：shell 修改后旧 green state 必须失效 ─────────────────
def test_shell_change_invalidates_previous_green(tmp_path):
    """先全绿、再用 shell 把源码改坏 → 修改前的 green 不得继续作为完成依据。

    需要 eval 守卫让第一轮全绿无法直接完成，shell 修改才有机会执行。
    """
    script = [
        [_pytest()],   # 初始全绿 → tests_passed=True, success_count=1
        [_set(0)],     # shell 改坏源码（工作区真的变了）
        None,
    ]
    ctx, actions = _run(tmp_path, script, extra_config=_EVAL_CFG)

    assert "complete" not in actions, f"改坏工作区后不得完成: {actions}"
    assert ctx.metadata.get("ever_modified") is True
    comp = ctx.metadata["completion"]
    assert comp.tests_passed is False, "修改必须让旧的 green 失效（stale green）"
    assert ctx.metadata.get("test_success_count", 0) == 0


# ── Test 4：eval 模式 shell 修改满足 require_code_change ────────
def test_eval_mode_shell_change_satisfies_require_code_change(tmp_path):
    """初始失败 → 只用 shell 修好 → ever_modified 置位，EvalGuard 不再阻塞完成。"""
    def failing_project(p: Path) -> None:
        _write_project(p)
        (p / "bug.py").write_text("VALUE = 0\n", encoding="utf-8")

    script = [
        [_set(1)],     # 只用 shell 修好，不用 edit/write_file
        [_pytest()],   # 全绿
        None,
    ]
    ctx, actions = _run(tmp_path, script, extra_config=_EVAL_CFG,
                        project=failing_project)

    assert ctx.metadata.get("ever_modified") is True, (
        "eval 模式下 shell 修改必须满足 require_code_change"
    )
    assert actions[-1] == "complete", (
        f"shell 已真实修复代码，不得再被 EvalGuard 永久阻塞: {actions}"
    )
    assert "尚未修改任何源码" not in _messages_text(ctx)


# ── Test 5：shell 修改后重新测试并正确 complete ─────────────────
def test_shell_change_then_retest_completes(tmp_path):
    """失败 → shell 修好 → 重跑测试全绿 → 正常 complete（非 eval 模式）。"""
    def failing_project(p: Path) -> None:
        _write_project(p)
        (p / "bug.py").write_text("VALUE = 0\n", encoding="utf-8")

    script = [
        [_pytest()],   # 1 failed → 建立基线
        [_set(1)],     # shell 修好
        [_pytest()],   # 1 passed → full_green
        None,
    ]
    ctx, actions = _run(tmp_path, script, project=failing_project)

    assert actions[-1] == "complete", f"shell 修复后重测全绿应 complete: {actions}"
    assert ctx.metadata.get("ever_modified") is True
    assert ctx.metadata["completion"].tests_complete is True


# ── Test 6：git 只读操作不误判 modification ─────────────────────
def test_readonly_git_is_not_a_modification(tmp_path):
    """git status / diff / log 是只读的，不得解除保护或计入修改证据。"""
    def clean_repo(p: Path) -> None:
        _write_project(p)
        _init_repo(p)

    script = [
        [_git_call("status --porcelain")],
        [_git_call("diff")],
        [_git_call("log --oneline")],
        None,
    ]
    ctx, _ = _run(tmp_path, script, project=clean_repo)

    assert ctx.metadata.get("ever_modified") is not True, (
        "只读 git 命令不得算作代码修改"
    )


# ── Test 7：git 真实改变工作区 → 计入 modification ──────────────
def test_git_restore_counts_as_modification(tmp_path):
    """`git checkout -- bug.py` 真实回退了工作区 → 必须被识别为修改。

    这条路径无法靠"判断命令意图"覆盖（checkout 既可读也可写），只能靠工作区状态。
    """
    script = [
        [_git_call("checkout -- bug.py")],   # 999 → 回到已提交的 1
        None,
    ]
    ctx, _ = _run(tmp_path, script, project=_write_dirty_repo_project)

    assert (tmp_path / "bug.py").read_text(encoding="utf-8") == "VALUE = 1\n", (
        "本用例必须真的回退了文件，否则断言空洞"
    )
    assert ctx.metadata.get("ever_modified") is True, (
        "git checkout 真实改动了工作区，必须计入修改证据"
    )
