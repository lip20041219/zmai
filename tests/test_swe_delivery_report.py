"""SC-M1 report generation tests. Authored for local execution; not run in this change."""

from __future__ import annotations

import hashlib
import json
import subprocess

from zmai.swe.delivery_report import capture_workspace, create_delivery_report


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _clean_repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "SC-M1 Test")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "baseline")
    return root


def _report(tmp_path, root, status, *, metadata=None):
    workspace = tmp_path / "workspace" / "agent"
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    before = capture_workspace(root)
    result = {
        "status": status,
        "agent_id": "agent",
        "steps": 3,
        "metadata": metadata or {},
    }
    pointer = create_delivery_report(
        result=result,
        task="repair the project",
        run_id="run-test-001",
        started_at="2026-11-12T00:00:00+00:00",
        elapsed_seconds=2.5,
        before=before,
        project_root=root,
        workspace_path=workspace,
    )
    report_dir = workspace / pointer["directory"]
    return pointer, report_dir, json.loads((report_dir / "report.json").read_text("utf-8"))


def test_reports_preserve_completed_candidate_and_failed_states(tmp_path):
    root = _clean_repo(tmp_path)
    for status in ("completed", "candidate", "failed"):
        pointer, report_dir, report = _report(tmp_path, root, status)
        assert pointer["status"] == "generated"
        assert (report_dir / "report.md").is_file()
        assert report["terminal"]["status"] == status
        assert report["verification"]["external_acceptance"] == "not_run"
        assert report["verification"]["evidence_status"] == "no_usable_evidence"
        assert any("没有可用的最新测试证据" in risk for risk in report["risks"])


def test_dirty_baseline_withholds_patch_and_marks_ambiguous_changes(tmp_path):
    root = _clean_repo(tmp_path)
    (root / "preexisting.txt").write_text("user change\n", encoding="utf-8")
    before = capture_workspace(root)
    (root / "agent_change.py").write_text("new_code = True\n", encoding="utf-8")
    workspace = tmp_path / "workspace" / "agent"
    (workspace / "output").mkdir(parents=True)
    pointer = create_delivery_report(
        result={"status": "candidate", "metadata": {}}, task="repair",
        run_id="run-dirty", started_at="2026-11-12T00:00:00+00:00",
        elapsed_seconds=1, before=before, project_root=root, workspace_path=workspace,
    )
    report_dir = workspace / pointer["directory"]
    report = json.loads((report_dir / "report.json").read_text("utf-8"))
    assert report["changes"]["patch"]["status"] == "withheld_dirty_baseline"
    assert report["changes"]["patch"]["path"] is None
    assert report["changes"]["attribution"].startswith("unknown")
    assert report["changes"]["files"][0]["path"] == "agent_change.py"
    assert report["changes"]["files"][0]["attribution"] == "run"
    assert "preexisting.txt" in report["changes"]["ambiguous_preexisting_paths"]


def test_sensitive_values_are_redacted_in_report_and_patch(tmp_path):
    root = _clean_repo(tmp_path)
    before = capture_workspace(root)
    (root / "app.py").write_text(
        'value = 2\napi_key = "sk-abcdefghijklmnopqrstuvwxyz123456"\n'
        'local = "D:\\\\Users\\\\private\\\\secret.txt"\n', encoding="utf-8"
    )
    workspace = tmp_path / "workspace" / "agent"
    (workspace / "output").mkdir(parents=True)
    state = workspace / ".state"
    state.mkdir()
    (state / "execution_log.json").write_text(json.dumps({
        "agent_id": "agent",
        "created_at": "2026-11-12T00:00:00Z",
        "task": "token=logtasksecret",
        "steps": [{
            "step_id": 1,
            "phase": "tool_result",
            "action": "shell_exec",
            "tool_name": "shell_exec",
            "tool_output": "authorization=log-output-secret",
            "error": "token=log-error-secret",
            "metadata": {"private": "nested-secret"},
            "success": True,
            "duration_ms": 8,
        }],
    }), encoding="utf-8")
    pointer = create_delivery_report(
        result={"status": "candidate", "metadata": {}}, task="repair with token=tasksecret",
        run_id="run-secret", started_at="2026-11-12T00:00:00+00:00",
        elapsed_seconds=1, before=before, project_root=root, workspace_path=workspace,
    )
    report_dir = workspace / pointer["directory"]
    artifacts = [path.read_text("utf-8") for path in report_dir.iterdir() if path.is_file()]
    combined = "\n".join(artifacts)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in combined
    assert "tasksecret" not in combined
    assert "logtasksecret" not in combined
    assert "log-output-secret" not in combined
    assert "nested-secret" not in combined
    assert "log-error-secret" not in combined
    assert "D:\\\\Users\\\\private" not in combined
    report = json.loads((report_dir / "report.json").read_text("utf-8"))
    assert report["changes"]["patch"]["redacted"] is True
    assert report["changes"]["patch"]["directly_applicable"] is False
    patch_bytes = (report_dir / "patch.diff").read_bytes()
    assert pointer["patch"] == "patch.diff"
    assert hashlib.sha256(patch_bytes).hexdigest() == report["changes"]["patch"]["sha256"]


def test_project_snapshot_excludes_runtime_workspace_files(tmp_path):
    root = _clean_repo(tmp_path)
    workspace = root / ".runtime_workspace" / "agent"
    (workspace / "output").mkdir(parents=True)
    before = capture_workspace(root, exclude_paths=(workspace,))
    (workspace / "private.log").write_text("should not enter patch", encoding="utf-8")
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    pointer = create_delivery_report(
        result={"status": "completed", "metadata": {}}, task="repair",
        run_id="run-exclude", started_at="2026-11-12T00:00:00+00:00",
        elapsed_seconds=1, before=before, project_root=root, workspace_path=workspace,
    )
    report_dir = workspace / pointer["directory"]
    report = json.loads((report_dir / "report.json").read_text("utf-8"))
    assert [item["path"] for item in report["changes"]["files"]] == ["app.py"]
    patch = (report_dir / "patch.diff").read_text("utf-8")
    assert "private.log" not in patch
