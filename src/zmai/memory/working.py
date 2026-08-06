"""Working Memory — pure in-memory storage, auto-destroyed after agent lifecycle."""

from __future__ import annotations

import threading
from typing import Any

from zmai.memory.base import Memory, MemoryEntry, _now


class WorkingMemory(Memory):
    """工作记忆。纯内存 dict 存储，支持 TTL 过期。

    namespace 超出 max_size 时自动淘汰最旧条目（LRU-like）。
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._data: dict[str, dict[str, MemoryEntry]] = {}  # namespace → {key → entry}
        self._lock = threading.Lock()
        self._max_size = max_size

    def store(self, key: str, value: Any, namespace: str = "default") -> None:
        with self._lock:
            if namespace not in self._data:
                self._data[namespace] = {}
            if len(self._data[namespace]) >= self._max_size:
                # LRU: 淘汰 namespace 中最旧的条目
                oldest_key, _ = min(
                    self._data[namespace].items(),
                    key=lambda item: item[1].created_at,
                )
                del self._data[namespace][oldest_key]
            self._data[namespace][key] = MemoryEntry(
                key=key, value=value, namespace=namespace,
            )

    def read(self, key: str, namespace: str = "default") -> Any:
        with self._lock:
            ns = self._data.get(namespace, {})
            entry = ns.get(key)
            if entry is None:
                return None
            if entry.is_expired:
                del ns[key]
                return None
            return entry.value

    def update(self, key: str, value: Any, namespace: str = "default") -> None:
        with self._lock:
            ns = self._data.get(namespace, {})
            if key not in ns:
                raise KeyError(f"key 不存在: {namespace}.{key}")
            ns[key].value = value
            ns[key].updated_at = _now()

    def delete(self, key: str, namespace: str = "default") -> None:
        with self._lock:
            ns = self._data.get(namespace, {})
            ns.pop(key, None)

    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]:
        with self._lock:
            ns = self._data.get(namespace, {})
            return [e for k, e in ns.items() if query.lower() in k.lower()]

    def clear(self, namespace: str = "default") -> None:
        with self._lock:
            self._data.pop(namespace, None)

    def list_namespaces(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())
