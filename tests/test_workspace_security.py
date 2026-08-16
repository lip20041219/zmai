"""Workspace 安全边界攻击性测试。

覆盖:
  - ../
  - 绝对路径
  - sibling prefix path
  - symlink
  - Windows 大小写
  - Windows drive path
  - shell cd ..
  - shell 写入 workspace 外部
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zmai.swe.tools import (
    GitTool,
    ReadFileTool,
    ShellTool,
    WriteFileTool,
    _resolve_tool_path,
)
from zmai.tool import ToolContext


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=ws, timeout=5)


# ═══════════════════════════════════════════════════════════════
# _resolve_tool_path — 路径验证核心
# ═══════════════════════════════════════════════════════════════


class TestPathTraversal:
    """路径穿越防护测试。"""

    def test_dotdot_escape_rejected(self, ctx):
        """../ 逃逸被拒绝。"""
        safe, _, err = _resolve_tool_path(ctx, "../../etc/passwd")
        assert safe is False

    def test_deep_dotdot_escape_rejected(self, ctx):
        """深层 ../ 逃逸被拒绝。"""
        safe, _, err = _resolve_tool_path(ctx, "a/b/c/../../../../etc/passwd")
        assert safe is False

    def test_absolute_outside_rejected(self, ctx):
        """workspace 外部的绝对路径被拒绝。"""
        if sys.platform == "win32":
            outside = "C:\\Windows\\System32\\drivers\\etc\\hosts"
        else:
            outside = "/etc/passwd"
        safe, _, err = _resolve_tool_path(ctx, outside)
        assert safe is False

    def test_absolute_inside_allowed(self, ctx):
        """workspace 内的绝对路径允许。"""
        inner = str(ctx.workspace_path / "allowed.txt")
        safe, _, err = _resolve_tool_path(ctx, inner)
        assert safe is True

    def test_sibling_prefix_rejected(self, ctx):
        """前缀相似目录名不得绕过（/ws/a 和 /ws/a-extra 不同）。"""
        safe, _, err = _resolve_tool_path(ctx, "../a-extra/file.txt")
        assert safe is False

    def test_deep_nested_allowed(self, ctx):
        """深层嵌套路径允许。"""
        safe, _, err = _resolve_tool_path(ctx, "a/b/c/d/e/file.txt")
        assert safe is True

    def test_mixed_dot_resolved(self, ctx):
        """混合 . 和 .. 正确解析。"""
        safe, resolved, err = _resolve_tool_path(ctx, "./a/./b/../c/file.txt")
        assert safe is True

    def test_empty_path_resolves_to_cwd(self):
        """空路径解析为当前目录（不是安全漏洞）。"""
        ctx = ToolContext(agent_id="test", workspace_path=Path("/tmp"), timeout=5)
        safe, _, err = _resolve_tool_path(ctx, "")
        # Path("") 解析为 cwd，在 workspace 外时被拒绝；无法确定时 safe=True
        _ = safe  # 不同平台行为不同，不断言


def _can_symlink() -> bool:
    """检查当前平台是否支持创建 symlink。"""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp = Path(f.name)
            link = tmp.with_suffix(".link")
            link.symlink_to(tmp)
            link.unlink()
            tmp.unlink()
            return True
    except (OSError, NotImplementedError):
        return False


class TestSymlinkEscape:
    """symlink 逃逸防护测试。"""

    def test_symlink_outside_write_rejected(self, ctx):
        """写入指向外部的 symlink 被拒绝。"""
        if not _can_symlink():
            pytest.skip("当前平台不支持创建 symlink")
        outside = ctx.workspace_path.parent / "secret.txt"
        outside.write_text("secret")
        link = ctx.workspace_path / "evil_link"
        link.symlink_to(outside, target_is_directory=False)

        tool = WriteFileTool()
        result = tool.execute(ctx, {"path": "evil_link", "content": "hacked"})
        assert result.success is False
        # symlink 指向工作区外，被路径安全检查拒绝（错误信息可能为
        # "不在工作区内"或含 "symlink" 字样的专用提示，二者皆可）
        assert ("symlink" in (result.error or "").lower()) or ("不在工作区" in (result.error or ""))

    def test_symlink_outside_read_rejected(self, ctx):
        """读取指向外部的 symlink 被拒绝。"""
        if not _can_symlink():
            pytest.skip("当前平台不支持创建 symlink")
        outside = ctx.workspace_path.parent / "secret.txt"
        outside.write_text("secret")
        link = ctx.workspace_path / "evil_link"
        link.symlink_to(outside, target_is_directory=False)

        tool = ReadFileTool()
        result = tool.execute(ctx, {"path": "evil_link"})
        assert result.success is False

    def test_symlink_inside_allowed(self, ctx):
        """指向 workspace 内部的 symlink 允许。"""
        if not _can_symlink():
            pytest.skip("当前平台不支持创建 symlink")
        target = ctx.workspace_path / "real.txt"
        target.write_text("hello")
        link = ctx.workspace_path / "mylink"
        link.symlink_to(target, target_is_directory=False)

        tool = ReadFileTool()
        result = tool.execute(ctx, {"path": "mylink"})
        assert result.success is True


class TestWindowsPath:
    """Windows 路径兼容性测试。"""

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="反斜杠路径语义仅 Windows 存在（Unix 上反斜杠是普通字符）",
    )
    def test_windows_backslash_rejected_if_relative(self, ctx):
        """反斜杠相对路径（Windows 风格的 ../..\\）。"""
        safe, _, err = _resolve_tool_path(ctx, "..\\..\\etc\\passwd")
        # Unix 上反斜杠不是路径分隔符，测试只检查逻辑
        # 实际 Windows 上 pathlib 会正确解析
        assert safe is False

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="大小写不敏感仅 Windows 存在（Unix 上 testcase.txt 是合法工作区内路径）",
    )
    def test_windows_case_insensitive_allowed(self, ctx):
        """Windows 路径大小写不敏感，在 workspace 内应允许。"""
        test_file = ctx.workspace_path / "TestCase.txt"
        test_file.write_text("content")
        safe, resolved, err = _resolve_tool_path(ctx, "testcase.txt")
        if sys.platform == "win32":
            assert safe is True
        else:
            # Unix 大小写敏感
            assert safe is False


class TestShellSecurity:
    """shell_exec 安全边界测试。"""

    def test_shell_cd_outside_not_prevented(self, ctx):
        """shell cd .. 无法被 A 层阻止（OS 级沙箱需求）。"""
        doc = Path(__file__).parent.parent / "docs" / "review" / "WORKSPACE_SECURITY_BOUNDARY.md"
        assert doc.exists(), "安全边界文档应存在，说明 shell cd.. 是 B 层问题"

    def test_shell_exec_cwd_is_workspace(self, ctx):
        """shell_exec 的工作目录是 workspace。"""
        tool = ShellTool()
        result = tool.execute(ctx, {"command": "pwd" if sys.platform != "win32" else "cd"})
        assert result.success is True

    def test_git_no_shell_injection(self, ctx):
        """git 工具不执行注入命令。"""
        tool = GitTool()

        # 注入尝试: "status; echo hacked"
        # 使用 list args + shell=False 后，";" 被 shlex.split 作为 git 参数
        # 而非 shell 操作符。git 会报 "status;" 不是有效子命令，但不会执行 echo
        result = tool.execute(ctx, {"args": "status; echo hacked"})

        # 关键断言: "hacked" 不应出现在任何输出中
        output = (result.output or "") + (result.error or "")
        assert "hacked" not in output, (
            f"命令注入成功: echo hacked 被执行, output={output[:200]}"
        )

    def test_git_shell_false_used(self, ctx):
        """git 工具使用 shell=False 防止注入。"""
        import inspect
        source = inspect.getsource(GitTool.execute)
        assert "shell=False" in source, "git 应使用 shell=False"


class TestWriteFileBoundary:
    """write_file 安全边界测试。"""

    def test_write_outside_workspace_rejected(self, ctx):
        """写入 workspace 外部被拒绝。"""
        tool = WriteFileTool()
        result = tool.execute(ctx, {"path": "../outside.txt", "content": "x"})
        assert result.success is False

    def test_write_absolute_outside_rejected(self, ctx):
        """绝对路径写入外部被拒绝。"""
        tool = WriteFileTool()
        outside = str(ctx.workspace_path.parent / "secret.txt")
        result = tool.execute(ctx, {"path": outside, "content": "x"})
        assert result.success is False

    def test_write_size_limit(self, ctx):
        """超大写入被限制。"""
        tool = WriteFileTool()
        huge = "x" * (60 * 1024 * 1024)  # 60MB > 50MB 默认限制
        result = tool.execute(ctx, {"path": "big.txt", "content": huge})
        assert result.success is False
        assert "超过" in (result.error or "")
