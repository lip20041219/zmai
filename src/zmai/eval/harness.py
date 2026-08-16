"""Eval Harness — 自动评测执行器。

流程:
  1. 创建隔离 Workspace（临时目录）
  2. 复制初始项目
  3. 启动 Agent（Runtime + ExecutionLog）
  4. 记录 StepRecord
  5. 等待 Agent 结束
  6. 独立执行外部 verification
  7. 根据 verification 判定 PASS/FAIL/TIMEOUT/ERROR
  8. 保存 JSON 结果

核心原则:
  - PASS 必须由外部 verification 决定，Agent 自报成功不等于 PASS
  - 评测任务之间完全隔离（独立临时目录）
  - 日志失败不影响评测
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.config import Config
from zmai.runtime import Runtime

logger = logging.getLogger("zmai.eval")

# ── 常量 ──────────────────────────────────────────────────────

FIXTURES_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tests" / "fixtures" / "swe_tasks"
)

_SESSION_DIR = Path.home() / ".zmai" / "eval"
_RESULTS_FILE = _SESSION_DIR / "latest_results.json"
_LOG_DIR = _SESSION_DIR / "logs"


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════


@dataclass
class EvalResult:
    """单个评测任务的完整结果。

      PASS    — verification 通过
      FAIL    — verification 未通过
      TIMEOUT — Agent 超时 (max_steps)
      ERROR   — 执行过程中出现异常
    """

    task_id: str
    status: str  # PASS | FAIL | TIMEOUT | ERROR
    duration: float = 0.0
    steps: int = 0
    verification: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    description: str = ""
    agent_status: str = ""
    execution_log_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalResult:
        return cls(**d)


@dataclass
class EvalReport:
    """完整评测报告。"""

    started_at: str
    completed_at: str
    total: int
    passed: int
    failed: int
    timedout: int
    errors: int
    success_rate: float
    results: list[EvalResult]
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


# ═══════════════════════════════════════════════════════════════
# 评测任务
# ═══════════════════════════════════════════════════════════════


class EvalTask:
    """单个评测任务。从 fixtures/swe_tasks/ 目录加载。"""

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description[:100],
            "expected": self.expected[:100],
            "files": self.files,
            "verification_cmd": self.verification.get("command", "")[:60],
        }


def discover_tasks() -> list[EvalTask]:
    """发现所有评测任务。"""
    tasks: list[EvalTask] = []
    if not FIXTURES_ROOT.exists():
        return tasks
    for entry in sorted(FIXTURES_ROOT.iterdir()):
        if entry.is_dir() and (entry / "task.json").exists():
            tasks.append(EvalTask(entry))
    return tasks


def find_task(task_id: str) -> EvalTask | None:
    """按 ID 查找任务。"""
    for t in discover_tasks():
        if t.id == task_id:
            return t
    return None


# ═══════════════════════════════════════════════════════════════
# 验证执行
# ═══════════════════════════════════════════════════════════════


def run_verification(
    task: EvalTask, workspace_dir: Path, timeout: int = 30
) -> dict[str, Any]:
    """运行外部验证命令。

    Returns:
        {"passed": bool, "output": str, "exit_code": int, "command": str}
    """
    cmd = task.verification.get("command", "")
    if not cmd:
        return {"passed": False, "output": "无验证命令", "exit_code": -1, "command": ""}

    # 复制验证脚本到工作区
    for f in task.path.iterdir():
        if f.suffix == ".py" and f.name.startswith("verify_"):
            try:
                shutil.copy2(f, workspace_dir)
            except Exception:
                pass

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
    start = time.time()
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=str(workspace_dir),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        output = r.stdout or ""
        if r.stderr:
            output += "\n[stderr]\n" + r.stderr
        exit_code = r.returncode
    except subprocess.TimeoutExpired:
        return {
            "passed": False, "output": f"验证超时 ({timeout}s)",
            "exit_code": -1, "command": cmd,
            "duration": time.time() - start,
        }
    except Exception as e:
        return {
            "passed": False, "output": f"验证失败: {e}",
            "exit_code": -1, "command": cmd,
            "duration": time.time() - start,
        }

    # 判定结果
    exit_ok = exit_code == 0
    expected = task.verification.get("expected_output", "")
    pattern_ok = True
    if expected:
        import re
        pattern_ok = bool(re.search(expected, output, re.IGNORECASE))

    has_failed_output = "FAILED" in output.splitlines()[0] if output else False
    passed = exit_ok and pattern_ok and not has_failed_output

    return {
        "passed": passed,
        "output": output[:2000],
        "exit_code": exit_code,
        "command": cmd,
        "duration": time.time() - start,
    }


# ═══════════════════════════════════════════════════════════════
# Eval Harness
# ═══════════════════════════════════════════════════════════════


class EvalHarness:
    """评测执行器。

    使用方式:
        harness = EvalHarness(backend_name="mock")
        result = harness.run_task("task_001_fix_bug")
        report = harness.run_all()
        print(harness.format_report(report))
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

    # ── 任务发现 ────────────────────────────────────────────

    def list_tasks(self) -> list[dict[str, Any]]:
        """列出所有可用评测任务。"""
        return [t.to_dict() for t in discover_tasks()]

    # ── 单个任务执行 ────────────────────────────────────────

    def run_task(self, task_id: str) -> EvalResult:
        """执行单个评测任务。"""
        task = find_task(task_id)
        if task is None:
            return EvalResult(
                task_id=task_id,
                status="ERROR",
                error=f"任务未找到: {task_id}",
            )

        logger.info("Eval 开始: %s (%s)", task.id, task.description[:60])
        start = time.time()

        with tempfile.TemporaryDirectory(prefix=f"zmai_eval_{task.id}_") as tmpdir:
            workspace = Path(tmpdir)
            result = self._execute(task, workspace)

        result.duration = time.time() - start
        logger.info(
            "Eval %s: %s (%.1fs, %d steps)",
            result.status, task.id, result.duration, result.steps,
        )
        return result

    def _execute(self, task: EvalTask, workspace: Path) -> EvalResult:
        """在工作区中执行评测。"""
        # ── 1. 复制初始项目 ──────────────────────────────────
        project_dir = workspace / "project"
        shutil.copytree(task.project_dir, project_dir)
        init_file = project_dir / "__init__.py"
        if not init_file.exists():
            try:
                init_file.write_text("")
            except Exception:
                pass

        # ── 2. 初始化 Runtime ────────────────────────────────
        config = Config(sources=[])
        config.set("project_path", str(project_dir))
        config.set("runtime.max_iterations", self.max_steps)
        runtime = Runtime(config=config)

        # 注册 Backend
        if self.backend_name == "mock":
            from zmai.gateway.base import (
                Backend, BackendCapability, BackendResponse, TokenUsage,
            )

            class _MockEvalBackend(Backend):
                name = "mock_eval"
                def __init__(self, config=None):
                    self._config = config or {}
                    self.invoke_count = 0
                def invoke(self, request):
                    self.invoke_count += 1
                    return BackendResponse(content="处理中...")
                def stream(self, request):
                    raise NotImplementedError
                @property
                def capabilities(self):
                    return set()

            runtime._gateway.register("mock", _MockEvalBackend, default=True)

        # ── 3. 运行 Agent ────────────────────────────────────
        agent_task = (
            f"项目在 {project_dir} 目录下。\n\n"
            f"任务: {task.description}\n\n"
            f"请读取项目代码，理解问题，做出修改，然后运行测试验证。"
        )

        steps = 0
        error: str | None = None
        exec_log_path: str | None = None

        try:
            result_dict = asyncio.run(
                runtime.run(
                    agent_id=f"eval_{task.id}",
                    task=agent_task,
                    config={"project_path": str(project_dir)},
                )
            )
            steps = result_dict.get("steps", 0)
            error = result_dict.get("error")
        except Exception as e:
            error = str(e)

        # ── 4. 保存 ExecutionLog ────────────────────────────
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_dst = _LOG_DIR / f"{task.id}_{int(time.time())}.json"
            # ExecutionLog should have been persisted to workspace/.state/
            ws_log = workspace / "project" / ".state" / "execution_log.json"
            if ws_log.exists():
                shutil.copy2(ws_log, log_dst)
                exec_log_path = str(log_dst)
        except Exception:
            pass

        # ── 5. 运行外部验证 ──────────────────────────────────
        verify_timeout = task.verification.get("timeout", 15)
        vresult = run_verification(task, workspace, timeout=verify_timeout)

        # ── 6. 判定最终状态 ──────────────────────────────────
        # Agent 的自我报告不能决定 PASS
        # 只有外部 verification 通过 → PASS
        if error:
            status = "ERROR"
        elif result_dict.get("status") == "timeout":
            status = "TIMEOUT"
        elif vresult.get("passed", False):
            status = "PASS"
        else:
            status = "FAIL"

        return EvalResult(
            task_id=task.id,
            status=status,
            description=task.description[:200],
            steps=steps,
            error=error,
            agent_status=result_dict.get("status", "unknown"),
            verification=vresult,
            execution_log_path=exec_log_path,
        )

    # ── 批量执行 ────────────────────────────────────────────

    def run_all(self, task_ids: list[str] | None = None) -> EvalReport:
        """运行全部（或指定）评测任务。"""
        started_at = _now()
        tasks = discover_tasks()
        if task_ids:
            tasks = [t for t in tasks if t.id in task_ids]
            missing = set(task_ids) - {t.id for t in tasks}
            for m in missing:
                logger.warning("评测任务未找到: %s", m)

        results: list[EvalResult] = []
        for task in tasks:
            result = self.run_task(task.id)
            results.append(result)

        return self._build_report(started_at, results)

    def _build_report(self, started_at: str, results: list[EvalResult]) -> EvalReport:
        total = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        timedout = sum(1 for r in results if r.status == "TIMEOUT")
        errors = sum(1 for r in results if r.status == "ERROR")
        rate = (passed / total * 100) if total > 0 else 0.0

        return EvalReport(
            started_at=started_at,
            completed_at=_now(),
            total=total,
            passed=passed,
            failed=failed,
            timedout=timedout,
            errors=errors,
            success_rate=round(rate, 1),
            results=results,
            backend_used=self.backend_name,
        )

    # ── 结果存储 ────────────────────────────────────────────

    @staticmethod
    def save_report(report: EvalReport) -> Path:
        """保存评测报告。"""
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        _RESULTS_FILE.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return _RESULTS_FILE

    @staticmethod
    def load_report() -> EvalReport | None:
        """加载最新评测报告。"""
        if not _RESULTS_FILE.exists():
            return None
        try:
            data = json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
            results = [EvalResult.from_dict(r) for r in data.get("results", [])]
            return EvalReport(
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

    # ── 格式化输出 ──────────────────────────────────────────

    @staticmethod
    def format_report(report: EvalReport) -> str:
        """格式化为终端可读报告。"""
        lines = [
            "=" * 60,
            "  ZMAI Eval Report",
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
            tag = {
                "PASS": "✅ PASS",
                "FAIL": "❌ FAIL",
                "TIMEOUT": "⏰ TIMEOUT",
                "ERROR": "💥 ERROR",
            }.get(r.status, "  ????")
            lines.append(
                f"  {r.task_id:<30} {tag:<10} {r.steps:<8} {r.duration:.1f}s"
            )
        lines.append("  " + "-" * 56)
        lines.append("")
        lines.append(
            f"  Success Rate: {report.success_rate}%"
            f" ({report.passed}/{report.total})"
        )
        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def format_list(tasks: list[dict[str, Any]]) -> str:
        """格式化任务列表。"""
        if not tasks:
            return "  (无可用评测任务)"
        lines = [
            f"\n  {'Task ID':<30} {'Description':<50} {'Files'}",
            f"  {'-' * 90}",
        ]
        for t in tasks:
            files = ", ".join(t.get("files", []))
            desc = t.get("description", "")[:50]
            lines.append(f"  {t['id']:<30} {desc:<50} {files}")
        lines.append(f"\n  {len(tasks)} task(s) available\n")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
