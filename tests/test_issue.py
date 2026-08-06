"""Tests for zmai.issue — IssueParser, IssueAgent, CLI."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zmai.issue import IssueDescription, IssueParser
from zmai.issue.agent import IssueAgent, IssueResult


class TestIssueParser:
    def test_parse_local_file(self):
        """解析本地 Markdown 文件。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Fix login bug\n\nThe login form crashes on submit.\n")
            tmp_path = f.name
        try:
            parser = IssueParser()
            issue = parser.parse(tmp_path)
            assert issue.title == "Fix login bug"
            assert "login form crashes" in issue.body
            assert issue.source == "file"
            assert issue.file_path == str(Path(tmp_path).resolve())
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_parse_local_file_no_title(self):
        """没有标题的文件使用默认标题。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("Just some text without a heading.")
            tmp_path = f.name
        try:
            parser = IssueParser()
            issue = parser.parse(tmp_path)
            assert issue.title == "Issue from file"
            assert issue.source == "file"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_parse_file_not_found(self):
        """不存在的文件抛出 FileNotFoundError。"""
        parser = IssueParser()
        with pytest.raises(FileNotFoundError):
            parser.parse("/nonexistent/issue.md")

    def test_parse_github_url_pattern_only(self):
        """检测到 GitHub URL 但无网络时应抛出 ValueError。"""
        parser = IssueParser()
        with pytest.raises((ValueError, ConnectionError, OSError)):
            parser.parse("https://github.com/owner/repo/issues/123")

    def test_unsupported_url(self):
        """不支持的 URL 格式抛出 ValueError。"""
        parser = IssueParser()
        with pytest.raises(ValueError, match="不支持"):
            parser.parse("https://gitlab.com/owner/repo/issues/1")

    def test_is_github_url(self):
        assert IssueParser.is_github_url("https://github.com/owner/repo/issues/42") is True
        assert IssueParser.is_github_url("https://gitlab.com/owner/repo/issues/1") is False
        assert IssueParser.is_github_url("/local/file.md") is False

    def test_issue_description_to_dict(self):
        """IssueDescription.to_dict() 包含必要字段。"""
        issue = IssueDescription(
            title="Test Issue",
            body="Description here",
            source="file",
            number=42,
            owner="owner",
            repo="repo",
            url="https://github.com/owner/repo/issues/42",
            labels=["bug"],
        )
        d = issue.to_dict()
        assert d["title"] == "Test Issue"
        assert d["source"] == "file"
        assert d["number"] == 42
        assert d["owner"] == "owner"
        assert d["repo"] == "repo"
        assert d["labels"] == ["bug"]

    def test_issue_description_full_name(self):
        issue = IssueDescription(title="", body="", source="", owner="a", repo="b")
        assert issue.full_name == "a/b"
        issue2 = IssueDescription(title="", body="", source="")
        assert issue2.full_name == ""

    def test_format_for_agent_includes_title(self):
        issue = IssueDescription(
            title="Fix bug", body="The bug is here", source="file",
            owner="me", repo="myapp", number=1,
        )
        text = issue.format_for_agent()
        assert "# Fix bug" in text
        assert "The bug is here" in text
        assert "Owner: me" in text
        assert "Repo: myapp" in text
        assert "Issue #1" in text

    def test_format_for_agent_with_comments(self):
        issue = IssueDescription(
            title="Issue", body="Body", source="github",
            comments=[{"author": "user1", "body": "I have a suggestion"}],
        )
        text = issue.format_for_agent()
        assert "user1" in text
        assert "suggestion" in text


class TestIssueAgent:
    def test_run_local_file_plan_only(self):
        """本地文件 + --plan 返回计划结果。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Fix calculation error\n\nThe subtract function returns wrong results.\n")
            tmp_path = f.name
        try:
            agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()))
            result = agent.run(tmp_path, plan_only=True)
            assert result.workflow_status in ("planned", "completed")
            assert result.issue.title == "Fix calculation error"
            assert result.error == ""
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_run_local_file_dry_run(self):
        """本地文件 + --dry-run 返回模拟结果。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Add new feature\n\nWe need a new feature.\n")
            tmp_path = f.name
        try:
            agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()))
            result = agent.run(tmp_path, dry_run=True)
            assert result.workflow_status == "completed"
            assert "[DRY RUN]" in (result.summary or "")
            assert result.error == ""
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_github_url_no_network(self):
        """无网络时 GitHub URL 应优雅报错。"""
        agent = IssueAgent()
        result = agent.run("https://github.com/owner/repo/issues/12345")
        assert result.error != ""
        assert "parse" in result.workflow_status or "error" in result.workflow_status.lower()

    def test_run_local_file_with_modify(self):
        """本地文件运行完整工作流（Mock）。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Simple test issue\n\nNothing to fix really.\n")
            tmp_path = f.name
        try:
            agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()), max_steps=2)
            result = agent.run(tmp_path)
            assert result.issue.title == "Simple test issue"
            # May fail at modify if no real project, but shouldn't crash
            assert isinstance(result, IssueResult)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_issue_result_to_dict(self):
        """IssueResult.to_dict() 包含必要字段。"""
        issue = IssueDescription(title="Test", body="Body", source="file")
        result = IssueResult(issue=issue, workflow_status="completed", plan="Plan text")
        d = result.to_dict()
        assert d["workflow_status"] == "completed"
        assert d["plan"] == "Plan text"
        assert "issue" in d
        assert "duration_seconds" in d

    def test_issue_result_defaults(self):
        issue = IssueDescription(title="", body="", source="")
        result = IssueResult(issue=issue, workflow_status="parse")
        assert result.plan == ""
        assert result.diff == ""
        assert result.error == ""
        assert result.duration_seconds == 0.0

    def test_analyze_local(self):
        """_analyze 对本地 issue 返回分析文本。"""
        issue = IssueDescription(title="Test bug", body="It crashes", source="file")
        agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()))
        analysis = agent._analyze(issue)
        assert "Test bug" in analysis
        assert "It crashes" in analysis

    def test_generate_plan(self):
        """_generate_plan 返回计划文本。"""
        issue = IssueDescription(title="Fix the bug", body="There is a bug", source="file")
        agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()))
        analysis = "Some analysis"
        plan = agent._generate_plan(issue, analysis)
        assert "Fix the bug" in plan
        assert "### 步骤" in plan

    def test_build_summary(self):
        """_build_summary 构建摘要。"""
        issue = IssueDescription(title="Test", body="Body", source="file", owner="o", repo="r", number=1)
        result = IssueResult(issue=issue, workflow_status="completed", diff="+1 line\n-1 line", verification="✅ All good")
        agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()))
        summary = agent._build_summary(result)
        assert "Test" in summary
        assert "+1/-1" in summary

    def test_simulate_diff(self):
        """_simulate_diff 返回模拟 diff 文本。"""
        issue = IssueDescription(title="X", body="Y", source="file")
        plan = "- src/main.py: Fix the bug\n- src/utils.py: Update tests"
        agent = IssueAgent(work_dir=Path(tempfile.mkdtemp()))
        diff = agent._simulate_diff(issue, plan)
        assert "DRY RUN" in diff
        assert "src/main.py" in diff


