"""Eval Harness & Benchmark — 自动化评测基础设施。

使用方式:
    zmai eval run                    运行全部评测任务
    zmai eval swebench              运行 SWE-bench 评测
    zmai eval humaneval             运行 HumanEval 评测
    zmai eval custom                运行自定义评测
    zmai eval report                查看最新报告

输出格式:
    zmai eval swebench --output json          JSON 格式
    zmai eval swebench --output markdown      Markdown 表格
    zmai eval swebench --output csv           CSV 格式
"""

from zmai.eval.benchmark import BenchmarkRunner
from zmai.eval.collector import BenchmarkStats, ResultCollector, StepTokenUsage
from zmai.eval.harness import EvalHarness, EvalResult, EvalTask
from zmai.eval.loader import BenchmarkTask, TaskLoader
from zmai.eval.reporter import ScoreReporter

__all__ = [
    "EvalHarness", "EvalResult", "EvalTask",
    "BenchmarkRunner", "BenchmarkTask", "TaskLoader",
    "BenchmarkStats", "ResultCollector", "StepTokenUsage",
    "ScoreReporter",
]
