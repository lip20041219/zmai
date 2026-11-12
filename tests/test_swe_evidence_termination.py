"""M1/P0-4：统一终态判定器的最终验收测试。

覆盖：
  * 不同测试命令之间的回归不能被另一个命令的绿运行掩盖
  * 同一命令的后一次绿运行可以清除该范围的历史回归
  * 完整套件绿不能掩盖另一范围的未解决回归
  * 关闭回放不得让未验证证据完成/候选
  * finalize 不再自行决定 SWE 终态
  * 无测试任务需要客观检查证据
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


def _run_agent(project: Path, script, *, steps: int = 2,
               initial_metadata: dict | None = None, **config):
    ScriptedBackend.script = script
    agent = SWEAgent("term_probe")
    ctx = AgentContext(
        agent_id="term_probe", task="fix", backend=ScriptedBackend(),
        tools=ToolRegistry(),
        config={"project_path": str(project), "timeout": 60,
                "loop_guard.threshold": 50, **config},
        metadata=dict(initial_metadata or {}),
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


def test_finalize_does_not_decide_tool_failure_terminal(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    result, ctx = asyncio.run(_finalize(
        tmp_path, {"tool_calls_fail": 3, "tool_calls_ok": 0}))
    assert result.status.value == "executing"
    assert ctx.metadata["evidence_ledger"].terminal_facts == []
    assert "evidence_decision" not in result.metadata


def test_finalize_does_not_decide_verification_failure_terminal(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    failed = VerificationResult(passed=False, summary="1 check failed")
    result, ctx = asyncio.run(_finalize(tmp_path, {"verification": failed}))
    assert result.status.value == "executing"
    assert ctx.metadata["evidence_ledger"].terminal_facts == []
    assert "evidence_decision" not in result.metadata


def test_finalize_does_not_decide_plan_exhaustion_terminal(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    plan = Plan(goal="x", steps=[PlanStep(id=1, action="a")])
    result, ctx = asyncio.run(_finalize(tmp_path, {
        "replan_count": MAX_REPLANS, "execution_plan": plan}))
    assert result.status.value == "executing"
    assert ctx.metadata["evidence_ledger"].terminal_facts == []
    assert "evidence_decision" not in result.metadata


def test_plan_exhaustion_emits_failure_action_at_detection(tmp_path):
    plan = Plan(goal="x", steps=[PlanStep(id=1, action="unfinished")])
    _, actions = _run_agent(
        tmp_path, ["continue"], steps=1,
        initial_metadata={"replan_count": MAX_REPLANS, "execution_plan": plan})
    assert actions[-1].type == "fail"
    assert "replans" in (actions[-1].error or "")


def test_finalize_leaves_no_test_completion_to_runtime_authority(tmp_path):
    """已确认无测试时，finalize 不得自行完成或补写失败事实。"""
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    result, ctx = asyncio.run(_finalize(tmp_path, {}))
    assert result.status.value == "executing"
    assert ctx.metadata["evidence_ledger"].objective_verification_required is True
    assert ctx.metadata["evidence_ledger"].terminal_facts == []
    assert "evidence_decision" not in result.metadata


def test_no_test_model_text_alone_cannot_complete(tmp_path):
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    ctx, actions = _run_agent(tmp_path, ["task done"], steps=1)
    assert actions[-1].type == "continue"
    assert ctx.metadata["evidence_ledger"].no_tests_confirmed is True
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind != "completed"


def test_no_test_objective_checks_are_completion_evidence():
    ledger = CompletionLedger(no_tests_confirmed=True,
                              objective_verification_required=True)
    ledger.record_objective_checks([
        {"strategy": "file_exists", "passed": True},
        {"strategy": "exit_code", "passed": True},
    ])
    assert decide_terminal(ledger).kind == "completed"


def test_runtime_terminal_facts_drive_failure_timeout_and_cancelled_shadow():
    for status, kind in (("failed", "failed"), ("exception", "failed"),
                         ("timeout", "timeout"), ("cancelled", "cancelled")):
        ledger = CompletionLedger()
        ledger.record_terminal(status, "observed", source="runtime", step=3)
        assert ledger.terminal_facts[-1].source == "runtime"
        assert decide_terminal(ledger).kind == kind
