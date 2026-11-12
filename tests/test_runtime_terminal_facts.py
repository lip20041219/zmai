"""P0-5: Runtime direct exits always retain an observed terminal fact."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

from zmai.agent import AgentAction, AgentResult, AgentState
from zmai.cli.formatters import render_summary
from zmai.config import Config
from zmai.gateway import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
)
from zmai.runtime.runtime import Runtime
from zmai.swe.evidence import (
    COVERAGE_FULL,
    COVERAGE_PARTIAL,
    SCOPE_FULL,
    SCOPE_TARGETED,
    EvidenceRecord,
    Integrity,
)


class _Backend(Backend):
    name = "terminal_facts_test"

    def __init__(self, config=None):
        pass

    def invoke(self, request: BackendRequest) -> BackendResponse:
        return BackendResponse(content="unused")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


class _Agent:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    async def initialize(self, context):
        self.context = context
        if context.task == "initialize_exception":
            raise RuntimeError("initialize exploded")
        ledger = context.metadata["evidence_ledger"]
        if context.task.startswith("no_test_objective"):
            ledger.no_tests_confirmed = True
            ledger.objective_verification_required = True
            if context.task == "no_test_objective_complete":
                ledger.record_objective_checks([
                    {"name": "file-check", "passed": True},
                ])
        if context.task in ("complete", "proposal_failed_but_evidence_completed"):
            ledger.record(EvidenceRecord(
                run_id="full-green", step=1, command="pytest -q",
                scope=SCOPE_FULL, coverage=COVERAGE_FULL, exit_code=0,
                passed=1, executed_tests=1,
                integrity=Integrity(replay_verified=True),
            ))
        elif context.task == "candidate":
            ledger.record_modification(0)
            ledger.record(EvidenceRecord(
                run_id="targeted-green", step=1, command="pytest test_fix.py",
                scope=SCOPE_TARGETED, coverage=COVERAGE_PARTIAL, exit_code=0,
                passed=1, executed_tests=1,
                integrity=Integrity(replay_verified=True),
            ))

    async def step(self, context):
        if context.task == "exception":
            raise RuntimeError("step exploded")
        if context.task == "cancel":
            await asyncio.Event().wait()
        if context.task in ("candidate", "unverified_candidate"):
            return AgentAction.candidate("candidate")
        if context.task == "failed":
            return AgentAction.fail("failed at step")
        return AgentAction.complete("done")

    async def finalize(self, context):
        if context.metadata.get("timed_out"):
            status = AgentState.TIMEOUT
        elif context.task == "candidate":
            status = AgentState.CANDIDATE
        elif context.task in ("failed", "proposal_failed_but_evidence_completed"):
            status = AgentState.FAILED
        else:
            status = AgentState.COMPLETED
        return AgentResult(agent_id=self.agent_id, status=status)


def _runtime(monkeypatch) -> Runtime:
    runtime = Runtime(Config(sources=[]))
    runtime._gateway.register("terminal_facts_test", _Backend, default=True)
    monkeypatch.setattr("zmai.swe.agent.SWEAgent", _Agent)
    return runtime


def _last_fact(result) -> dict:
    metadata = result.get("metadata", result)
    facts = metadata["evidence_ledger"]["terminal_facts"]
    assert facts
    return facts[-1]


def test_runtime_records_normal_and_agent_terminal_returns(monkeypatch):
    for requested, expected in (("complete", "completed"),
                                ("candidate", "candidate"),
                                ("failed", "failed")):
        runtime = _runtime(monkeypatch)
        result = asyncio.run(runtime.run(requested, requested, backend="terminal_facts_test"))
        assert result["status"] == expected
        assert _last_fact(result)["status"] == expected
        assert result["metadata"]["evidence_shadow"][-1]["point"] == (
            "runtime_terminal_authority")
        assert result["metadata"]["evidence_shadow"][-1]["agree"] is True
        report = result["delivery_report"]
        assert report["status"] == "generated"
        report_dir = runtime._workspace._root / requested / report["directory"]
        assert (report_dir / "report.json").is_file()
        assert (report_dir / "report.md").is_file()
        report_data = json.loads((report_dir / "report.json").read_text("utf-8"))
        assert report_data["terminal"]["status"] == expected
        assert report_data["verification"]["external_acceptance"] == "not_run"
        assert report_data["run"]["execution_log"]["status"] == "available"
        assert "REPORT: generated" in render_summary(result)
        if requested == "complete":
            assert report_data["verification"]["evidence_status"] == "valid_complete"
        elif requested == "candidate":
            assert report_data["verification"]["evidence_status"] == "valid_incomplete"


def test_runtime_terminal_authority_ignores_finalize_proposal(monkeypatch):
    runtime = _runtime(monkeypatch)
    result = asyncio.run(runtime.run(
        "proposal", "proposal_failed_but_evidence_completed",
        backend="terminal_facts_test"))
    assert result["status"] == "completed"
    assert result["metadata"]["terminal_legacy_status"] == "failed"
    assert result["metadata"]["evidence_decision"]["kind"] == "completed"
    assert result["metadata"]["evidence_shadow"][-1]["agree"] is False


def test_runtime_refuses_unverified_completed_and_candidate_proposals(monkeypatch):
    for task in ("unverified_complete", "unverified_candidate"):
        runtime = _runtime(monkeypatch)
        result = asyncio.run(runtime.run(task, task, backend="terminal_facts_test"))
        assert result["status"] == "failed"
        assert result["metadata"]["terminal_legacy_status"] in (
            "completed", "candidate")
        assert result["metadata"]["evidence_decision"]["kind"] == "failed"
        assert result["metadata"]["evidence_shadow"][-1]["agree"] is False
        report = result["delivery_report"]
        report_dir = runtime._workspace._root / task / report["directory"]
        report_data = json.loads((report_dir / "report.json").read_text("utf-8"))
        assert report_data["terminal"]["status"] == "failed"
        assert report_data["terminal"]["decision_kind"] == "failed"


def test_runtime_uses_objective_checks_for_confirmed_no_test_projects(monkeypatch):
    runtime = _runtime(monkeypatch)
    verified = asyncio.run(runtime.run(
        "no_test_verified", "no_test_objective_complete",
        backend="terminal_facts_test"))
    assert verified["status"] == "completed"
    assert verified["metadata"]["evidence_decision"]["kind"] == "completed"
    report_dir = (
        runtime._workspace._root
        / verified["delivery_report"]["workspace_directory"]
        / verified["delivery_report"]["directory"]
    )
    verified_report = json.loads((report_dir / "report.json").read_text("utf-8"))
    assert verified_report["verification"]["evidence_status"] == "objective_checks_passed"
    assert verified_report["verification"]["objective_checks"][0]["passed"] is True

    runtime = _runtime(monkeypatch)
    unverified = asyncio.run(runtime.run(
        "no_test_unverified", "no_test_objective_missing",
        backend="terminal_facts_test"))
    assert unverified["status"] == "failed"
    assert unverified["metadata"]["evidence_decision"]["kind"] == "failed"


def test_runtime_records_timeout_and_exception(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._config.set("runtime.max_iterations", 0)
    timeout = asyncio.run(runtime.run("timeout_fact", "timeout",
                                      backend="terminal_facts_test"))
    assert timeout["status"] == "timeout"
    assert _last_fact(timeout)["status"] == "timeout"
    assert timeout["metadata"]["evidence_shadow"][-1]["agree"] is True
    assert timeout["delivery_report"]["status"] == "generated"

    runtime = _runtime(monkeypatch)
    exception = asyncio.run(runtime.run("exception_fact", "exception",
                                        backend="terminal_facts_test"))
    assert exception["status"] == "failed"
    assert _last_fact(exception)["status"] == "exception"
    assert exception["metadata"]["evidence_decision"]["kind"] == "failed"
    assert exception["metadata"]["evidence_shadow"][-1]["agree"] is True
    assert exception["delivery_report"]["status"] == "generated"

    runtime = _runtime(monkeypatch)
    init_exception = asyncio.run(runtime.run(
        "initialize_exception", "initialize_exception",
        backend="terminal_facts_test"))
    assert init_exception["status"] == "failed"
    assert init_exception["metadata"]["evidence_shadow"][-1]["point"] == (
        "runtime_initialize")
    assert init_exception["delivery_report"]["status"] == "generated"


def test_runtime_records_cancellation(monkeypatch):
    runtime = _runtime(monkeypatch)

    async def run_and_cancel():
        task = asyncio.create_task(runtime.run(
            "cancel_fact", "cancel", backend="terminal_facts_test"))
        await asyncio.sleep(0.05)
        await runtime.cancel("cancel_fact")
        return await task

    result = asyncio.run(run_and_cancel())
    assert result["status"] == "cancelled"
    assert _last_fact(result)["status"] == "cancelled"
    assert result["metadata"]["evidence_shadow"][-1]["agree"] is True
    assert result["delivery_report"]["status"] == "generated"


def test_runtime_preflight_and_schedule_direct_failures_are_recorded(monkeypatch):
    runtime = Runtime(Config(sources=[]))
    preflight = asyncio.run(runtime.run("preflight", "unused", backend="missing"))
    assert preflight["status"] == "failed"
    assert _last_fact(preflight)["status"] == "failed"
    assert preflight["evidence_shadow"][-1]["agree"] is True
    assert preflight["delivery_report"]["status"] == "generated"
    preflight_report_dir = (
        runtime._workspace._root
        / preflight["delivery_report"]["workspace_directory"]
        / preflight["delivery_report"]["directory"]
    )
    preflight_report = json.loads((preflight_report_dir / "report.json").read_text("utf-8"))
    assert preflight_report["terminal"]["decision_kind"] == "failed"
    assert preflight_report["terminal"]["facts"]

    runtime = _runtime(monkeypatch)

    async def reject_schedule(agent_id, coro):
        raise RuntimeError("schedule rejected")

    monkeypatch.setattr(runtime._scheduler, "schedule", reject_schedule)
    scheduled = asyncio.run(runtime.run(
        "schedule", "unused", backend="terminal_facts_test"))
    assert scheduled["status"] == "failed"
    assert _last_fact(scheduled)["status"] == "exception"
    assert scheduled["evidence_shadow"][-1]["agree"] is True
    assert scheduled["delivery_report"]["status"] == "generated"


def test_report_generation_failure_does_not_change_runtime_terminal(monkeypatch, tmp_path):
    from zmai.workspace import Workspace

    runtime = _runtime(monkeypatch)
    runtime._workspace = Workspace(root=tmp_path / "workspace")

    def fail_report(**kwargs):
        raise OSError("report disk write failed")

    monkeypatch.setattr("zmai.swe.delivery_report.create_delivery_report", fail_report)
    result = asyncio.run(runtime.run("report_error", "complete",
                                     backend="terminal_facts_test"))
    assert result["status"] == "completed"
    assert result["metadata"]["evidence_decision"]["kind"] == "completed"
    assert result["delivery_report"]["status"] == "failed"
