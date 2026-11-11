"""M1/P0-4：统一终态判定器的最终验收测试。

覆盖：
  * 不同测试命令之间的回归不能被另一个命令的绿运行掩盖
  * 同一命令的后一次绿运行可以清除该范围的历史回归
  * 完整套件绿不能掩盖另一范围的未解决回归
  * 关闭回放不得让未验证证据完成/候选
  * finalize 的 FAILED 分支映射为 failed（不再与影子判定分歧）
  * 无测试任务的 legacy completed 与证据判定的已知差异
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.test_swe_evidence_decision import rec
from tests.test_swe_evidence_environment import (
    ScriptedBackend,
    _edit,
    call,
    pytest_cmd,
)
from zmai.agent import AgentContext
from zmai.swe.agent import SWEAgent
from zmai.swe.evidence import CompletionLedger, decide_terminal
from zmai.swe.models import MAX_REPLANS, Plan, PlanStep
from zmai.swe.verifier import VerificationResult
from zmai.tool import ToolRegistry


def _record(command: str, **kwargs):
    record = rec(**kwargs)
    record.command = command
    return record


# ═══════════════════════════════════════════════════════════════
# 判定反例
# ═══════════════════════════════════════════════════════════════


def test_other_command_green_cannot_mask_regression():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(_record(
        "python -m pytest test_regression.py -q",
        run_id="reg", scope="targeted", coverage="partial",
        exit_code=1, passed=0, failed=1, regression_detected=True))
    # 另一个命令的后一次绿运行不应覆盖上面的未解决回归。
    ledger.record(_record(
        "python -m pytest test_target.py -q",
        run_id="green", scope="targeted", coverage="partial"))

    decision = decide_terminal(ledger)
    assert decision.kind == "continue"
    assert "regression" in decision.reason


def test_same_command_green_clears_regression():
    ledger = CompletionLedger(ever_modified=True)
    cmd = "python -m pytest test_regression.py -q"
    ledger.record(_record(cmd, run_id="reg", scope="targeted", coverage="partial",
                          exit_code=1, passed=0, failed=1,
                          regression_detected=True))
    ledger.record(_record(cmd, run_id="green", scope="targeted",
                          coverage="partial"))
    assert decide_terminal(ledger).kind == "candidate"


def test_full_green_cannot_mask_regression_on_other_command():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(_record("python -m pytest -q", run_id="full",
                          scope="full", coverage="full"))
    ledger.record(_record(
        "python -m pytest test_other.py -q", run_id="reg",
        scope="targeted", coverage="partial",
        exit_code=1, passed=0, failed=1, regression_detected=True))
    assert decide_terminal(ledger).kind != "completed"


# ═══════════════════════════════════════════════════════════════
# 验证机制关闭 / finalize 终态映射
# ═══════════════════════════════════════════════════════════════


def _run_agent(project: Path, script, *, steps: int = 2, **config):
    ScriptedBackend.script = script
    agent = SWEAgent("term_probe")
    ctx = AgentContext(
        agent_id="term_probe", task="fix", backend=ScriptedBackend(),
        tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 60,
                "loop_guard.threshold": 50, **config},
        metadata={},
    )

    async def run():
        await agent.initialize(ctx)
        actions = []
        for _ in range(steps):
            action = await agent.step(ctx)
            actions.append(action)
            if action.type in ("complete", "candidate", "fail"):
                break
        return actions

    return ctx, asyncio.run(run())


def test_replay_disabled_blocks_unverified_completion(tmp_path):
    from tests.test_swe_evidence_environment import _SIMPLE, _project

    project = _project(tmp_path, _SIMPLE)
    ctx, actions = _run_agent(project, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ], **{"replay.enabled": "false"})

    assert actions[-1].type == "continue", actions
    record = ctx.metadata["evidence_ledger"].records[-1]
    assert record.replay.attempted is False
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind not in (
        "completed", "candidate")
    text = "\n".join(str(m.get("content", ""))
                     for m in ctx.metadata.get("messages", []))
    assert "replay disabled" in text


async def _finalize(project: Path, metadata: dict):
    agent = SWEAgent("fin_probe")
    ctx = AgentContext(agent_id="fin_probe", task="x", tools=ToolRegistry(),
                       config={"project_path": str(project)}, metadata=metadata)
    await agent.initialize(ctx)
    return await agent.finalize(ctx), ctx


def test_finalize_tool_failure_maps_to_failed(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    result, ctx = asyncio.run(_finalize(
        tmp_path, {"tool_calls_fail": 3, "tool_calls_ok": 0}))
    assert result.status.value == "failed"
    assert result.metadata["evidence_decision"]["kind"] == "failed"
    assert ctx.metadata["evidence_shadow"][-1]["agree"] is True


def test_finalize_verification_failure_maps_to_failed(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    failed = VerificationResult(passed=False, summary="1 check failed")
    result, ctx = asyncio.run(_finalize(tmp_path, {"verification": failed}))
    assert result.status.value == "failed"
    assert result.metadata["evidence_decision"]["kind"] == "failed"
    assert ctx.metadata["evidence_shadow"][-1]["agree"] is True


def test_finalize_plan_exhaustion_maps_to_failed(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    plan = Plan(goal="x", steps=[PlanStep(id=1, action="a")])
    result, ctx = asyncio.run(_finalize(tmp_path, {
        "replan_count": MAX_REPLANS, "execution_plan": plan}))
    assert result.status.value == "failed"
    assert result.metadata["evidence_decision"]["kind"] == "failed"
    assert ctx.metadata["evidence_shadow"][-1]["agree"] is True


def test_no_test_legacy_completion_is_a_known_difference(tmp_path):
    """无测试任务仍由旧逻辑完成；统一判定器没有证据可判 → continue。

    这是已知的语义边界（不是状态机缺陷）：若直接切换，非测试任务会被判为
    continue。需要先决定"无测试任务"的终态政策，才能切换控制权。
    """
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    result, ctx = asyncio.run(_finalize(tmp_path, {}))
    assert result.status.value == "completed"
    assert result.metadata["evidence_decision"]["kind"] == "continue"
    diff = ctx.metadata["evidence_shadow"][-1]
    assert (diff["old"], diff["new"], diff["agree"]) == (
        "completed", "continue", False)
