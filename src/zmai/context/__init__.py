"""Context — modular context management components.

Components:
  - SlidingWindow:    固定大小消息滑动窗口
  - RecentMessages:   活跃消息 + 工具结果缓冲区
  - SummaryMemory:    摘要记忆 + 状态追踪
  - ContextPruner:    token 预算裁减编排
  - ContextManager:   统一 Facade（与旧 API 兼容）
"""

from zmai.context.manager import ContextManager
from zmai.context.memory import SummaryMemory
from zmai.context.pruner import ContextPruner, PruneAction
from zmai.context.window import RecentMessages, SlidingWindow

__all__ = [
    "SlidingWindow", "RecentMessages",
    "SummaryMemory",
    "ContextPruner", "PruneAction",
    "ContextManager",
]
