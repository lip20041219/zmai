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


def _message_size(message: dict[str, Any]) -> int:
    return sum(len(str(message.get(key, ""))) for key in
               ("content", "reasoning_content", "tool_calls"))


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
        #
        # 条数上限只作兜底 —— **真正的约束是 max_chars 预算**（compact/_ensure_budget）。
        # 旧实现把条数上限与 compact() 的 keep 都取 recent_window：窗口在预算几乎还
        # 没用上时就把消息滑走，于是 compact() 里的 old_messages 恒为空（keep ==
        # message_count），压缩永不生效，模型可见历史被钉死在 6 条 = 3 轮问答。
        # 实测默认配置下第 N 步读到的正文在第 N+3 步就消失，32000 的预算只用到 4284
        # （13%）；真实 129 步轨迹里 58 次工具调用有 35 次是 read_file，同一文件被
        # 重复读 10 次 —— 上下文留不住，只能反复重新获取。
        #
        # 上限取 max_chars：单条消息至少 1 字符，预算内不可能容纳比它更多的消息，
        # 所以这个上限永远不会先于预算成为约束，也就不会再把预算架空。
        self._recent_win = RecentMessages(
            window_size=max(self.recent_window, self.max_chars),
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
        self._pending_tool_ids: set[str] = set()
        self._deferred_user_messages: list[tuple[str, dict[str, Any] | None]] = []

    # ═══════════════════════════════════════════════════════════════
    # 估算
    # ═══════════════════════════════════════════════════════════════

    def estimate_size(self) -> int:
        """模型实际可见上下文的字符数。

        直接量 ``_render_messages()`` —— 与 ``get_context()`` 同一段渲染代码，
        两者天然同口径。**不**计入 ``_recent_win.tool_results``：那份列表只服务于
        压缩记账与 agent 侧完成度判据（agent.py 用 ``cm._tool_results`` 找"修改之后
        仍失败"的证据），``get_context()`` 从不交付它，而同一份文本已经作为 user
        消息进过窗口。把它算进来就是重复计数 —— 实测微预算场景下占掉 60% 的预算
        （1007 字符里 600 是模型看不到的），预算被架空，压缩永远压不回 max_chars。
        """
        return sum(_message_size(m) for m in self._render_messages())

    def estimate_tokens(self) -> int:
        text = "\n".join(str(m.get(key, "")) for m in self._render_messages()
                         for key in ("content", "reasoning_content", "tool_calls"))
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
        if role == "user" and self._pending_tool_ids:
            self._deferred_user_messages.append((content, metadata))
            return
        slid = self._recent_win.add_message(role, content, metadata)
        if slid:
            self._memory.compress(slid, [])
        self._ensure_budget()

    def add_api_message(self, message: dict[str, Any]) -> None:
        """Keep provider assistant/tool protocol fields in the recent window."""
        if message.get("role") == "assistant" and message.get("tool_calls"):
            self._pending_tool_ids = {call["id"] for call in message["tool_calls"]}
        slid = self._recent_win.add_messages([message])
        if slid:
            self._memory.compress(slid, [])
        # Prune only after the complete assistant/tool exchange is present.
        if not self._pending_tool_ids:
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
        tool_call_id: str | None = None,
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
                使 ``read_state()`` 能按 key 判断该结果在可见窗口中的交付状态。
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
        # P1-2: 本次交付是否被截断 —— read_state() 靠它区分"内容已完整给到模型"
        # 与"只给了一部分"。判据用"截断后的文本是否与原文逐字相同"，两条截断路径
        # （_truncate / _truncate_head_tail）在未超预算时都原样返回。
        _was_truncated = truncated != (output or "")
        entry = {
            **(meta or {}),
            "name": name,
            "success": success,
            "output": truncated,
            "duration_ms": duration_ms,
            "truncated": _was_truncated,
        }
        if error:
            entry["error"] = error
        self._recent_win.add_tool_result(entry)

        if not success:
            brief = (error or output or "")[:100]
            self._memory.track_failure(f"{name}: {brief}")

        status = "OK" if success else "FAIL"
        detail = output or error or ""
        if tool_call_id:
            slid = self._recent_win.add_messages([{
                "role": "tool", "tool_call_id": tool_call_id, "content": _cut(detail),
                "metadata": {**(meta or {}), "tool": name,
                             "truncated": _was_truncated}}])
        else:
            result_msg = f"[工具 {name} 结果]\n{status}: {_cut(detail)}"
            slid = self._recent_win.add_message(
                "user", result_msg,
                metadata={**(meta or {}), "tool": name, "truncated": _was_truncated})
        if slid:
            self._memory.compress(slid, [])
        if tool_call_id and tool_call_id in self._pending_tool_ids:
            self._pending_tool_ids.remove(tool_call_id)
            if not self._pending_tool_ids:
                deferred = self._deferred_user_messages
                self._deferred_user_messages = []
                for content, metadata in deferred:
                    self.add_message("user", content, metadata)
        if not self._pending_tool_ids:
            self._ensure_budget()

    def read_state(self, read_key: Any) -> str:
        """该 key 对应的结果在**模型可见活跃窗口**中的交付状态（P1-1 / P1-2）。

        只查最近消息窗口 —— 那才是模型实际读到的那份内容；不查 ``_memory`` 里的
        历史摘要，摘要不是原文，模型无法据它拿到源码。

        返回 ``"complete"`` / ``"truncated"`` / ``"absent"``。``"truncated"`` 是
        P1-2：注入时按 tool_truncate / evidence 预算截断过的结果，模型手上只是
        一部分 —— 只有"仍在窗口"这一条不足以宣告"你已经有这份内容"。实测真实
        轨迹里 39 次 ReadCache 命中中有 18 次的上一次交付是被截断的，模型因此被
        告知复用一份不完整的正文。

        找不到一律返回 ``"absent"``（fail-closed）：判"不可见"的代价只是多注入
        一次正文，判"可见"的代价是模型被告知复用一份它已经看不到的内容 —— 后者
        正是 P1-1。
        """
        if not read_key:
            return "absent"
        for msg in self._recent_win.messages:
            _meta = msg.get("metadata") or {}
            if _meta.get("read_key") == read_key:
                return "truncated" if _meta.get("truncated") else "complete"
        return "absent"

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
        if not self._pending_tool_ids:
            self._ensure_budget()
        return self._render_messages()

    def _render_messages(self) -> list[dict[str, Any]]:
        """构建真实交付的消息序列（**不做**预算检查）。

        ``get_context()`` 与 ``estimate_size()`` 共用这一段 —— 之前的估算口径与交付
        口径是两套独立算式：估算漏掉了这里渲染出来的 ``[历史摘要]`` 头、Unresolved /
        Modified files / Test results / Failures 四段与各段标题，实测默认配置下
        少算 1294/12451 ≈ 10%。于是"estimate_size <= max_chars"并不能保证模型收到
        的 payload 也在预算内，压缩的取舍判据也跟着偏小。
        """
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
            keep = self._retain_count()
            messages = self._recent_win.messages
            start = len(messages) - keep if keep > 0 else len(messages)
            # A kept suffix must not begin with a tool result whose assistant
            # call was compressed away.
            while start < len(messages) and messages[start].get("role") == "tool":
                start += 1
            old_messages = messages[:start]
            new_messages = messages[start:]

            keep_tools = min(self.tool_result_window, len(self._recent_win.tool_results))
            old_tools = self._recent_win.tool_results[:-keep_tools] if keep_tools > 0 else []
            new_tools = self._recent_win.tool_results[-keep_tools:] if keep_tools > 0 else []

            # 没有可下沉的旧消息就**不要**声称压缩过：旧实现无论是否压到东西都
            # 递增计数并返回 True，于是一个必然无效的 compact 会被反复调用，
            # 每次都把摘要重新 compress 一遍、把窗口 clear 后重建。
            if not old_messages and not old_tools:
                return False

            self._compact_count += 1
            self._memory.compress(old_messages, old_tools)

            self._recent_win.clear()
            for msg in new_messages:
                self._recent_win.add_messages([msg])
            for t in new_tools:
                self._recent_win.add_tool_result(t)

            logger.info(
                "Compact done: size=%d, kept=%d, summary_len=%d",
                self.estimate_size(), len(new_messages), self._memory.estimate_size(),
            )
            return True
        except Exception as e:
            logger.error("Compact 失败: %s", e)
            return False

    def _retain_count(self) -> int:
        """压缩后保留的最近消息条数。

        下限 ``recent_window``；上限由 ``max_chars`` 决定 —— 从最新往回累计，
        装得下就留。

        旧实现固定只留 ``recent_window`` 条：预算一被顶到，窗口立刻从几十条塌到 6 条，
        **刚读到、还没用上**的证据一起被压成无信息的计数摘要。实测默认配置
        （task 5466 字符、每步 1 assistant + 1 条 500 字符工具结果、第 5 步读一份
        4000 字符证据）：第 33 步首次压缩后窗口只剩 6 条，第 5 步的证据消失，
        摘要只剩 ``Previous 61 conversation rounds completed``（41 字符，零信息）。
        改成按预算保留后，同一 45 步轨迹保留约 80 条（预算允许的全部），
        交付大小仍 <= max_chars。

        注意这不等于"证据永不丢失"：预算就是预算，更早的内容最终仍会离开窗口，
        只是推迟到真正装不下的时候，而不是固定 3 轮问答之后。
        """
        msgs = self._recent_win.messages
        # 非窗口部分（摘要消息 + Working Memory 消息）由 estimate_size 减出，
        # 与交付口径同源（见 _render_messages）。
        window_chars = sum(_message_size(m) for m in msgs)
        budget = max(0, self.max_chars - (self.estimate_size() - window_chars))
        keep, used = 0, 0
        for msg in reversed(msgs):
            n = _message_size(msg)
            if keep >= self.recent_window and used + n > budget:
                break
            used += n
            keep += 1
        return min(keep, len(msgs))

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

        # tool_results 不计入 estimate_size（模型看不到，见 estimate_size），
        # 丢弃它换不来任何预算 —— 这里只丢真正占预算的消息。
        #
        # 已知边界（未修，见 temp/state.json "not_fixed"）：单条消息自身就超过
        # force_truncate 预算时，这个循环会把窗口清空，模型收到空上下文。默认
        # 配置下不可达（所有注入内容受 test_evidence_chars=4000 / tool_truncate=500
        # 约束，而默认 ft 预算 40800）；只有人为把 context.max_chars 调到几百时
        # 才触发，而"截断必须压进硬上限"是既有测试刻意断言的不变量
        # （test_ensure_budget_after_add_*），不为一个没人使用的配置路径推翻它。
        while self._recent_win.messages and _size() > budget:
            oldest = self._recent_win.pop_oldest_message()
            if oldest and oldest.get("role") == "assistant" and oldest.get("tool_calls"):
                while (self._recent_win.messages
                       and self._recent_win.messages[0].get("role") == "tool"):
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
        self._pending_tool_ids.clear()
        self._deferred_user_messages.clear()

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
