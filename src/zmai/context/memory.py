"""SummaryMemory — 自动摘要和历史压缩。

将旧消息压缩为摘要文本，支持多轮摘要合并与恢复。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("zmai.context.memory")


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算。中文 ~1.5 字符/token，英文 ~4 字符/token。"""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    unicode_chars = len(text) - ascii_chars
    return ascii_chars // 4 + unicode_chars // 2 + 1


def _truncate(text: str, max_chars: int) -> str:
    """截断文本，添加截断标记。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(截断)"


class SummaryMemory:
    """摘要记忆 — 将旧消息压缩为摘要并管理。

    支持:
      - 自动摘要生成: 从旧消息中提取关键信息
      - 多轮合并: 新摘要与旧摘要合并
      - 状态追踪: 记录修改文件、未解决问题、失败信息
      - 预算控制: 摘要超过大小时自动截断

    使用方式:
        mem = SummaryMemory()
        mem.add_summary("已完成步骤 1-3")
        mem.track_file("src/main.py")
        mem.track_unresolved("需要修复 token 过期")
        summary = mem.get_combined_summary()
    """

    def __init__(self, max_chars: int = 2000) -> None:
        self._max_chars = max(100, max_chars)
        self._summaries: list[str] = []
        self._compact_count: int = 0

        # 追踪状态（压缩时保留）
        self._modified_files: list[str] = []
        self._pending_unresolved: list[str] = []
        self._test_results: list[str] = []
        self._failures: list[str] = []

    # ── 摘要管理 ──────────────────────────────────────────────

    @property
    def summaries(self) -> list[str]:
        return list(self._summaries)

    @property
    def summary_count(self) -> int:
        return len(self._summaries)

    @property
    def compact_count(self) -> int:
        return self._compact_count

    def add_summary(self, text: str) -> None:
        """添加摘要。超过最大字符数时自动截断。"""
        truncated = _truncate(text, self._max_chars)
        if truncated and truncated not in self._summaries:
            self._summaries.append(truncated)

    def add_summaries(self, texts: list[str]) -> None:
        for t in texts:
            self.add_summary(t)

    def get_combined_summary(self) -> str:
        """返回合并后的所有摘要文本。"""
        return "\n".join(self._summaries)

    def clear_summaries(self) -> None:
        self._summaries.clear()

    # ── 状态追踪 ──────────────────────────────────────────────

    @property
    def modified_files(self) -> list[str]:
        return list(self._modified_files)

    @property
    def pending_unresolved(self) -> list[str]:
        return list(self._pending_unresolved)

    @property
    def test_results(self) -> list[str]:
        return list(self._test_results)

    @property
    def failures(self) -> list[str]:
        return list(self._failures)

    def track_file(self, filepath: str) -> None:
        if filepath and filepath not in self._modified_files:
            self._modified_files.append(filepath)

    def track_files(self, files: list[str]) -> None:
        for f in files:
            self.track_file(f)

    def track_unresolved(self, issue: str) -> None:
        if issue and issue not in self._pending_unresolved:
            self._pending_unresolved.append(issue)

    def track_test_result(self, result: str) -> None:
        if result:
            self._test_results.append(_truncate(result, 200))

    def track_failure(self, failure: str) -> None:
        if failure and failure not in self._failures:
            self._failures.append(failure)

    # ── 压缩（从旧消息生成摘要） ─────────────────────────────

    def compress(
        self,
        old_messages: list[dict[str, Any]],
        old_tool_results: list[dict[str, Any]],
    ) -> str:
        """从旧消息和工具结果生成压缩摘要。

        Args:
            old_messages: 被滑出的旧消息。
            old_tool_results: 被滑出的旧工具结果。

        Returns:
            生成的摘要文本。
        """
        self._compact_count += 1
        summary = self._extract_summary(old_messages, old_tool_results)
        if summary:
            self.add_summary(summary)
        return summary

    def _extract_summary(
        self,
        old_messages: list[dict[str, Any]],
        old_tools: list[dict[str, Any]],
    ) -> str:
        """从旧消息中提取关键信息。"""
        summary_lines: list[str] = []
        files: list[str] = []
        unresolved: list[str] = []
        failures: list[str] = []
        tests: list[str] = []

        for msg in old_messages:
            content = msg.get("content", "")
            role = msg.get("role", "")
            meta = msg.get("metadata", {})

            # 工具结果中提取文件名
            if meta and meta.get("tool"):
                tool_name = meta["tool"]
                if "write_file" in tool_name or "edit" in tool_name:
                    for find_kw in ("written ", "replaced ", "appended "):
                        if find_kw in content:
                            for line in content.split("\n"):
                                if find_kw in line:
                                    parts = line.split(find_kw, 1)
                                    if len(parts) > 1:
                                        fname = parts[1].split()[0] if parts[1].split() else ""
                                        if fname and fname not in files:
                                            files.append(fname)

            # 失败信息
            if role == "user" and "FAIL:" in content:
                for line in content.split("\n"):
                    if line.startswith("FAIL:"):
                        fi = line[5:].strip()[:100]
                        if fi and fi not in failures:
                            failures.append(fi)

            # 测试结果
            if "test" in content.lower() or "pytest" in content.lower():
                short = _truncate(content, 150)
                if short not in tests:
                    tests.append(short)

            # 未解决
            if "unresolved" in content.lower():
                short = _truncate(content, 100)
                if short not in unresolved:
                    unresolved.append(short)

        # 构建摘要文本
        if old_messages:
            summary_lines.append(
                f"Previous {len(old_messages)} conversation rounds completed"
            )
            if failures:
                summary_lines.append(f"Encountered {len(failures)} issues")
            if files:
                summary_lines.append(f"Files involved: {', '.join(files)}")
            if tests:
                summary_lines.append(f"Test results: {len(tests)} entries")

        # 合并到追踪状态
        self.track_files(files)
        for u in unresolved:
            self.track_unresolved(u)
        for f in failures:
            self.track_failure(f)
        for t in tests:
            self.track_test_result(t)

        return "\n".join(summary_lines) if summary_lines else ""

    # ── 预算控制 ──────────────────────────────────────────────

    def shrink(self, max_chars: int) -> None:
        """收缩摘要到指定大小。"""
        combined = self.get_combined_summary()
        if len(combined) <= max_chars:
            return
        # 从最旧的摘要开始丢弃
        while self._summaries and len(self.get_combined_summary()) > max_chars:
            self._summaries.pop(0)
        # 如果还是超，截断最后一个
        if self._summaries and len(self.get_combined_summary()) > max_chars:
            self._summaries[-1] = _truncate(self._summaries[-1], max_chars // 2)

    def estimate_size(self) -> int:
        return len(self.get_combined_summary())

    def estimate_tokens(self) -> int:
        return _estimate_tokens(self.get_combined_summary())

    # ── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_count": self.summary_count,
            "combined_size": self.estimate_size(),
            "compact_count": self._compact_count,
            "modified_files": self._modified_files,
            "pending_unresolved": self._pending_unresolved,
            "test_results": self._test_results,
            "failures": self._failures,
        }

    def clear(self) -> None:
        self._summaries.clear()
        self._compact_count = 0
        self._modified_files.clear()
        self._pending_unresolved.clear()
        self._test_results.clear()
        self._failures.clear()
