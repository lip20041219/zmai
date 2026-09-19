"""P1：LoopGuard 的 modification 定义必须与 Agent 的真实工作区证据一致。

修复前 `LoopGuard._WRITE_TOOLS = {"write_file", "edit", "git"}`，按工具名判定修改：

  * **只读 git 变成逃逸口**：git status / diff / log 是只读的，但名字命中 "git"
    → 每次调用都 `_record_modification()` 重置 `_steps_without_change`。
    实测每步跑一条 `git status`，计数器 8 步内始终在 0~1 之间摆动，
    no-change guard 永不触发（`loopguard_blocks == 0`）。
  * **shell 的真实修改不被记录**：`shell_exec` 不在集合内，且 `record_tool_call`
    拿不到工作区证据 → `_last_modification_step` 始终为 -1。

修复后：`_WRITE_TOOLS` 去掉 "git"；`record_tool_call` 接收 Agent 已算出的
`ws_changed`（P1-3 工作区指纹），写工具仍按工具名认定（语义不变）。

全部用例走真实 Runtime + 真实工具（真跑 git / shell / pytest、真写文件）。
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
from zmai.swe.loop_guard import LoopGuard
from zmai.tool import ToolCall, ToolRegistry

THRESHOLD = 3


def _sh(cmd: str) -> ToolCall:
    return ToolCall(id=cmd, name="shell_exec", params={"command": cmd})


def _git(args: str) -> ToolCall:
    return ToolCall(id=f"git {args}", name="git", params={"args": args})


def _write_file(content: str = "def value():\n    return 2\n") -> ToolCall:
    return ToolCall(id="w", name="write_file",
                    params={"path": "app.py", "content": content})


def _git_cli(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=root, capture_output=True, check=False)


def _write_project(tmp_path: Path, *, dirty: bool = False) -> None:
    (tmp_path / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import value\n\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    _git_cli(tmp_path, "init", "-q")
    _git_cli(tmp_path, "add", "-A")
    _git_cli(tmp_path, "commit", "-qm", "init")
    if dirty:
        # 工作区先弄脏，git checkout/restore 才会真实改写文件
        (tmp_path / "app.py").write_text(
            "def value():\n    return 999  # dirty\n", encoding="utf-8")


class _StepBackend(Backend):
    """按脚本逐 step 返回工具调用；脚本用尽后重复最后一项。"""

    name = "lg_mod"

    def __init__(self, script):
        self._script = script
        self._i = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        idx = min(self._i, len(self._script) - 1)
        self._i += 1
        return BackendResponse(content="", tool_calls=list(self._script[idx]),
                               usage=TokenUsage(1, 1), stop_reason="tool_use")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _run(tmp_path: Path, script, max_steps: int = 6, *, dirty: bool = False):
    _write_project(tmp_path, dirty=dirty)
    ctx = AgentContext(
        agent_id="lg_mod",
        task="修复 bug 使测试通过",
        backend=_StepBackend(script),
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": THRESHOLD,
                "loop_guard.recover_limit": 99},
        metadata={},
    )
    agent = SWEAgent("lg_mod")
    asyncio.run(agent.initialize(ctx))
    guard: LoopGuard = ctx.metadata["loop_guard"]
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        if action.type in ("complete", "fail"):
            break
    blocks = (ctx.metadata.get("swe_stats") or {}).get("loopguard_blocks", 0)
    return ctx, guard, blocks


# ── A：只读 git 不得成为 no-change 逃逸口 ────────────────────────
def test_readonly_git_does_not_reset_no_change_counter(tmp_path):
    """连续只读 git 命令下，_steps_without_change 必须累积并最终触发 guard。"""
    script = [
        [_git("status --porcelain")],
        [_git("diff")],
        [_git("log --oneline -1")],
        [_git("status")],
        [_git("diff --stat")],
        [_git("log -1")],
    ]
    ctx, guard, blocks = _run(tmp_path, script)

    # 1) 只读 git 不得被当成 modification
    assert guard.get_status()["last_modification_step"] == -1, (
        "只读 git 命令不得被记录为代码修改"
    )
    # 2) no-change guard 必须触发过（修复前恒为 0：每步 git status 无限续命）
    assert blocks >= 1, (
        f"连续只读 git 应触发 no-change guard（修复前为 0）: blocks={blocks}"
    )
    assert ctx.metadata.get("loop_recovery_count", 0) >= 1


def test_repeated_git_status_cannot_extend_life_forever(tmp_path):
    """同一条 git status 反复执行：不能靠它续命（签名相同也不得绕过）。"""
    _, guard, blocks = _run(tmp_path, [[_git("status --porcelain")]], max_steps=8)

    assert guard.get_status()["last_modification_step"] == -1
    assert blocks >= 1, f"重复 git status 应触发 guard: blocks={blocks}"


# ── B：shell_exec 真实修改源码 → 记录 modification ───────────────
def test_shell_source_change_records_modification(tmp_path):
    """shell 真改 .py（含 __pycache__ 噪声）→ LoopGuard 记录 modification。"""
    script = [
        [_sh("python -c \"open('app.py','a').write('# x\\n')\"")],
        [_sh("python -m pytest -q")],   # 产生 __pycache__，不得干扰判定
        [_sh("python -c \"print(2)\"")],
    ]
    ctx, guard, _ = _run(tmp_path, script)

    assert ctx.metadata.get("ever_modified") is True, "Agent 必须检测到工作区变化"
    assert guard.get_status()["last_modification_step"] >= 1, (
        "shell 的真实修改必须被 LoopGuard 记录（修复前恒为 -1）"
    )


# ── C：shell_exec 只读不得被记录为 modification ─────────────────
def test_readonly_shell_is_not_a_modification(tmp_path):
    """只读 shell（cat / pytest，含 __pycache__ 与 .pytest_cache）不得算修改。"""
    script = [
        [_sh("python -c \"print(open('app.py').read())\"")],
        [_sh("python -m pytest -q")],
        [_sh("dir")],
    ]
    ctx, guard, _ = _run(tmp_path, script)

    assert ctx.metadata.get("ever_modified") is not True
    assert guard.get_status()["last_modification_step"] == -1, (
        "只读 shell 与 pytest 缓存目录不得被误判为代码修改"
    )


# ── D：write_file / edit 原有语义不回归 ─────────────────────────
def test_write_file_still_records_modification(tmp_path):
    """写工具按工具名认定，语义不变（即使 ws_changed 未参与判定）。"""
    _, guard, _ = _run(tmp_path, [[_write_file()], [_sh("dir")], [_sh("dir /b")]],
                       max_steps=3)

    assert guard.get_status()["last_modification_step"] == 1


# ── E：git checkout/restore 真实改动 → 记录 modification ─────────
def test_git_checkout_real_change_records_modification(tmp_path):
    """git checkout 真实回退了工作区 → 必须被识别为 modification。"""
    script = [
        [_git("status --porcelain")],
        [_git("checkout -- .")],     # 999 → 回到已提交的 1（真实改动）
        [_sh("python -c \"print(4)\"")],
    ]
    ctx, guard, _ = _run(tmp_path, script, dirty=True)

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == \
        "def value():\n    return 1\n", "夹具必须真的回退了文件，否则断言空洞"
    assert ctx.metadata.get("ever_modified") is True, "git checkout 真实改了工作区"
    assert guard.get_status()["last_modification_step"] == 2, (
        "git checkout 的真实改动必须被记录（修复前恒为 -1）"
    )


# ── F：no-change 收敛（guard 触发后计数器被 reset，不得无限续命）──
def test_readonly_loop_converges_and_guard_keeps_firing(tmp_path):
    """连续只读调用：guard 反复触发而不是被只读命令无限续命。"""
    _, guard, blocks = _run(
        tmp_path, [[_sh("dir")], [_sh("dir /b")], [_sh("dir /a")], [_sh("dir /o")]],
        max_steps=9,
    )
    assert blocks >= 2, f"每 {THRESHOLD} 步无修改应再次触发 guard: blocks={blocks}"
    assert guard.get_status()["last_modification_step"] == -1


# ── 单元层：record_tool_call 的 ws_changed 语义 ─────────────────
def test_record_tool_call_ws_changed_semantics():
    """ws_changed=True 记录修改；False 不记录；None 退回按工具名判断。"""
    # ws_changed=True（shell/git 的真实改动）
    g = LoopGuard(threshold=3)
    g.record_no_modification()
    g.record_no_modification()
    g.record_tool_call(name="shell_exec", params={"command": "x"}, success=True,
                       ws_changed=True)
    assert g.get_status()["steps_without_change"] == 0

    # ws_changed=False（只读 git）→ 不重置
    g2 = LoopGuard(threshold=3)
    g2.record_no_modification()
    g2.record_no_modification()
    g2.record_tool_call(name="git", params={"args": "status"}, success=True,
                        ws_changed=False)
    assert g2.get_status()["steps_without_change"] == 2, "只读 git 不得重置计数器"

    # ws_changed=None（旧调用点）→ 退回工具名：write_file 重置、git 不重置
    g3 = LoopGuard(threshold=3)
    g3.record_no_modification()
    g3.record_tool_call(name="write_file", params={}, success=True)
    assert g3.get_status()["steps_without_change"] == 0

    g4 = LoopGuard(threshold=3)
    g4.record_no_modification()
    g4.record_tool_call(name="git", params={"args": "status --porcelain"}, success=True)
    assert g4.get_status()["steps_without_change"] == 1, (
        "git 已不在 _WRITE_TOOLS 中，旧调用点也不得再重置"
    )

    # 失败调用 + 工作区确实变了 → 必须记录 modification（progress 由工作区证据决定，
    # 不由工具的 success 位决定）：`git stash pop` 冲突、`git checkout` 部分失败、
    # shell 改完文件才返回非零，都是"执行失败但真实推进了工作区"。
    g5 = LoopGuard(threshold=3)
    g5.record_no_modification()
    g5.record_tool_call(name="shell_exec", params={"command": "x"}, success=False,
                        ws_changed=True)
    assert g5.get_status()["steps_without_change"] == 0, (
        "失败但工作区真变了，必须算 modification"
    )
    assert g5.get_status()["last_modification_step"] == 1

    # 失败调用 + 工作区没变 → 不记录
    g6 = LoopGuard(threshold=3)
    g6.record_no_modification()
    g6.record_tool_call(name="shell_exec", params={"command": "x"}, success=False,
                        ws_changed=False)
    assert g6.get_status()["steps_without_change"] == 1


def test_git_removed_from_write_tools():
    """_WRITE_TOOLS 不再包含 git（只读/写无法按名字区分）。"""
    assert "git" not in LoopGuard._WRITE_TOOLS
    assert LoopGuard._WRITE_TOOLS == frozenset({"write_file", "edit"})


# ── G：失败但真实修改了工作区 → 进度必须恢复（no-change guard 不误判停滞）──
def _mutating_fail(tag: str) -> ToolCall:
    """改文件后 exit 1：典型的"执行失败但产生真实 mutation"。

    错误文本带上不同的 tag，避免命中 identical_failures（那条判定按错误文本聚类）。
    """
    return _sh(f"python -c \"import sys; open('app.py','a').write('# {tag}'); "
               f"sys.stderr.write('boom-{tag}'); sys.exit(1)\"")


def test_failed_command_that_mutates_workspace_counts_as_progress(tmp_path):
    """shell 改了文件后返回非零：真实 Runtime 下不得被当成"无修改"。

    每条命令都不同，避免命中 identical_failures（那是另一条既有判定），
    从而本用例只考察 no_progress 维度。
    """
    ctx, guard, blocks = _run(
        tmp_path,
        [[_mutating_fail("a")], [_mutating_fail("b")], [_mutating_fail("c")]],
        max_steps=3,
    )

    assert ctx.metadata.get("ever_modified") is True, "工作区真实变化必须被 Agent 检测到"
    assert guard.get_status()["last_modification_step"] >= 1, (
        "失败但改了工作区，必须记录为 modification（修复前恒为 -1）"
    )
    assert guard.get_status()["steps_without_change"] == 0, (
        "真实修改后无修改计数必须归零"
    )
    assert blocks == 0, f"有真实进展时不得触发 no-change guard: {blocks}"
