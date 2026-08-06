"""LoopGuard — Agent 执行循环检测与防护。

检测三种循环模式:
  1. 连续相同 tool call（同名 + 同关键参数）
  2. 连续相同失败结果（同名 + 同错误）
  3. 连续无代码修改（多次 step 没有任何文件变更）

阈值: LOOP_THRESHOLD = 5 次连续无进展 → 触发阻塞。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("zmai.swe.loop_guard")

# ── 常量 ──────────────────────────────────────────────────────────

LOOP_THRESHOLD = 5
"""连续无进展步数阈值。"""


# ═══════════════════════════════════════════════════════════════════
# LoopResult — 检测结果
# ═══════════════════════════════════════════════════════════════════


@dataclass
class LoopResult:
    """循环检测结果。

    Attributes:
        blocked: 是否应阻塞当前执行。
        reason: 阻塞原因（"identical_calls" | "identical_failures" | "no_progress"）。
        suggestion: 建议操作（"change_strategy"）。
        details: 检测详情，供日志/调试使用。
    """

    blocked: bool
    reason: str = ""
    suggestion: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str | dict]:
        return {
            "status": "blocked" if self.blocked else "ok",
            "reason": self.reason,
            "suggestion": self.suggestion,
            "details": self.details,
        }


# ── 快速构造 ──────────────────────────────────────────────────────

def _blocked(reason: str, details: dict[str, Any] | None = None) -> LoopResult:
    return LoopResult(
        blocked=True,
        reason=reason,
        suggestion="change_strategy",
        details=details or {},
    )


def _ok() -> LoopResult:
    return LoopResult(blocked=False)


# ═══════════════════════════════════════════════════════════════════
# 工具调用签名
# ═══════════════════════════════════════════════════════════════════


def _tool_call_signature(name: str, params: dict[str, Any]) -> str:
    """生成工具调用的签名，用于比较是否相同。

    签名 = 工具名 + 关键参数的值（去除路径末尾分隔符、规范化空格）。
    对于不同工具，关键参数不同:
      - shell_exec/git: command/args 参数
      - read_file/write_file/edit: path 参数
      - grep: pattern 参数
      - show_to_user: content 参数
    """
    key_params = {
        "shell_exec": params.get("command", ""),
        "git": params.get("args", ""),
        "read_file": params.get("path", ""),
        "write_file": params.get("path", ""),
        "edit": params.get("path", ""),
        "grep": params.get("pattern", ""),
        "show_to_user": params.get("content", ""),
    }
    key = key_params.get(name, "")
    if key is None:
        key = ""
    elif not isinstance(key, str):
        key = str(key)
    # 规范化：去除首尾空格，统一路径分隔符
    key = key.strip().replace("\\", "/")
    return f"{name}:{key}"


def _tool_failure_signature(name: str, error: str | None) -> str:
    """生成工具失败的签名。"""
    err = (error or "").strip()[:200]
    return f"{name}:FAIL:{err}"


# ═══════════════════════════════════════════════════════════════════
# LoopGuard
# ═══════════════════════════════════════════════════════════════════


class LoopGuard:
    """Agent 执行循环检测器。

    在每次 step 的工具调用后记录，检测无进展模式。
    所有检测阈值均由 LOOP_THRESHOLD 控制。

    使用方式:
        guard = LoopGuard()
        guard.record_tool_call("shell_exec", {"command": "dir *.py"}, False, "error msg")
        guard.record_no_modification()
        result = guard.check()
        if result.blocked:
            # 改变策略
            guard.reset()
    """

    # ── 写操作工具 ──────────────────────────────────────────

    _WRITE_TOOLS = frozenset({"write_file", "edit", "git"})

    def __init__(self, threshold: int = LOOP_THRESHOLD) -> None:
        self._threshold = threshold

        # 工具调用历史（用于检测重复失败）
        self._call_history: list[dict[str, Any]] = []

        # 连续相同调用计数
        self._consecutive_identical: int = 0
        self._last_signature: str = ""

        # 连续相同失败计数
        self._consecutive_failures: int = 0
        self._last_failure_sig: str = ""

        # 无修改计数
        self._steps_without_change: int = 0
        self._last_modification_step: int = -1
        self._call_count_since_last_mod: int = 0

        # 当前 step 的工具调用（暂存，record_tool_call 时更新）
        self._current_step_calls: list[dict[str, Any]] = []

    # ── 记录 ────────────────────────────────────────────────

    def record_tool_call(
        self,
        name: str,
        params: dict[str, Any],
        success: bool,
        output: str = "",
        error: str | None = None,
    ) -> None:
        """记录一次工具调用。

        参数与 SWEAgent.step() 中的工具调用一一对应。
        自动更新：
          - 连续相同调用计数
          - 连续相同失败计数
          - 无修改计数（写工具成功时重置）
        """
        sig = _tool_call_signature(name, params)
        entry = {
            "name": name,
            "signature": sig,
            "success": success,
            "output": (output or "")[:200],
            "error": error,
        }
        self._call_history.append(entry)
        self._current_step_calls.append(entry)

        # ── 检测连续相同调用 ─────────────────────────────
        # 首次调用计为 1，后续相同调用递增，不同调用重置为 1
        if sig == self._last_signature:
            self._consecutive_identical += 1
        else:
            self._consecutive_identical = 1
        self._last_signature = sig

        # ── 检测连续相同失败 ─────────────────────────────
        if not success:
            fail_sig = _tool_failure_signature(name, error)
            if fail_sig == self._last_failure_sig:
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 1
            self._last_failure_sig = fail_sig
        else:
            # 成功的工具调用重置失败计数（因为不再是连续失败）
            self._consecutive_failures = 0
            self._last_failure_sig = ""

        # ── 检测代码修改 ─────────────────────────────────
        if success and name in self._WRITE_TOOLS:
            self._record_modification()

    def record_no_modification(self) -> None:
        """标记当前 step 没有产生代码修改。

        在 step() 工具调用全部执行完毕后，如果没有任何写操作成功，
        则应调用此方法增加无修改计数。
        """
        self._steps_without_change += 1

    def _record_modification(self) -> None:
        """重置无修改计数。"""
        self._steps_without_change = 0
        self._last_modification_step = len(self._call_history)
        self._call_count_since_last_mod = 0

    def reset(self) -> None:
        """重置所有循环计数器。

        在策略变更后（如重新规划）调用，清空历史但保留阈值设置。
        """
        self._consecutive_identical = 0
        self._last_signature = ""
        self._consecutive_failures = 0
        self._last_failure_sig = ""
        self._steps_without_change = 0
        self._call_count_since_last_mod = 0
        # 不清除 _call_history（保留完整历史用于调试）
        # 不清除 _last_modification_step

    def reset_hard(self) -> None:
        """完全重置（包括调用历史）。"""
        self.reset()
        self._call_history.clear()
        self._last_modification_step = -1

    # ── 检测 ────────────────────────────────────────────────

    def check(self) -> LoopResult:
        """检测当前是否处于循环状态。

        优先级（从高到低）:
          1. identical_failures — 连续相同失败（最具指示性）
          2. no_progress        — 连续无代码修改
          3. identical_calls    — 连续相同工具调用
        """
        # 1. 连续相同失败检测（最高优先级 — 明确表示当前方法无效）
        if self._consecutive_failures >= self._threshold:
            return _blocked("identical_failures", {
                "count": self._consecutive_failures,
                "threshold": self._threshold,
                "last_failure": self._last_failure_sig,
            })

        # 2. 连续无修改检测
        if self._steps_without_change >= self._threshold:
            return _blocked("no_progress", {
                "count": self._steps_without_change,
                "threshold": self._threshold,
                "total_calls": len(self._call_history),
            })

        # 3. 连续相同调用检测
        if self._consecutive_identical >= self._threshold:
            return _blocked("identical_calls", {
                "count": self._consecutive_identical,
                "threshold": self._threshold,
                "last_signature": self._last_signature,
            })

        return _ok()

    # ── 查询 ────────────────────────────────────────────────

    @property
    def call_count(self) -> int:
        return len(self._call_history)

    @property
    def is_stuck(self) -> bool:
        """快速判断是否陷入了循环。"""
        return self.check().blocked

    def get_status(self) -> dict[str, Any]:
        """获取当前防护状态。"""
        return {
            "call_count": len(self._call_history),
            "last_signature": self._last_signature,
            "consecutive_identical": self._consecutive_identical,
            "consecutive_failures": self._consecutive_failures,
            "steps_without_change": self._steps_without_change,
            "last_modification_step": self._last_modification_step,
            "threshold": self._threshold,
        }

    def get_recent_calls(self, n: int = 10) -> list[dict[str, Any]]:
        """获取最近 N 次工具调用记录（用于日志/调试）。"""
        return self._call_history[-n:]


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════


def create_loop_guard(threshold: int = LOOP_THRESHOLD) -> LoopGuard:
    """创建 LoopGuard 实例。"""
    return LoopGuard(threshold=threshold)
