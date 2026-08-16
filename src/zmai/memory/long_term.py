"""Long-term Memory — 文件持久化，JSON Lines 格式，按 namespace 分文件。

写入策略: Append-only（O(1)），读取时后覆盖前（最后写入的 key 生效）。
删除策略: 写入墓碑标记 `{"__tombstone__": key}`。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from zmai.memory.base import Memory, MemoryEntry, _now

_TOMBSTONE = "__tombstone__"


class LongTermMemory(Memory):
    """长期记忆。JSONL 文件持久化，append-only 写入。"""

    def __init__(self, root_dir: str | Path, max_file_size: int = 10 * 1024 * 1024) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_file_size = max_file_size
        self._cache: dict[str, dict[str, MemoryEntry]] = {}
        self._lock = threading.Lock()

    def _file_path(self, namespace: str) -> Path:
        safe = namespace.replace("/", "_").replace("\\", "_")
        return self._root / f"{safe}.jsonl"

    def _load_namespace(self, namespace: str) -> dict[str, MemoryEntry]:
        """从 JSONL 文件加载 namespace，后写入的 key 覆盖先写入的。"""
        path = self._file_path(namespace)
        if not path.exists():
            return {}
        entries: dict[str, MemoryEntry] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if _TOMBSTONE in d:
                        entries.pop(d[_TOMBSTONE], None)
                        continue
                    e = MemoryEntry.from_dict(d)
                    if not e.is_expired:
                        entries[e.key] = e  # 后写入覆盖前写入
                except (json.JSONDecodeError, KeyError):
                    continue
        except OSError:
            pass
        return entries

    def _append_entry(self, namespace: str, entry: MemoryEntry) -> None:
        """O(1) append 一行到 JSONL 文件。"""
        path = self._file_path(namespace)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def _append_tombstone(self, namespace: str, key: str) -> None:
        """写入墓碑标记行。"""
        path = self._file_path(namespace)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({_TOMBSTONE: key}) + "\n")

    def store(self, key: str, value: Any, namespace: str = "default") -> None:
        with self._lock:
            if namespace not in self._cache:
                self._cache[namespace] = self._load_namespace(namespace)
            entry = MemoryEntry(
                key=key, value=value, namespace=namespace,
            )
            self._cache[namespace][key] = entry
            self._append_entry(namespace, entry)  # O(1) append

    def read(self, key: str, namespace: str = "default") -> Any:
        with self._lock:
            if namespace not in self._cache:
                self._cache[namespace] = self._load_namespace(namespace)
            entry = self._cache[namespace].get(key)
            if entry is None or entry.is_expired:
                return None
            return entry.value

    def update(self, key: str, value: Any, namespace: str = "default") -> None:
        with self._lock:
            if namespace not in self._cache:
                self._cache[namespace] = self._load_namespace(namespace)
            if key not in self._cache[namespace]:
                raise KeyError(f"key 不存在: {namespace}.{key}")
            self._cache[namespace][key].value = value
            self._cache[namespace][key].updated_at = _now()
            # Append 新值行，读取时后覆盖前
            self._append_entry(namespace, self._cache[namespace][key])

    def delete(self, key: str, namespace: str = "default") -> None:
        with self._lock:
            if namespace not in self._cache:
                self._cache[namespace] = self._load_namespace(namespace)
            self._cache[namespace].pop(key, None)
            self._append_tombstone(namespace, key)

    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]:
        with self._lock:
            if namespace not in self._cache:
                self._cache[namespace] = self._load_namespace(namespace)
            return [e for k, e in self._cache[namespace].items()
                    if query.lower() in k.lower() or query.lower() in str(e.value).lower()]

    def clear(self, namespace: str = "default") -> None:
        with self._lock:
            self._cache.pop(namespace, None)
            path = self._file_path(namespace)
            if path.exists():
                path.unlink()

    def list_namespaces(self) -> list[str]:
        return [p.stem for p in self._root.glob("*.jsonl")]
