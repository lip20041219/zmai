from __future__ import annotations

import json
import os
import subprocess

from zmai.swe.candidate_verify import verify_candidate
from zmai.swe.delivery_report import capture_workspace, create_delivery_report
from zmai.runtime import Runtime
from zmai.swe.evidence import CompletionLedger, EvidenceRecord, Integrity, decide_terminal


def _candidate(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Verifier Test"], cwd=project, check=True)
    (project / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project / "test_app.py").write_text(
        "import os, app\n"
        "def test_value():\n"
        "    assert app.VALUE == 1 and os.getenv('VERIFY_FAIL') != '1'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=project, check=True)
    before = capture_workspace(project)
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    ledger = CompletionLedger(ever_modified=True)
    ledger.record(EvidenceRecord(
        run_id="source-targeted", step=1, command="python -m pytest test_app.py -q",
        scope="targeted", coverage="partial", exit_code=0, passed=1,
        integrity=Integrity(nonce_verified=True, replay_verified=True),
    ))
    create_delivery_report(result={"status": "candidate", "agent_id": "agent",
                                   "metadata": {"evidence_ledger": ledger.summary(),
                                                "evidence_decision": decide_terminal(ledger).to_dict()}},
                           task="fix value", run_id="candidate-1",
                           started_at="2026-11-12T00:00:00+00:00", elapsed_seconds=1,
                           before=before, project_root=project, workspace_path=workspace)
    return project, workspace / "output" / "delivery" / "candidate-1" / "report.json"


def test_candidate_upgrades_with_new_full_trusted_evidence(tmp_path, monkeypatch):
    project, report = _candidate(tmp_path)
    monkeypatch.setenv("PATH", str(__import__("sys").executable.rsplit(os.sep, 1)[0])
                       + os.pathsep + os.environ.get("PATH", ""))
    result = verify_candidate(report, project, "python -m pytest -q")
    assert result["status"] == "completed", result
    assert result["evidence_id"]
    assert json.loads(report.read_text("utf-8"))["terminal"]["status"] == "candidate"


def test_failed_validation_keeps_candidate(tmp_path, monkeypatch):
    project, report = _candidate(tmp_path)
    monkeypatch.setenv("VERIFY_FAIL", "1")
    result = verify_candidate(report, project, "python -m pytest -q")
    assert result["status"] == "rejected"
    assert json.loads(report.read_text("utf-8"))["terminal"]["status"] == "candidate"


def test_changed_candidate_is_rejected_before_running(tmp_path, monkeypatch):
    project, report = _candidate(tmp_path)
    (project / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = verify_candidate(report, project, "python -m pytest -q")
    assert result["status"] == "rejected"
    assert "workspace changed" in result["reason"]


def test_repeat_verification_reuses_completed_record(tmp_path, monkeypatch):
    project, report = _candidate(tmp_path)
    monkeypatch.setenv("PATH", str(__import__("sys").executable.rsplit(os.sep, 1)[0])
                       + os.pathsep + os.environ.get("PATH", ""))
    first = verify_candidate(report, project, "python -m pytest -q")
    assert first["status"] == "completed", first
    second = verify_candidate(report, project, "python -m pytest -q")
    assert second == first


def test_scope_narrowing_and_shell_composition_are_rejected(tmp_path):
    project, report = _candidate(tmp_path)
    for command in ("python -m pytest test_app.py -q",
                    "python -m pytest -q && echo unsafe",
                    "python -m pytest -q --ignore=test_app.py"):
        result = verify_candidate(report, project, command)
        assert result["status"] == "rejected"
    assert not (report.parent / "verification.json").exists()


def test_patch_changed_after_validation_is_rejected(tmp_path, monkeypatch):
    project, report = _candidate(tmp_path)
    monkeypatch.setenv("PATH", str(__import__("sys").executable.rsplit(os.sep, 1)[0])
                       + os.pathsep + os.environ.get("PATH", ""))
    real_run = Runtime.run

    async def run_then_modify(self, *args, **kwargs):
        result = await real_run(self, *args, **kwargs)
        (project / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return result

    monkeypatch.setattr(Runtime, "run", run_then_modify)
    result = verify_candidate(report, project, "python -m pytest -q")
    assert result["status"] == "rejected"
    assert result["reason"] == "candidate workspace changed during verification"
