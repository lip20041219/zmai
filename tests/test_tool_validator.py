"""Tool Validator 测试：验证工具名校验与 Agent 自恢复能力。

覆盖：
  Test 1: 调用不存在的工具 → 返回 tool_not_found 结构化错误
  Test 2: 调用正常工具（shell_exec）→ 正常执行
  Test 3: 模拟 LLM 输出错误工具（dirx）→ 通过重试正确工具可恢复
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zmai.swe.tools import (
    GrepTool,
    ReadFileTool,
    ShellTool,
    ShowToUserTool,
    WriteFileTool,
)
from zmai.tool import ToolContext, ToolRegistry


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(ws: Path) -> ToolContext:
    return ToolContext(
        agent_id="test_validator",
        workspace_path=ws,
        project_path=ws,
        config={"_quiet": True},
        timeout=15,
    )


@pytest.fixture
def registry(ctx: ToolContext) -> ToolRegistry:
    r = ToolRegistry()
    for tool in [
        ReadFileTool(), WriteFileTool(), GrepTool(),
        ShellTool(), ShowToUserTool(),
    ]:
        r.register(tool)
    return r


# ── Test 1: 调用不存在工具 → tool_not_found ─────────────────


class TestToolNotFound:
    def test_unknown_tool_returns_structured_error(self, registry, ctx):
        result = registry.execute_tool("fake_tool", {}, ctx)
        assert not result.success
        assert result.metadata.get("error_type") == "tool_not_found"
        assert result.metadata.get("tool_name") == "fake_tool"
        assert isinstance(result.metadata.get("available_tools"), list)
        # 错误信息必须包含可用工具列表，便于 LLM 重新规划
        assert "可用工具" in (result.error or "")

    def test_unknown_tool_does_not_raise(self, registry, ctx):
        # 关键：不能抛异常导致 Agent 崩溃
        result = registry.execute_tool("nonexistent", {"x": 1}, ctx)
        assert isinstance(result.success, bool)
        assert result.success is False

    def test_execute_still_raises_for_direct_callers(self, registry, ctx):
        # 兼容旧契约：execute() 对直接调用仍抛 ToolError
        from zmai.errors import ToolError

        with pytest.raises(ToolError):
            registry.execute("fake_tool", {}, ctx)

    def test_available_tools_listed(self, registry, ctx):
        result = registry.execute_tool("nope", {}, ctx)
        available = result.metadata.get("available_tools", [])
        assert "shell_exec" in available
        assert "read_file" in available


# ── Test 2: 正常工具执行 ─────────────────────────────────────


class TestValidToolExecution:
    def test_shell_exec_normal(self, registry, ctx, ws: Path):
        (ws / "hello.txt").write_text("hi\n", encoding="utf-8")
        # 跨平台：Windows cmd 用 type，bash 用 cat（CI 的 ubuntu runner 无 type）
        result = registry.execute_tool("shell_exec", {"command": "cat hello.txt"}, ctx)
        assert result.success
        assert "hi" in result.output

    def test_read_file_normal(self, registry, ctx, ws: Path):
        (ws / "a.txt").write_text("content-abc\n", encoding="utf-8")
        result = registry.execute_tool("read_file", {"path": "a.txt"}, ctx)
        assert result.success
        assert "content-abc" in result.output


# ── Test 3: LLM 输出错误工具 → Agent 可恢复 ──────────────────


class TestAgentRecovery:
    def test_wrong_tool_then_correct(self, registry, ctx, ws: Path):
        (ws / "data.txt").write_text("recover-me\n", encoding="utf-8")

        # 第 1 步：LLM 幻觉出工具 "dirx"
        bad = registry.execute_tool("dirx", {"path": "data.txt"}, ctx)
        assert not bad.success
        assert bad.metadata.get("error_type") == "tool_not_found"

        # 第 2 步：Agent 依据错误里的 available_tools 重新选择正确工具
        available = bad.metadata.get("available_tools", [])
        assert "shell_exec" in available

        good = registry.execute_tool("shell_exec", {"command": "cat data.txt"}, ctx)
        assert good.success
        assert "recover-me" in good.output

    def test_recovery_preserves_working_state(self, registry, ctx, ws: Path):
        # 一次错误调用后，后续正确调用不受影响（无副作用残留）
        registry.execute_tool("ghost_tool", {}, ctx)
        assert registry.has("shell_exec")
        result = registry.execute_tool("shell_exec", {"command": "echo ok"}, ctx)
        assert result.success
        assert "ok" in result.output
