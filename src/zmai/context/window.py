"""SlidingWindow — 固定大小的消息滑动窗口。

当窗口满时，最旧的消息自动滑出并触发回调。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("zmai.context.window")

# 滑动回调签名：on_slide(message, index)
SlideCallback = Callable[[dict[str, Any], int], None] | None


class SlidingWindow:
    """固定大小的消息滑动窗口。

    使用方式:
        win = SlidingWindow(size=10)
        win.add({"role": "user", "content": "hello"})  # 自动滑出旧的
        win.add_many([msg1, msg2])
        current = win.items  # 当前窗口内容
    """

    def __init__(
        self,
        size: int = 10,
        on_slide: SlideCallback = None,
    ) -> None:
        self._size = max(1, size)
        self._items: list[dict[str, Any]] = []
        self._on_slide = on_slide
        self._total_added: int = 0

    @property
    def items(self) -> list[dict[str, Any]]:
        """当前窗口中的所有消息。"""
        return list(self._items)

    @property
    def size(self) -> int:
        return self._size

    @size.setter
    def size(self, value: int) -> None:
        """调整窗口大小，自动裁剪超出的消息。"""
        self._size = max(1, value)
        while len(self._items) > self._size:
            old = self._items.pop(0)
            self._on_slide_cb(old, 0)

    @property
    def count(self) -> int:
        return len(self._items)

    @property
    def total_added(self) -> int:
        return self._total_added

    @property
    def is_full(self) -> bool:
        return len(self._items) >= self._size

    def add(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        """添加消息。返回被滑出的消息列表（可能为空）。"""
        slid: list[dict[str, Any]] = []
        self._items.append(item)
        self._total_added += 1
        while len(self._items) > self._size:
            old = self._items.pop(0)
            self._on_slide_cb(old, 0)
            slid.append(old)
        return slid

    def add_many(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量添加消息。返回所有被滑出的消息。"""
        all_slid: list[dict[str, Any]] = []
        for item in items:
            all_slid.extend(self.add(item))
        return all_slid

    def _on_slide_cb(self, item: dict[str, Any], index: int) -> None:
        if self._on_slide:
            try:
                self._on_slide(item, index)
            except Exception as e:
                logger.warning("SlidingWindow 回调失败: %s", e)

    def remove_oldest(self, n: int = 1) -> list[dict[str, Any]]:
        """移除最旧的 N 条消息，返回被移除的消息。"""
        removed: list[dict[str, Any]] = []
        for _ in range(min(n, len(self._items))):
            old = self._items.pop(0)
            removed.append(old)
        return removed

    def clear(self) -> None:
        self._items.clear()
        self._total_added = 0

    def get_status(self) -> dict[str, Any]:
        return {
            "size": self._size,
            "count": self.count,
            "total_added": self._total_added,
            "is_full": self.is_full,
        }


class RecentMessages:
    """活跃消息缓冲区 — 管理最近的消息和工具结果。

    组合了消息窗口 + 工具结果列表，并提供格式化输出。

    使用方式:
        recent = RecentMessages(window_size=6, tool_window=6)
        recent.add_message("user", "hello")
        recent.add_tool_result("read_file", True, "content")
        msgs = recent.get_all_messages()
        results = recent.tool_results
    """

    def __init__(
        self,
        window_size: int = 6,
        tool_window: int = 6,
        on_slide_message: SlideCallback = None,
    ) -> None:
        self._window = SlidingWindow(size=window_size, on_slide=on_slide_message)
        self._tool_window = max(1, tool_window)
        self._tool_results: list[dict[str, Any]] = []

    # ── 消息 ───────────────────────────────────────────────────

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._window.items

    @property
    def message_count(self) -> int:
        return self._window.count

    def add_message(
        self,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """添加消息，返回滑出的消息。"""
        msg: dict[str, Any] = {"role": role, "content": content}
        if metadata:
            msg["metadata"] = metadata
        return self._window.add(msg)

    def add_messages(self, msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._window.add_many(msgs)

    def pop_oldest_message(self) -> dict[str, Any] | None:
        """移除并返回最旧的消息。"""
        items = self._window._items
        if items:
            return items.pop(0)
        return None

    # ── 工具结果 ───────────────────────────────────────────────

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        return list(self._tool_results)

    def add_tool_result(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """添加工具结果，返回被滑出的旧结果。"""
        self._tool_results.append(entry)
        slid: list[dict[str, Any]] = []
        while len(self._tool_results) > self._tool_window:
            slid.append(self._tool_results.pop(0))
        return slid

    def pop_oldest_tool_result(self) -> dict[str, Any] | None:
        """移除并返回最旧的工具结果。"""
        if self._tool_results:
            return self._tool_results.pop(0)
        return None

    # ── 管理 ───────────────────────────────────────────────────

    def clear(self) -> None:
        self._window.clear()
        self._tool_results.clear()

    def get_status(self) -> dict[str, Any]:
        return {
            "messages": self._window.get_status(),
            "tool_results": {
                "window": self._tool_window,
                "count": len(self._tool_results),
            },
        }
