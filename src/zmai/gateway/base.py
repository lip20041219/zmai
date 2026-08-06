"""Backend 抽象基类与数据类型。

所有 LLM Backend 必须继承 Backend(ABC) 并实现 invoke()、stream()、capabilities。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from zmai.tool import ToolCall, ToolDefinition

logger = logging.getLogger("zmai.gateway")


class BackendCapability(Enum):
    """Backend 能力枚举。

    声明 Backend 支持的功能特性。
    """

    STREAMING = "streaming"
    TOOL_USE = "tool_use"
    SYSTEM_PROMPT = "system_prompt"
    MULTI_TURN = "multi_turn"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"


@dataclass
class TokenUsage:
    """Token 用量统计。

    Attributes:
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数。
        cache_read_tokens: 缓存读取 token 数。
        cache_write_tokens: 缓存写入 token 数。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total": self.total,
        }


@dataclass
class BackendRequest:
    """Backend 调用请求。

    Attributes:
        messages: 消息列表，每项含 role 和 content。
        tools: 可用工具定义列表。
        system_prompt: 系统提示词。
        max_tokens: 最大生成 token 数。
        temperature: 采样温度。
        stop_sequences: 停止序列。
        metadata: Backend 特定元数据。
    """

    messages: list[dict[str, Any]]
    tools: list[ToolDefinition] | None = None
    system_prompt: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7
    stop_sequences: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendResponse:
    """Backend 调用响应。

    Attributes:
        content: 模型生成的文本。
        tool_calls: 模型请求的工具调用列表。
        usage: Token 用量。
        stop_reason: 停止原因（end_turn / max_tokens / stop_sequence / tool_use）。
        metadata: Backend 特定元数据。
    """

    content: str = ""
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage | None = None
    stop_reason: str = "end_turn"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendEvent:
    """流式调用事件。

    Attributes:
        type: 事件类型（text / tool_call / error / done）。
        data: 事件数据。
        index: 事件序号。
    """

    type: str  # "text", "tool_call", "error", "done"
    data: Any
    index: int = 0


class Backend(ABC):
    """LLM Backend 抽象基类。

    所有 Backend 实现必须：
    1. 设置 name 类属性
    2. 实现 invoke() 和 stream()
    3. 实现 capabilities 属性
    """

    name: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise TypeError(f"Backend subclass {cls.__name__} must define 'name'")

    @property
    def model(self) -> str:
        """模型名称，子类应覆盖返回 self._model。"""
        return ""

    @property
    def provider(self) -> str:
        """提供商名称，默认为 name。"""
        return self.name

    @property
    def config(self) -> dict[str, Any]:
        """Backend 配置字典（只读），由子类 __init__ 填充。

        Runtime 通过此属性读取 backend 的有效配置参数
        （如 temperature、max_tokens），无需访问私有属性。
        """
        return getattr(self, "_config", {})

    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse:
        """同步调用模型，返回完整响应。

        Args:
            request: 调用请求。

        Returns:
            模型响应。

        Raises:
            BackendError: API 调用失败时抛出。
        """
        ...

    @abstractmethod
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        """流式调用模型，逐个产生事件。

        Args:
            request: 调用请求。

        Yields:
            BackendEvent: 流式事件（text / tool_call / error / done）。
        """
        ...

    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]:
        """声明 Backend 支持的能力集。"""
        ...

    def supports(self, capability: BackendCapability) -> bool:
        """检查是否支持某个能力。

        Args:
            capability: 待检查的能力。

        Returns:
            True 支持，False 不支持。
        """
        return capability in self.capabilities
