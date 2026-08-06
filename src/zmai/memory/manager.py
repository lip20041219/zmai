"""MemoryManager — unified Working and Long-term Memory management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmai.memory.base import MemoryEntry
from zmai.memory.long_term import LongTermMemory
from zmai.memory.working import WorkingMemory


class MemoryManager:
    """Memory 管理器。管理 Agent 的 Working 和 Long-term Memory 配对。"""

    def __init__(
        self,
        working_max_size: int = 1000,
        long_term_root: str | Path | None = None,
    ) -> None:
        self._working: dict[str, WorkingMemory] = {}
        self._long_term: dict[str, LongTermMemory] = {}
        self._working_max_size = working_max_size
        self._long_term_root = Path(long_term_root or Path.home() / ".zmai" / "memory")

    def working(self, agent_id: str) -> WorkingMemory:
        if agent_id not in self._working:
            self._working[agent_id] = WorkingMemory(max_size=self._working_max_size)
        return self._working[agent_id]

    def long_term(self, agent_id: str) -> LongTermMemory:
        if agent_id not in self._long_term:
            self._long_term[agent_id] = LongTermMemory(
                root_dir=self._long_term_root / agent_id,
            )
        return self._long_term[agent_id]

    def persist(self, agent_id: str) -> None:
        """将 Working Memory 同步到 Long-term Memory。"""
        wm = self._working.get(agent_id)
        if not wm:
            return
        lm = self.long_term(agent_id)
        for ns in wm.list_namespaces():
            entries = wm._data.get(ns, {})
            for key, entry in entries.items():
                lm.store(key, entry.value, ns)

    def restore(self, agent_id: str) -> int:
        """从 Long-term Memory 恢复到 Working Memory。

        Returns:
            恢复的条目数。
        """
        lm = self.long_term(agent_id)
        wm = self.working(agent_id)
        count = 0
        for ns in lm.list_namespaces():
            entries = lm.search("", namespace=ns)
            for entry in entries:
                wm.store(entry.key, entry.value, ns)
                count += 1
        return count

    def cleanup(self, agent_id: str) -> None:
        self._working.pop(agent_id, None)
        self._long_term.pop(agent_id, None)

    def exists(self, agent_id: str) -> bool:
        return (self._long_term_root / agent_id).exists()
