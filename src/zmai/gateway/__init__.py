"""ZMAI Gateway — Backend abstraction layer."""

from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.gateway.mcp import MCPClient
from zmai.gateway.plugin import BackendPlugin, PluginRegistry
from zmai.gateway.registry import BackendRegistry
from zmai.gateway.tool_router import ToolRouter

__all__ = [
    "Backend",
    "BackendCapability",
    "BackendEvent",
    "BackendPlugin",
    "BackendRequest",
    "BackendResponse",
    "BackendRegistry",
    "MCPClient",
    "PluginRegistry",
    "TokenUsage",
    "ToolRouter",
]