class TestCLIIntegration:
    def test_cmd_issue_local_file_plan_only(self):
        """CLI issue 本地文件 + --plan。"""
        from zmai.cli.github_cmd import _cmd_issue
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# CLI test\n\nTest issue.\n")
            tmp_path = f.name
        try:
            # Should not raise
            _cmd_issue([tmp_path, "--plan"])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_cmd_issue_local_file_dry_run(self):
        """CLI issue 本地文件 + --dry-run。"""
        from zmai.cli.github_cmd import _cmd_issue
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Dry run test\n\nTest.\n")
            tmp_path = f.name
        try:
            _cmd_issue([tmp_path, "--dry-run"])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_cmd_issue_local_file_json(self):
        """CLI issue 本地文件 + --json。"""
        from zmai.cli.github_cmd import _cmd_issue
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# JSON test\n\nTest.\n")
            tmp_path = f.name
        try:
            _cmd_issue([tmp_path, "--json"])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_cmd_issue_no_args(self):
        """无参数时打印帮助。"""
        from zmai.cli.github_cmd import _cmd_issue
        with pytest.raises(SystemExit):
            _cmd_issue([])

    def test_cmd_issue_nonexistent_file(self):
        """不存在的文件。"""
        from zmai.cli.github_cmd import _cmd_issue
        _cmd_issue(["/nonexistent/file.md"])  # 不应崩溃

    def test_output_json_format(self):
        """_output_json 输出合法 JSON。"""
        from zmai.cli.github_cmd import _output_json
        issue = IssueDescription(title="JSON output", body="Test", source="file")
        result = IssueResult(issue=issue, workflow_status="completed", plan="My plan")
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            _output_json(result)
        text = f.getvalue()
        data = json.loads(text)
        assert data["workflow_status"] == "completed"
        assert data["plan"] == "My plan"
