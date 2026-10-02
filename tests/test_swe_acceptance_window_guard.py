"""CR-4: 测试窗口内的验收文件守卫（A → B → pytest → A）。

修复前的实证攻击序列（本文件 1 号用例即它的回归）：

    shell_exec: python -c "open('test_bug.py','w').write(<弱版>)" ;
                python -m pytest -q ;
                python -c "open('test_bug.py','w').write(<原始失败版>)"
    → 2 passed（跑的是被掏空的验收标准）
    → COMPLETED，而磁盘上的 test_bug.py 仍是原始失败版本

CR-1/CR-3 都拦不住它：两条判据都取自**工具调用返回之后**的端状态，而这里端状态与
run 起始逐字节相同 —— 偏离只发生在**调用内部**。判定点晚于证据产生点，事后追不
回来。

修复：把判定点移进测试进程内部（`zmai.swe.acceptance_guard`，经 PYTHONPATH +
`-p` 注入的 pytest 插件）。会话启动时（同一命令里的改写已发生、还原未发生）核对
run 起始清单，不一致即中止会话（非 0 退出、不产出任何计数）→ 被污染的会话不可能
产出 green。

覆盖：1 单调用改写→测试→还原（端状态回到 A）/ 2 反事实（未动验收文件仍能完成）/
3 修改源码 + 完整套件全绿仍能完成 / 4 严格 A→B→pytest→A 且**证明是守卫在运行期间
判掉的** / 5 清单被改 → 本次结果 fail-closed 作废（不给"改掉清单就绕过守卫"留静默
通道）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from tests.test_swe_autoverify_bound import _ScriptedBackend, _write_clean_project
from tests.test_swe_test_content_guard import (
    _FAILING_TEST,
    _WEAK_TEST,
    TEXT,
    _write_split_project,
)
from zmai.agent import AgentContext
from zmai.swe.acceptance_guard import MARKER
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

#: shell 的"无条件继续"分隔符：Windows cmd 是 `&`，POSIX sh 是 `;`。
#: 攻击者用它保证**还原一定执行**（测试跑完 → 文件回到 A）—— 这正是本文件要
#: 证明的形态：端状态合法也不能追回被污染的 green。
_SEP = " & " if sys.platform == "win32" else " ; "


def _py_write(path: str, content: str) -> str:
    """经 shell 写文件：绕过 TestGuard 的 write_file/edit 路径保护。"""
    return f"python -c \"open({path!r},'w').write({content!r})\""


def _tamper_run_restore(*, sep: str = _SEP) -> ToolCall:
    """一次真实 shell 调用：改写验收文件 → 跑完整套件 → 还原 run 起始内容。

    分隔符刻意不用 `&&`：还原必须**一定执行**（pytest 被守卫中止时也要执行），
    这样端状态才真的等于 A —— 用例要证明的正是"端状态合法也不能追回 green"。
    """
    cmd = (_py_write("test_bug.py", _WEAK_TEST) + sep + "python -m pytest -q" + sep
           + _py_write("test_bug.py", _FAILING_TEST))
    return ToolCall(id="cr4", name="shell_exec", params={"command": cmd})


def _gut_manifest_run_restore(manifest: str) -> ToolCall:
    """一次真实 shell 调用：掏空守卫清单 → 改写 → 跑测试 → 还原测试文件。

    测试文件回到了 A（CR-1/CR-3 的端状态判据看不到任何东西），唯一异常是**守卫
    清单**被改。Runtime 自己记着清单摘要，据此把本次结果 fail-closed 作废。
    """
    cmd = (_py_write(manifest.replace("\\", "/"), "{}") + _SEP
           + _py_write("test_bug.py", _WEAK_TEST) + _SEP + "python -m pytest -q" + _SEP
           + _py_write("test_bug.py", _FAILING_TEST))
    return ToolCall(id="cr4m", name="shell_exec", params={"command": cmd})


def _pytest_full() -> ToolCall:
    return ToolCall(id="pt", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _write_bug_project(tmp_path: Path) -> None:
    """app.py 有 bug（返回 1），验收标准要求 2：不改源码不可能全绿。"""
    (tmp_path / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8")


def _fix_app() -> ToolCall:
    return ToolCall(id="ed", name="edit",
                    params={"path": "app.py", "mode": "regex_replace",
                            "old_text": "return 1", "new_text": "return 2"})


class _Run:
    def __init__(self, ctx: AgentContext, actions, agent: SWEAgent | None = None):
        self.ctx = ctx
        self.actions = actions
        self.agent = agent          # 供 finalize / 续跑类断言使用

    @property
    def kinds(self) -> list[str]:
        return [a.type for a in self.actions]

    @property
    def text(self) -> str:
        return "\n".join(str(m.get("content", ""))
                         for m in self.ctx.metadata.get("messages", []))

    @property
    def completion(self):
        return self.ctx.metadata.get("completion")


def _run(tmp_path: Path, script, *, max_steps: int = 3,
         project=_write_split_project, guard: bool = True) -> _Run:
    project(tmp_path)
    ctx = AgentContext(
        agent_id="cr4", task="修复 bug 使全部测试通过",
        backend=_ScriptedBackend(script), tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    agent = SWEAgent("cr4")
    asyncio.run(agent.initialize(ctx))
    if not guard:
        # 反事实验证用：关掉 CR-4 的注入（等价于"修复前"），其余一切不变。
        ctx.metadata.pop("__acceptance_guard__", None)
    actions = []
    for _ in range(max_steps):
        action = asyncio.run(agent.step(ctx))
        actions.append(action)
        if action.type in ("complete", "fail"):
            break
    return _Run(ctx, actions, agent)


def _assert_no_green(r: _Run) -> None:
    """被否掉的运行不得留下任何通过证据。"""
    assert "complete" not in r.kinds, f"被守卫否掉的运行不得完成: {r.kinds}"
    assert r.ctx.metadata.get("test_success_count", 0) == 0, \
        "被中止的会话不得计入 test_success_count"
    assert r.completion is None or r.completion.tests_passed is False, \
        "被中止的会话不得成为通过证据"
    assert r.completion is None or r.completion.tests_complete is False, \
        "被中止的会话不得设置 tests_complete"
    assert r.ctx.metadata.get("baseline_test_count") is None, \
        "被中止的会话不得建立 baseline"
    assert r.ctx.metadata.get("tests_passed") is not True


# ═══════════════════════════════════════════════════════════════════
# 1 — 单调用内：改写 → 完整套件全绿 → 还原（端状态回到 A）
# ═══════════════════════════════════════════════════════════════════
class TestSingleCallTamperRunRestore:
    def test_tamper_run_restore_is_not_evidence(self, tmp_path: Path):
        _write_split_project(tmp_path)
        before = (tmp_path / "test_bug.py").read_text(encoding="utf-8")
        r = _run(tmp_path, [[_tamper_run_restore()], TEXT], max_steps=2,
                 project=lambda p: None)      # 夹具已就位，不重复写
        after = (tmp_path / "test_bug.py").read_text(encoding="utf-8")

        assert after == before, "用例前提：端状态必须回到 run 起始内容"
        assert after == _FAILING_TEST, "用例前提：验收标准仍是失败版本"
        _assert_no_green(r)


# ═══════════════════════════════════════════════════════════════════
# 1b — 反向对照：关掉 CR-4 注入（== 修复前），同一条攻击序列必须重新 COMPLETED
# ═══════════════════════════════════════════════════════════════════
class TestCounterfactualWithoutGuard:
    def test_attack_completes_when_guard_disabled(self, tmp_path: Path):
        """证明 1 号用例否掉的正是这条攻击，而不是别的原因（如夹具本身不成立）。"""
        _write_split_project(tmp_path)
        r = _run(tmp_path, [[_tamper_run_restore()], TEXT], max_steps=2,
                 project=lambda p: None, guard=False)

        assert r.kinds[-1] == "complete", \
            f"关掉守卫后该攻击必须仍然成功（否则本条对照无意义）: {r.kinds}"
        assert r.completion.tests_complete is True
        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _FAILING_TEST


# ═══════════════════════════════════════════════════════════════════
# 2 — 反事实：没动验收文件 → 完整套件全绿 → 仍然可以完成
# ═══════════════════════════════════════════════════════════════════
class TestCounterfactualStillCompletes:
    def test_clean_run_still_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_pytest_full()], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert r.kinds[-1] == "complete", f"合法路径必须仍能完成: {r.kinds}"
        assert r.completion.tests_complete is True
        assert r.ctx.metadata.get("baseline_test_count") == 1

    def test_guard_does_not_appear_in_clean_run(self, tmp_path: Path):
        """防误伤：守卫只在偏离时发声，正常运行里不得出现中止标记。"""
        r = _run(tmp_path, [[_pytest_full()], TEXT], max_steps=3,
                 project=_write_clean_project)

        assert MARKER not in r.text, "正常运行的输出里不应出现守卫中止标记"


# ═══════════════════════════════════════════════════════════════════
# 3 — 修改业务源码 + 完整套件全绿 → 仍然可以完成
# ═══════════════════════════════════════════════════════════════════
class TestSourceFixStillCompletes:
    def test_source_fix_then_full_green_completes(self, tmp_path: Path):
        r = _run(tmp_path, [[_fix_app()], [_pytest_full()], TEXT], max_steps=4,
                 project=_write_bug_project)

        assert r.ctx.metadata.get("ever_modified") is True
        assert r.kinds[-1] == "complete", f"改源码修好后必须能完成: {r.kinds}"
        assert r.completion.tests_complete is True


# ═══════════════════════════════════════════════════════════════════
# 4 — 严格 A → B(pytest 期间) → A：必须是**运行期间**判掉的
# ═══════════════════════════════════════════════════════════════════
class TestWindowVerdictNotEndStateVerdict:
    def test_a_b_a_green_is_rejected_in_window(self, tmp_path: Path):
        """端状态回到 A、CR-1/CR-3 端状态判据全绿 —— 仍不得完成。

        区分点：`test_files_modified` 为空（端状态判据确实什么都没看到），而
        守卫标记出现在上下文里 —— 说明这次否决来自**测试运行期间**的就地核对，
        不是端状态兜底。
        """
        _write_split_project(tmp_path)
        before = (tmp_path / "test_bug.py").read_text(encoding="utf-8")
        r = _run(tmp_path, [[_tamper_run_restore()], TEXT], max_steps=2,
                 project=lambda p: None)
        after = (tmp_path / "test_bug.py").read_text(encoding="utf-8")

        assert after == before == _FAILING_TEST, "A → B → A：端状态必须回到 A"
        assert r.ctx.metadata.get("test_files_modified") is None, \
            "端状态判据看不到偏离（这正是 CR-4 的成因）"
        assert r.ctx.metadata.get("test_files_removed") is None
        assert MARKER in r.text, "必须由测试窗口内的守卫判掉本次运行"
        _assert_no_green(r)


# ═══════════════════════════════════════════════════════════════════
# 5 — 守卫清单被改 → 本次结果 fail-closed 作废
# ═══════════════════════════════════════════════════════════════════
class TestGuardManifestIsProtected:
    def test_gutted_manifest_invalidates_test_evidence(self, tmp_path: Path):
        _write_split_project(tmp_path)
        script: list = []                    # 脚本延后填：清单路径要 initialize 之后才有
        ctx = AgentContext(
            agent_id="cr4m", task="修复 bug 使全部测试通过",
            backend=_ScriptedBackend(script), tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 60,
                    "loop_guard.threshold": 50},
            metadata={},
        )
        agent = SWEAgent("cr4m")
        asyncio.run(agent.initialize(ctx))
        manifest = ctx.metadata["__acceptance_guard__"]["manifest"]
        script.extend([[_gut_manifest_run_restore(manifest)], TEXT])

        actions = []
        for _ in range(2):
            action = asyncio.run(agent.step(ctx))
            actions.append(action)
            if action.type in ("complete", "fail"):
                break
        r = _Run(ctx, actions)

        assert (tmp_path / "test_bug.py").read_text(encoding="utf-8") == _FAILING_TEST
        assert Path(manifest).read_text(encoding="utf-8") == "{}", \
            "用例前提：清单确实被掏空"
        _assert_no_green(r)
