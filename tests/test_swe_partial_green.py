"""Regression: 子集 pytest 全绿（partial_green）不得导致无限循环。

根因：agent 把"子集全绿但测试数 < 基线"静默当成失败（passed=False），
导致 test_success_count 永不为 1、CompletionState 永不完成 → read/edit/pytest
无限循环到 max_steps → TIMEOUT。

修复后语义：
- failed       (如 7 passed, 2 failed)  → 不 complete，走修复逻辑
- partial_green (如 7 passed, baseline=9) → 不 complete、不增加 success_count、
  明确告知模型运行完整套件 → continue
- full_green    (如 9 passed, baseline=9) → test_success_count=1 → complete
"""

from __future__ import annotations

import asyncio
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
from zmai.swe.completion import CompletionState
from zmai.swe.tools import _cap_shell_output
from zmai.swe.verifier import parse_test_totals
from zmai.tool import ToolCall, ToolRegistry


def _write_project(tmp_path: Path) -> None:
    # bug.py：初始 FIXED=False；edit 修复为 True。
    (tmp_path / "bug.py").write_text("FIXED = False\n", encoding="utf-8")
    # test_all.py：9 个测试，其中 7 个恒过，2 个依赖 bug.FIXED。
    lines = ["import bug\n"]
    for i in range(1, 8):
        lines.append(f"def test_ok{i}():\n    assert True\n")
    lines.append("def test_fix1():\n    assert bug.FIXED\n")
    lines.append("def test_fix2():\n    assert bug.FIXED\n")
    (tmp_path / "test_all.py").write_text("".join(lines), encoding="utf-8")
    # test_app.py：子集，7 个恒过测试。
    sub = [f"def test_sub{i}():\n    assert True\n" for i in range(7)]
    (tmp_path / "test_app.py").write_text("".join(sub), encoding="utf-8")


def _pytest(file: str) -> ToolCall:
    return ToolCall(id=f"pt_{file}", name="shell_exec",
                    params={"command": f"python -m pytest {file} -q"})


def _bare() -> ToolCall:
    """未指定测试目标 → 唯一能"证明"覆盖完整套件的运行方式。"""
    return ToolCall(id="pt_bare", name="shell_exec",
                    params={"command": "python -m pytest -q"})


def _fix() -> ToolCall:
    return ToolCall(id="fix", name="edit",
                    params={"path": "bug.py", "mode": "regex_replace",
                            "old_text": "FIXED = False", "new_text": "FIXED = True"})


class _ScriptedBackend(Backend):
    name = "partial_green"

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


async def _run(tmp_path: Path, script, max_steps: int = 8, project=_write_project):
    project(tmp_path)
    backend = _ScriptedBackend(script)
    agent = SWEAgent("pg")
    ctx = AgentContext(
        agent_id="pg",
        task="修复 bug 使全部测试通过",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    await agent.initialize(ctx)
    actions = []
    for _ in range(max_steps):
        action = await agent.step(ctx)
        actions.append(action.type)
        if action.type in ("complete", "fail"):
            break
    return ctx, actions


def _messages_text(ctx: AgentContext) -> str:
    return "\n".join(
        (m.get("content", "") if isinstance(m, dict) else str(m))
        for m in ctx.metadata.get("messages", [])
    )


def test_partial_green_then_full_green(tmp_path):
    """子集 7 全绿(baseline=9) → partial_green，不 complete、不累计；
    完整 9 全绿 → complete。"""
    script = [
        [_pytest("test_all.py")],  # 7 passed, 2 failed → baseline=9, failed
        [_fix()],                  # 修复 bug.py
        [_pytest("test_app.py")],  # 子集 7 passed → partial_green
        [_pytest("test_all.py")],  # 9 passed → full_green → complete
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script))

    # failed（round 0）不 complete
    assert actions[0] != "complete"
    # partial_green（round 2）不 complete
    assert actions[2] != "complete", f"子集全绿不应 complete: {actions}"
    # full_green（round 3）complete
    assert actions[-1] == "complete", f"完整全绿应 complete: {actions}"

    # baseline 与 success_count 语义
    assert ctx.metadata["baseline_test_count"] == 9
    assert ctx.metadata["test_success_count"] == 1, (
        "只有 full_green 才应累计 success_count"
    )
    # partial_green 必须给模型明确反馈（结构化 recovery 状态）
    assert "[TEST_SCOPE_INCOMPLETE]" in _messages_text(ctx), (
        "partial_green 应注入 [TEST_SCOPE_INCOMPLETE] 结构化反馈"
    )
    # 最终 full_green 已清空 scope 标记，但历史必须出现过 partial_green
    assert any(h.get("scope_complete") is False for h in
               ctx.metadata["completion"].history), "历史中应存在 partial_green 记录"


