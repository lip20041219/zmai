"""Benchmark — SWE 任务基准测试。

命令:
  zmai benchmark run          运行全部任务
  zmai benchmark run -t ID    运行单个任务
  zmai benchmark report       显示最新报告
"""

from __future__ import annotations

from zmai.benchmark.runner import (
    BenchmarkRunner,
    BenchmarkResult,
    BenchmarkReport,
    MockBenchmarkBackend,
    format_report,
    find_task,
    discover_tasks,
)

__all__ = [
    "BenchmarkRunner",
    "BenchmarkResult",
    "BenchmarkReport",
    "MockBenchmarkBackend",
    "format_report",
    "find_task",
    "discover_tasks",
]
