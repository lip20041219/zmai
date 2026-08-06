"""ContextPruner — token 预算管理与上下文裁减编排器。

两级策略:
  1. 智能压缩: 滑出旧消息 → 生成摘要 → 合并
  2. 强制截断: 按优先级丢弃最不重要的数据

使用方式:
    pruner = ContextPruner(max_chars=32000)
    action = pruner.evaluate(current_size)
    if action.should_compact:
        pruner.compact(window, memory)
    if action.should_force_truncate:
        pruner.force_truncate(window, memory, total_size_fn)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from zmai.context.memory import SummaryMemory, _truncate
from zmai.context.window import RecentMessages

logger = logging.getLogger("zmai.context.pruner")

DEFAULT_MAX_CHARS = 32000
_HARD_LIMIT_RATIO = 1.5
DEFAULT_RECENT_WINDOW = 6
DEFAULT_TOOL_RESULT_WINDOW = 6


@dataclass
class PruneAction:
    """裁减动作描述。"""
    should_prune: bool = False
    should_compact: bool = False
    should_force_truncate: bool = False
    reason: str = ""
    current_size: int = 0
    budget: int = 0
    hard_limit: int = 0


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    unicode_chars = len(text) - ascii_chars
    return ascii_chars // 4 + unicode_chars // 2 + 1


class ContextPruner:
    """上下文裁减编排器。"""

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        recent_window: int = DEFAULT_RECENT_WINDOW,
        tool_window: int = DEFAULT_TOOL_RESULT_WINDOW,
    ) -> None:
        self.max_chars = max(100, max_chars)
        self.hard_limit = int(self.max_chars * _HARD_LIMIT_RATIO)
        self.recent_window = max(1, recent_window)
        self.tool_window = max(1, tool_window)
        self._compact_count = 0
        self._force_truncate_count = 0

    @property
    def compact_count(self) -> int:
        return self._compact_count

    def evaluate(self, current_size: int) -> PruneAction:
        """评估当前上下文，返回建议动作。"""
        action = PruneAction(
            current_size=current_size,
            budget=self.max_chars,
            hard_limit=self.hard_limit,
        )
        if current_size <= self.max_chars:
            return action
        action.should_prune = True
        action.reason = f"size {current_size} > budget {self.max_chars}"
        if current_size > self.hard_limit:
            action.should_force_truncate = True
        action.should_compact = True
        return action

    # ── 智能压缩 ──────────────────────────────────────────────

    def compact_step(
        self,
        recent: RecentMessages,
        memory: SummaryMemory,
    ) -> bool:
        """执行一步压缩：将最旧消息移出窗口 → 摘要化。

        Returns:
            True 执行了压缩，False 无操作。
        """
        # 检查是否需要压缩
        total = 0
        total += sum(len(str(m.get("content", ""))) for m in recent.messages)
        total += sum(len(str(r.get("output", ""))) for r in recent.tool_results)
        total += memory.estimate_size()

        if total <= self.max_chars:
            return False

        self._compact_count += 1
        keep = min(self.recent_window, recent.message_count)
        remove_count = recent.message_count - keep

        if remove_count <= 0 and not recent.tool_results:
            return False

        # 收集要压缩的旧消息
        old_messages: list[dict[str, Any]] = recent.messages[:remove_count] if remove_count > 0 else []
        old_tools: list[dict[str, Any]] = []

        # 限制工具结果窗口
        if len(recent.tool_results) > self.tool_window:
            old_tools = recent.tool_results[:len(recent.tool_results) - self.tool_window]

        # 压缩到摘要
        memory.compress(old_messages, old_tools)

        # 移除已压缩的消息和工具结果
        if remove_count > 0:
            recent.clear()
            # 重新添加保留的消息
            all_msgs = recent._window.items  # 直接用内部窗口
            # 实际上我们应该保留最近的消息
            recent.messages.extend(all_msgs)

        # 简化：直接操作内部
        try:
            from zmai.context.window import SlidingWindow
            # 重建窗口以保留最近消息
            kept_msgs = recent.messages[-keep:] if keep > 0 else []
            kept_tools = recent.tool_results[-self.tool_window:] if self.tool_window > 0 else []
            recent.clear()
            for msg in kept_msgs:
                recent._window.add(dict(msg))
            recent._tool_results = list(kept_tools)
        except Exception as e:
            logger.warning("Compact 重建失败: %s", e)
            return False

        return True

    # ── 强制截断 ──────────────────────────────────────────────

    def force_truncate(
        self,
        recent: RecentMessages,
        memory: SummaryMemory,
        _size_fn: Callable[[], int] | None = None,
    ) -> None:
        """强制截断上下文到预算内。"""
        self._force_truncate_count += 1
        budget = int(self.hard_limit * 0.85)

        def _size() -> int:
            if _size_fn:
                return _size_fn()
            total = 0
            total += sum(len(str(m.get("content", ""))) for m in recent.messages)
            total += sum(len(str(r.get("output", ""))) for r in recent.tool_results)
            total += memory.estimate_size()
            return total

        # Phase 1: 丢弃最旧工具结果
        while recent.tool_results and _size() > budget:
            recent.pop_oldest_tool_result()

        # Phase 2: 丢弃最旧消息
        while recent.messages and _size() > budget:
            recent.pop_oldest_message()

        # Phase 3: 收缩摘要
        if _size() > budget:
            memory.shrink(500)

        # Phase 4: 收缩失败列表
        while memory.failures and _size() > budget:
            memory._failures.pop(0)

        logger.warning(
            "Force truncate #%d: size=%d, budget=%d",
            self._force_truncate_count, _size(), budget,
        )

    def get_status(self) -> dict[str, Any]:
        return {
            "max_chars": self.max_chars,
            "hard_limit": self.hard_limit,
            "recent_window": self.recent_window,
            "tool_window": self.tool_window,
            "compact_count": self._compact_count,
            "force_truncate_count": self._force_truncate_count,
        }
