"""Agent 生命周期状态机。

状态定义（对齐 AgentState 枚举）:
  CREATED    — Agent 已创建，尚未开始执行
  PLANNING   — 规划阶段（预留）
  EXECUTING  — 执行阶段
  VERIFYING  — 验证阶段（预留）
  COMPLETED  — 终态：成功完成
  FAILED     — 终态：执行失败
  CANCELLED  — 终态：用户取消
  TIMEOUT    — 终态：超时（max_steps 或 wall-clock）

转换规则（_TRANSITIONS 表强制执行）:
  CREATED     → PLANNING | EXECUTING | FAILED | CANCELLED
  PLANNING    → EXECUTING | FAILED | CANCELLED
  EXECUTING   → VERIFYING | FAILED | CANCELLED | TIMEOUT
  VERIFYING   → COMPLETED | FAILED | EXECUTING | CANCELLED | TIMEOUT
  COMPLETED   — 终态，不可转换
  FAILED      — 终态，不可转换
  CANCELLED   — 终态，不可转换
  TIMEOUT     — 终态，不可转换
"""

from __future__ import annotations

import threading

from zmai.errors import RuntimeError

_TRANSITIONS: dict[tuple[str, str], bool] = {
    # ── CREATED → 活跃态 ──────────────────────────
    ("created", "planning"): True,    # 进入规划
    ("created", "executing"): True,   # 直接执行（无 Plan 模式）
    ("created", "failed"): True,      # 初始化失败
    ("created", "cancelled"): True,   # 创建后取消

    # ── PLANNING → 下一步 ─────────────────────────
    ("planning", "plan_ready"): True, # 规划完成，等待确认
    ("planning", "failed"): True,     # 规划阶段失败
    ("planning", "cancelled"): True,  # 规划阶段取消

    # ── PLAN_READY → 下一步 ───────────────────────
    ("plan_ready", "executing"): True, # 用户确认，开始执行
    ("plan_ready", "failed"): True,    # 用户拒绝或验证失败
    ("plan_ready", "cancelled"): True, # 用户取消

    # ── EXECUTING → 下一步 ────────────────────────
    ("executing", "verifying"): True, # 执行完成，进入验证
    ("executing", "completed"): True, # 执行完成后直接完成（简化路径）
    ("executing", "failed"): True,    # 执行失败
    ("executing", "cancelled"): True, # 执行阶段取消
    ("executing", "timeout"): True,   # 执行超时

    # ── VERIFYING → 下一步 ────────────────────────
    ("verifying", "completed"): True, # 验证通过
    ("verifying", "failed"): True,    # 验证失败
    ("verifying", "executing"): True, # 验证失败后重试执行（修复循环）
    ("verifying", "cancelled"): True, # 验证阶段取消
    ("verifying", "timeout"): True,   # 验证超时
}

_TERMINAL = {"completed", "failed", "cancelled", "timeout"}


class LifecycleManager:
    """Agent 生命周期状态机。线程安全。"""

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._lock = threading.Lock()

    def _transition(self, agent_id: str, to: str) -> None:
        with self._lock:
            cur = self._states.get(agent_id, "created")
            if cur in _TERMINAL:
                raise RuntimeError(
                    f"Agent {agent_id} 已处于终态 '{cur}'，无法再转换到 '{to}'"
                )
            if not _TRANSITIONS.get((cur, to)):
                raise RuntimeError(
                    f"非法状态转换: {cur} → {to} (agent={agent_id})"
                )
            self._states[agent_id] = to

    def create(self, agent_id: str) -> None:
        """创建 Agent → CREATED 状态。"""
        with self._lock:
            if agent_id in self._states:
                existing = self._states[agent_id]
                if existing in _TERMINAL:
                    raise RuntimeError(
                        f"Agent {agent_id} 已处于终态 '{existing}'，不可重复创建"
                    )
                raise RuntimeError(
                    f"Agent {agent_id} 已存在，当前状态: {existing}"
                )
        # 首次创建时 _states 为空，_transition 默认从 created → created
        # 但这里我们直接设置状态，因为这是初始创建
        with self._lock:
            self._states[agent_id] = "created"

    def plan(self, agent_id: str) -> None:
        """进入规划阶段 → PLANNING。"""
        self._transition(agent_id, "planning")

    def plan_ready(self, agent_id: str) -> None:
        """Plan 生成完成，等待确认 → PLAN_READY。"""
        self._transition(agent_id, "plan_ready")

    def execute(self, agent_id: str) -> None:
        """进入执行阶段 → EXECUTING。"""
        self._transition(agent_id, "executing")

    def verify(self, agent_id: str) -> None:
        """进入验证阶段 → VERIFYING（预留）。"""
        self._transition(agent_id, "verifying")

    def complete(self, agent_id: str) -> None:
        """标记完成 → COMPLETED。"""
        self._transition(agent_id, "completed")

    def fail(self, agent_id: str) -> None:
        """标记失败 → FAILED。"""
        self._transition(agent_id, "failed")

    def cancel(self, agent_id: str) -> None:
        """取消 Agent → CANCELLED。如果已是终态则忽略。"""
        with self._lock:
            cur = self._states.get(agent_id, "created")
            if cur in _TERMINAL:
                return  # 已是终态，忽略重复取消
        self._transition(agent_id, "cancelled")

    def timeout(self, agent_id: str) -> None:
        """标记超时 → TIMEOUT。"""
        self._transition(agent_id, "timeout")

    def has(self, agent_id: str) -> bool:
        """检查 Agent 是否已在生命周期中注册。"""
        with self._lock:
            return agent_id in self._states

    def get_state(self, agent_id: str) -> str:
        """获取 Agent 当前状态字符串。"""
        with self._lock:
            return self._states.get(agent_id, "created")

    def is_terminal(self, agent_id: str) -> bool:
        """Agent 是否已处于终态。"""
        return self.get_state(agent_id) in _TERMINAL

    def is_active(self, agent_id: str) -> bool:
        """Agent 是否在活跃态（PLANNING | PLAN_READY | EXECUTING | VERIFYING）。"""
        return self.get_state(agent_id) in {"planning", "plan_ready", "executing", "verifying"}

    def list(self) -> dict[str, str]:
        """列出所有 Agent 及其状态。"""
        with self._lock:
            return dict(self._states)

    def remove(self, agent_id: str) -> None:
        """移除 Agent 的生命周期记录。"""
        with self._lock:
            self._states.pop(agent_id, None)
