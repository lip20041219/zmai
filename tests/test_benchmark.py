"""Tests for zmai.eval.benchmark — BenchmarkRunner, TaskLoader, ResultCollector, ScoreReporter."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zmai.eval import (
    BenchmarkRunner,
    BenchmarkTask,
    BenchmarkStats,
    ResultCollector,
    ScoreReporter,
    StepTokenUsage,
    TaskLoader,
)
from zmai.eval.harness import EvalResult


class TestTaskLoader:
    def test_load_custom_default(self):
        loader = TaskLoader()
        tasks = loader.load("custom")
        assert len(tasks) >= 5

    def test_load_custom_with_path(self):
        from zmai.eval.harness import FIXTURES_ROOT
        loader = TaskLoader()
        tasks = loader.load("custom", path=str(FIXTURES_ROOT))
        assert len(tasks) >= 5

    def test_load_custom_single_task_dir(self):
        from zmai.eval.harness import FIXTURES_ROOT
        task_dir = FIXTURES_ROOT / "task_001_fix_bug"
        loader = TaskLoader()
        tasks = loader.load("custom", path=str(task_dir))
        assert len(tasks) == 1
        assert tasks[0].id == "task_001_fix_bug"

    def test_load_custom_nonexistent(self):
        loader = TaskLoader()
        with pytest.raises(FileNotFoundError):
            loader.load("custom", path="/nonexistent/path")

    def test_load_custom_json_file(self):
        data = [
            {"id": "json_001", "description": "add function",
             "prompt": "def add(a,b): return a+b", "test": "assert add(1,2)==3",
             "entry_point": "add"},
            {"id": "json_002", "description": "sub function",
             "prompt": "def sub(a,b): return a-b", "test": "assert sub(5,3)==2",
             "entry_point": "sub"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            tmp_path = f.name
        try:
            loader = TaskLoader()
            tasks = loader.load("custom", path=tmp_path)
            assert len(tasks) == 2
            assert tasks[0].id == "json_001"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_load_unsupported_source(self):
        loader = TaskLoader()
        with pytest.raises(ValueError, match="不支持"):
            loader.load("unknown_source")

    def test_benchmark_task_to_dict(self):
        task = BenchmarkTask(id="test_001", source="custom", description="test task", repo="owner/repo", verification={"command": "pytest"})
        d = task.to_dict()
        assert d["id"] == "test_001"
        assert d["source"] == "custom"
        assert "verification_cmd" in d


class TestResultCollector:
    def test_collect_empty(self):
        collector = ResultCollector()
        stats = collector.collect([])
        assert stats.total == 0
        assert stats.success_rate == 0.0

    def test_collect_all_pass(self):
        results = [
            EvalResult(task_id="t1", status="PASS", duration=10.0, steps=5),
            EvalResult(task_id="t2", status="PASS", duration=20.0, steps=8),
        ]
        collector = ResultCollector()
        stats = collector.collect(results)
        assert stats.total == 2
        assert stats.passed == 2
        assert stats.success_rate == 100.0

    def test_collect_mixed_status(self):
        results = [
            EvalResult(task_id="t1", status="PASS", duration=5.0, steps=3),
            EvalResult(task_id="t2", status="FAIL", duration=10.0, steps=5),
            EvalResult(task_id="t3", status="TIMEOUT", duration=30.0, steps=20),
            EvalResult(task_id="t4", status="ERROR", duration=2.0, steps=0),
            EvalResult(task_id="t5", status="PASS", duration=8.0, steps=4),
        ]
        collector = ResultCollector()
        stats = collector.collect(results)
        assert stats.total == 5
        assert stats.passed == 2
        assert stats.failed == 1
        assert stats.timedout == 1
        assert stats.errors == 1
        assert stats.success_rate == 40.0

    def test_collect_with_token_logs(self):
        results = [EvalResult(task_id="t1", status="PASS", duration=5.0)]
        logs = [
            StepTokenUsage(step_id=1, phase="tool_call", input_tokens=500, output_tokens=200, duration_ms=1000),
            StepTokenUsage(step_id=2, phase="tool_result", input_tokens=100, output_tokens=50, duration_ms=500),
        ]
        collector = ResultCollector(model="deepseek-chat")
        stats = collector.collect(results, token_logs=logs)
        assert stats.total_input_tokens == 600
        assert stats.total_output_tokens == 250
        assert stats.estimated_cost_usd > 0
        assert stats.model_used == "deepseek-chat"

    def test_collect_per_task_summary(self):
        results = [
            EvalResult(task_id="t1", status="PASS", duration=5.0, steps=3, error=None, agent_status="completed"),
            EvalResult(task_id="t2", status="FAIL", duration=10.0, steps=5, error="verification failed", agent_status="completed"),
        ]
        collector = ResultCollector()
        stats = collector.collect(results)
        assert len(stats.per_task) == 2
        assert stats.per_task[1]["error"] == "verification failed"

    def test_extract_token_usage_nonexistent_dir(self):
        logs = ResultCollector.extract_token_usage("/nonexistent/logs")
        assert logs == []

    def test_estimate_cost_default_model(self):
        from zmai.eval.collector import _estimate_cost
        cost = _estimate_cost(1000, 500)
        assert cost > 0


class _Fixture:
    @staticmethod
    def make_stats():
        return BenchmarkStats(
            total=5, passed=3, failed=1, timedout=1, errors=0,
            success_rate=60.0, pass_at_1=60.0,
            latency_min=2.0, latency_max=30.0, latency_avg=11.0, latency_median=8.0,
            total_duration=55.0,
            total_input_tokens=5000, total_output_tokens=3000,
            avg_input_tokens=250.0, avg_output_tokens=150.0,
            estimated_cost_usd=0.005, model_used="mock",
            per_task=[
                {"task_id": "t1", "status": "PASS", "duration_s": 5.0, "steps": 3, "error": "", "agent_status": "completed"},
                {"task_id": "t2", "status": "PASS", "duration_s": 8.0, "steps": 4, "error": "", "agent_status": "completed"},
                {"task_id": "t3", "status": "PASS", "duration_s": 10.0, "steps": 5, "error": "", "agent_status": "completed"},
                {"task_id": "t4", "status": "FAIL", "duration_s": 15.0, "steps": 7, "error": "verification failed", "agent_status": "completed"},
                {"task_id": "t5", "status": "TIMEOUT", "duration_s": 30.0, "steps": 20, "error": "", "agent_status": "timeout"},
            ],
            token_breakdown=[],
        )


class TestScoreReporter:
    def test_json_output(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        text = reporter.to_json(stats)
        data = json.loads(text)
        assert data["summary"]["total"] == 5
        assert data["summary"]["passed"] == 3
        assert data["summary"]["success_rate"] == 60.0
        assert len(data["per_task"]) == 5

    def test_json_roundtrip(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            tmp_path = f.name
        try:
            reporter.to_json(stats, path=tmp_path)
            data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
            assert data["summary"]["total"] == 5
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_markdown_output(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        text = reporter.to_markdown(stats)
        assert "# ZMAI Benchmark Report" in text
        assert "## Summary" in text
        assert "60.0%" in text

    def test_markdown_to_file(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            tmp_path = f.name
        try:
            reporter.to_markdown(stats, path=tmp_path)
            content = Path(tmp_path).read_text(encoding="utf-8")
            assert "# ZMAI Benchmark Report" in content
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_csv_output(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        text = reporter.to_csv(stats)
        assert "Task ID" in text
        assert "Success Rate" in text
        assert "60.0%" in text

    def test_csv_to_file(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            tmp_path = f.name
        try:
            reporter.to_csv(stats, path=tmp_path)
            content = Path(tmp_path).read_text(encoding="utf-8")
            assert "Task ID" in content
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_format_console(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        text = reporter.format_console(stats)
        assert "Success Rate:" in text
        assert "60.0%" in text
        assert "Pass@1:" in text

    def test_format_console_empty(self):
        stats = BenchmarkStats()
        reporter = ScoreReporter()
        text = reporter.format_console(stats)
        assert "Success Rate:" in text
        assert "0.0%" in text

    def test_all_format_consistency(self):
        stats = _Fixture.make_stats()
        reporter = ScoreReporter()
        json_text = reporter.to_json(stats)
        md_text = reporter.to_markdown(stats)
        csv_text = reporter.to_csv(stats)
        assert "60.0" in json_text
        assert "60.0%" in md_text
        assert "60.0%" in csv_text


class TestBenchmarkRunner:
    def test_run_custom_default(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom()
        assert stats.total >= 5
        assert stats.passed + stats.failed + stats.timedout + stats.errors == stats.total

    def test_run_custom_with_task_ids(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom(task_ids=["task_001_fix_bug", "task_002_modify_function"])
        assert stats.total == 2

    def test_run_custom_with_path(self):
        from zmai.eval.harness import FIXTURES_ROOT
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom(path=str(FIXTURES_ROOT))
        assert stats.total >= 5

    def test_format_console_from_runner(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom(task_ids=["task_001_fix_bug"])
        text = runner.format_console(stats)
        assert "Success Rate" in text

    def test_report_to_json(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom(task_ids=["task_001_fix_bug"])
        text = runner.report_to_json(stats)
        data = json.loads(text)
        assert data["summary"]["total"] == 1

    def test_report_to_markdown(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom(task_ids=["task_001_fix_bug"])
        text = runner.report_to_markdown(stats)
        assert "# ZMAI Benchmark Report" in text

    def test_report_to_csv(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_custom(task_ids=["task_001_fix_bug"])
        text = runner.report_to_csv(stats)
        assert "Task ID" in text


class TestBenchmarkStats:
    def test_to_dict_contains_all_keys(self):
        stats = BenchmarkStats(total=5, passed=3, success_rate=60.0, pass_at_1=60.0)
        d = stats.to_dict()
        assert d["total"] == 5
        assert d["passed"] == 3
        assert d["success_rate"] == 60.0
        assert "latency" in d
        assert "tokens" in d

    def test_default_values(self):
        stats = BenchmarkStats()
        assert stats.total == 0
        assert stats.passed == 0
        assert stats.success_rate == 0.0


class TestCLIIntegration:
    def test_run_eval_custom_cmd(self):
        from zmai.cli.eval_cmd import _cmd_custom
        _cmd_custom(["--backend", "mock", "--max-steps", "2"])

    def test_run_eval_list_cmd(self):
        from zmai.cli.eval_cmd import _cmd_list
        _cmd_list([])

    def test_run_eval_custom_with_output_json(self):
        from zmai.cli.eval_cmd import _cmd_custom
        _cmd_custom(["--output", "json", "--backend", "mock", "--max-steps", "2"])

    def test_run_eval_custom_with_output_markdown(self):
        from zmai.cli.eval_cmd import _cmd_custom
        _cmd_custom(["--output", "markdown", "--backend", "mock", "--max-steps", "2"])

    def test_run_eval_custom_with_output_csv(self):
        from zmai.cli.eval_cmd import _cmd_custom
        _cmd_custom(["--output", "csv", "--backend", "mock", "--max-steps", "2"])

    def test_run_eval_custom_with_output_file(self):
        from zmai.cli.eval_cmd import _cmd_custom
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            tmp_path = f.name
        try:
            _cmd_custom(["--output", "json", "--output-file", tmp_path, "--backend", "mock", "--max-steps", "2"])
            content = Path(tmp_path).read_text(encoding="utf-8")
            data = json.loads(content)
            assert "summary" in data
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_parse_common_args_defaults(self):
        from zmai.cli.eval_cmd import _parse_common_args
        args = _parse_common_args([])
        assert args["output"] == "console"
        assert args["output_file"] is None

    def test_parse_common_args_custom(self):
        from zmai.cli.eval_cmd import _parse_common_args
        args = _parse_common_args(["--output", "json", "--output-file", "report.json", "--backend", "deepseek", "--max-steps", "50"])
        assert args["output"] == "json"
        assert args["output_file"] == "report.json"
        assert args["backend"] == "deepseek"
        assert args["max_steps"] == 50

    def test_list_sources_help(self):
        text = BenchmarkRunner.list_sources_help()
        assert "swebench" in text
        assert "humaneval" in text
        assert "custom" in text

    def test_swebench_not_crash(self):
        runner = BenchmarkRunner(backend="mock", max_steps=2)
        stats = runner.run_swebench(max_instances=1)
        assert stats is not None
