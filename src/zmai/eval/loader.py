"""TaskLoader — 从多种来源加载评测任务。

支持:
  - SWE-bench (lite / verified)
  - HumanEval (本地 JSON 文件)
  - Custom (本地目录/文件, task.json 格式)

每个任务统一为 BenchmarkTask 结构。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("zmai.eval.loader")


# ═══════════════════════════════════════════════════════════════
# 统一任务数据模型
# ═══════════════════════════════════════════════════════════════


@dataclass
class BenchmarkTask:
    """统一的评测任务结构，屏蔽不同来源的差异。"""

    id: str
    source: str  # "swebench_lite" | "humaneval" | "custom"
    description: str
    project_dir: str | None = None
    files: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    expected: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    repo: str = ""
    base_commit: str = ""
    hints: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "description": self.description[:200],
            "repo": self.repo,
            "base_commit": self.base_commit,
            "files": self.files,
            "verification_cmd": str(self.verification.get("command", ""))[:60],
        }


# ═══════════════════════════════════════════════════════════════
# TaskLoader
# ═══════════════════════════════════════════════════════════════


class TaskLoader:
    """评测任务加载器。

    使用方式:
        loader = TaskLoader()
        tasks = loader.load("swebench", max_instances=5)
        tasks = loader.load("humaneval", path="./data/humaneval.json")
        tasks = loader.load("custom", path="./my_tasks/")
    """

    def load(
        self,
        source: str,
        path: str | None = None,
        max_instances: int = 0,
        repos: list[str] | None = None,
    ) -> list[BenchmarkTask]:
        """从指定来源加载评测任务。

        Args:
            source: "swebench" | "humaneval" | "custom"
            path: 来源路径（humaneval/custom 需要）
            max_instances: 最大加载数量（0=全部）
            repos: 按仓库过滤（swebench）

        Returns:
            统一格式的 BenchmarkTask 列表。
        """
        source_map = {
            "swebench": self._load_swebench,
            "humaneval": self._load_humaneval,
            "custom": self._load_custom,
        }
        loader = source_map.get(source)
        if loader is None:
            raise ValueError(f"不支持的任务来源: {source}，支持: {list(source_map.keys())}")
        return loader(path=path, max_instances=max_instances, repos=repos)

    # ── SWE-bench ──────────────────────────────────────────────

    def _load_swebench(
        self,
        path: str | None = None,
        max_instances: int = 0,
        repos: list[str] | None = None,
    ) -> list[BenchmarkTask]:
        from zmai.eval.swebench import load_instances

        instances = load_instances(
            split=path or "lite",
            max_instances=max_instances,
            repos=repos,
        )
        tasks: list[BenchmarkTask] = []
        for inst in instances:
            task = BenchmarkTask(
                id=inst.instance_id,
                source="swebench_lite" if (path or "lite") == "lite" else "swebench_verified",
                description=inst.problem_statement[:200],
                repo=inst.repo,
                base_commit=inst.base_commit,
                hints=inst.hints_text,
                expected=inst.patch[:200],
                files=list(inst.FAIL_TO_PASS) + list(inst.PASS_TO_PASS),
                metadata={
                    "FAIL_TO_PASS": inst.FAIL_TO_PASS,
                    "PASS_TO_PASS": inst.PASS_TO_PASS,
                    "test_patch": inst.test_patch,
                    "patch": inst.patch,
                },
            )
            tasks.append(task)
        logger.info("Loaded %d SWE-bench tasks", len(tasks))
        return tasks

    # ── HumanEval ──────────────────────────────────────────────

    def _load_humaneval(
        self,
        path: str | None = None,
        max_instances: int = 0,
        repos: list[str] | None = None,
    ) -> list[BenchmarkTask]:
        """加载 HumanEval 格式的评测任务。

        HumanEval JSON 格式:
        {
            "task_id": "HumanEval/0",
            "prompt": "def add(a, b):\\n    ...",
            "entry_point": "add",
            "test": "assert add(1, 2) == 3",
            "canonical_solution": "..."
        }
        """
        if not path:
            raise ValueError("HumanEval 需要提供 JSON 文件路径 (--path)")

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"HumanEval 文件不存在: {p}")

        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = list(data.values())

        tasks: list[BenchmarkTask] = []
        for item in data:
            task_id = item.get("task_id", "")
            prompt = item.get("prompt", "")
            entry_point = item.get("entry_point", "")
            test_code = item.get("test", "")
            solution = item.get("canonical_solution", "")

            if not task_id:
                continue

            tasks.append(BenchmarkTask(
                id=task_id,
                source="humaneval",
                description=prompt.strip()[:200],
                expected=solution[:200],
                verification={
                    "command": f"python -c \"{prompt}\\n{test_code}\\ncheck({entry_point})\"",
                    "timeout": 30,
                },
                metadata={
                    "entry_point": entry_point,
                    "prompt": prompt,
                    "test": test_code,
                },
            ))

        if max_instances > 0:
            tasks = tasks[:max_instances]

        logger.info("Loaded %d HumanEval tasks from %s", len(tasks), p)
        return tasks

    # ── Custom ─────────────────────────────────────────────────

    def _load_custom(
        self,
        path: str | None = None,
        max_instances: int = 0,
        repos: list[str] | None = None,
    ) -> list[BenchmarkTask]:
        """加载自定义评测任务。

        支持两种格式:
          1. 目录: 包含多个子目录，每个子目录有 task.json + project/
          2. 单个 task.json 文件
        """
        if not path:
            # 默认使用内置 fixtures
            from zmai.eval.harness import FIXTURES_ROOT, discover_tasks
            path = str(FIXTURES_ROOT)
            etasks = discover_tasks()
        else:
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"自定义任务路径不存在: {p}")
            if p.is_dir() and (p / "task.json").exists():
                # 单个任务目录
                from zmai.eval.harness import EvalTask
                etasks = [EvalTask(p)]
            elif p.is_dir():
                # 多个任务目录
                from zmai.eval.harness import EvalTask
                etasks = []
                for entry in sorted(p.iterdir()):
                    if entry.is_dir() and (entry / "task.json").exists():
                        etasks.append(EvalTask(entry))
            elif p.suffix == ".json":
                # 单个 JSON 文件 -> 自定义任务描述
                return self._load_custom_json(p, max_instances)
            else:
                raise ValueError(f"不支持的自定义路径: {p}")

        tasks = [
            BenchmarkTask(
                id=t.id,
                source="custom",
                description=t.description[:200],
                project_dir=str(t.project_dir),
                files=t.files,
                verification=t.verification,
                expected=t.expected,
            )
            for t in etasks
        ]

        if max_instances > 0:
            tasks = tasks[:max_instances]

        logger.info("Loaded %d custom tasks from %s", len(tasks), path)
        return tasks

    def _load_custom_json(
        self, path: Path, max_instances: int = 0,
    ) -> list[BenchmarkTask]:
        """从自定义 JSON 文件加载任务。

        格式:
        [
            {
                "id": "task_001",
                "description": "...",
                "prompt": "def add(a, b):",
                "test": "assert add(1,2) == 3",
                "entry_point": "add"
            }
        ]
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = [data]

        tasks: list[BenchmarkTask] = []
        for item in data:
            tid = item.get("id", item.get("task_id", ""))
            if not tid:
                continue
            prompt = item.get("prompt", item.get("description", ""))
            test = item.get("test", "")
            entry_point = item.get("entry_point", "")

            tasks.append(BenchmarkTask(
                id=tid,
                source="custom",
                description=str(prompt)[:200],
                verification={
                    "command": f"python -c \"{prompt}\\n{test}\\ncheck({entry_point})\"",
                    "timeout": 30,
                },
            ))

        if max_instances > 0:
            tasks = tasks[:max_instances]
        return tasks

    # ── 列表 ───────────────────────────────────────────────────

    def list_sources(self) -> str:
        """列出所有支持的任务来源。"""
        return (
            "  支持的任务来源:\n"
            "    swebench              SWE-bench Lite/Verified\n"
            "    humaneval             HumanEval (需 --path)\n"
            "    custom                自定义任务 (需 --path 或使用内置 fixtures)"
        )