def test_partial_green_alone_never_completes(tmp_path):
    """只有子集全绿、从不跑完整套件 → 永不 complete，只 continue。"""
    script = [
        [_pytest("test_all.py")],  # baseline=9, failed
        [_fix()],
        [_pytest("test_app.py")],  # 子集 7 → partial_green
        [_pytest("test_app.py")],  # 再子集 7 → 仍 partial_green
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script))

    assert actions[-1] != "complete", f"只子集全绿不应 complete: {actions}"
    assert ctx.metadata.get("test_success_count", 0) == 0
    assert ctx.metadata["baseline_test_count"] == 9
    # 结构化 recovery 状态：子集未覆盖完整基线 → 明确下一步
    assert ctx.metadata.get("test_scope_incomplete") is True
    assert ctx.metadata.get("required_next_action") == "run_full_test_suite"
    assert ctx.metadata.get("tests_passed") is False
    assert "[TEST_SCOPE_INCOMPLETE]" in _messages_text(ctx)


def test_failed_does_not_complete(tmp_path):
    """7 passed, 2 failed → failed → 不 complete。"""
    script = [
        [_pytest("test_all.py")],  # 7 passed, 2 failed
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script))
    assert actions[0] == "continue", f"failed 应 continue: {actions}"
    assert ctx.metadata.get("test_success_count", 0) == 0


def test_large_output_summary_still_parsed():
    """>10000 字符的 pytest 输出，最终 summary 仍可被 parse_test_totals 解析。"""
    big = ("A" * 20000) + "\n1302 passed, 9 skipped, 0 failed in 3.50s\n"
    capped = _cap_shell_output(big, "python -m pytest -q")
    assert len(capped) < len(big), "应发生截断"
    totals = parse_test_totals(capped)
    assert totals["passed"] == 1302, f"summary 应保留且可解析: {totals}"
    assert totals["failed"] == 0
    # 非 pytest 命令维持原样（仍截断，但无需保留尾部）
    plain = _cap_shell_output(big, "dir")
    assert len(plain) == 10000


def test_partial_green_exit0_does_not_complete():
    """partial_green 即便 exit_code=0 也不得 should_complete。"""
    c = CompletionState()
    c.record_test_result(exit_code=0, passed=True, step=1, scope_complete=False)
    assert c.tests_passed, "partial_green 仍是 passed"
    assert not c.tests_complete, "partial_green 不算 scope complete"
    assert not c.should_complete(), "partial_green 不得 complete"
    # full_green 才 complete
    c.record_test_result(exit_code=0, passed=True, step=2, scope_complete=True)
    assert c.should_complete(), "full_green 应 complete"
    # 向后兼容：不传 scope_complete 视为 full_green
    c2 = CompletionState()
    c2.record_test_result(exit_code=0, passed=True, step=1)
    assert c2.should_complete()


def test_full_green_sets_success_count_and_scope_complete(tmp_path):
    """TEST 6/7：完整 pytest 全绿后 test_success_count==1 且 should_complete()==True。"""
    script = [
        [_pytest("test_all.py")],  # baseline=9, failed
        [_fix()],
        [_pytest("test_all.py")],  # 9 passed → full_green
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script))
    assert actions[-1] == "complete"
    assert ctx.metadata["test_success_count"] == 1
    comp: CompletionState = ctx.metadata["completion"]
    assert comp.should_complete() is True
    assert comp.tests_complete is True
    assert ctx.metadata.get("test_scope_incomplete") is False


