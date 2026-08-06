"""SWE Agent tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zmai.swe.tools import (
    EditTool,
    GitTool,
    GrepTool,
    OpenInBrowserTool,
    ReadFileTool,
    ShellTool,
    ShowToUserTool,
    WriteFileTool,
)
from zmai.tool import ToolContext, ToolResult


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=ws, timeout=10)


class TestReadFileTool:
    def test_tool_defined(self):
        t = ReadFileTool()
        assert t.name == "read_file"
        assert t.description
        assert "path" in t.parameters.get("required", [])

    def test_read_file(self, ctx, ws: Path):
        (ws / "test.txt").write_text("hello\nworld\nline3\n")
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "test.txt"})
        assert r.success
        assert "hello" in r.output
        assert "3 lines" in r.output

    def test_read_partial(self, ctx, ws: Path):
        (ws / "test.txt").write_text("a\nb\nc\nd\ne\n")
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "test.txt", "start_line": 2, "end_line": 4})
        assert r.success
        assert "b" in r.output
        assert "a" not in r.output

    def test_read_nonexistent(self, ctx):
        t = ReadFileTool()
        r = t.execute(ctx, {"path": "nope.txt"})
        assert not r.success


class TestWriteFileTool:
    def test_tool_defined(self):
        t = WriteFileTool()
        assert t.name == "write_file"

    def test_write_and_read_back(self, ctx, ws: Path):
        t = WriteFileTool()
        r = t.execute(ctx, {"path": "sub/hello.py", "content": "print('hi')"})
        assert r.success
        assert (ws / "sub" / "hello.py").exists()
        assert (ws / "sub" / "hello.py").read_text() == "print('hi')"

    def test_overwrite(self, ctx, ws: Path):
        (ws / "f.txt").write_text("old")
        t = WriteFileTool()
        t.execute(ctx, {"path": "f.txt", "content": "new"})
        assert (ws / "f.txt").read_text() == "new"


class TestGrepTool:
    def test_tool_defined(self):
        t = GrepTool()
        assert t.name == "grep"

    def test_grep_found(self, ctx, ws: Path):
        (ws / "a.py").write_text("import os\nx = 1\n")
        (ws / "b.py").write_text("import sys\n")
        t = GrepTool()
        r = t.execute(ctx, {"pattern": "import"})
        assert r.success
        assert "a.py" in r.output
        assert "b.py" in r.output

    def test_grep_not_found(self, ctx, ws: Path):
        (ws / "a.py").write_text("hello")
        t = GrepTool()
        r = t.execute(ctx, {"pattern": "zzzzz"})
        assert r.success
        assert "no matches" in r.output


class TestShellTool:
    def test_tool_defined(self):
        t = ShellTool()
        assert t.name == "shell_exec"

    def test_shell_exec(self, ctx):
        t = ShellTool()
        r = t.execute(ctx, {"command": "echo hello"})
        assert r.success
        assert "hello" in r.output

    def test_shell_fail(self, ctx):
        t = ShellTool()
        r = t.execute(ctx, {"command": "exit 1"})
        assert not r.success


class TestGitTool:
    def test_tool_defined(self):
        t = GitTool()
        assert t.name == "git"


class TestEditTool:
    def test_tool_defined(self):
        t = EditTool()
        assert t.name == "edit"
        assert "path" in t.parameters["required"]

    def test_replace_lines(self, ctx, ws: Path):
        (ws / "f.py").write_text("line1\nline2\nline3\nline4\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 2, "end_line": 3, "new_text": "modified\n"})
        assert r.success
        content = (ws / "f.py").read_text()
        assert "line1" in content
        assert "modified" in content
        assert "line2" not in content
        assert "line3" not in content
        assert "line4" in content

    def test_regex_replace(self, ctx, ws: Path):
        (ws / "f.py").write_text("foo bar\nfoo baz\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "regex_replace",
                            "old_text": "foo", "new_text": "qux", "count": 1})
        assert r.success
        content = (ws / "f.py").read_text()
        assert content.startswith("qux")
        assert "foo" in content.split("\n")[1]

    def test_insert(self, ctx, ws: Path):
        (ws / "f.py").write_text("a\nc\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "insert",
                            "start_line": 2, "new_text": "b\n"})
        assert r.success
        assert (ws / "f.py").read_text() == "a\nb\nc\n"

    def test_append(self, ctx, ws: Path):
        t = EditTool()
        r = t.execute(ctx, {"path": "new.txt", "mode": "append",
                            "new_text": "hello"})
        assert r.success
        assert (ws / "new.txt").exists()
        assert "hello" in (ws / "new.txt").read_text()

    def test_out_of_range(self, ctx, ws: Path):
        (ws / "f.py").write_text("a\nb\n")
        t = EditTool()
        r = t.execute(ctx, {"path": "f.py", "mode": "replace_lines",
                            "start_line": 10, "end_line": 12, "new_text": "x"})
        assert not r.success


class TestShowToUserTool:
    def test_tool_defined(self):
        t = ShowToUserTool()
        assert t.name == "show_to_user"

    def test_show_content(self, ctx):
        t = ShowToUserTool()
        r = t.execute(ctx, {"content": "hello world", "title": "Test"})
        assert r.success


class TestOpenInBrowserTool:
    def test_tool_defined(self):
        t = OpenInBrowserTool()
        assert t.name == "open_in_browser"

    def test_file_not_found(self, ctx):
        t = OpenInBrowserTool()
        r = t.execute(ctx, {"path": "nonexistent.html"})
        assert not r.success


class TestIntegration:
    def test_read_write_grep_flow(self, ctx, ws: Path):
        wt = WriteFileTool()
        assert wt.execute(ctx, {"path": "main.py", "content": "def foo():\n    pass\n"}).success
        rt = ReadFileTool()
        r = rt.execute(ctx, {"path": "main.py"})
        assert r.success
        assert "foo" in r.output
        gt = GrepTool()
        r = gt.execute(ctx, {"pattern": "def "})
        assert r.success
        assert "main.py" in r.output


# ═══════════════════════════════════════════════════════════════
# _resolve_tool_path 路径安全性测试
# ═══════════════════════════════════════════════════════════════


class TestResolveToolPath:
    """_resolve_tool_path 的路径隔离行为验证。"""

    def test_relative_path_within_workspace(self, ctx: ToolContext) -> None:
        """相对路径在 workspace 内应通过。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "output/file.txt")
        assert safe is True
        assert resolved.exists() is False  # 文件不存在不影响路径合法性
        assert err == ""

    def test_dot_path_within_workspace(self, ctx: ToolContext) -> None:
        """. 应解析为 workspace 根目录。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, ".")
        assert safe is True
        assert resolved == ctx.workspace_path.resolve()

    def test_dotdot_escape_rejected(self, ctx: ToolContext) -> None:
        """.. 逃逸应被拒绝。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "../../etc/passwd")
        assert safe is False
        assert "不在工作区" in err or "安全限制" in err

    def test_absolute_path_outside_rejected(self, ctx: ToolContext) -> None:
        """workspace 外部的绝对路径应被拒绝。"""
        from zmai.swe.tools import _resolve_tool_path
        import sys
        if sys.platform == "win32":
            outside = "C:\\Windows\\System32\\drivers\\etc\\hosts"
        else:
            outside = "/etc/passwd"
        safe, resolved, err = _resolve_tool_path(ctx, outside)
        assert safe is False
        assert "不在" in err or "安全限制" in err

    def test_absolute_path_inside_allowed_with_project(self, tmp_path: Path) -> None:
        """project_path 内的绝对路径应允许。"""
        from zmai.swe.tools import _resolve_tool_path
        from zmai.tool import ToolContext
        (tmp_path / "allowed.txt").write_text("ok")
        ctx = ToolContext(agent_id="test", workspace_path=tmp_path,
                          project_path=tmp_path, timeout=10)
        safe, resolved, err = _resolve_tool_path(ctx, str(tmp_path / "allowed.txt"))
        assert safe is True
        assert resolved.exists()

    def test_sibling_prefix_rejected(self, ctx: ToolContext) -> None:
        """前缀相似的目录名不得绕过。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "../a-extra/file.txt")
        assert safe is False

    def test_deep_nested_allowed(self, ctx: ToolContext) -> None:
        """深层嵌套路径应允许。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "a/b/c/d/e/f/g/file.txt")
        assert safe is True

    def test_mixed_dot_resolved(self, ctx: ToolContext) -> None:
        """混合 . 和 .. 应正确解析。"""
        from zmai.swe.tools import _resolve_tool_path
        safe, resolved, err = _resolve_tool_path(ctx, "./a/./b/../c/file.txt")
        assert safe is True
        # ./a/./b/../c/file.txt → ./a/c/file.txt
        assert str(resolved).endswith("a/c/file.txt") or str(resolved).endswith("a\\c\\file.txt")
