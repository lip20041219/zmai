"""Agent abstract base class and data types."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from zmai.gateway import Backend
from zmai.tool import ToolRegistry


class AgentState(Enum):
    """Agent 状态枚举。

    状态转换规则（由 LifecycleManager 强制执行）:
      CREATED → PLANNING | EXECUTING | FAILED | CANCELLED
      PLANNING → EXECUTING | FAILED | CANCELLED
      EXECUTING → VERIFYING | FAILED | CANCELLED | TIMEOUT
      VERIFYING → COMPLETED | FAILED | EXECUTING | CANCELLED | TIMEOUT
      COMPLETED  — 终态，不可转换
      FAILED     — 终态，不可转换
      CANCELLED  — 终态，不可转换
      TIMEOUT    — 终态，不可转换
    """

    CREATED = "created"
    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    @property
    def is_terminal(self) -> bool:
        return self in (
            AgentState.COMPLETED,
            AgentState.FAILED,
            AgentState.CANCELLED,
            AgentState.TIMEOUT,
        )

    @property
    def is_active(self) -> bool:
        return self in (
            AgentState.PLANNING,
            AgentState.PLAN_READY,
            AgentState.EXECUTING,
            AgentState.VERIFYING,
        )


@dataclass
class AgentAction:
    type: Literal["continue", "pause", "complete", "fail"]
    output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    pause_reason: str | None = None
    error: str | None = None

    @classmethod
    def cont(cls, output: str = "", metadata: dict | None = None) -> AgentAction:
        return cls(type="continue", output=output, metadata=metadata or {})

    @classmethod
    def pause(cls, reason: str, metadata: dict | None = None) -> AgentAction:
        return cls(type="pause", pause_reason=reason, metadata=metadata or {})

    @classmethod
    def complete(cls, output: str = "", metadata: dict | None = None) -> AgentAction:
        return cls(type="complete", output=output, metadata=metadata or {})

    @classmethod
    def fail(cls, error: str, metadata: dict | None = None) -> AgentAction:
        return cls(type="fail", error=error, metadata=metadata or {})


@dataclass
class AgentContext:
    agent_id: str
    task: str
    config: dict[str, Any] = field(default_factory=dict)
    backend: Backend | None = None
    workspace: Path | None = None
    tools: ToolRegistry | None = None
    logger: logging.Logger | None = None
    max_steps: int = 300
    step_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    memory: Any = None  # MemoryManager 实例，Agent 可通过此存取记忆


@dataclass
class AgentResult:
    agent_id: str
    status: AgentState = AgentState.COMPLETED
    output: str = ""
    steps: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentInfo:
    agent_id: str
    name: str = ""
    status: AgentState = AgentState.CREATED
    task: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    step_count: int = 0


class Agent(ABC):
    """Agent 抽象基类。所有 Agent 实现必须继承此类。"""

    agent_id: str
    name: str = ""
    description: str = ""
    state: AgentState = AgentState.CREATED

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState.CREATED

    @abstractmethod
    async def initialize(self, context: AgentContext) -> None:
        ...

    @abstractmethod
    async def step(self, context: AgentContext) -> AgentAction:
        ...

    @abstractmethod
    async def finalize(self, context: AgentContext) -> AgentResult:
        ...

    def on_pause(self, reason: str | None = None) -> None:
        """Pause is not supported in the current state model. No-op."""
        pass

    def on_resume(self, input: str | None = None) -> None:
        """Resume is not supported in the current state model. No-op."""
        pass
