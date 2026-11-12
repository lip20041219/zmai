from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

from zmai.agent import AgentContext
from zmai.gateway.base import (Backend, BackendCapability, BackendEvent,
                               BackendRequest, BackendResponse, TokenUsage)
from zmai.swe.evidence import CompletionLedger, EvidenceRecord, Integrity, decide_terminal
from zmai.swe.agent import SWEAgent
from zmai.swe.tools import ShellTool
from zmai.tool import ToolCall, ToolContext, ToolRegistry


FULL = "python -m pytest -q"


class _BudgetBackend(Backend):
    name = "budget_guard_test"
    action = ToolCall(id="full-suite", name="shell_exec", params={"command": FULL})

    def invoke(self, request: BackendRequest) -> BackendResponse:
        return BackendResponse(content="", tool_calls=[self.action],
                               usage=TokenUsage(input_tokens=1, output_tokens=1),
                               stop_reason="tool_use")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _context(tmp_path, *, remaining: float, ledger=None, candidate=False,
             fallback: int = 60) -> ToolContext:
    return ToolContext(
        agent_id="budget-test",
        workspace_path=tmp_path,
        project_path=tmp_path,
        config={"_wall_deadline_monotonic": time.monotonic() + remaining,
                "timeout.test": fallback, "_quiet": True},
        evidence_ledger=ledger,
        candidate_evidence_available=candidate,
    )


def _history(command=FULL, duration_ms=10_000) -> CompletionLedger:
    return CompletionLedger(records=[EvidenceRecord(
        run_id="previous", step=1, command=command, duration_ms=duration_ms,
        scope="full", coverage="full", exit_code=0, passed=3,
    )])


def _candidate_history() -> CompletionLedger:
    record = EvidenceRecord(
        run_id="targeted", step=2, command="pytest tests/test_fix.py -q",
        scope="targeted", coverage="partial", exit_code=0, passed=1,
        integrity=Integrity(),
    )
    return CompletionLedger(records=[_history(duration_ms=30_000).records[0], record],
                            ever_modified=True)


def test_full_suite_runs_when_estimated_cost_fits(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "3 passed in 0.2s", "")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", run)
    result = ShellTool().execute(_context(tmp_path, remaining=30,
                                         ledger=_history()), {"command": FULL})
    assert calls == [FULL]
    assert result.success


def test_full_suite_is_blocked_before_process_when_history_says_budget_is_short(
        tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("full suite process must not start")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", unexpected)
    result = ShellTool().execute(_context(tmp_path, remaining=30,
                                         ledger=_history(duration_ms=30_000)),
                                 {"command": FULL})
    assert not result.success
    assert result.metadata["budget_guard"] == "full_suite"
    assert result.metadata["blocked"] is True
    assert result.metadata["estimate_source"] == "same_command_history"


def test_no_history_allows_short_suite_to_establish_duration(
        tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "1 passed in 0.1s", "")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", run)
    result = ShellTool().execute(_context(tmp_path, remaining=80), {"command": FULL})
    assert result.success
    assert calls == [FULL]


def test_stale_run_duration_can_block_but_cannot_support_candidate(tmp_path, monkeypatch):
    ledger = _history(duration_ms=30_000)
    ledger.records[0].evidence_status = "stale"
    ledger.ever_modified = True

    def unexpected(*args, **kwargs):
        raise AssertionError("long full suite process must not start")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", unexpected)
    result = ShellTool().execute(_context(tmp_path, remaining=30, ledger=ledger,
                                         candidate=True), {"command": FULL})
    assert result.metadata["estimate_source"] == "same_command_history"
    assert result.metadata["candidate_evidence_available"] is False
    assert "不得提交 candidate" in result.error
    assert decide_terminal(ledger).kind == "continue"


def test_insufficient_budget_guides_candidate_only_with_usable_targeted_evidence(
        tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("full suite process must not start")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", unexpected)
    result = ShellTool().execute(
        _context(tmp_path, remaining=30, ledger=_candidate_history(), candidate=True),
        {"command": FULL},
    )
    assert not result.success
    assert result.metadata["candidate_evidence_available"] is True
    assert "SUBMIT_CANDIDATE" in result.error


def test_targeted_test_is_not_blocked_by_full_suite_budget_guard(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "1 passed in 0.1s", "")

    monkeypatch.setattr("zmai.swe.tools._run_shell_command", run)
    command = "python -m pytest tests/test_fix.py -q"
    result = ShellTool().execute(_context(tmp_path, remaining=10), {"command": command})
    assert calls == [command]
    assert result.success


def test_agent_step_passes_ledger_and_wall_budget_into_shell_guard(tmp_path, monkeypatch):
    observed = []
    execute = ShellTool.execute

    def capture_context(self, context, params):
        observed.append(context)
        return execute(self, context, params)

    def unexpected(*args, **kwargs):
        raise AssertionError("budget guard should prevent subprocess launch")

    monkeypatch.setattr(ShellTool, "execute", capture_context)
    monkeypatch.setattr("zmai.swe.tools._run_shell_command", unexpected)

    async def run_step():
        agent = SWEAgent("budget-agent")
        context = AgentContext(
            agent_id="budget-agent", task="Run the full suite", backend=_BudgetBackend(),
            tools=ToolRegistry(), workspace=tmp_path,
            config={"project_path": str(tmp_path), "timeout.test": 5,
                    "_wall_deadline_monotonic": time.monotonic() + 2},
            metadata={"evidence_ledger": _history(duration_ms=30_000)},
        )
        await agent.initialize(context)
        action = await agent.step(context)
        assert action.type == "continue"
        return context

    context = asyncio.run(run_step())
    assert len(observed) == 1
    assert observed[0].evidence_ledger is context.metadata["evidence_ledger"]
    assert observed[0].candidate_evidence_available is False
    assert observed[0].config["_wall_deadline_monotonic"] == \
        context.config["_wall_deadline_monotonic"]
