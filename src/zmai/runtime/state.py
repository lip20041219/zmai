"""Unified JSON state management, single source of truth."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AgentStateData:
    agent_id: str
    status: str  # idle|initializing|running|paused|completed|failed|cancelled
    task: str = ""
    step_count: int = 0
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentStateData:
        return cls(**d)


class StateManager:
    """状态管理器，统一 JSON 持久化。"""

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._states: dict[str, AgentStateData] = {}
        self._lock = threading.Lock()
        self._path = Path(persist_path) if persist_path else None
        self._last_persist: float = 0
        self._persist_interval: float = 2.0  # 两次持久化之间最少间隔 2s
        self._dirty: bool = False

    def get(self, agent_id: str) -> AgentStateData | None:
        with self._lock:
            return self._states.get(agent_id)

    def update(self, agent_id: str, **fields: Any) -> None:
        with self._lock:
            state = self._states.get(agent_id)
            if state is None:
                state = AgentStateData(agent_id=agent_id, status="idle", created_at=_now())
                self._states[agent_id] = state
            for k, v in fields.items():
                if hasattr(state, k):
                    setattr(state, k, v)
            state.updated_at = _now()
        self.persist()
        # 如果有积压的脏数据，立即刷盘
        if self._dirty:
            self._flush()

    def delete(self, agent_id: str) -> None:
        with self._lock:
            self._states.pop(agent_id, None)
        self.persist()

    def list(self) -> list[AgentStateData]:
        with self._lock:
            return list(self._states.values())

    def persist(self) -> None:
        """持久化状态到磁盘（带节流：至少间隔 2 秒）。"""
        if not self._path:
            return
        import time as _time
        now = _time.time()
        if now - self._last_persist < self._persist_interval:
            self._dirty = True
            return
        self._flush()

    def _flush(self) -> None:
        """实际写入磁盘。"""
        import time as _time
        with self._lock:
            data = {k: v.to_dict() for k, v in self._states.items()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self._last_persist = _time.time()
            self._dirty = False
        except OSError:
            pass

    def flush(self) -> None:
        """强制立即写入磁盘。"""
        self._dirty = True
        self._flush()

    def restore(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            with self._lock:
                self._states = {
                    k: AgentStateData.from_dict(v) for k, v in data.items()
                }
        except (json.JSONDecodeError, OSError):
            pass
