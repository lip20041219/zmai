"""BenchmarkRunner — 评测流程编排器。

整合 TaskLoader → EvalHarness → ResultCollector → ScoreReporter
为 CLI 提供一站式评测入口。

使用方式:
    runner = BenchmarkRunner(backend="mock")
    stats = runner.run(source="custom")
    runner.report_to_json(stats, "report.json")
    runner.report_to_markdown(stats, "BENCHMARK.md")
    runner.report_to_csv(stats, "results.csv")
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from zmai.eval.collector import BenchmarkStats, ResultCollector
from zmai.eval.harness import EvalHarness, EvalResult, discover_tasks
from zmai.eval.loader import BenchmarkTask, TaskLoader
from zmai.eval.reporter import ScoreReporter

logger = logging.getLogger("zmai.eval.benchmark")


class BenchmarkRunner:
    """评测流程编排器。

    Args:
        backend: Backend 名称 ("mock" 用于测试).
        max_steps: Agent 最大步数.
        task_timeout: 单个任务超时秒数.
        model: 模型名称（用于成本估算）.
    """

    def __init__(
        self,
        backend: str = "mock",
        max_steps: int = 20,
        task_timeout: int = 120,
        model: str = "",
    ) -> None:
        self.backend = backend
        self.max_steps = max_steps
        self.task_timeout = task_timeout
        self.model = model or backend

    # ═══════════════════════════════════════════════════════════════
    # 运行入口
    # ═══════════════════════════════════════════════════════════════

    def run(
        self,
        source: str = "custom",
        path: str | None = None,
        max_instances: int = 0,
        task_ids: list[str] | None = None,
        repos: list[str] | None = None,
    ) -> BenchmarkStats:
        """运行评测。

        Args:
            source: "custom" | "swebench" | "humaneval"
            path: 任务来源路径（humaneval/custom 需要）
            max_instances: 最大任务数（0=全部）
            task_ids: 仅运行指定任务 ID
            repos: 按仓库过滤（swebench）

        Returns:
            BenchmarkStats 评测统计。
        """
        # 1. 加载任务
        loader = TaskLoader()
        tasks = loader.load(
            source=source,
            path=path,
            max_instances=max_instances,
            repos=repos,
        )

        if not tasks:
            logger.warning("没有找到评测任务 (source=%s, path=%s)", source, path)
            return BenchmarkStats()

        # 2. 过滤指定任务
        if task_ids:
            id_set = set(task_ids)
            tasks = [t for t in tasks if t.id in id_set]
            if not tasks:
                logger.warning("指定过滤后没有剩余任务: %s", task_ids)

        # 3. 运行评测
        return self._run_tasks(tasks)

    def run_custom(
        self,
        path: str | None = None,
        task_ids: list[str] | None = None,
    ) -> BenchmarkStats:
        """运行自定义评测（内置 fixtures）。"""
        return self.run(source="custom", path=path, task_ids=task_ids)

    def run_swebench(
        self,
        split: str = "lite",
        max_instances: int = 0,
        repos: list[str] | None = None,
    ) -> BenchmarkStats:
        """运行 SWE-bench 评测。"""
        try:
            return self.run(
                source="swebench", path=split,
                max_instances=max_instances, repos=repos,
            )
        except (RuntimeError, OSError, ConnectionError) as e:
            logger.warning("SWE-bench 不可用: %s", e)
            stats = BenchmarkStats()
            stats.per_task = [{"task_id": "error", "status": "ERROR", "duration_s": 0, "steps": 0, "error": str(e)[:100], "agent_status": ""}]  # noqa: E501
            return stats

    def run_humaneval(
        self, path: str, max_instances: int = 0,
    ) -> BenchmarkStats:
        """运行 HumanEval 评测。"""
        return self.run(
            source="humaneval", path=path, max_instances=max_instances,
        )

    # ═══════════════════════════════════════════════════════════════
    # 内部执行
    # ═══════════════════════════════════════════════════════════════

    def _run_tasks(self, tasks: list[BenchmarkTask]) -> BenchmarkStats:
        """在 EvalHarness 上执行一组任务。"""
        harness = EvalHarness(
            backend_name=self.backend,
            task_timeout=self.task_timeout,
            max_steps=self.max_steps,
        )

        # 将 BenchmarkTask 映射为 EvalHarness 能运行的 task_id
        # 对于 custom 任务，直接用 ID；swebench/humaneval 任务通过 loader 处理
        task_ids = [t.id for t in tasks]
        logger.info(
            "Benchmark 开始: %d tasks, backend=%s, max_steps=%d",
            len(task_ids), self.backend, self.max_steps,
        )

        results: list[EvalResult] = []
        for task in tasks:
            # 对于 swebench/humaneval 任务直接返回统计
            if task.source in ("swebench_lite", "swebench_verified", "humaneval"):
                import tempfile

                from zmai.eval.swebench import (
                    evaluate_instance,
                    load_instances,
                    setup_instance_repo,
                )
                start = time.time()
                try:
                    if task.source.startswith("swebench"):
                        instances = load_instances(
                            split="lite" if "lite" in task.source else "verified",
                            max_instances=1,
                            repos=[task.repo] if task.repo else None,
                        )
                        # 找到匹配的 instance
                        instance = None
                        for inst in instances:
                            if inst.instance_id == task.id:
                                instance = inst
                                break
                        if instance:
                            tmpdir = Path(tempfile.mkdtemp(prefix=f"zmai_swe_{task.id}_"))
                            repo_path = setup_instance_repo(instance, tmpdir)
                            # 用 Runtime 执行
                            from zmai.config import Config
                            from zmai.runtime import Runtime
                            config = Config(sources=[])
                            config.set("runtime.max_iterations", self.max_steps)
                            rt = Runtime(config=config)
                            if self.backend == "mock":
                                from zmai.gateway.base import Backend, BackendResponse
                                class _MockBackend(Backend):
                                    name = "mock"
                                    def invoke(self, request): return BackendResponse(content="处理中...")  # noqa: E501
                                    def stream(self, request): raise NotImplementedError
                                    @property
                                    def capabilities(self): return set()
                                rt._gateway.register("mock", _MockBackend, default=True)
                                backend = "mock"
                            else:
                                backend = self.backend
                            rdict = asyncio.run(rt.run(
                                agent_id=f"eval_{task.id}",
                                task=task.description,
                                backend=backend,
                                config={"project_path": str(repo_path)},
                            ))
                            resolved = False
                            if rdict.get("status") == "completed":
                                resolved = evaluate_instance(instance, repo_path, "")
                            dur = time.time() - start
                            results.append(EvalResult(
                                task_id=task.id,
                                status="PASS" if resolved else "FAIL",
                                duration=dur,
                                steps=rdict.get("steps", 0),
                                error=rdict.get("error"),
                                agent_status=rdict.get("status", ""),
                            ))
                        else:
                            results.append(EvalResult(task_id=task.id, status="ERROR", error="Instance not found"))  # noqa: E501
                    else:
                        results.append(EvalResult(task_id=task.id, status="ERROR", error="HumanEval mock not implemented"))  # noqa: E501
                except Exception as e:
                    logger.exception("Task %s failed", task.id)
                    dur = time.time() - start
                    results.append(EvalResult(task_id=task.id, status="ERROR", error=str(e)[:200], duration=dur))  # noqa: E501
            else:
                # Custom 任务 → 用 EvalHarness 执行
                result = harness.run_task(task.id)
                results.append(result)

        # 收集统计
        collector = ResultCollector(model=self.model)
        stats = collector.collect(results)
        return stats

    # ═══════════════════════════════════════════════════════════════
    # 报告输出
    # ═══════════════════════════════════════════════════════════════

    def report_to_json(self, stats: BenchmarkStats, path: str | Path | None = None) -> str:
        return ScoreReporter().to_json(stats, path=path)

    def report_to_markdown(self, stats: BenchmarkStats, path: str | Path | None = None) -> str:
        return ScoreReporter().to_markdown(stats, path=path)

    def report_to_csv(self, stats: BenchmarkStats, path: str | Path | None = None) -> str:
        return ScoreReporter().to_csv(stats, path=path)

    def format_console(self, stats: BenchmarkStats) -> str:
        return ScoreReporter().format_console(stats)

    # ═══════════════════════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def list_sources_help() -> str:
        return TaskLoader().list_sources()

    @staticmethod
    def list_tasks_custom(path: str | None = None) -> list[dict[str, Any]]:
        """列出可用的自定义评测任务。"""
        if path:
            loader = TaskLoader()
            tasks = loader.load("custom", path=path)
        else:
            tasks_raw = discover_tasks()
            tasks = [
                BenchmarkTask(
                    id=t.id, source="custom", description=t.description,
                    files=t.files, project_dir=str(t.project_dir),
                )
                for t in tasks_raw
            ]
        return [t.to_dict() for t in tasks]
