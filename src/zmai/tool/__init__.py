"""ZMAI Tool — abstraction layer for agent-environment interaction."""

from zmai.tool.base import Tool, ToolCall, ToolContext, ToolDefinition, ToolResult
from zmai.tool.registry import ToolRegistry

__all__ = [
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolDefinition",
    "ToolResult",
    "ToolRegistry",
]
