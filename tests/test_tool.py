"""Tests for zmai.tool module."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from zmai.errors import ToolError
from zmai.tool import Tool, ToolCall, ToolContext, ToolDefinition, ToolRegistry, ToolResult

# ── 测试用 Tool 实现 ──────────────────────────────────────────


class EchoTool(Tool):
    name = "echo"
    description = "回声工具，返回输入内容"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要回显的文本"},
        },
        "required": ["text"],
    }

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.ok(f"echo: {params.get('text', '')}")


class AddTool(Tool):
    name = "add"
    description = "加法工具"
    parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "number"},
        },
        "required": ["a", "b"],
    }

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        a = params.get("a", 0)
        b = params.get("b", 0)
        return ToolResult.ok(str(a + b), metadata={"a": a, "b": b})


class FailTool(Tool):
    name = "fail"
    description = "总是失败的工具"
    parameters = {"type": "object", "properties": {}}

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.err("always fails")


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def tool_context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        agent_id="test_agent",
        workspace_path=tmp_path,
        config={},
        timeout=30,
        env={"TEST": "1"},
        logger=logging.getLogger("zmai.test"),
    )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


# ── 测试: Tool ABC ────────────────────────────────────────────


class TestToolABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            Tool()  # type: ignore

    def test_subclass_must_define_name(self) -> None:
        with pytest.raises(TypeError, match="must define 'name'"):
            type("BadTool", (Tool,), {"description": "desc", "execute": lambda s, c, p: None})

    def test_subclass_must_define_description(self) -> None:
        with pytest.raises(TypeError, match="must define 'description'"):
            type("BadTool", (Tool,), {"name": "bad", "execute": lambda s, c, p: None})

    def test_concrete_tool_can_execute(self, tool_context: ToolContext) -> None:
        tool = EchoTool()
        result = tool.execute(tool_context, {"text": "hello"})
        assert result.success
        assert result.output == "echo: hello"

    def test_to_definition(self) -> None:
        tool = EchoTool()
        td = tool.to_definition()
        assert isinstance(td, ToolDefinition)
        assert td.name == "echo"
        assert td.description == "回声工具，返回输入内容"
        assert td.input_schema == EchoTool.parameters


# ── 测试: Tool 参数校验 ──────────────────────────────────────


class TestToolValidation:
    def test_validate_required_params(self) -> None:
        tool = EchoTool()
        assert tool.validate({"text": "hello"})
        assert not tool.validate({})
        assert not tool.validate({"other": "val"})

    def test_validate_type_checking(self) -> None:
        tool = EchoTool()
        assert not tool.validate({"text": 123})  # 应该是 string

    def test_validate_number_type(self) -> None:
        tool = AddTool()
        assert tool.validate({"a": 1, "b": 2})
        assert tool.validate({"a": 1.5, "b": 2.5})
        assert not tool.validate({"a": "1", "b": 2})

    def test_validate_empty_schema(self) -> None:
        class NoParamsTool(Tool):
            name = "no_params"
            description = "无参数"
            parameters = {}
            def execute(self, context: ToolContext, params: dict) -> ToolResult:
                return ToolResult.ok("done")
        assert NoParamsTool().validate({})
        assert NoParamsTool().validate({"unexpected": "val"})

    def test_validate_no_schema(self) -> None:
        class NoSchemaTool(Tool):
            name = "no_schema"
            description = "无 schema"
            parameters = {}
            def execute(self, context: ToolContext, params: dict) -> ToolResult:
                return ToolResult.ok("done")
        assert NoSchemaTool().validate({"anything": "goes"})


# ── 测试: ToolResult ─────────────────────────────────────────


class TestToolResult:
    def test_ok_factory(self) -> None:
        r = ToolResult.ok("success")
        assert r.success
        assert r.output == "success"
        assert r.error is None

    def test_err_factory(self) -> None:
        r = ToolResult.err("failure")
        assert not r.success
        assert r.error == "failure"

    def test_to_dict(self) -> None:
        r = ToolResult.ok("out", metadata={"key": "val"})
        d = r.to_dict()
        assert d["success"] is True
        assert d["output"] == "out"
        assert d["metadata"] == {"key": "val"}

    def test_fail_tool(self, tool_context: ToolContext) -> None:
        tool = FailTool()
        result = tool.execute(tool_context, {})
        assert not result.success
        assert result.error == "always fails"


# ── 测试: Data Classes ───────────────────────────────────────


class TestToolDataClasses:
    def test_tool_call(self) -> None:
        tc = ToolCall(id="call_1", name="echo", params={"text": "hi"})
        assert tc.id == "call_1"
        assert tc.name == "echo"
        assert tc.params == {"text": "hi"}

    def test_tool_context_defaults(self, tmp_path: Path) -> None:
        ctx = ToolContext(agent_id="a", workspace_path=tmp_path)
        assert ctx.timeout == 120
        assert ctx.config == {}
        assert ctx.env == {}
        assert ctx.logger is None

    def test_tool_context_custom(self, tmp_path: Path) -> None:
        ctx = ToolContext(
            agent_id="a",
            workspace_path=tmp_path,
            timeout=60,
            config={"key": "val"},
        )
        assert ctx.timeout == 60
        assert ctx.config == {"key": "val"}


# ── 测试: ToolRegistry ───────────────────────────────────────


class TestToolRegistry:
    def test_register_and_get(self, tool_registry: ToolRegistry) -> None:
        tool_registry.register(EchoTool())
        tool = tool_registry.get("echo")
        assert isinstance(tool, EchoTool)

    def test_get_nonexistent(self, tool_registry: ToolRegistry) -> None:
        with pytest.raises(ToolError, match="工具未注册"):
            tool_registry.get("nonexistent")

    def test_register_overwrite(self, tool_registry: ToolRegistry, caplog: pytest.LogCaptureFixture) -> None:  # noqa: E501
        tool_registry.register(EchoTool())
        tool_registry.register(EchoTool())  # 覆盖应记录 warning
        assert any("将被覆盖" in msg for msg in caplog.messages)

    def test_list(self, tool_registry: ToolRegistry) -> None:
        tool_registry.register(EchoTool())
        tool_registry.register(AddTool())
        tools = tool_registry.list()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"echo", "add"}

    def test_unregister(self, tool_registry: ToolRegistry) -> None:
        tool_registry.register(EchoTool())
        tool_registry.unregister("echo")
        assert len(tool_registry.list()) == 0

    def test_unregister_nonexistent(self, tool_registry: ToolRegistry) -> None:
        with pytest.raises(ToolError, match="工具未注册"):
            tool_registry.unregister("nonexistent")

    def test_execute(self, tool_registry: ToolRegistry, tool_context: ToolContext) -> None:
        tool_registry.register(EchoTool())
        result = tool_registry.execute("echo", {"text": "hello"}, tool_context)
        assert result.success
        assert result.output == "echo: hello"

    def test_execute_nonexistent(self, tool_registry: ToolRegistry, tool_context: ToolContext) -> None:  # noqa: E501
        with pytest.raises(ToolError, match="工具未注册"):
            tool_registry.execute("nonexistent", {}, tool_context)

    def test_definitions(self, tool_registry: ToolRegistry) -> None:
        tool_registry.register(EchoTool())
        tool_registry.register(AddTool())
        defs = tool_registry.definitions()
        assert len(defs) == 2
        names = {d.name for d in defs}
        assert names == {"echo", "add"}
        for d in defs:
            assert isinstance(d, ToolDefinition)
            assert "input_schema" in dir(d) or d.input_schema

    def test_thread_safety(self, tool_registry: ToolRegistry) -> None:
        """验证注册表线程安全。"""
        import concurrent.futures

        def register_tool(i: int) -> None:
            name = f"tool_{i:03d}"
            tool_cls = type(
                name,
                (Tool,),
                {
                    "name": name,
                    "description": f"Tool {i}",
                    "parameters": {},
                    "execute": lambda s, c, p: ToolResult.ok(str(i)),
                },
            )
            tool_registry.register(tool_cls())

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(register_tool, range(50)))

        assert len(tool_registry.list()) == 50
