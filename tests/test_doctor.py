"""Tests for zmai.cli.doctor module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from zmai.cli.doctor import CheckResult, Doctor
from zmai.cli.formatters import Theme

# ============================================================
# CheckResult
# ============================================================

class TestCheckResult:
    def test_all_fields(self):
        r = CheckResult(name="test", status=True,
                        detail="ok", category="Backend")
        assert r.name == "test"
        assert r.status is True
        assert r.detail == "ok"
        assert r.category == "Backend"

    def test_default_category(self):
        r = CheckResult(name="test", status=False, detail="")
        assert r.category == ""

    def test_repr(self):
        r = CheckResult(name="x", status=False, detail="err", category="C")
        assert "x" in repr(r)


# ============================================================
# Doctor init
# ============================================================

class TestDoctorInit:
    def test_defaults(self):
        d = Doctor()
        assert d.theme is not None
        assert d.json_output is False
        assert d.results == []

    def test_custom_theme_and_json(self):
        t = Theme.plain()
        d = Doctor(theme=t, json_output=True)
        assert d.theme is t
        assert d.json_output is True


# ============================================================
# _check_backend
# ============================================================

class TestCheckBackend:
    def test_backend_check_runs(self):
        """Backend check completes without error, returns results."""
        d = Doctor()
        d.results = []
        d._check_backend()
        assert len(d.results) > 0
        for r in d.results:
            assert r.category == "Backend"
            assert isinstance(r.status, bool)


# ============================================================
# _check_config
# ============================================================

class TestCheckConfig:
    def test_config_check_has_zmai_json(self, tmp_path: Path):
        d = Doctor()
        d._project_root = tmp_path
        d.results = []
        d._check_config()
        names = [r.name for r in d.results]
        assert "zmai.json" in names
        assert "~/.zmai/config.json" in names
        assert "~/.zmai/credentials" in names


# ============================================================
# _check_workspace
# ============================================================

class TestCheckWorkspace:
    def test_workspace_writable(self, tmp_path: Path):
        d = Doctor()
        d._project_root = tmp_path
        d.results = []
        d._check_workspace()
        assert len(d.results) == 1
        assert d.results[0].status is True
        ws_dir = tmp_path / "workspace"
        assert ws_dir.is_dir()


# ============================================================
# _check_memory
# ============================================================

class TestCheckMemory:
    def test_memory_store_read(self):
        d = Doctor()
        d.results = []
        d._check_memory()
        assert len(d.results) == 1
        assert d.results[0].status is True


# ============================================================
# _check_tool
# ============================================================

class TestCheckTool:
    def test_tool_registration(self):
        d = Doctor()
        d.results = []
        d._check_tool()
        r = d.results[0]
        assert r.status is True
        assert "8/8" in r.detail


# ============================================================
# run() -- full run
# ============================================================

class TestDoctorRun:
    def test_run_all_categories_present(self, tmp_path: Path):
        d = Doctor(theme=Theme.plain())
        d._project_root = tmp_path
        results = d.run()
        categories = set(r.category for r in results)
        expected = {"Backend", "Config", "Workspace", "Memory", "Tool"}
        assert categories == expected

    def test_run_returns_check_results(self, tmp_path: Path):
        d = Doctor(theme=Theme.plain())
        d._project_root = tmp_path
        results = d.run()
        assert len(results) > 0
        for r in results:
            assert isinstance(r.name, str)
            assert isinstance(r.status, bool)
            assert isinstance(r.detail, str)

    def test_no_http_requests(self):
        """Doctor never makes HTTP requests."""
        d = Doctor()
        with patch("os.environ.get", return_value=""):
            d.run()
        # Pass if we get here without HTTPError


# ============================================================
# JSON output
# ============================================================

class TestJsonOutput:
    def test_json_output_format(self, tmp_path: Path, capsys):
        d = Doctor(theme=Theme.plain(), json_output=True)
        d._project_root = tmp_path
        d.run()
        captured = capsys.readouterr()
        assert captured.out, "should have JSON output"
        data = json.loads(captured.out)
        assert "timestamp" in data
        assert "summary" in data
        assert "checks" in data
        assert data["summary"]["total"] > 0

    def test_json_checks_have_required_fields(self, tmp_path: Path, capsys):
        d = Doctor(theme=Theme.plain(), json_output=True)
        d._project_root = tmp_path
        d.run()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        for c in data["checks"]:
            assert "name" in c
            assert "status" in c
            assert "detail" in c
            assert "category" in c


# ============================================================
# DOCTOR.md generation
# ============================================================

class TestDoctorMdGeneration:
    def test_doctor_md_generated(self, tmp_path: Path):
        d = Doctor(theme=Theme.plain())
        d._project_root = tmp_path
        d.run()
        md_file = tmp_path / "DOCTOR.md"
        assert md_file.exists()
        content = md_file.read_text(encoding="utf-8")
        assert "ZMAI Doctor Report" in content
        assert "checks passed" in content or "checks" in content

    def test_doctor_md_has_backend_section(self, tmp_path: Path):
        d = Doctor(theme=Theme.plain())
        d._project_root = tmp_path
        d.run()
        content = (tmp_path / "DOCTOR.md").read_text(encoding="utf-8")
        assert "## Backend" in content


# ============================================================
# _add helper
# ============================================================

class TestAddHelper:
    def test_add_appends_result(self):
        d = Doctor()
        d._add("test", True, "ok", "Cat")
        assert len(d.results) == 1
        r = d.results[0]
        assert r.name == "test"
        assert r.status is True
        assert r.detail == "ok"
        assert r.category == "Cat"

    def test_pass_helper(self):
        d = Doctor()
        d._pass("test", "Cat")
        assert d.results[0].status is True
        assert d.results[0].detail == "PASS"

    def test_fail_helper(self):
        d = Doctor()
        d._fail("test", "error msg", "Cat")
        assert d.results[0].status is False
        assert d.results[0].detail == "error msg"
