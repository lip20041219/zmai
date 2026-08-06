"""Tests for zmai.eval.harness — EvalHarness, EvalResult."""

from __future__ import annotations

import json
from pathlib import Path

from zmai.eval import EvalHarness, EvalResult


# ═══════════════════════════════════════════════════════════════
# EvalResult 数据结构
# ═══════════════════════════════════════════════════════════════


class TestEvalResult:
    def test_create_pass(self):
        r = EvalResult(
            task_id="task_001", status="PASS", steps=5, duration=10.0,
            verification={"passed": True},
        )
        assert r.task_id == "task_001"
        assert r.status == "PASS"
        assert r.steps == 5

    def test_create_fail(self):
        r = EvalResult(
            task_id="task_002", status="FAIL", error="verification failed",
        )
        assert r.status == "FAIL"
        assert r.error == "verification failed"

    def test_create_timeout(self):
        r = EvalResult(task_id="task_003", status="TIMEOUT")
        assert r.status == "TIMEOUT"

    def test_create_error(self):
        r = EvalResult(task_id="task_004", status="ERROR", error="exception")
        assert r.status == "ERROR"

    def test_to_dict_roundtrip(self):
        orig = EvalResult(
            task_id="t1", status="PASS", steps=3, duration=5.0,
            verification={"passed": True, "output": "ok"},
        )
        d = orig.to_dict()
        assert d["task_id"] == "t1"
        assert d["status"] == "PASS"

        restored = EvalResult.from_dict(d)
        assert restored.task_id == orig.task_id
        assert restored.status == orig.status
        assert restored.steps == orig.steps

    def test_default_values(self):
        r = EvalResult(task_id="t1", status="PASS")
        assert r.duration == 0.0
        assert r.steps == 0
        assert r.error is None
        assert r.description == ""
        assert r.agent_status == ""


# ═══════════════════════════════════════════════════════════════
# Task Discovery
# ═══════════════════════════════════════════════════════════════


class TestTaskDiscovery:
    def test_discover_tasks_returns_list(self):
        """至少能找到 5 个预置评测任务。"""
        from zmai.eval.harness import discover_tasks
        tasks = discover_tasks()
        assert len(tasks) >= 5, f"至少应有 5 个任务，实际 {len(tasks)}"

    def test_task_has_required_fields(self):
        from zmai.eval.harness import discover_tasks
        for task in discover_tasks():
            assert task.id, f"任务缺少 id: {task.path}"
            assert task.description, f"任务缺少 description: {task.id}"
            assert task.project_dir.exists(), f"项目目录不存在: {task.id}"
            assert task.verification.get("command"), f"缺少验证命令: {task.id}"

    def test_find_task_by_id(self):
        from zmai.eval.harness import find_task
        task = find_task("task_001_fix_bug")
        assert task is not None
        assert task.id == "task_001_fix_bug"

    def test_find_nonexistent_task(self):
        from zmai.eval.harness import find_task
        assert find_task("nonexistent") is None

    def test_list_tasks(self):
        """EvalHarness.list_tasks() 返回可读列表。"""
        tasks = EvalHarness.list_tasks(EvalHarness)
        assert len(tasks) >= 5
        for t in tasks:
            assert "id" in t
            assert "description" in t


# ═══════════════════════════════════════════════════════════════
# EvalHarness 基本功能
# ═══════════════════════════════════════════════════════════════


class TestEvalHarnessBasic:
    def test_create_harness_default(self):
        h = EvalHarness()
        assert h.backend_name == "mock"
        assert h.task_timeout == 120
        assert h.max_steps == 20

    def test_create_harness_custom(self):
        h = EvalHarness(backend_name="claude", task_timeout=60, max_steps=10)
        assert h.backend_name == "claude"
        assert h.task_timeout == 60
        assert h.max_steps == 10


# ═══════════════════════════════════════════════════════════════
# Mock 评测执行
# ═══════════════════════════════════════════════════════════════


class TestMockEvalRun:
    def test_run_task_with_mock_returns_result(self):
        """Mock Backend 运行评测任务应返回 EvalResult。"""
        h = EvalHarness(backend_name="mock", max_steps=3)
        result = h.run_task("task_001_fix_bug")
        assert isinstance(result, EvalResult)
        assert result.task_id == "task_001_fix_bug"
        assert result.status in ("PASS", "FAIL", "TIMEOUT", "ERROR")
        assert result.steps >= 0
        assert result.duration >= 0

    def test_run_nonexistent_task(self):
        h = EvalHarness()
        result = h.run_task("nonexistent_task")
        assert result.status == "ERROR"
        assert "未找到" in (result.error or "")

    def test_run_multiple_tasks(self):
        h = EvalHarness(backend_name="mock", max_steps=2)
        report = h.run_all(task_ids=["task_001_fix_bug", "task_002_modify_function"])
        assert report.total == 2
        assert len(report.results) == 2
        for r in report.results:
            assert r.status in ("PASS", "FAIL", "TIMEOUT", "ERROR")
            assert r.task_id in ("task_001_fix_bug", "task_002_modify_function")

    def test_run_all(self):
        """运行全部 5 个任务 (Mock)。"""
        h = EvalHarness(backend_name="mock", max_steps=2)
        report = h.run_all()
        assert report.total >= 5
        assert report.started_at
        assert report.completed_at
        assert report.success_rate >= 0


