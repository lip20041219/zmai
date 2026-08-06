"""ResultCollector — 聚合评测结果，计算统计指标。

指标:
  - Success Rate:  PASS / (PASS + FAIL + TIMEOUT + ERROR) × 100
  - Pass@1:       首次尝试通过率
  - Latency:      任务平均/中位耗时
  - Token Usage:  输入/输出 token 数（来自 ExecutionLog）
  - Cost:         基于 token 用量 × 模型定价估算
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.eval.harness import EvalResult

logger = logging.getLogger("zmai.eval.collector")

# ── 模型定价（每 1K tokens，单位 USD） ────────────────────

_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4": {"input": 0.003, "output": 0.015},
    "claude-haiku-3": {"input": 0.00025, "output": 0.00125},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
    "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
    "default": {"input": 0.003, "output": 0.015},
}


def _estimate_cost(input_tokens: int, output_tokens: int, model: str = "") -> float:
    """估算 API 调用成本（USD）。"""
    pricing = _MODEL_PRICING.get(model)
    if pricing is None:
        for key in sorted(_MODEL_PRICING.keys(), key=len, reverse=True):
            if model.startswith(key):
                pricing = _MODEL_PRICING[key]
                break
    if pricing is None:
        pricing = _MODEL_PRICING["default"]
    input_cost = (input_tokens / 1000) * pricing["input"]
    output_cost = (output_tokens / 1000) * pricing["output"]
    return round(input_cost + output_cost, 6)


# ═══════════════════════════════════════════════════════════════
# StepTokenUsage
# ═══════════════════════════════════════════════════════════════


@dataclass
class StepTokenUsage:
    """单步 token 用量快照。"""
    step_id: int = 0
    phase: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    success: bool | None = None


# ═══════════════════════════════════════════════════════════════
# BenchmarkStats
# ═══════════════════════════════════════════════════════════════


@dataclass
class BenchmarkStats:
    """完整评测统计。"""

    # ── 基本计数 ────────────────────────────────────────────
    total: int = 0
    passed: int = 0
    failed: int = 0
    timedout: int = 0
    errors: int = 0

    # ── 比率 ────────────────────────────────────────────────
    success_rate: float = 0.0
    pass_at_1: float = 0.0

    # ── 延迟 (秒) ──────────────────────────────────────────
    latency_min: float = 0.0
    latency_max: float = 0.0
    latency_avg: float = 0.0
    latency_median: float = 0.0
    total_duration: float = 0.0

    # ── Token ──────────────────────────────────────────────
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0

    # ── 成本 (USD) ─────────────────────────────────────────
    estimated_cost_usd: float = 0.0
    model_used: str = ""

    # ── 详细结果（供报告使用） ────────────────────────────
    per_task: list[dict[str, Any]] = field(default_factory=list)
    token_breakdown: list[StepTokenUsage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "timedout": self.timedout,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 1),
            "pass_at_1": round(self.pass_at_1, 1),
            "latency": {
                "min_s": round(self.latency_min, 2),
                "max_s": round(self.latency_max, 2),
                "avg_s": round(self.latency_avg, 2),
                "median_s": round(self.latency_median, 2),
                "total_s": round(self.total_duration, 2),
            },
            "tokens": {
                "total_input": self.total_input_tokens,
                "total_output": self.total_output_tokens,
                "avg_input": round(self.avg_input_tokens, 1),
                "avg_output": round(self.avg_output_tokens, 1),
            },
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "model_used": self.model_used or "",
        }


# ═══════════════════════════════════════════════════════════════
# ResultCollector
# ═══════════════════════════════════════════════════════════════


class ResultCollector:
    """评测结果收集与统计计算器。

    使用方式:
        collector = ResultCollector()
        stats = collector.collect(results)
        print(stats.to_dict())
    """

    def __init__(self, model: str = ""):
        self._model = model

    def collect(
        self,
        results: list[EvalResult],
        token_logs: list[StepTokenUsage] | None = None,
    ) -> BenchmarkStats:
        """从 EvalResult 列表计算评测统计。

        Args:
            results: 评测结果列表。
            token_logs: 可选的 token 用量日志。

        Returns:
            完整评测统计 BenchmarkStats。
        """
        total = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        timedout = sum(1 for r in results if r.status == "TIMEOUT")
        errors = sum(1 for r in results if r.status == "ERROR")

        success_rate = (passed / total * 100) if total > 0 else 0.0

        durations = [r.duration for r in results if r.duration > 0]
        latency_min = min(durations) if durations else 0.0
        latency_max = max(durations) if durations else 0.0
        latency_avg = statistics.mean(durations) if durations else 0.0
        latency_median = statistics.median(durations) if durations else 0.0
        total_duration = sum(durations)

        # Token usage
        total_input = sum(t.input_tokens for t in (token_logs or []))
        total_output = sum(t.output_tokens for t in (token_logs or []))
        avg_input = statistics.mean([t.input_tokens for t in (token_logs or [])]) if token_logs else 0.0
        avg_output = statistics.mean([t.output_tokens for t in (token_logs or [])]) if token_logs else 0.0

        # Cost estimate
        cost = _estimate_cost(total_input, total_output, self._model)

        # Per-task summary
        per_task = [
            {
                "task_id": r.task_id,
                "status": r.status,
                "duration_s": round(r.duration, 2),
                "steps": r.steps,
                "error": (r.error or "")[:100],
                "agent_status": r.agent_status,
            }
            for r in results
        ]

        return BenchmarkStats(
            total=total,
            passed=passed,
            failed=failed,
            timedout=timedout,
            errors=errors,
            success_rate=success_rate,
            pass_at_1=success_rate,
            latency_min=latency_min,
            latency_max=latency_max,
            latency_avg=latency_avg,
            latency_median=latency_median,
            total_duration=total_duration,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            avg_input_tokens=avg_input,
            avg_output_tokens=avg_output,
            estimated_cost_usd=cost,
            model_used=self._model,
            per_task=per_task,
            token_breakdown=token_logs or [],
        )

    @staticmethod
    def extract_token_usage(log_dir: str | Path) -> list[StepTokenUsage]:
        """从 ExecutionLog 文件中提取 token 用量。

        Args:
            log_dir: ExecutionLog 文件所在目录。

        Returns:
            StepTokenUsage 列表。
        """
        usages: list[StepTokenUsage] = []
        log_dir = Path(log_dir)

        for log_file in sorted(log_dir.glob("*.json")):
            try:
                data = json.loads(log_file.read_text(encoding="utf-8"))
                for step in data.get("steps", []):
                    meta = step.get("metadata", {})
                    usage = StepTokenUsage(
                        step_id=step.get("step_id", 0),
                        phase=step.get("phase", ""),
                        input_tokens=meta.get("input_tokens", 0),
                        output_tokens=meta.get("output_tokens", 0),
                        duration_ms=step.get("duration_ms", 0),
                        success=step.get("success"),
                    )
                    usages.append(usage)
            except Exception as e:
                logger.debug("跳过日志文件 %s: %s", log_file.name, e)

        return usages
