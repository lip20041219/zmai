"""配置管理器 — 多源合并，按优先级覆盖。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from zmai.config.sources import CLISource, ConfigSource, EnvSource, FileSource


class Config:
    """统一配置管理器。

    配置源按优先级从低到高：
    1. Global Config  (~/.zmai/config.json)
    2. Project Config (zmai.json)
    3. Environment    (ZMAI_*)
    4. CLI Arguments  (--key=value)

    使用方式:
        config = Config()
        timeout = config.get("runtime.timeout", 30)
    """

    def __init__(self, sources: list[ConfigSource] | None = None) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

        # sources=None → 使用默认配置源
        # sources=[]  → 明确不加载任何源（禁止用 falsy 判断混淆二者）
        self._sources = sources if sources is not None else [
            FileSource("zmai.json"),
            FileSource(str(Path.home() / ".zmai" / "config.json")),
            EnvSource(),
            CLISource(),
        ]
        self._load()

    def _load(self) -> None:
        merged: dict[str, Any] = {}
        for src in self._sources:
            merged.update(src.load())
        self._data = merged

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def export(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def reload(self) -> None:
        with self._lock:
            self._load()
