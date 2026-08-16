"""Workflow abstract base class and state enumeration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Workflow(ABC):
    """Workflow 抽象基类。实现 build() 定义步骤。"""

    name: str = ""
    description: str = ""

    @abstractmethod
    def build(self) -> list[dict[str, Any]]:
        ...

    def validate(self, steps: list[dict[str, Any]]) -> None:
        ids = {s["id"] for s in steps}
        for s in steps:
            if s.get("next_on_success") and s["next_on_success"] not in ids:
                raise ValueError(f"next_on_success 引用不存在: {s['next_on_success']}")
            if s.get("next_on_failure") and s["next_on_failure"] not in ids:
                raise ValueError(f"next_on_failure 引用不存在: {s['next_on_failure']}")
