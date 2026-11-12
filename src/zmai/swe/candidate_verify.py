"""Offline full-suite verification of an immutable candidate report."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from zmai.config import Config
from zmai.gateway.base import (Backend, BackendCapability, BackendEvent,
                               BackendRequest, BackendResponse, TokenUsage)
from zmai.runtime import Runtime
from zmai.swe.agent import _WS_IGNORE_DIRS, _replay_safe_command
from zmai.swe.delivery_report import (WorkspaceSnapshot, _atomic_write,
                                      _collect_changes, capture_workspace)
from zmai.swe.tools import ShellTool
from zmai.tool import ToolCall


class _VerifyBackend(Backend):
    name = "candidate_verifier"
    command = ""

    def __init__(self, config=None):
        self.calls = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.calls += 1
        if self.calls == 1:
            return BackendResponse(
                content="", tool_calls=[ToolCall(id="verify-suite", name="shell_exec",
                                                 params={"command": self.command})],
                usage=TokenUsage(), stop_reason="tool_use")
        return BackendResponse(content="Verification finished", tool_calls=None,
                               usage=TokenUsage(), stop_reason="end_turn")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _patch_identity(project: Path, base_commit: str, *, ignore_runtime: bool = True) -> str | None:
    snapshot = capture_workspace(project)
    if snapshot.error or snapshot.git_root != project or snapshot.commit != base_commit:
        return None
    if ignore_runtime:
        snapshot = replace(snapshot, status=tuple(
            (code, path) for code, path in snapshot.status
            if not any(part in _WS_IGNORE_DIRS for part in Path(path).parts)
        ))
    baseline = WorkspaceSnapshot(snapshot.root, snapshot.git_root, base_commit, ())
    patch = _collect_changes(baseline, snapshot, project).get("patch", {})
    return patch.get("sha256") if patch.get("status") == "available" else None


def verify_candidate(report_path: Path, project_path: Path, command: str) -> dict:
    """Create a separate upgrade record; never rewrite the candidate report."""
    report_path = report_path.resolve()
    project = project_path.resolve()
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "rejected", "reason": f"candidate report unavailable: {type(exc).__name__}"}
    if (report.get("terminal", {}).get("status") != "candidate"
            or report.get("terminal", {}).get("decision_kind") != "candidate"):
        return {"status": "rejected", "reason": "report is not a candidate"}
    source_records = report.get("verification", {}).get("tests", {}).get("records", [])
    if not any(
        r.get("evidence_status") == "valid" and r.get("scope") != "full"
        and r.get("exit_code") == 0 and (r.get("passed") or 0) > 0
        and r.get("failed") == 0 and r.get("integrity", {}).get("replay_verified") is True
        and all(r.get("integrity", {}).get(key) is True for key in (
            "test_files_unchanged", "config_unchanged", "no_scope_narrowing",
            "ran_after_last_edit"))
        and r.get("integrity", {}).get("nonce_verified") is not False
        and r.get("regression_detected") is False and not r.get("new_failures")
        for r in source_records if isinstance(r, dict)
    ):
        return {"status": "rejected", "reason": "candidate has no trusted source evidence"}
    patch = report.get("changes", {}).get("patch", {})
    base = report.get("project", {}).get("base_commit")
    if patch.get("status") != "available" or not isinstance(base, str) or base == "unknown":
        return {"status": "rejected", "reason": "candidate patch identity unavailable"}
    safe, _ = _replay_safe_command(command)
    if not safe or not ShellTool._full_suite(command):
        return {"status": "rejected", "reason": "command does not prove full test scope"}
    code_identity = _patch_identity(project, base)
    identity = patch.get("sha256")
    if (not code_identity or identity not in (
            code_identity, _patch_identity(project, base, ignore_runtime=False))):
        return {"status": "rejected", "reason": "candidate workspace changed or cannot be identified"}
    output = report_path.parent / "verification.json"
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    if output.is_file():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
            if (previous.get("candidate_patch_sha256") == identity
                    and previous.get("verified_code_sha256") == code_identity
                    and previous.get("command_sha256") == command_hash
                    and previous.get("status") == "completed"):
                return previous
        except (OSError, ValueError):
            pass

    verification_command = command

    class VerifierBackend(_VerifyBackend):
        command = verification_command

    config = Config(sources=[])
    config.set("runtime.max_iterations", 3)
    config.set("workspace.root", str(report_path.parent / ".verify_workspace"))
    runtime = Runtime(config=config)
    runtime._gateway.register(VerifierBackend.name, VerifierBackend)
    try:
        result = asyncio.run(runtime.run(
            agent_id="verify_" + uuid.uuid4().hex[:12], task="Independently verify candidate",
            backend=_VerifyBackend.name, config={"project_path": str(project)},
        ))
        decision = result.get("metadata", {}).get("evidence_decision", {})
        ledger = result.get("metadata", {}).get("evidence_ledger", {})
        records = ledger.get("records", [])
        evidence_id = decision.get("evidence_id")
        proof = next((r for r in records if r.get("run_id") == evidence_id), None)
        identity_after = _patch_identity(project, base)
        trusted = bool(proof and proof.get("scope") == "full"
                       and proof.get("coverage") == "full"
                       and proof.get("evidence_status") == "valid"
                       and proof.get("integrity", {}).get("replay_verified") is True)
        completed = (result.get("status") == "completed"
                     and decision.get("kind") == "completed" and trusted
                     and identity_after == code_identity)
        reason = (decision.get("reason", "verification failed") if identity_after == code_identity
                  else "candidate workspace changed during verification")
        record = {"status": "completed" if completed else "rejected",
                  "reason": reason, "candidate_run_id": report.get("run", {}).get("id"),
                  "candidate_patch_sha256": identity,
                  "verified_code_sha256": code_identity,
                  "command_sha256": command_hash,
                  "verification_run_id": result.get("run_id"),
                  "verification_report": result.get("delivery_report"),
                  "evidence_id": evidence_id if completed else None}
    except Exception as exc:
        record = {"status": "rejected", "reason": f"verification failed: {type(exc).__name__}",
                  "candidate_run_id": report.get("run", {}).get("id"),
                  "candidate_patch_sha256": identity}
        record["verified_code_sha256"] = code_identity
        record["command_sha256"] = command_hash
    _atomic_write(output, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
    return record
