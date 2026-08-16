"""统一 Mock Backend 集合。

提供 7 种 Mock Backend，覆盖所有关键失败场景。
所有测试必须从此导入，不散落在各测试文件中。

每个 Mock 遵循 Backend ABC 接口契约：
  - invoke() 返回 BackendResponse 或抛出 BackendError
  - stream() 产生 BackendEvent 序列
  - capabilities 返回 set[BackendCapability]

异常约定（对齐真实 Backend 行为）：
  - BackendError      → 最终用户看到的错误（401/网络错误/超时）
  - Exception(非BE)   → 临时错误（会触发真实 Backend 的重试逻辑）
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from zmai.errors import BackendError
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.tool import ToolCall

# ── Helpers ─────────────────────────────────────────────────────────

_DEFAULT_USAGE = TokenUsage(input_tokens=10, output_tokens=5)
_DEFAULT_META = {"model": "mock-v1"}


def _text_response(content: str = "ok") -> BackendResponse:
    return BackendResponse(
        content=content,
        usage=_DEFAULT_USAGE,
        stop_reason="end_turn",
        metadata=_DEFAULT_META,
    )


def _tool_response(content: str, tool_name: str, params: dict | None = None) -> BackendResponse:
    return BackendResponse(
        content=content,
        tool_calls=[
            ToolCall(
                id="call_mock_1",
                name=tool_name,
                params=params or {},
            )
        ],
        usage=_DEFAULT_USAGE,
        stop_reason="tool_use",
        metadata=_DEFAULT_META,
    )


def _empty_stream() -> Iterator[BackendEvent]:
    yield BackendEvent(type="done", data="", index=0)


# ═════════════════════════════════════════════════════════════════════
# SuccessBackend
# ═════════════════════════════════════════════════════════════════════


class SuccessBackend(Backend):
    """正常返回预设回复。用于 happy path 测试。

    行为:
      - invoke() 返回预设内容
      - 记录调用次数在 invoke_count
      - 支持预设回复序列（多轮对话）
      - 支持工具调用回复
    """

    name = "success"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._responses: list[dict] = self._config.get("responses", [])
        self._delay: float = self._config.get("delay", 0.0)

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        if self._delay > 0:
            time.sleep(self._delay)

        if self._responses:
            idx = min(self.invoke_count - 1, len(self._responses) - 1)
            resp = self._responses[idx]
            content = resp.get("content", "ok")
            tool = resp.get("tool")
            if tool:
                return _tool_response(content, tool["name"], tool.get("params"))
            return _text_response(content)

        last = request.messages[-1].get("content", "") if request.messages else ""
        return BackendResponse(
            content=f"mock: {last[:100]}",
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="text", data="mock ", index=0)
        yield BackendEvent(type="text", data="stream", index=1)
        yield BackendEvent(type="done", data="", index=2)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE, BackendCapability.SYSTEM_PROMPT}


# ═════════════════════════════════════════════════════════════════════
# AuthErrorBackend
# ═════════════════════════════════════════════════════════════════════


class AuthErrorBackend(Backend):
    """模拟 API Key 认证失败（401）。

    行为:
      - invoke() 始终抛出 BackendError 含 "401"
      - 匹配真实 Backend 在 Key 无效时的行为
    """

    name = "auth_error"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        raise BackendError(
            "auth_error API HTTP 401: authentication_error - invalid API Key",
            status_code=401,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise BackendError(
            "auth_error API HTTP 401: authentication_error - invalid API Key",
            status_code=401,
        )

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


# ═════════════════════════════════════════════════════════════════════
# ConnectionErrorBackend
# ═════════════════════════════════════════════════════════════════════


class ConnectionErrorBackend(Backend):
    """模拟网络连接失败（DNS 解析失败、连接被拒绝）。

    行为:
      - invoke() 抛出 ConnectionError（而非 BackendError）
      - 匹配真实 Backend 在 urllib 连接失败时的原始异常
      - 上层 BackendError 包装留给 backends 层处理
      - status_code=None（网络错误无 HTTP 状态码）
      - 用于测试 SWEAgent 的重试路径（非 BackendError 异常会被重试）
    """

    name = "connection_error"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._error_msg: str = self._config.get(
            "error_msg",
            "[Errno 11001] getaddrinfo failed",
        )

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        raise ConnectionError(self._error_msg)

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise ConnectionError(self._error_msg)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


# ═════════════════════════════════════════════════════════════════════
# TimeoutBackend
# ═════════════════════════════════════════════════════════════════════


class TimeoutBackend(Backend):
    """模拟 Backend 请求超时。

    行为:
      - invoke() 抛出 TimeoutError（而非 BackendError）
      - 不 sleep 延迟，立即抛出
      - 匹配真实 Backend 中 urllib 抛出的 TimeoutError
      - 用于测试 SWEAgent 的重试路径
    """

    name = "timeout"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._error_msg: str = self._config.get(
            "error_msg",
            "timed out after 30 seconds",
        )

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        raise TimeoutError(self._error_msg)

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise TimeoutError(self._error_msg)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


# ═════════════════════════════════════════════════════════════════════
# InvalidResponseBackend
# ═════════════════════════════════════════════════════════════════════


class InvalidResponseBackend(Backend):
    """模拟 Backend 返回非法/空响应。

    行为:
      - 默认返回 BackendResponse(content="") — 合法结构但内容为空
      - empty_response=True（默认）: 返回 {}，触发 validate_backend_response 空响应检查
      - tool_only=True: 返回 BackendResponse(content="", tool_calls=[...]) 有工具调用无文本
      - 用于测试 Runtime/BACKEND_INVALID_RESPONSE 路径
    """

    name = "invalid_response"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._empty: bool = self._config.get("empty_content", True)
        self._tool_only: bool = self._config.get("tool_only", False)
        self._empty_response: bool = self._config.get("empty_response", False)

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        if self._empty_response:
            # 返回空 dict，触发 validate_backend_response 的 "空响应体" 检查
            from zmai.errors import BackendInvalidResponse
            raise BackendInvalidResponse(
                "返回了空响应体", provider="invalid_response",
            )
        if self._tool_only:
            return _tool_response("", "shell_exec", {"command": "echo invalid"})
        return BackendResponse(
            content="",
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="text", data="", index=0)
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


# ═════════════════════════════════════════════════════════════════════
# FlakyBackend
# ═════════════════════════════════════════════════════════════════════


class FlakyBackend(Backend):
    """模拟时好时坏的 Backend。

    行为:
      - 前 fail_count 次调用抛出 Exception（非 BackendError）
      - 第 fail_count+1 次开始返回成功
      - 使用普通 Exception 而非 BackendError：
        真实 Backend 对 BackendError 直接透传（不重试），
        对普通 Exception 才进入重试逻辑。
      - 用于测试重试链：FlakyBackend → ClaudeBackend/DeepSeekBackend 重试
    """

    name = "flaky"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._fail_count: int = self._config.get("fail_count", 2)
        self._error_msg: str = self._config.get(
            "error_msg",
            "503 Service Unavailable: upstream temporarily unavailable",
        )

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        if self.invoke_count <= self._fail_count:
            raise Exception(self._error_msg)
        return _text_response("ok after retry")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        self.invoke_count += 1
        if self.invoke_count <= self._fail_count:
            raise Exception(self._error_msg)
        yield BackendEvent(type="text", data="stream ok", index=0)
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.STREAMING}


# ═════════════════════════════════════════════════════════════════════
# InfiniteLoopBackend
# ═════════════════════════════════════════════════════════════════════


class InfiniteLoopBackend(Backend):
    """模拟 LLM 陷入工具调用循环永不返回文本。

    行为:
      - 每次 invoke() 都返回 tool_call（shell_exec echo loop）
      - 永不返回 content（无 end_turn）
      - 用于测试 Runtime/max_steps 能否终止循环
      - Runtime 的 max_steps 循环是唯一的终止保障
    """

    name = "infinite_loop"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        return BackendResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id=f"call_loop_{self.invoke_count}",
                    name="shell_exec",
                    params={"command": "echo loop_step"},
                )
            ],
            usage=_DEFAULT_USAGE,
            stop_reason="tool_use",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        self.invoke_count += 1
        yield BackendEvent(type="tool_call", data={
            "id": f"call_loop_{self.invoke_count}",
            "name": "shell_exec",
            "input": {"command": "echo loop_step"},
        }, index=0)
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE, BackendCapability.STREAMING}


# ── 兼容别名 ──────────────────────────────────────────────────────
# 旧代码引用 MockBackend / SlowMockBackend 可逐步迁移
MockBackend = SuccessBackend
