"""端到端集成测试 — Runtime + Config + Gateway + SWE 工具。"""

from __future__ import annotations

from pathlib import Path

from zmai.config import Config
from zmai.gateway import BackendRegistry
from zmai.gateway.backends import ClaudeBackend
from zmai.runtime import Runtime
from zmai.swe.agent import SWEAgent
from zmai.swe.tools import ReadFileTool, ShellTool, WriteFileTool
from zmai.tool import ToolContext, ToolRegistry


class TestToolRegistryIntegration:
    def test_register_swe_tools(self):
        registry = ToolRegistry()
        tools = [ReadFileTool(), WriteFileTool(), ShellTool()]
        for t in tools:
            registry.register(t)
        assert len(registry.list()) == 3
        names = [t.name for t in registry.list()]
        assert "read_file" in names
        assert "write_file" in names
        assert "shell_exec" in names

    def test_tool_definitions_produced(self):
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        defs = registry.definitions()
        assert len(defs) == 1
        assert defs[0].name == "read_file"
        assert "path" in defs[0].input_schema.get("required", [])


class TestShellToolOnDisk:
    def test_shell_echo(self, tmp_path: Path):
        tool = ShellTool()
        ctx = ToolContext(agent_id="t", workspace_path=tmp_path, timeout=10)
        r = tool.execute(ctx, {"command": "echo hello_world"})
        assert r.success
        assert "hello_world" in r.output

    def test_shell_failure(self, tmp_path: Path):
        tool = ShellTool()
        ctx = ToolContext(agent_id="t", workspace_path=tmp_path, timeout=10)
        r = tool.execute(ctx, {"command": "exit 42"})
        assert not r.success
        assert "42" in (r.error or "")
