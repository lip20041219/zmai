"""Benchmark Runner — 自动运行 SWE 任务并收集结果。

流程:
  1. 加载 tests/fixtures/swe_tasks/ 中的全部任务
  2. 对每个任务:
     a. 创建隔离临时工作区
     b. 复制初始项目
     c. 执行 Agent（Mock 或真实 Backend）
     d. 收集 Agent 输出
     e. 运行外部 verification command
     f. 判定 PASS / FAIL / TIMEOUT / ERROR
  3. 输出 JSON 报告

核心原则:
  - Benchmark PASS 必须由外部 verification 判定
  - 不把 Agent 自报的成功当作通过
  - PASS = verification 通过 + exit code 0
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.config import Config
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.runtime import Runtime

logger = logging.getLogger("zmai.benchmark")

# ── Fixtures 路径 ──────────────────────────────────────────

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "swe_tasks"  # noqa: E501

_SESSION_DIR = Path.home() / ".zmai" / "benchmark"
_RESULTS_FILE = _SESSION_DIR / "latest_results.json"


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════


@dataclass
class BenchmarkResult:
    """单个任务的 Benchmark 结果。"""

    task_id: str
    status: str  # "pass" | "fail" | "timeout" | "error"
    description: str = ""
    steps: int = 0
    duration: float = 0.0
    agent_status: str = ""
    agent_output: str = ""
    verification_command: str = ""
    verification_output: str = ""
    verification_passed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BenchmarkResult:
        return cls(**d)


@dataclass
class BenchmarkReport:
    """完整 Benchmark 运行报告。"""

    started_at: str
    completed_at: str
    total: int
    passed: int
    failed: int
    timedout: int
    errors: int
    success_rate: float
    results: list[BenchmarkResult]
    backend_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "timedout": self.timedout,
            "errors": self.errors,
            "success_rate": self.success_rate,
            "backend_used": self.backend_used,
            "results": [r.to_dict() for r in self.results],
        }


# ═══════════════════════════════════════════════════════════
# SWE Task 加载
# ═══════════════════════════════════════════════════════════


class SWETask:
    """单个 SWE 测试任务。"""

    def __init__(self, task_dir: Path) -> None:
        self.path = task_dir
        with open(task_dir / "task.json", encoding="utf-8") as f:
            self.data: dict[str, Any] = json.load(f)
        self.id: str = self.data["id"]
        self.description: str = self.data["description"]
        self.expected: str = self.data.get("expected", "")
        self.files: list[str] = self.data.get("files", [])
        self.verification: dict[str, Any] = self.data.get("verification", {})
        self.project_dir: Path = task_dir / "project"


def discover_tasks() -> list[SWETask]:
    """发现所有 SWE 任务。"""
    tasks: list[SWETask] = []
    if not FIXTURES_ROOT.exists():
        return tasks
    for entry in sorted(FIXTURES_ROOT.iterdir()):
        if entry.is_dir() and (entry / "task.json").exists():
            tasks.append(SWETask(entry))
    return tasks


def find_task(task_id: str) -> SWETask | None:
    """按 ID 查找任务。"""
    for t in discover_tasks():
        if t.id == task_id:
            return t
    return None


# ═══════════════════════════════════════════════════════════
# Mock Backend — 用于测试 Harness 本身
# ═══════════════════════════════════════════════════════════


class MockBenchmarkBackend(Backend):
    """Mock Backend — 返回预设响应。

    用于在不需要真实 API 的情况下测试 Benchmark Harness。
    实际 Benchmark 运行时会用真实 Backend 替换。
    """

    name = "benchmark_mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._responses: list[str] = [
            "读取项目代码...",
            "我发现了一个问题，让我修复它。",
            "已修复，运行测试验证。",
            "测试通过，所有检查已完成。",
        ]

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        idx = min(self.invoke_count - 1, len(self._responses) - 1)
        return BackendResponse(
            content=self._responses[idx],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "mock-bench"},
        )

    def stream(self, request: BackendRequest) -> Any:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


# ═══════════════════════════════════════════════════════════
# 验证执行
# ═══════════════════════════════════════════════════════════


def run_verification(
    task: SWETask, workspace_dir: Path, timeout: int = 30
) -> tuple[bool, str]:
    """运行任务的验证命令。

    复制验证脚本到工作区，执行命令，检查结果。

    Returns:
        (passed: bool, output: str)
    """
    cmd = task.verification.get("command", "")
    if not cmd:
        return False, "无验证命令"

    # 复制验证脚本到工作区
    for f in task.path.iterdir():
        if f.suffix == ".py" and f.name.startswith("verify_"):
            shutil.copy2(f, workspace_dir)

    # 执行 setup
    setup = task.verification.get("setup", "")
    if setup:
        try:
            subprocess.run(
                setup, shell=True, cwd=str(workspace_dir),
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass

    # 执行验证命令
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=str(workspace_dir),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        output = r.stdout or ""
        if r.stderr:
            output += "\n[stderr]\n" + r.stderr
    except subprocess.TimeoutExpired:
        return False, f"验证超时 ({timeout}s)"
    except Exception as e:
        return False, f"验证失败: {e}"

    # 判断结果
    has_failed_output = "FAILED" in output.splitlines()[0] if output else False
    exit_ok = r.returncode == 0
    expected = task.verification.get("expected_output", "")
    pattern_ok = True
    if expected:
        import re
        pattern_ok = bool(re.search(expected, output, re.IGNORECASE))

    passed = exit_ok and pattern_ok and not has_failed_output
    return passed, output


# ═══════════════════════════════════════════════════════════
# Benchmark Runner
# ═══════════════════════════════════════════════════════════


class BenchmarkRunner:
    """Benchmark 执行器。

    使用方式:
        runner = BenchmarkRunner(backend_name="claude")
        report = runner.run_all()
        print(report.success_rate)
    """

    def __init__(
        self,
        backend_name: str = "mock",
        task_timeout: int = 120,
        max_steps: int = 20,
    ) -> None:
        self.backend_name = backend_name
        self.task_timeout = task_timeout
        self.max_steps = max_steps
        self.results: list[BenchmarkResult] = []

    # ── 单个任务执行 ────────────────────────────────────

    def run_task(self, task: SWETask) -> BenchmarkResult:
        """执行单个 SWE 任务并返回结果。"""
        logger.info("Benchmark 开始: %s (%s)", task.id, task.description[:60])
        start = time.time()

        with tempfile.TemporaryDirectory(prefix=f"zmai_bench_{task.id}_") as tmpdir:
            workspace = Path(tmpdir)
            result = self._execute(task, workspace)

        result.duration = time.time() - start
        logger.info(
            "Benchmark %s: %s (%.1fs, %d steps)",
            result.status.upper(), task.id, result.duration, result.steps,
        )
        return result

    def _execute(self, task: SWETask, workspace: Path) -> BenchmarkResult:
        """在工作区中执行任务。"""
        # 1. 复制项目
        project_dir = workspace / "project"
        shutil.copytree(task.project_dir, project_dir)
        init_file = project_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        # 2. 初始化 Runtime
        config = Config(sources=[])
        config.set("project_path", str(project_dir))
        config.set("runtime.max_iterations", self.max_steps)
        runtime = Runtime(config=config)

        # 注册 Backend
        if self.backend_name == "mock":
            runtime._gateway.register("mock", MockBenchmarkBackend, default=True)
            backend = "mock"
        else:
            runtime._gateway.register("mock", MockBenchmarkBackend, default=False)
            backend = self.backend_name

        # 3. 运行 Agent
        agent_task = (
            f"项目在 {project_dir} 目录下。\n\n"
            f"任务: {task.description}\n\n"
            f"请读取项目代码，理解问题，做出修改，然后运行测试验证。"
        )

        agent_status = ""
        agent_output = ""
        steps = 0
        error: str | None = None

        try:
            result_dict = asyncio.run(
                runtime.run(
                    agent_id=f"bench_{task.id}",
                    task=agent_task,
                    backend=backend,
                    config={"project_path": str(project_dir)},
                )
            )
            agent_status = result_dict.get("status", "unknown")
            agent_output = result_dict.get("output", "")[:500]
            steps = result_dict.get("steps", 0)
        except Exception as e:
            error = str(e)
            agent_status = "error"

        # 4. 运行验证
        verify_timeout = task.verification.get("timeout", 15)
        v_passed, v_output = run_verification(task, workspace, timeout=verify_timeout)

        # 5. 判断最终状态
        if error:
            status = "error"
        elif agent_status == "timeout":
            status = "timeout"
        elif v_passed:
            status = "pass"
        else:
            status = "fail"

        return BenchmarkResult(
            task_id=task.id,
            status=status,
            description=task.description[:100],
            steps=steps,
            agent_status=agent_status,
            agent_output=agent_output[:200],
            verification_command=task.verification.get("command", "")[:100],
            verification_output=v_output[:300],
            verification_passed=v_passed,
            error=error,
        )

    # ── 批量执行 ────────────────────────────────────────

    def run_all(self, task_ids: list[str] | None = None) -> BenchmarkReport:
        """运行全部（或指定）任务。"""
        started_at = _now()
        tasks = discover_tasks()
        if task_ids:
            tasks = [t for t in tasks if t.id in task_ids]
            missing = set(task_ids) - {t.id for t in tasks}
            for m in missing:
                logger.warning("任务未找到: %s", m)

        self.results = []
        for task in tasks:
            result = self.run_task(task)
            self.results.append(result)

        return self._build_report(started_at)

    def _build_report(self, started_at: str) -> BenchmarkReport:
        """构建报告。"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "pass")
        failed = sum(1 for r in self.results if r.status == "fail")
        timedout = sum(1 for r in self.results if r.status == "timeout")
        errors = sum(1 for r in self.results if r.status == "error")
        rate = (passed / total * 100) if total > 0 else 0.0

        return BenchmarkReport(
            started_at=started_at,
            completed_at=_now(),
            total=total,
            passed=passed,
            failed=failed,
            timedout=timedout,
            errors=errors,
            success_rate=round(rate, 1),
            results=self.results,
            backend_used=self.backend_name,
        )

    # ── 结果存储 ────────────────────────────────────────

    @staticmethod
    def save_report(report: BenchmarkReport) -> Path:
        """保存报告到 JSON 文件。"""
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = _RESULTS_FILE
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load_report() -> BenchmarkReport | None:
        """加载最新报告。"""
        if not _RESULTS_FILE.exists():
            return None
        try:
            data = json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
            results = [BenchmarkResult.from_dict(r) for r in data.get("results", [])]
            return BenchmarkReport(
                started_at=data.get("started_at", ""),
                completed_at=data.get("completed_at", ""),
                total=data.get("total", 0),
                passed=data.get("passed", 0),
                failed=data.get("failed", 0),
                timedout=data.get("timedout", 0),
                errors=data.get("errors", 0),
                success_rate=data.get("success_rate", 0.0),
                results=results,
                backend_used=data.get("backend_used", ""),
            )
        except Exception:
            return None

    @staticmethod
    def list_tasks() -> list[dict[str, Any]]:
        """列出所有可用任务。"""
        return [
            {
                "id": t.id,
                "description": t.description[:80],
                "files": t.files,
                "verification_cmd": t.verification.get("command", "")[:60],
            }
            for t in discover_tasks()
        ]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════════════════════════════════════════════════
# 终端输出格式化
# ═══════════════════════════════════════════════════════════


def format_report(report: BenchmarkReport) -> str:
    """格式化为终端可读的 Benchmark 报告。"""
    lines = [
        "=" * 60,
        "  ZMAI Benchmark Report",
        "=" * 60,
        f"  Backend:  {report.backend_used or 'N/A'}",
        f"  Started:  {report.started_at}",
        f"  Duration: {report.completed_at}",
        f"  Total:    {report.total} tasks",
        "",
        f"  {'Task':<30} {'Result':<10} {'Steps':<8} {'Time':<8}",
        "  " + "-" * 56,
    ]
    for r in report.results:
        status_tag = {
            "pass": "✅ PASS",
            "fail": "❌ FAIL",
            "timeout": "⏰ TIMEOUT",
            "error": "💥 ERROR",
        }.get(r.status, "  ????")
        lines.append(
            f"  {r.task_id:<30} {status_tag:<10} {r.steps:<8} {r.duration:.1f}s"
        )
    lines.append("  " + "-" * 56)
    lines.append("")
    lines.append(f"  Success Rate: {report.success_rate}% ({report.passed}/{report.total})")
    lines.append("=" * 60)
    return "\n".join(lines)
