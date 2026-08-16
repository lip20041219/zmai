"""Tests for zmai.gateway module."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from zmai.errors import BackendError, BackendInvalidResponse
from zmai.gateway import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRegistry,
    BackendRequest,
    BackendResponse,
    MCPClient,
    TokenUsage,
    ToolRouter,
)
from zmai.gateway.backends import ClaudeBackend, DeepSeekBackend, GeminiBackend
from zmai.tool import Tool, ToolCall, ToolContext, ToolDefinition, ToolRegistry, ToolResult

# ── Mock Backend ──────────────────────────────────────────────


class MockBackend(Backend):
    name = "mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self.stream_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        return BackendResponse(
            content=f"mock: {request.messages[-1].get('content', '')}",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "mock-v1"},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        self.stream_count += 1
        yield BackendEvent(type="text", data="mock ", index=0)
        yield BackendEvent(type="text", data="stream", index=1)
        yield BackendEvent(type="usage", data={"input_tokens": 10, "output_tokens": 5}, index=2)
        yield BackendEvent(type="done", data="", index=3)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.STREAMING}


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def tool_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_EchoTool())
    return r


@pytest.fixture
def tool_router(tool_registry: ToolRegistry) -> ToolRouter:
    return ToolRouter(tool_registry)


@pytest.fixture
def backend_registry() -> BackendRegistry:
    return BackendRegistry()


@pytest.fixture
def tool_context(tmp_path: Path) -> ToolContext:
    return ToolContext(agent_id="test", workspace_path=tmp_path)


# ── 测试用 Tool ──────────────────────────────────────────────


class _EchoTool(Tool):
    name = "echo"
    description = "回声工具"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.ok(f"echo: {params.get('text', '')}")


# ── 测试: Backend ABC ────────────────────────────────────────


class TestBackendABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError, match="Can't instantiate abstract class|Backend"):
            Backend()  # type: ignore

    def test_subclass_must_define_name(self) -> None:
        with pytest.raises(TypeError, match="must define 'name'"):
            type(
                "NoNameBackend",
                (Backend,),
                {
                    "name": "",
                    "invoke": lambda s, r: BackendResponse(),
                    "stream": lambda s, r: iter([BackendEvent(type="done", data="")]),
                    "capabilities": property(lambda s: set()),
                },
            )

    def test_concrete_backend_invoke(self) -> None:
        backend = MockBackend()
        req = BackendRequest(messages=[{"role": "user", "content": "hello"}])
        resp = backend.invoke(req)
        assert resp.content == "mock: hello"
        assert resp.usage is not None
        assert resp.usage.input_tokens == 10
        assert resp.usage.output_tokens == 5

    def test_concrete_backend_stream(self) -> None:
        backend = MockBackend()
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        events = list(backend.stream(req))
        assert len(events) == 4
        assert events[0].type == "text"
        assert events[-1].type == "done"

    def test_supports(self) -> None:
        backend = MockBackend()
        assert backend.supports(BackendCapability.STREAMING)
        assert not backend.supports(BackendCapability.TOOL_USE)

    def test_capabilities(self) -> None:
        backend = MockBackend()
        caps = backend.capabilities
        assert BackendCapability.STREAMING in caps
        assert BackendCapability.TOOL_USE not in caps


# ── 测试: BackendRegistry ────────────────────────────────────


class TestBackendRegistry:
    def test_register_and_get(self, backend_registry: BackendRegistry) -> None:
        backend_registry.register("mock", MockBackend, default=True)
        backend = backend_registry.get("mock")
        assert isinstance(backend, MockBackend)

    def test_get_default(self, backend_registry: BackendRegistry) -> None:
        backend_registry.register("mock", MockBackend)
        backend = backend_registry.get()  # 唯一注册的即为默认
        assert isinstance(backend, MockBackend)

    def test_get_nonexistent(self, backend_registry: BackendRegistry) -> None:
        with pytest.raises(BackendError, match="Backend 未注册"):
            backend_registry.get("nonexistent")

    def test_get_no_default(self, backend_registry: BackendRegistry) -> None:
        with pytest.raises(BackendError, match="未设置默认 Backend"):
            backend_registry.get()

    def test_register_non_backend(self, backend_registry: BackendRegistry) -> None:
        with pytest.raises(BackendError, match="不是 Backend 子类"):
            backend_registry.register("bad", str)  # type: ignore

    def test_set_default(self, backend_registry: BackendRegistry) -> None:
        backend_registry.register("a", MockBackend)
        backend_registry.register("b", MockBackend)
        backend_registry.set_default("b")
        assert backend_registry.default_name == "b"

    def test_set_default_nonexistent(self, backend_registry: BackendRegistry) -> None:
        with pytest.raises(BackendError, match="未注册|nonexistent"):
            backend_registry.set_default("nonexistent")

    def test_list(self, backend_registry: BackendRegistry) -> None:
        backend_registry.register("a", MockBackend)
        backend_registry.register("b", MockBackend)
        names = backend_registry.list()
        assert set(names) == {"a", "b"}

    def test_instance_caching(self, backend_registry: BackendRegistry) -> None:
        backend_registry.register("mock", MockBackend, default=True)
        b1 = backend_registry.get("mock")
        b2 = backend_registry.get("mock")
        assert b1 is b2  # 同一个实例


# ── 测试: ToolRouter ────────────────────────────────────────


class TestToolRouter:
    def test_execute(self, tool_router: ToolRouter, tool_context: ToolContext) -> None:
        tc = ToolCall(id="c1", name="echo", params={"text": "hello"})
        result = tool_router.execute(tc, tool_context)
        assert result.success
        assert result.output == "echo: hello"

    def test_execute_nonexistent_tool(self, tool_router: ToolRouter, tool_context: ToolContext) -> None:  # noqa: E501
        tc = ToolCall(id="c1", name="nonexistent", params={})
        with pytest.raises(BackendError, match="工具未注册"):
            tool_router.execute(tc, tool_context)

    def test_definitions(self, tool_router: ToolRouter) -> None:
        defs = tool_router.definitions()
        assert len(defs) == 1
        assert defs[0].name == "echo"

    def test_execute_with_timeout(self, tool_router: ToolRouter, tool_context: ToolContext) -> None:
        tc = ToolCall(id="c1", name="echo", params={"text": "fast"})
        result = tool_router.execute_with_timeout(tc, tool_context, timeout=5)
        assert result.success

    def test_execute_records_duration(self, tool_router: ToolRouter, tool_context: ToolContext) -> None:  # noqa: E501
        tc = ToolCall(id="c1", name="echo", params={"text": "timing"})
        result = tool_router.execute(tc, tool_context)
        assert result.duration_ms >= 0


# ── 测试: ClaudeBackend ──────────────────────────────────────


class TestClaudeBackend:
    def test_init_defaults(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test-key"})
        assert backend.name == "claude"
        assert backend._model == "claude-sonnet-4-6"
        assert backend._base_url == "https://api.anthropic.com/v1"
        assert backend._max_retries == 3

    def test_init_custom_config(self) -> None:
        backend = ClaudeBackend(config={
            "api_key": "key",
            "model": "claude-opus-4-8",
            "base_url": "https://custom.example.com",
            "max_retries": 5,
            "timeout": 600,
        })
        assert backend._model == "claude-opus-4-8"
        assert backend._base_url == "https://custom.example.com"
        assert backend._max_retries == 5

    def test_capabilities(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test"})
        caps = backend.capabilities
        assert BackendCapability.STREAMING in caps
        assert BackendCapability.TOOL_USE in caps
        assert BackendCapability.SYSTEM_PROMPT in caps
        assert BackendCapability.MULTI_TURN in caps

    def test_supports(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test"})
        assert backend.supports(BackendCapability.STREAMING)
        assert backend.supports(BackendCapability.TOOL_USE)
        assert not backend.supports(BackendCapability.VISION)

    def test_build_request_body(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test"})
        req = BackendRequest(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="You are a helpful assistant.",
            tools=[ToolDefinition(name="echo", description="Echo", input_schema={"type": "object"})],  # noqa: E501
            max_tokens=2048,
            temperature=0.5,
            stop_sequences=["\n\n"],
        )
        body = backend._build_request_body(req)
        assert body["model"] == "claude-sonnet-4-6"
        assert body["max_tokens"] == 2048
        assert body["system"] == "You are a helpful assistant."
        assert body["temperature"] == 0.5
        assert body["stop_sequences"] == ["\n\n"]
        assert len(body["tools"]) == 1
        assert body["tools"][0]["name"] == "echo"

    def test_parse_response_with_text(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test"})
        raw = {
            "id": "msg_123",
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hello!"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        resp = backend._parse_response(raw)
        assert resp.content == "Hello!"
        assert resp.tool_calls is None
        assert resp.stop_reason == "end_turn"
        assert resp.usage is not None
        assert resp.usage.input_tokens == 10
        assert resp.usage.output_tokens == 5

    def test_parse_response_with_tool_calls(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test"})
        raw = {
            "id": "msg_456",
            "model": "claude-sonnet-4-6",
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "I'll use a tool."},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "echo",
                    "input": {"text": "hello"},
                },
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        resp = backend._parse_response(raw)
        assert "I'll use a tool." in resp.content
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "echo"
        assert resp.tool_calls[0].params == {"text": "hello"}
        assert resp.stop_reason == "tool_use"

    def test_parse_response_max_tokens(self) -> None:
        backend = ClaudeBackend(config={"api_key": "test"})
        raw = {"id": "m1", "model": "c", "stop_reason": "max_tokens", "content": [], "usage": {}}
        resp = backend._parse_response(raw)
        assert resp.stop_reason == "max_tokens"

    def test_tool_def_to_claude(self) -> None:
        td = ToolDefinition(name="my_tool", description="My tool", input_schema={"type": "object"})
        result = ClaudeBackend._tool_def_to_claude(td)
        assert result == {
            "name": "my_tool",
            "description": "My tool",
            "input_schema": {"type": "object"},
        }

    def test_invoke_http_error(self) -> None:
        """无 API Key 时调用应抛出 BackendError（网络/认证/超时均可）。"""
        backend = ClaudeBackend(config={"api_key": "", "timeout": 1})
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendError):
            backend.invoke(req)


# ── 测试: Data Classes ───────────────────────────────────────


class TestDataClasses:
    def test_backend_request_defaults(self) -> None:
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.max_tokens == 4096
        assert req.temperature == 0.7
        assert req.tools is None
        assert req.system_prompt is None
        assert req.stop_sequences is None

    def test_backend_response_defaults(self) -> None:
        resp = BackendResponse(content="hello")
        assert resp.content == "hello"
        assert resp.tool_calls is None
        assert resp.usage is None
        assert resp.stop_reason == "end_turn"

    def test_backend_event(self) -> None:
        e = BackendEvent(type="text", data="hello", index=0)
        assert e.type == "text"
        assert e.data == "hello"
        assert e.index == 0

    def test_token_usage(self) -> None:
        u = TokenUsage(input_tokens=100, output_tokens=50)
        assert u.total == 150
        d = u.to_dict()
        assert d["total"] == 150

    def test_token_usage_with_cache(self) -> None:
        u = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=20, cache_write_tokens=10)  # noqa: E501
        assert u.total == 150
        assert u.cache_read_tokens == 20

    def test_backend_capability_enum(self) -> None:
        assert BackendCapability.STREAMING.value == "streaming"
        assert BackendCapability.TOOL_USE.value == "tool_use"
        assert BackendCapability.SYSTEM_PROMPT.value == "system_prompt"
        assert BackendCapability.MULTI_TURN.value == "multi_turn"
        assert BackendCapability.VISION.value == "vision"
        assert BackendCapability.STRUCTURED_OUTPUT.value == "structured_output"


# ── 测试: ToolRouter with real ToolRegistry ──────────────────


class TestToolRouterIntegration:
    def test_router_uses_registry_definitions(self, tool_registry: ToolRegistry) -> None:
        router = ToolRouter(tool_registry)
        defs = router.definitions()
        assert len(defs) == 1

    def test_multiple_tools_route(self, tool_registry: ToolRegistry, tool_context: ToolContext) -> None:  # noqa: E501
        class _AddTool(Tool):
            name = "add"
            description = "Add"
            parameters = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}  # noqa: E501
            def execute(self, context: ToolContext, params: dict) -> ToolResult:
                return ToolResult.ok(str(params.get("a", 0) + params.get("b", 0)))

        tool_registry.register(_AddTool())
        router = ToolRouter(tool_registry)

        r1 = router.execute(ToolCall(id="c1", name="echo", params={"text": "hi"}), tool_context)
        assert r1.output == "echo: hi"

        r2 = router.execute(ToolCall(id="c2", name="add", params={"a": 3, "b": 4}), tool_context)
        assert r2.output == "7"


# ── 测试: MCPClient ──────────────────────────────────────────


class TestMCPClient:
    def test_init(self) -> None:
        client = MCPClient("http://localhost:8080/mcp")
        assert client._server_url == "http://localhost:8080/mcp"
        assert client._timeout == 30
        client.close()

    def test_init_custom_config(self) -> None:
        client = MCPClient(
            "http://localhost:9090/mcp",
            config={"timeout": 10, "headers": {"Authorization": "Bearer tok"}},
        )
        assert client._server_url == "http://localhost:9090/mcp"
        assert client._timeout == 10
        assert client._headers["Authorization"] == "Bearer tok"
        client.close()

    def test_init_strips_trailing_slash(self) -> None:
        client = MCPClient("http://localhost/mcp/")
        assert client._server_url == "http://localhost/mcp"
        client.close()

    def test_mcp_connection_error(self) -> None:
        client = MCPClient("http://localhost:1/mcp", config={"timeout": 1})
        with pytest.raises(Exception, match="|"):
            client.list_tools()
        client.close()


# ── 测试: ClaudeBackend + BackendRegistry 集成 ──────────────


class TestClaudeBackendIntegration:
    def test_register_in_registry(self, backend_registry: BackendRegistry) -> None:
        backend_registry.register("claude", ClaudeBackend, default=True, config={"api_key": "test"})
        backend = backend_registry.get()
        assert isinstance(backend, ClaudeBackend)
        assert backend.name == "claude"


# ── 测试: DeepSeekBackend ────────────────────────────────────


class TestDeepSeekBackend:
    def test_init_defaults(self):
        backend = DeepSeekBackend(config={"api_key": "test-key"})
        assert backend.name == "deepseek"
        assert backend.model == "deepseek-v4-flash"
        assert backend._base_url == "https://api.deepseek.com/v1"

    def test_init_custom_config(self):
        backend = DeepSeekBackend(config={
            "api_key": "custom-key",
            "model": "deepseek-v4",
            "base_url": "https://custom.deepseek.com",
            "max_tokens": 2048,
        })
        assert backend.model == "deepseek-v4"
        assert backend._base_url == "https://custom.deepseek.com"
        assert backend._max_tokens == 2048

    def test_capabilities(self):
        backend = DeepSeekBackend(config={"api_key": "test"})
        caps = backend.capabilities
        assert BackendCapability.TOOL_USE in caps
        assert BackendCapability.SYSTEM_PROMPT in caps
        assert BackendCapability.STREAMING not in caps

    def test_supports(self):
        backend = DeepSeekBackend(config={"api_key": "test"})
        assert backend.supports(BackendCapability.TOOL_USE) is True
        assert backend.supports(BackendCapability.STREAMING) is False

    def test_stream_not_supported(self):
        backend = DeepSeekBackend(config={"api_key": "test"})
        from zmai.gateway.base import BackendRequest
        req = BackendRequest(messages=[])
        with pytest.raises(NotImplementedError, match="不支持流式"):
            list(backend.stream(req))

    def test_invoke_http_error(self):
        backend = DeepSeekBackend(config={"api_key": "bad-key"})
        from zmai.errors import BackendError
        from zmai.gateway.base import BackendRequest
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendError, match="KEY_INVALID|401|API Key"):
            backend.invoke(req)


# ═══════════════════════════════════════════════════════════════
# 测试: ClaudeBackend 重试
# ═══════════════════════════════════════════════════════════════


class TestClaudeBackendRetry:
    """ClaudeBackend 重试逻辑测试。

    ClaudeBackend.invoke() 在遇到非 BackendError 异常时，
    以指数退避（1s, 2s, 4s…）重试最多 max_retries 次。
    BackendError 直接透传，不进入重试逻辑。
    """

    def test_retry_eventually_succeeds(self):
        """连续失败后重试最终成功，验证重试次数和最终状态。"""
        backend = ClaudeBackend(config={"api_key": "test", "max_retries": 3})
        backend._post = MagicMock(side_effect=[
            Exception("503 Service Unavailable"),
            Exception("502 Bad Gateway"),
            {
                "id": "msg_1", "model": "c",
                "content": [{"type": "text", "text": "ok after retry"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        ])
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        resp = backend.invoke(req)
        assert resp.content == "ok after retry"
        assert backend._post.call_count == 3

    def test_retry_backend_error_not_retried(self):
        """BackendError 不被重试，直接透传。"""
        backend = ClaudeBackend(config={"api_key": "test", "max_retries": 3})
        backend._post = MagicMock(side_effect=BackendError("401 auth error", status_code=401))
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendError, match="401"):
            backend.invoke(req)
        assert backend._post.call_count == 1

    def test_retry_all_attempts_fail(self):
        """全部重试耗尽，最终抛出 BackendError。"""
        backend = ClaudeBackend(config={"api_key": "test", "max_retries": 2})
        backend._post = MagicMock(side_effect=Exception("connection refused"))
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendError, match="API 调用失败"):
            backend.invoke(req)
        assert backend._post.call_count == 2


# ═══════════════════════════════════════════════════════════════
# 测试: Backend 接口一致性
# ═══════════════════════════════════════════════════════════════


class TestBackendInterfaceConsistency:
    """所有 Backend 实现必须满足统一接口契约。"""

    _BACKENDS = [
        ("claude", ClaudeBackend),
        ("deepseek", DeepSeekBackend),
        ("gemini", GeminiBackend),
    ]

    def test_all_have_name_set(self):
        """每个 Backend 的 name 类属性必须非空。"""
        for name, cls in self._BACKENDS:
            assert cls.name == name, f"{cls.__name__}.name != '{name}'"

    def test_all_implement_required_methods(self):
        """每个 Backend 实现 invoke、stream、capabilities。"""
        for _, cls in self._BACKENDS:
            assert hasattr(cls, "invoke") and callable(getattr(cls, "invoke"))
            assert hasattr(cls, "stream") and callable(getattr(cls, "stream"))
            assert hasattr(cls, "capabilities")

    def test_all_accept_same_config_keys(self):
        """每个 Backend 接受相同的 7 个配置字段。"""
        config = {
            "api_key": "test-key",
            "model": "test-model",
            "base_url": "https://test.example.com",
            "timeout": 60,
            "max_tokens": 2048,
            "temperature": 0.5,
        }
        for _, cls in self._BACKENDS:
            instance = cls(config=config)
            assert instance._config.get("api_key") == "test-key"

    def test_all_have_model_property(self):
        """model 属性返回字符串。"""
        for _, cls in self._BACKENDS:
            instance = cls(config={"api_key": "test"})
            assert isinstance(instance.model, str)

    def test_all_have_provider_property(self):
        """provider 属性返回非空字符串。"""
        for _, cls in self._BACKENDS:
            instance = cls(config={"api_key": "test"})
            assert isinstance(instance.provider, str) and len(instance.provider) > 0

    def test_all_have_config_property(self):
        """config 属性返回 dict。"""
        for _, cls in self._BACKENDS:
            instance = cls(config={"api_key": "test"})
            assert isinstance(instance.config, dict)

    def test_all_capabilities_is_set(self):
        """capabilities 返回 set[BackendCapability]。"""
        for _, cls in self._BACKENDS:
            instance = cls(config={"api_key": "test"})
            caps = instance.capabilities
            assert isinstance(caps, set)

    def test_all_supports_returns_bool(self):
        """supports() 返回 bool。"""
        for _, cls in self._BACKENDS:
            instance = cls(config={"api_key": "test"})
            assert isinstance(instance.supports(BackendCapability.TOOL_USE), bool)

    def test_known_interface_gaps_documented(self):
        """已知接口差异：DeepSeekBackend 不支持 STREAMING。"""
        instance = DeepSeekBackend(config={"api_key": "test"})
        assert not instance.supports(BackendCapability.STREAMING)
        with pytest.raises(NotImplementedError):
            list(instance.stream(BackendRequest(messages=[])))


# ═══════════════════════════════════════════════════════════════
# 测试: Backend 无效响应防护
# ═══════════════════════════════════════════════════════════════


class TestBackendInvalidResponse:
    """所有 Backend 遇到无效 API 响应必须抛出 BackendInvalidResponse。

    三种 Invalid Response 场景:
      1. {}                  — 空响应（200 OK 但 body={}）
      2. {"error": ...}      — API 层错误（body 内嵌）
      3. 缺字段 / 空列表     — 结构不完整
    """

    # ── ClaudeBackend ──────────────────────────────────────

    def test_claude_empty_response(self):
        backend = ClaudeBackend(config={"api_key": "test"})
        backend._post = MagicMock(return_value={})
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendInvalidResponse, match="空响应体"):
            backend.invoke(req)

    def test_claude_error_response(self):
        backend = ClaudeBackend(config={"api_key": "test"})
        backend._post = MagicMock(return_value={"error": {"message": "rate limit exceeded"}})
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendInvalidResponse, match="rate limit exceeded"):
            backend.invoke(req)

    def test_claude_missing_content(self):
        backend = ClaudeBackend(config={"api_key": "test"})
        backend._post = MagicMock(return_value={"id": "msg_1", "usage": {}, "stop_reason": "end_turn"})  # noqa: E501
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendInvalidResponse, match="缺少必要字段"):
            backend.invoke(req)

    # ── DeepSeekBackend ────────────────────────────────────

    @staticmethod
    def _mock_urlopen(mock_urlopen, body: bytes) -> None:
        """配置 urlopen mock: with 上下文 → read() → decode() 链。"""
        # urlopen 返回上下文管理器, __enter__ 返回 response
        # 设置 resp.read() → bytes
        resp = mock_urlopen.return_value.__enter__.return_value
        resp.read.return_value = body

    def test_deepseek_empty_response(self):
        backend = DeepSeekBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.deepseek.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b"{}")
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="空响应体"):
                backend.invoke(req)

    def test_deepseek_error_response(self):
        backend = DeepSeekBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.deepseek.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b'{"error": {"message": "invalid API key"}}')
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="invalid API key"):
                backend.invoke(req)

    def test_deepseek_empty_choices(self):
        backend = DeepSeekBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.deepseek.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b'{"choices": []}')
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="choices 为空"):
                backend.invoke(req)

    def test_deepseek_missing_choices(self):
        """choices 字段缺失 → BackendInvalidResponse（代替原 IndexError）。"""
        backend = DeepSeekBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.deepseek.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b'{"id": "123", "model": "deepseek-chat"}')
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="缺少必要字段"):
                backend.invoke(req)

    def test_deepseek_invoke_openai_compatible_endpoint(self):
        """默认协议必须为 OpenAI-compatible，发往 /chat/completions，且用 Bearer。

        回归：DeepSeek 默认模型配置不得被误路由到 Anthropic /messages 协议。
        """
        backend = DeepSeekBackend(config={"api_key": "test-key"})
        body = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode()
        with patch("zmai.gateway.backends.deepseek.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, body)
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            backend.invoke(req)
        request = mock_urlopen.call_args[0][0]
        url = request.full_url
        assert url.startswith("https://api.deepseek.com/v1"), f"端点域名错误: {url}"
        assert url.endswith("/chat/completions"), f"应为 OpenAI-compatible 端点, 实际 {url}"
        assert request.headers["Authorization"].startswith("Bearer ")
        sent = json.loads(request.data.decode())
        assert "messages" in sent, "应为 OpenAI messages 格式"
        assert "model" in sent
        assert sent["model"] == "deepseek-v4-flash"

    # ── GeminiBackend ──────────────────────────────────────

    def test_gemini_empty_response(self):
        backend = GeminiBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.gemini.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b"{}")
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="空响应体"):
                backend.invoke(req)

    def test_gemini_error_response(self):
        backend = GeminiBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.gemini.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b'{"error": {"message": "API key not valid"}}')
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="API key not valid"):
                backend.invoke(req)

    def test_gemini_empty_candidates(self):
        backend = GeminiBackend(config={"api_key": "test"})
        with patch("zmai.gateway.backends.gemini.urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, b'{"candidates": []}')
            req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(BackendInvalidResponse, match="candidates 为空"):
                backend.invoke(req)


# ── PluginRegistry 默认 Backend 选择 ──────────────────────────

class TestPluginRegistryDefaultSelection:
    def test_default_stays_deepseek_when_anthropic_key_present(self, monkeypatch):
        """存在 ANTHROPIC_API_KEY 时，若 AuthStore 指定 deepseek，
        默认 backend 仍为 deepseek，绝不因 Anthropic 凭据切换为 claude。"""
        monkeypatch.setattr(
            "zmai.auth.store.AuthStore.get_active_backend",
            lambda self: "deepseek",
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test1234567890123456789")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        from zmai.gateway.plugin import PluginRegistry
        reg = PluginRegistry(config={"gateway.default_backend": "auto"})
        assert reg.default_name == "deepseek"

    def test_default_stays_deepseek_via_config_when_anthropic_key_present(self, monkeypatch):
        """即使 AuthStore 无 active，config 显式指定 deepseek 也优先于 ANTHROPIC_API_KEY。"""
        monkeypatch.setattr(
            "zmai.auth.store.AuthStore.get_active_backend",
            lambda self: "",
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test1234567890123456789")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        from zmai.gateway.plugin import PluginRegistry
        reg = PluginRegistry(config={"gateway.default_backend": "deepseek"})
        assert reg.default_name == "deepseek"
