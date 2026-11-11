"""M1/P0-1：证据账本与统一终态判定回归测试。

覆盖：
  * 决策表互斥：completed / candidate / failed / timeout / continue
  * 证据失效：修改后 stale、篡改后 polluted，均不得完成或候选
  * 真实调用链：Runtime → SWEAgent → 测试结果 → 账本 → 影子判定
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

from zmai.agent import AgentContext
from zmai.config.config import Config
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.runtime import Runtime
from zmai.swe.agent import SWEAgent
from zmai.swe.evidence import (
    BaselineCompare,
    CompletionLedger,
    EvidenceRecord,
    Integrity,
    decide_terminal,
)
from zmai.tool import ToolCall, ToolRegistry

# ═══════════════════════════════════════════════════════════════
# 纯函数决策表
# ═══════════════════════════════════════════════════════════════


def rec(
    *,
    run_id: str = "r1",
    scope: str = "full",
    coverage: str = "full",
    status: str = "valid",
    exit_code: int = 0,
    passed: int = 3,
    failed: int = 0,
    new_failures: list[str] | None = None,
    integrity: Integrity | None = None,
    unverified_scope: str = "",
    regression_detected: bool = False,
) -> EvidenceRecord:
    return EvidenceRecord(
        run_id=run_id,
        step=1,
        command="python -m pytest -q",
        scope=scope,
        coverage=coverage,
        evidence_status=status,
        exit_code=exit_code,
        passed=passed,
        failed=failed,
        failed_ids=list(new_failures or []),
        baseline_compare=BaselineCompare(new_failures=list(new_failures or [])),
        # P0-2：可支撑 derived 终态的证据必须 nonce + 回放均通过。
        integrity=integrity or Integrity(nonce_verified=True, replay_verified=True),
        unverified_scope=unverified_scope,
        regression_detected=regression_detected,
    )


def kinds(ledger: CompletionLedger) -> list[str]:
    return [r.evidence_status for r in ledger.records]


def test_completed_requires_latest_full_scope_valid_green():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec())
    # 即使同时存在定向绿，只要完整范围证据有效，仍判 completed。
    ledger.record(rec(run_id="r2", scope="targeted", coverage="partial"))
    assert decide_terminal(ledger).kind == "completed"

    # 完整范围运行失败 → 不得 completed（即使之前有过全绿）。
    ledger.record(rec(run_id="r3", exit_code=1, passed=2, failed=1))
    assert decide_terminal(ledger).kind != "completed"


def test_coverage_partial_cannot_complete_but_can_candidate():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec(scope="full", coverage="partial"))
    assert decide_terminal(ledger).kind == "candidate"


def test_candidate_requires_post_edit_local_green():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec(scope="targeted", coverage="partial",
                      unverified_scope="full suite"))
    decision = decide_terminal(ledger)
    assert decision.kind == "candidate"
    assert decision.unverified_scope == "full suite"

    # 没有修改 → 局部绿不能成为候选。
    untouched = CompletionLedger(ever_modified=False)
    untouched.record(rec(scope="targeted", coverage="partial"))
    assert decide_terminal(untouched).kind == "continue"


def test_new_failures_block_candidate():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec(scope="targeted", coverage="partial",
                      unverified_scope="full suite"))
    ledger.record(rec(run_id="r2", scope="full", coverage="full",
                      exit_code=1, passed=2, failed=1,
                      new_failures=["test_new"]))
    decision = decide_terminal(ledger)
    assert decision.kind == "continue"
    assert "test_new" in decision.reason


def test_unclassified_regression_blocks_candidate():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec(scope="targeted", coverage="partial",
                      unverified_scope="full suite"))
    ledger.record(rec(run_id="r2", scope="full", coverage="full",
                      exit_code=1, passed=2, failed=1,
                      regression_detected=True))
    decision = decide_terminal(ledger)
    assert decision.kind == "continue"
    assert "regression" in decision.reason


def test_stale_evidence_is_discarded_after_modification():
    ledger = CompletionLedger()
    ledger.record(rec())
    ledger.record_modification(step=3)
    assert kinds(ledger) == ["stale"]
    assert decide_terminal(ledger).kind == "continue"

    ledger.record(rec(run_id="r2", scope="targeted", coverage="partial"))
    assert kinds(ledger) == ["stale", "valid"]
    assert decide_terminal(ledger).kind == "candidate"


def test_polluted_evidence_cannot_complete_or_candidate():
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec())
    ledger.mark_polluted("test files rewritten")
    assert kinds(ledger) == ["polluted"]
    assert decide_terminal(ledger).kind == "continue"

    # 运行期间就被污染的证据同样不可用。
    ledger.record(rec(run_id="r2", status="polluted"))
    assert decide_terminal(ledger).kind == "continue"


def test_integrity_failures_block_completed_and_candidate():
    for broken in (
        Integrity(test_files_unchanged=False, nonce_verified=True,
                  replay_verified=True),
        Integrity(config_unchanged=False, nonce_verified=True,
                  replay_verified=True),
        Integrity(no_scope_narrowing=False, nonce_verified=True,
                  replay_verified=True),
        Integrity(ran_after_last_edit=False, nonce_verified=True,
                  replay_verified=True),
        Integrity(nonce_verified=False, replay_verified=True),
        Integrity(nonce_verified=True, replay_verified=False),
    ):
        ledger = CompletionLedger(ever_modified=True)
        ledger.record(rec(integrity=broken))
        assert decide_terminal(ledger).kind != "completed", broken
        targeted = CompletionLedger(ever_modified=True)
        targeted.record(rec(scope="targeted", coverage="partial",
                            integrity=broken))
        assert decide_terminal(targeted).kind != "candidate", broken

    # P0-2：nonce 不适用（None）可以，但回放必须实际通过。
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec(integrity=Integrity(nonce_verified=None,
                                          replay_verified=True)))
    assert decide_terminal(ledger).kind == "completed"

    ledger = CompletionLedger(ever_modified=True)
    ledger.record(rec(integrity=Integrity(nonce_verified=True,
                                          replay_verified=None)))
    assert decide_terminal(ledger).kind == "continue"


def test_timeout_and_fatal_outrank_surviving_evidence():
    timeout = CompletionLedger(ever_modified=True)
    timeout.record(rec())
    timeout.mark_budget_exhausted()
    assert decide_terminal(timeout).kind == "timeout"

    failed = CompletionLedger(ever_modified=True)
    failed.record(rec())
    failed.mark_fatal("step failed")
    assert decide_terminal(failed).kind == "failed"

    assert decide_terminal(CompletionLedger()).kind == "continue"


def test_terminal_kinds_are_mutually_exclusive():
    seen = set()
    for ledger in (
        CompletionLedger(ever_modified=True, records=[rec()]),
        CompletionLedger(ever_modified=True,
                         records=[rec(scope="targeted", coverage="partial")]),
        CompletionLedger(ever_modified=True,
                         records=[rec(exit_code=1, passed=0, failed=1)]),
    ):
        decision = decide_terminal(ledger)
        assert decision.kind in {"completed", "candidate", "continue"}
        seen.add(decision.kind)
    assert seen == {"completed", "candidate", "continue"}

    for ledger, expected in (
        (CompletionLedger(budget_exhausted=True), "timeout"),
        (CompletionLedger(fatal_error="boom"), "failed"),
    ):
        assert decide_terminal(ledger).kind == expected


# ═══════════════════════════════════════════════════════════════
# 真实调用链：Runtime → SWEAgent → 账本 → 影子判定
# ═══════════════════════════════════════════════════════════════


def call(name: str, **params) -> ToolCall:
    return ToolCall(id=name, name=name, params=params)


def pytest_cmd(*args: str) -> str:
    """固定用当前解释器，避免测试环境 PATH 指向无 pytest 的 Python。"""
    return " ".join([f'"{sys.executable}"', "-m", "pytest", *args])


class ScriptedBackend(Backend):
    name = "scripted_evidence"
    script: list[ToolCall | str] = []

    def __init__(self, config=None):
        self.index = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        action = self.script[self.index]
        self.index += 1
        return BackendResponse(
            content=action if isinstance(action, str) else "",
            tool_calls=None if isinstance(action, str) else [action],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason="end_turn" if isinstance(action, str) else "tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _run(tmp_path: Path, script: list[ToolCall | str], *,
         extra_failure: bool = False) -> dict:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("FIXED = False\n", encoding="utf-8")
    (source / "test_target.py").write_text(
        "import app\ndef test_target():\n    assert app.FIXED\n", encoding="utf-8")
    if extra_failure:
        (source / "test_extra.py").write_text(
            "def test_extra():\n    assert False\n", encoding="utf-8")
    ScriptedBackend.script = script
    cfg = Config(sources=[])
    cfg.set("runtime.max_iterations", len(script) + 2)
    cfg.set("workspace.root", str(tmp_path / "runtime_workspace"))
    runtime = Runtime(config=cfg)
    runtime._gateway.register(ScriptedBackend.name, ScriptedBackend)
    return asyncio.run(runtime.run(
        agent_id="evidence_probe", task="Fix the failing test",
        backend=ScriptedBackend.name, config={"project_path": str(source)},
    ))


def _edit() -> ToolCall:
    return call("edit", path="app.py", mode="regex_replace",
                old_text="FIXED = False", new_text="FIXED = True")


def test_runtime_full_scope_green_completes_and_records_evidence(tmp_path):
    result = _run(tmp_path, [
        _edit(),
        call("shell_exec", command=pytest_cmd("-q")),
    ])

    assert result["status"] == "completed", result
    meta = result["metadata"]
    assert meta["evidence_decision"]["kind"] == "completed"
    ledger = meta["evidence_ledger"]
    assert ledger["ever_modified"] is True
    assert len(ledger["records"]) == 1
    record = ledger["records"][0]
    assert record["scope"] == "full"
    assert record["coverage"] == "full"
    assert record["evidence_status"] == "valid"
    assert record["exit_code"] == 0
    # P0-2：nonce 与独立回放都必须通过，并记录实际回放成本。
    assert record["integrity"]["nonce_verified"] is True
    assert record["integrity"]["replay_verified"] is True
    assert record["replay"]["attempted"] is True
    assert record["replay"]["verified"] is True
    assert record["replay"]["duration_ms"] >= 0
    assert len(record["nonce"]) == 32
    assert record["replay"]["nonce"] != record["nonce"]
    assert ledger["replay_attempts"] >= 1
    assert ledger["replay_total_ms"] >= record["replay"]["duration_ms"]
    assert any(s["point"] == "post_tool_complete" and s["agree"]
               for s in meta["evidence_shadow"])


def test_runtime_candidate_after_edit_and_stale_pre_edit_evidence(tmp_path):
    result = _run(tmp_path, [
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
        call("read_file", path="app.py"),
        _edit(),
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
        call("shell_exec", command=pytest_cmd("-q")),
        "SUBMIT_CANDIDATE",
    ], extra_failure=True)

    assert result["status"] == "candidate", result
    meta = result["metadata"]
    assert meta["evidence_decision"]["kind"] == "candidate"
    assert meta["evidence_decision"]["unverified_scope"] == "full suite"

    records = meta["evidence_ledger"]["records"]
    # 编辑前的失败证据被后续修改作废；编辑后的定向绿有效且回放通过。
    assert records[0]["evidence_status"] == "stale"
    green = next(r for r in records if r["scope"] == "targeted"
                 and r["evidence_status"] == "valid" and r["exit_code"] == 0)
    assert green["integrity"]["nonce_verified"] is True
    assert green["integrity"]["replay_verified"] is True
    assert green["replay"]["verified"] is True
    assert meta["evidence_ledger"]["replay_attempts"] >= 1
    assert any(s["point"] == "candidate_submit" and s["agree"]
               for s in meta["evidence_shadow"])


def test_runtime_records_polluted_evidence_when_scope_config_changes(tmp_path):
    (tmp_path / "app.py").write_text("FIXED = True\n", encoding="utf-8")
    (tmp_path / "test_target.py").write_text(
        "import app\ndef test_target():\n    assert app.FIXED\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = ""\n', encoding="utf-8")
    ScriptedBackend.script = [
        call("shell_exec", command=pytest_cmd("test_target.py", "-q")),
    ]
    agent = SWEAgent("evidence_polluted")
    ctx = AgentContext(
        agent_id="evidence_polluted",
        task="verify",
        backend=ScriptedBackend(),
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 60,
                "loop_guard.threshold": 50},
        metadata={},
    )

    async def run():
        await agent.initialize(ctx)
        # pytest 收集范围配置在测试运行前被改动 —— 走真实的 scope 基线检测路径。
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\naddopts = "--strict-markers"\n',
            encoding="utf-8")
        return await agent.step(ctx)

    action = asyncio.run(run())
    assert action.type == "continue"
    records = ctx.metadata["evidence_ledger"].records
    assert records and records[0].evidence_status == "polluted"
    assert records[0].integrity.config_unchanged is False
    assert decide_terminal(ctx.metadata["evidence_ledger"]).kind == "continue"
