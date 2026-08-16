"""Memory abstract base class and data types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MemoryEntry:
    key: str
    value: Any
    namespace: str = "default"
    created_at: str = ""
    updated_at: str = ""
    ttl: int | None = None  # seconds, None = 永不过期

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = _now()

    @property
    def is_expired(self) -> bool:
        if self.ttl is None:
            return False
        from datetime import datetime, timezone
        created = datetime.fromisoformat(self.created_at)
        delta = (datetime.now(timezone.utc) - created).total_seconds()
        return delta > self.ttl

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryEntry:
        return cls(**d)


class Memory(ABC):
    """记忆抽象基类。"""

    @abstractmethod
    def store(self, key: str, value: Any, namespace: str = "default") -> None: ...
    @abstractmethod
    def read(self, key: str, namespace: str = "default") -> Any: ...
    @abstractmethod
    def update(self, key: str, value: Any, namespace: str = "default") -> None: ...
    @abstractmethod
    def delete(self, key: str, namespace: str = "default") -> None: ...
    @abstractmethod
    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]: ...
    @abstractmethod
    def clear(self, namespace: str = "default") -> None: ...
    @abstractmethod
    def list_namespaces(self) -> list[str]: ...