def test_complete_then_no_more_llm_or_tool(tmp_path):
    """TEST 4：complete 后不得再调用 LLM/tool（backend.invoke 不得再被调用）。"""
    script = [
        [_pytest("test_all.py")],  # baseline=9, failed
        [_fix()],
        [_pytest("test_all.py")],  # 9 passed → full_green → complete
        [_pytest("test_all.py")],  # 多余调用——应永远不会执行
        None,
    ]
    backend = _ScriptedBackend(script)
    # 直接驱动：记录 invoke 次数，complete 后必须不再增长
    _write_project(tmp_path)
    agent = SWEAgent("pg4")
    ctx = AgentContext(
        agent_id="pg4", task="修复 bug 使全部测试通过",
        backend=backend, tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    asyncio.run(agent.initialize(ctx))
    complete_step = None
    for i in range(6):
        action = asyncio.run(agent.step(ctx))
        if action.type == "complete":
            complete_step = i
            break
    assert complete_step is not None, "应 complete"
    invokes_after_complete = backend._i
    # complete 后 driver 已 break，不应再有额外 LLM/tool 调用
    assert complete_step <= 3, f"应在全绿 step 即 complete, 实际 step={complete_step}"
    assert invokes_after_complete == complete_step + 1, (
        f"complete 后不应再调用 backend: invokes={invokes_after_complete}"
    )


def test_stale_verification_does_not_override_green_completion(tmp_path):
    """mid-run 的一次过期 failed verification 不得覆盖合法全绿完成。

    回归：agent 已 full_green complete，但 metadata 里残留一次失败的
    _auto_verify 结果（如 "1/2 checks passed"），finalize 曾据此把
    COMPLETED 误判为 FAILED。修复后全绿为决定性信号。
    """
    from zmai.swe.verifier import VerificationCheck, VerificationResult
    script = [
        [_pytest("test_all.py")],  # baseline=9, failed
        [_fix()],
        [_pytest("test_all.py")],  # 9 passed → full_green → complete
        None,
    ]
    _write_project(tmp_path)
    backend = _ScriptedBackend(script)
    agent = SWEAgent("pg6")
    ctx = AgentContext(
        agent_id="pg6", task="修复 bug 使全部测试通过",
        backend=backend, tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )
    asyncio.run(agent.initialize(ctx))
    final = None
    for i in range(5):
        action = asyncio.run(agent.step(ctx))
        if i == 1:  # 模拟 mid-run 残留失败的 verification
            ctx.metadata["verification"] = VerificationResult(
                passed=False, summary="1/2 checks passed",
                checks=[VerificationCheck(name="x", strategy="exit_code", passed=False)])
        if action.type in ("complete", "fail"):
            final = asyncio.run(agent.finalize(ctx))
            break
    from zmai.agent import AgentState
    assert final is not None
    assert final.status == AgentState.COMPLETED, f"全绿完成不得被过期 verification 覆盖: {final.status}"  # noqa: E501
    assert ctx.metadata["test_success_count"] == 1


def test_loopguard_blocks_repeated_subset(tmp_path):
    """TEST 5：重复执行同一子集 → LoopGuard/scope recovery 阻止无限重复。"""
    script = [
        [_pytest("test_all.py")],  # baseline=9, failed
        [_fix()],
        [_pytest("test_app.py")],  # 子集 7 → partial_green
        [_pytest("test_app.py")],  # 再子集 → partial_green
        [_pytest("test_app.py")],  # 再子集
        None,
    ]
    # 低阈值让 LoopGuard 快速介入
    backend = _ScriptedBackend(script)
    agent = SWEAgent("pg5")
    _write_project(tmp_path)
    ctx = AgentContext(
        agent_id="pg5", task="修复 bug 使全部测试通过",
        backend=backend, tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 2},
        metadata={},
    )
    asyncio.run(agent.initialize(ctx))
    recovery = False
    for _ in range(8):
        action = asyncio.run(agent.step(ctx))
        if "[TestScope]" in _messages_text(ctx):
            recovery = True
        if action.type in ("complete", "fail"):
            break
    assert recovery, "重复子集测试应触发 scope-aware LoopRecovery"
    assert ctx.metadata.get("required_next_action") == "run_full_test_suite"
    assert action.type != "complete", "子集未达基线不得 complete"


