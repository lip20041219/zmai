"""结构化 Agent 执行日志。

为每个 Agent 执行步骤产生可观察、可调试、可回放、可评测、可持久化的记录。

架构:
  ExecutionLog
    ├── agent_id, task       ← 任务标识
    ├── created_at           ← 创建时间
    └── steps: [StepRecord]  ← 有序的记录列表

  StepRecord
    ├── step_id              ← 步骤序号
    ├── timestamp            ← ISO8601 时间戳
    ├── phase                ← "plan" | "tool_call" | "tool_result" | "verification" | "finalize" | "error"
    ├── action               ← 可读描述
    ├── tool_name            ← 工具名（tool_call/tool_result 时）
    ├── tool_input           ← 工具入参（已脱敏）
    ├── tool_output          ← 工具输出（已截断）
    ├── success              ← 是否成功
    ├── error                ← 错误信息
    ├── duration_ms          ← 耗时
    └── metadata             ← 扩展属性
"""  # noqa: E501

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.execution.log")

# ── 常量 ──────────────────────────────────────────────────────

MAX_OUTPUT_LENGTH = 5000
"""单个 tool_output 的最大字符数，超出部分截断。"""

_SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "api-key",
    "token", "access_token",
    "secret", "secret_key", "secret-key",
    "password", "passwd", "credential",
    "authorization", "auth",
    "key", "x-api-key",
})
"""工具入参中需要脱敏的 key 名称（不区分大小写）。"""

_SENSITIVE_MASK = "***REDACTED***"
"""脱敏替换文本。"""


# ── 辅助函数 ──────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def truncate_output(text: str | None, max_len: int = MAX_OUTPUT_LENGTH) -> str:
    """截断工具输出到指定长度。

    安全处理：None → ""，短文本直接返回，长文本末尾加截断标记。
    """
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... (truncated {len(text) - max_len} chars)"


def _sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """脱敏工具入参，移除/掩盖 API Key 等敏感字段。

    递归遍历 dict，对 key 名匹配敏感词的值做 masking。
    不修改原始 dict。
    """
    if not params:
        return {}
    result: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, dict):
            result[k] = _sanitize_params(v)
        elif isinstance(v, list):
            result[k] = [
                _sanitize_params(item) if isinstance(item, dict) else item
                for item in v
            ]
        elif k.lower() in _SENSITIVE_KEYS:
            result[k] = _SENSITIVE_MASK
        else:
            result[k] = v
    return result


# ── StepRecord ────────────────────────────────────────────────


@dataclass
class StepRecord:
    """单步执行记录。"""

    step_id: int
    timestamp: str
    phase: str
    action: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    success: bool | None = None
    error: str | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 确保超大字段已截断
        if d.get("tool_output") and len(d["tool_output"]) > MAX_OUTPUT_LENGTH:
            d["tool_output"] = d["tool_output"][:MAX_OUTPUT_LENGTH] + "..."
        return d


# ── ExecutionLog ──────────────────────────────────────────────


class ExecutionLog:
    """结构化 Agent 执行日志。

    线程安全。日志失败不影响 Agent 执行（所有方法内捕获异常）。

    使用方式:
        log = ExecutionLog(agent_id="agent_1", task="修复登录 bug")
        log.record_step(phase="tool_call", action="read_file",
                        tool_name="read_file", tool_input={"path": "main.py"})
        log.record_step(phase="tool_result", action="read_file",
                        tool_name="read_file", success=True,
                        tool_output="file content...", duration_ms=5)
        log.persist(Path("./workspace/agent_1/.state/execution_log.json"))
    """

    def __init__(self, agent_id: str, task: str) -> None:
        self.agent_id = agent_id
        self.task = task
        self.created_at: str = _now_iso()
        self._steps: list[StepRecord] = []
        self._lock = threading.Lock()
        self._step_counter: int = 0

    @property
    def steps(self) -> list[StepRecord]:
        with self._lock:
            return list(self._steps)

    def record_step(
        self,
        phase: str,
        action: str,
        *,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
        success: bool | None = None,
        error: str | None = None,
        duration_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> StepRecord:
        """记录一步执行。

        所有参数均经过安全处理（脱敏、截断）。
        异常不会传播给调用方。
        """
        try:
            with self._lock:
                self._step_counter += 1
                record = StepRecord(
                    step_id=self._step_counter,
                    timestamp=_now_iso(),
                    phase=phase,
                    action=action,
                    tool_name=tool_name,
                    tool_input=_sanitize_params(tool_input),
                    tool_output=truncate_output(tool_output),
                    success=success,
                    error=error,
                    duration_ms=max(0, duration_ms),
                    metadata=metadata or {},
                )
                self._steps.append(record)
                return record
        except Exception as exc:
            logger.warning("ExecutionLog 记录失败 (忽略): %s", exc)
            return StepRecord(
                step_id=-1, timestamp=_now_iso(),
                phase=phase, action=action,
            )

    def to_dict(self) -> dict[str, Any]:
        """导出为可序列化 dict。"""
        with self._lock:
            return {
                "agent_id": self.agent_id,
                "task": self.task,
                "created_at": self.created_at,
                "step_count": len(self._steps),
                "steps": [s.to_dict() for s in self._steps],
            }

    def to_json(self, indent: int = 2) -> str:
        """导出为 JSON 字符串。"""
        try:
            return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
        except Exception as exc:
            logger.warning("ExecutionLog JSON 序列化失败: %s", exc)
            return "{}"

    def persist(self, path: str | Path) -> bool:
        """持久化到磁盘文件。返回 True 表示成功，False 表示失败。

        日志写入失败不影响 Agent 执行。
        """
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self.to_json(), encoding="utf-8")
            return True
        except Exception as exc:
            logger.warning("ExecutionLog 持久化失败 (忽略): %s", exc)
            return False

    def summary(self) -> dict[str, Any]:
        """快速摘要，不含完整步骤数据。"""
        with self._lock:
            tool_calls = sum(1 for s in self._steps if s.phase in ("tool_call", "tool_result"))
            errors = sum(1 for s in self._steps if s.success is False)
            return {
                "agent_id": self.agent_id,
                "task_preview": self.task[:100],
                "total_steps": len(self._steps),
                "tool_calls": tool_calls,
                "errors": errors,
                "created_at": self.created_at,
            }
