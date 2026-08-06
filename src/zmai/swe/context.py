"""Context Manager — 结构化上下文管理（向后兼容导出）。

此文件从 zmai.context 模块重新导出 ContextManager，
保证 `from zmai.swe.context import ContextManager` 仍可正常工作。
"""

from zmai.context.manager import ContextManager
from zmai.context.pruner import (
    DEFAULT_MAX_CHARS,
    DEFAULT_RECENT_WINDOW,
    DEFAULT_TOOL_RESULT_WINDOW,
)
from zmai.context.memory import _estimate_tokens, _truncate

__all__ = [
    "ContextManager",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_RECENT_WINDOW",
    "DEFAULT_TOOL_RESULT_WINDOW",
    "_estimate_tokens",
    "_truncate",
]