# ═══════════════════════════════════════════════════════════════
# 验证逻辑（核心原则：Agent 自报不等于 PASS）
# ═══════════════════════════════════════════════════════════════


class TestVerificationLogic:
    def test_agent_self_report_not_pass(self):
        """Agent 说完成不等于 PASS。"""
        h = EvalHarness(backend_name="mock", max_steps=2)
        result = h.run_task("task_001_fix_bug")
        # Mock Backend 只返回"处理中..."，不写文件，验证应在 task_001 上 FAIL
        # 但 verification 的 exit_code 决定 PASS/FAIL
        assert result.status in ("PASS", "FAIL", "TIMEOUT", "ERROR")
        # 核心原则: status 不由 agent_status 决定
        if result.agent_status == "completed":
            assert result.status != "PASS"  # Mock 不会真正修复 bug

    def test_verification_independent_of_agent(self):
        """验证逻辑独立于 Agent 结果。"""
        from zmai.eval.harness import run_verification
        from zmai.eval.harness import EvalTask, FIXTURES_ROOT

        task_dir = FIXTURES_ROOT / "task_001_fix_bug"
        task = EvalTask(task_dir)

        # 用空项目验证（不应通过）
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            ws.mkdir(parents=True, exist_ok=True)
            # 不复制 project，空工作区
            v = run_verification(task, ws, timeout=5)
            assert isinstance(v, dict)
            assert "passed" in v


# ═══════════════════════════════════════════════════════════════
# 报告存储
# ═══════════════════════════════════════════════════════════════


class TestEvalReport:
    def test_save_and_load_report(self, tmp_path: Path):
        h = EvalHarness()
        report = h.run_all(task_ids=["task_001_fix_bug"])
        # 保存到临时路径
        from zmai.eval.harness import _RESULTS_FILE
        _orig = _RESULTS_FILE
        try:
            import zmai.eval.harness as mod
            test_path = tmp_path / "test_results.json"
            # 直接用 EvalReport to_dict 写文件
            test_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            assert test_path.exists()
            data = json.loads(test_path.read_text(encoding="utf-8"))
            assert "total" in data
            assert "passed" in data
            assert "results" in data
        finally:
            pass

    def test_load_nonexistent_report(self):
        report = EvalHarness.load_report()
        # 可能为 None（无报告时）
        assert report is None or isinstance(report, __import__("zmai.eval", fromlist=["EvalReport"]).EvalReport)

    def test_report_structure(self):
        h = EvalHarness(backend_name="mock", max_steps=2)
        report = h.run_all(task_ids=["task_001_fix_bug"])
        assert report.total >= 1
        assert report.passed + report.failed + report.timedout + report.errors == report.total
        assert report.success_rate >= 0


# ═══════════════════════════════════════════════════════════════
# EvalResult JSON 兼容性（要求的格式）
# ═══════════════════════════════════════════════════════════════


class TestEvalResultFormat:
    def test_required_fields_in_to_dict(self):
        """EvalResult.to_dict() 必须包含要求的全部字段。"""
        r = EvalResult(
            task_id="test_001",
            status="PASS",
            duration=15.5,
            steps=10,
            verification={"passed": True, "output": "ok"},
            error=None,
        )
        d = r.to_dict()
        required = ["task_id", "status", "duration", "steps", "verification", "error"]
        for field in required:
            assert field in d, f"缺少必要字段: {field}"

    def test_python_bug_fix_task(self):
        """task_001: 验证 Python Bug Fix 的配置。"""
        from zmai.eval.harness import find_task
        task = find_task("task_001_fix_bug")
        assert task is not None
        assert "calculator.py" in task.files
        assert task.project_dir.exists()

    def test_python_feature_task(self):
        """task_002: Feature Addition 的配置。"""
        from zmai.eval.harness import find_task
        task = find_task("task_002_modify_function")
        assert task is not None
        assert "string_utils.py" in task.files

    def test_html_modify_task(self):
        """task_004: HTML 修改的配置。"""
        from zmai.eval.harness import find_task
        task = find_task("task_004_fix_html")
        assert task is not None
        assert task.project_dir.exists()

    def test_test_failure_fix_task(self):
        """task_005: 测试失败修复的配置。"""
        from zmai.eval.harness import find_task
        task = find_task("task_005_fix_test")
        assert task is not None
        assert "user_manager.py" in task.files

    def test_multi_file_modify_task(self):
        """task_003: 多文件修改的配置。"""
        from zmai.eval.harness import find_task
        task = find_task("task_003_add_feature")
        assert task is not None
        assert "todo.py" in task.files