# ══════════════════════════════════════════════════════════════════
# P1-2：首次子集测试锁定过小 baseline → 子集全绿即判定完成
# ══════════════════════════════════════════════════════════════════
# 复现：模型改完代码，**第一次**测试运行就是子集（`pytest -q test_a.py`，1/1 通过）。
# 修复前 baseline 被直接锁成 1，_scope_complete 保持 True → full_green → complete，
# 而项目里另外 2 个测试从未运行（且实际失败）。
# 目标语义：baseline 是"完整套件有多少测试"的断言，只有失败的诊断运行、或未指定
# 测试目标的裸运行才有资格建立它。


def _write_scope_project(tmp_path: Path) -> None:
    """完整套件 3 个测试全绿；test_sub.py 是其中 1 个测试的子集。"""
    (tmp_path / "bug.py").write_text("FIXED = True\n", encoding="utf-8")
    (tmp_path / "test_g.py").write_text(
        "def test_g1():\n    assert True\n\n\ndef test_g2():\n    assert True\n",
        encoding="utf-8")
    (tmp_path / "test_sub.py").write_text(
        "def test_sub():\n    assert True\n", encoding="utf-8")


def test_full_scope_command_detection():
    """裸运行（无测试目标）才是可证明的完整套件。"""
    from zmai.swe.agent import _is_full_scope_test_command as full

    assert full("python -m pytest -q")
    assert full("pytest")
    assert full("python -m pytest")
    # discover 是子命令，跑的就是全套件
    assert full("python -m unittest discover")
    # 显式指定目标 = 子集
    assert not full("python -m pytest -q test_a.py")
    assert not full("python -m pytest test_all.py -q")
    assert not full("pytest tests/")
    # -k/-m 选择器无法证明覆盖完整套件 → 同样按子集处理（会提示跑完整套件）
    assert not full("python -m pytest -k foo")


def test_first_green_subset_does_not_lock_baseline(tmp_path):
    """首次运行就是子集全绿（7/7 passed）→ 不得锁定 baseline，更不得 complete。"""
    script = [
        [_fix()],                  # 先产生修改（解除 eval 守卫）
        [_pytest("test_app.py")],  # **首次**测试运行：子集 7 全绿
        [_pytest("test_app.py")],  # 再跑一次子集，仍不得 complete
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script))

    assert "complete" not in actions, f"子集全绿不得 complete: {actions}"
    assert ctx.metadata.get("baseline_test_count") is None, (
        "首次即子集全绿，不得用 7 锁定完整 baseline"
    )
    assert ctx.metadata.get("test_scope_incomplete") is True
    assert ctx.metadata.get("test_success_count", 0) == 0
    # completion state 本身必须是 False —— 不只是 baseline 为 None
    comp: CompletionState = ctx.metadata["completion"]
    assert comp.tests_complete is False
    assert comp.should_complete() is False
    assert "[TEST_SCOPE_INCOMPLETE]" in _messages_text(ctx)


def test_first_green_full_suite_establishes_baseline(tmp_path):
    """首次运行的是完整套件且全绿 → 正常建立 baseline 并 complete（既有行为不变）。"""
    script = [
        [_fix()],
        [_bare()],   # 首次测试运行 = 完整套件（3 passed）
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script, project=_write_scope_project))

    assert actions[-1] == "complete", f"完整套件全绿应 complete: {actions}"
    assert ctx.metadata["baseline_test_count"] == 3
    assert ctx.metadata["test_success_count"] == 1
    assert ctx.metadata.get("test_scope_incomplete") is False


def test_subset_green_then_full_suite_green_completes(tmp_path):
    """子集全绿不 complete；随后完整套件全绿才拿到 completion 资格。"""
    script = [
        [_fix()],
        [_pytest("test_sub.py")],  # 子集 1/1 passed → partial，不建立 baseline
        [_bare()],                 # 完整套件 3 passed → 建立 baseline=3 → full_green
        None,
    ]
    ctx, actions = asyncio.run(_run(tmp_path, script, project=_write_scope_project))

    # 子集全绿那一步不得 complete
    assert actions[1] != "complete", f"子集全绿不应 complete: {actions}"
    # 完整套件全绿才 complete
    assert actions[-1] == "complete", f"完整全绿应 complete: {actions}"
    assert ctx.metadata["baseline_test_count"] == 3
    assert ctx.metadata["test_success_count"] == 1
    comp: CompletionState = ctx.metadata["completion"]
    assert comp.should_complete() is True
    assert comp.tests_complete is True
