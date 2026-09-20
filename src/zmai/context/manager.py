"""ContextManager — 统一的上下文管理器（Facade）。

组合 SlidingWindow + RecentMessages + SummaryMemory + ContextPruner，
对外暴露与原始 ContextManager 完全相同的 API，保证 Agent 无需修改调用方式。

使用方式（不变）:
    cm = ContextManager(config={"max_chars": 32000})
    cm.set_task("修复登录 Bug")
    cm.add_message("assistant", "我来分析代码...")
    cm.add_tool_result("read_file", True, "file content")
    messages = cm.get_context()
"""

from __future__ import annotations

import logging
from typing import Any

from zmai.context.memory import SummaryMemory, _truncate, _truncate_head_tail
from zmai.context.pruner import (
    DEFAULT_MAX_CHARS,
    DEFAULT_RECENT_WINDOW,
    DEFAULT_TOOL_RESULT_WINDOW,
    ContextPruner,
    _estimate_tokens,
)
from zmai.context.window import RecentMessages

logger = logging.getLogger("zmai.context.manager")


class ContextManager:
    """上下文管理器 — 管理 Agent 执行上下文。

    内部架构:
      _recent_win (RecentMessages)  ← 最近 N 轮消息
      _memory (SummaryMemory)        ← 压缩后的历史摘要
      _pruner (ContextPruner)        ← token 预算裁减决策引擎
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}

        self.max_chars: int = int(cfg.get("context.max_chars", DEFAULT_MAX_CHARS))
        self.recent_window: int = int(cfg.get("context.recent_window", DEFAULT_RECENT_WINDOW))
        self.tool_result_window: int = int(cfg.get("context.tool_result_window", DEFAULT_TOOL_RESULT_WINDOW))  # noqa: E501
        self.tool_truncate: int = int(cfg.get("context.tool_truncate", 500))
        # 测试失败证据预算（仅对显式声明为证据的 tool result 生效，见 add_tool_result）
        self.test_evidence_chars: int = int(cfg.get("context.test_evidence_chars", 4000))

        # 组件（使用 _recent_win 而非 _recent 避免与兼容属性冲突）
        self._recent_win = RecentMessages(
            window_size=self.recent_window,
            tool_window=self.tool_result_window,
        )
        self._memory = SummaryMemory(max_chars=self.max_chars)
        self._pruner = ContextPruner(
            max_chars=self.max_chars,
            recent_window=self.recent_window,
            tool_window=self.tool_result_window,
        )

        # 状态
        self._task: str = ""
        self._plan: str = ""
        self._working_memory: list[str] = []
        self._has_task: bool = False
        self._compact_count: int = 0

    # ═══════════════════════════════════════════════════════════════
    # 估算
    # ═══════════════════════════════════════════════════════════════

    def estimate_size(self) -> int:
        total = 0
        total += len(self._task or "")
        total += len(self._plan or "")
        total += sum(len(m) for m in self._working_memory)
        total += sum(len(str(m.get("content", ""))) for m in self._recent_win.messages)
        total += sum(len(str(r.get("output", ""))) for r in self._recent_win.tool_results)
        total += self._memory.estimate_size()
        return total

    def estimate_tokens(self) -> int:
        text = ""
        text += self._task or ""
        text += self._plan or ""
        text += "\n".join(self._working_memory)
        text += "\n".join(str(m.get("content", "")) for m in self._recent_win.messages)
        text += "\n".join(str(r.get("output", "")) for r in self._recent_win.tool_results)
        text += self._memory.get_combined_summary()
        return _estimate_tokens(text)

    def should_compact(self) -> bool:
        return self.estimate_size() > self.max_chars

    # ═══════════════════════════════════════════════════════════════
    # 数据输入
    # ═══════════════════════════════════════════════════════════════

    def set_task(self, task: str) -> None:
        self._task = task
        if not self._has_task:
            self._has_task = True
            if not self._recent_win.messages:
                self._recent_win.add_message("user", task)

    def set_plan(self, plan_summary: str) -> None:
        self._plan = plan_summary

    def set_working_memory(self, items: list[str]) -> None:
        self._working_memory = items

    def add_message(self, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        slid = self._recent_win.add_message(role, content, metadata)
        if slid:
            self._memory.compress(slid, [])
        self._ensure_budget()

    def add_tool_result(
        self,
        name: str,
        success: bool,
        output: str,
        error: str | None = None,
        duration_ms: int = 0,
        truncate: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """记录工具结果并注入一条 user 消息。

        Args:
            truncate: 本次调用的截断预算（字符）。
                None（默认）→ ``self.tool_truncate``，**头部截断**，普通工具
                    （read_file / grep / git / 普通 shell）行为不变。
                给定值 → **证据保留截断**：头部保留结构化摘要（如 ``[test summary]``），
                    尾部保留失败详情（traceback / 根因），中间省略。供测试失败证据
                    使用，避免模型只看到 pytest 的 session/collection 头。
            meta: 随结果一起携带的归属信息（可选，默认 None，行为与改造前一致）。
                同时写入 tool-result entry 与**注入模型的那条消息**的 metadata，
                使 ``is_read_visible()`` 能按 key 判断该结果是否仍在可见窗口。
                结构性字段（name/success/output/duration_ms/tool）优先，meta 不覆盖它们。
        """
        if truncate is None:
            def _cut(text: str) -> str:
                return _truncate(text, self.tool_truncate)
        else:
            budget = max(0, int(truncate))

            def _cut(text: str) -> str:
                return _truncate_head_tail(text, budget)

        truncated = _cut(output or "")
        entry = {
            **(meta or {}),
            "name": name,
            "success": success,
            "output": truncated,
            "duration_ms": duration_ms,
        }
        if error:
            entry["error"] = error
        self._recent_win.add_tool_result(entry)

        if not success:
            brief = (error or output or "")[:100]
            self._memory.track_failure(f"{name}: {brief}")

        status = "OK" if success else "FAIL"
        detail = output or error or ""
        result_msg = f"[工具 {name} 结果]\n{status}: {_cut(detail)}"
        slid = self._recent_win.add_message(
            "user", result_msg, metadata={**(meta or {}), "tool": name})
        if slid:
            self._memory.compress(slid, [])
        self._ensure_budget()

    def is_read_visible(self, read_key: Any) -> bool:
        """该 key 对应的结果是否仍逐字位于**模型可见的活跃窗口**（P1-1）。

        只查最近消息窗口 —— 那才是模型实际读到的那份内容；不查 ``_memory`` 里的
        历史摘要，摘要不是原文，模型无法据它拿到源码。

        找不到一律返回 False（fail-closed）：判"不可见"的代价只是多注入一次正文，
        判"可见"的代价是模型被告知复用一份它已经看不到的内容 —— 后者正是 P1-1。
        """
        if not read_key:
            return False
        for msg in self._recent_win.messages:
            if (msg.get("metadata") or {}).get("read_key") == read_key:
                return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # 追踪
    # ═══════════════════════════════════════════════════════════════

    def track_file_change(self, filepath: str) -> None:
        self._memory.track_file(filepath)

    def track_test_result(self, result: str) -> None:
        self._memory.track_test_result(result)

    def track_unresolved(self, issue: str) -> None:
        self._memory.track_unresolved(issue)

    # ═══════════════════════════════════════════════════════════════
    # 上下文输出
    # ═══════════════════════════════════════════════════════════════

    def get_context(self) -> list[dict[str, Any]]:
        self._ensure_budget()
        messages: list[dict[str, Any]] = []

        combined = self._memory.get_combined_summary()
        has_tracked = any([
            self._memory.pending_unresolved,
            self._memory.modified_files,
            self._memory.test_results,
            self._memory.failures,
        ])
        if combined or has_tracked:
            base = self._build_compact_summary()
            content = f"[历史摘要]\n{base}"
            if combined:
                content += f"\n{combined}"
            parts = [content]
            if self._memory.pending_unresolved:
                parts.append(f"Unresolved: {'; '.join(self._memory.pending_unresolved)}")
            if self._memory.modified_files:
                parts.append(f"Modified files: {', '.join(self._memory.modified_files)}")
            if self._memory.test_results:
                parts.append("Test results:\n" + "\n".join(self._memory.test_results[:3]))
            if self._memory.failures:
                parts.append("Failures:\n" + "\n".join(self._memory.failures[:3]))
            messages.append({"role": "user", "content": "\n\n".join(parts)})

        if self._working_memory:
            wm_text = "## Working Memory\n" + "\n".join(f"- {m}" for m in self._working_memory)
            messages.append({"role": "user", "content": wm_text})

        messages.extend(self._recent_win.messages)
        return messages

    def get_context_with_system(self, system_prompt: str) -> list[dict[str, Any]]:
        return self.get_context()

    def _build_compact_summary(self) -> str:
        parts = []
        if self._task:
            parts.append(f"Original task: {self._task}")
        if self._plan:
            parts.append(f"Execution plan: {self._plan[:200]}")
        if self._memory.modified_files:
            parts.append(f"Modified files: {', '.join(self._memory.modified_files)}")
        if self._memory.pending_unresolved:
            parts.append(f"Unresolved: {'; '.join(self._memory.pending_unresolved)}")
        return "\n".join(parts)

    # ═══════════════════════════════════════════════════════════════
    # 压缩
    # ═══════════════════════════════════════════════════════════════

    def compact(self) -> bool:
        if not self.should_compact():
            return False

        logger.info(
            "Compact: size=%d, recent=%d, tools=%d",
            self.estimate_size(), self._recent_win.message_count, len(self._recent_win.tool_results),  # noqa: E501
        )

        try:
            keep = min(self.recent_window, self._recent_win.message_count)
            old_messages = self._recent_win.messages[:-keep] if keep > 0 else []
            new_messages = self._recent_win.messages[-keep:] if keep > 0 else []

            keep_tools = min(self.tool_result_window, len(self._recent_win.tool_results))
            old_tools = self._recent_win.tool_results[:-keep_tools] if keep_tools > 0 else []
            new_tools = self._recent_win.tool_results[-keep_tools:] if keep_tools > 0 else []

            self._compact_count += 1
            if old_messages or old_tools:
                self._memory.compress(old_messages, old_tools)

            self._recent_win.clear()
            for msg in new_messages:
                self._recent_win.add_message(
                    msg.get("role", "user"),
                    msg.get("content", ""),
                    msg.get("metadata"),
                )
            for t in new_tools:
                self._recent_win.add_tool_result(t)

            logger.info(
                "Compact done: size=%d, summary_len=%d",
                self.estimate_size(), self._memory.estimate_size(),
            )
            return True
        except Exception as e:
            logger.error("Compact 失败: %s", e)
            return False

    # ═══════════════════════════════════════════════════════════════
    # 预算保证
    # ═══════════════════════════════════════════════════════════════

    def _ensure_budget(self) -> None:
        size = self.estimate_size()
        action = self._pruner.evaluate(size)
        if not action.should_prune:
            return

        if action.should_compact:
            self.compact()

        if action.should_force_truncate or self.estimate_size() > self._pruner.hard_limit:
            self._force_truncate()

    def _force_truncate(self) -> None:
        budget = int(self._pruner.hard_limit * 0.85)

        def _size() -> int:
            return self.estimate_size()

        while self._recent_win.tool_results and _size() > budget:
            self._recent_win.pop_oldest_tool_result()

        while self._recent_win.messages and _size() > budget:
            self._recent_win.pop_oldest_message()

        if _size() > budget:
            self._memory.shrink(500)

        while self._memory.failures and _size() > budget:
            self._memory._failures.pop(0)

        logger.warning("Force truncate: size=%d, budget=%d", _size(), budget)

    # ═══════════════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════════════

    @property
    def recent_message_count(self) -> int:
        return self._recent_win.message_count

    @property
    def compact_count(self) -> int:
        return self._compact_count

    def get_status(self) -> dict[str, Any]:
        return {
            "total_chars": self.estimate_size(),
            "estimated_tokens": self.estimate_tokens(),
            "max_chars": self.max_chars,
            "recent_messages": self._recent_win.message_count,
            "tool_results": len(self._recent_win.tool_results),
            "summary_len": self._memory.estimate_size(),
            "compact_count": self._compact_count,
            "has_task": self._has_task,
            "has_plan": bool(self._plan),
            "modified_files": len(self._memory.modified_files),
            "pending_unresolved": len(self._memory.pending_unresolved),
            "failures": len(self._memory.failures),
        }

    def clear(self) -> None:
        self._task = ""
        self._plan = ""
        self._working_memory = []
        self._recent_win.clear()
        self._memory.clear()
        self._has_task = False
        self._compact_count = 0

    # ═══════════════════════════════════════════════════════════════
    # 向后兼容属性（供测试访问内部状态）
    # ═══════════════════════════════════════════════════════════════

    # _recent 属性：返回消息列表（兼容 cm._recent[0], len(cm._recent)）
    @property
    def _recent(self) -> list[dict[str, Any]]:
        return self._recent_win.messages

    @_recent.setter
    def _recent(self, value: list[dict[str, Any]]) -> None:
        self._recent_win.clear()
        for m in value:
            self._recent_win.add_message(
                m.get("role", "user"), m.get("content", ""), m.get("metadata"),
            )

    @property
    def _tool_results(self) -> list[dict[str, Any]]:
        return self._recent_win.tool_results

    @_tool_results.setter
    def _tool_results(self, value: list[dict[str, Any]]) -> None:
        self._recent_win._tool_results = list(value)

    @property
    def _summary(self) -> str:
        return self._memory.get_combined_summary()

    @_summary.setter
    def _summary(self, value: str) -> None:
        self._memory.clear_summaries()
        if value:
            self._memory.add_summary(value)

    @property
    def _modified_files(self) -> list[str]:
        return self._memory.modified_files

    @property
    def _pending_unresolved(self) -> list[str]:
        return self._memory.pending_unresolved

    @property
    def _test_results(self) -> list[str]:
        return self._memory.test_results

    @property
    def _failures(self) -> list[str]:
        return self._memory.failures

    @property
    def _hard_max_chars(self) -> int:
        return self._pruner.hard_limit

    def _do_compact(self) -> None:
        self.compact()
