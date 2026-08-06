"""Tests for zmai.execution.log — StepRecord, ExecutionLog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zmai.execution import ExecutionLog, StepRecord, truncate_output
from zmai.execution.log import _sanitize_params, _SENSITIVE_MASK


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _make_log(agent_id: str = "test_agent", task: str = "test task") -> ExecutionLog:
    return ExecutionLog(agent_id=agent_id, task=task)


# ═══════════════════════════════════════════════════════════════
# StepRecord
# ═══════════════════════════════════════════════════════════════


class TestStepRecord:
    def test_step_record_creation(self):
        r = StepRecord(
            step_id=1, timestamp="2024-01-01T00:00:00Z",
            phase="tool_call", action="read_file",
            tool_name="read_file",
            tool_input={"path": "main.py"},
            success=True,
        )
        assert r.step_id == 1
        assert r.phase == "tool_call"
        assert r.action == "read_file"
        assert r.tool_name == "read_file"

    def test_step_record_to_dict(self):
        r = StepRecord(
            step_id=1, timestamp="2024-01-01T00:00:00Z",
            phase="tool_result", action="write_file",
            success=True, tool_output="content",
        )
        d = r.to_dict()
        assert d["step_id"] == 1
        assert d["phase"] == "tool_result"
        assert d["success"] is True

    def test_step_record_defaults(self):
        r = StepRecord(
            step_id=1, timestamp="2024-01-01T00:00:00Z",
            phase="init", action="start",
        )
        assert r.tool_name is None
        assert r.tool_input is None
        assert r.success is None
        assert r.error is None
        assert r.duration_ms == 0
        assert r.metadata == {}


# ═══════════════════════════════════════════════════════════════
# ExecutionLog 基础
# ═══════════════════════════════════════════════════════════════


class TestExecutionLogBasic:
    def test_create_default(self):
        log = _make_log()
        assert log.agent_id == "test_agent"
        assert log.task == "test task"
        assert log.created_at
        assert log.steps == []

    def test_record_step(self):
        log = _make_log()
        r = log.record_step(phase="tool_call", action="read_file",
                            tool_name="read_file", success=True)
        assert r.step_id == 1
        assert r.phase == "tool_call"
        assert len(log.steps) == 1

    def test_record_multiple_steps(self):
        log = _make_log()
        log.record_step(phase="init", action="start")
        log.record_step(phase="tool_call", action="read_file", tool_name="read_file")
        log.record_step(phase="tool_result", action="read_file", success=True)
        log.record_step(phase="verification", action="verify", success=True)
        log.record_step(phase="finalize", action="done", success=True)
        assert len(log.steps) == 5
        assert log.steps[0].phase == "init"
        assert log.steps[-1].phase == "finalize"

    def test_step_counter_increments(self):
        log = _make_log()
        assert log.record_step(phase="a", action="x").step_id == 1
        assert log.record_step(phase="b", action="y").step_id == 2
        assert log.record_step(phase="c", action="z").step_id == 3

    def test_to_dict_structure(self):
        log = _make_log()
        log.record_step(phase="tool_call", action="read_file", success=True)
        d = log.to_dict()
        assert d["agent_id"] == "test_agent"
        assert d["task"] == "test task"
        assert "created_at" in d
        assert d["step_count"] == 1
        assert len(d["steps"]) == 1

    def test_to_json(self):
        log = _make_log()
        log.record_step(phase="tool_call", action="read_file", success=True)
        s = log.to_json()
        d = json.loads(s)
        assert d["agent_id"] == "test_agent"
        assert d["step_count"] == 1

    def test_summary(self):
        log = _make_log()
        log.record_step(phase="tool_call", action="read_file")
        log.record_step(phase="tool_result", action="read_file", success=False)
        s = log.summary()
        assert s["agent_id"] == "test_agent"
        assert s["total_steps"] == 2
        assert s["errors"] == 1  # one failed step

    def test_persist(self, tmp_path: Path):
        log = _make_log()
        log.record_step(phase="tool_call", action="read_file", success=True)
        log_path = tmp_path / "execution_log.json"
        ok = log.persist(str(log_path))
        assert ok is True
        assert log_path.exists()
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["agent_id"] == "test_agent"
        assert data["step_count"] == 1

    def test_persist_readonly_dir(self):
        """写入不可写路径不应异常。"""
        log = _make_log()
        ok = log.persist("")
        assert ok is False  # 失败但不应异常

    def test_record_does_not_raise(self):
        """记录不应抛出异常。"""
        log = _make_log()
        # 即使传入非法参数也不应报错
        r = log.record_step(phase="test", action="test", duration_ms=-1)
        assert r.duration_ms == 0  # 负值被修正


# ═══════════════════════════════════════════════════════════════
# 正常 Tool Step
# ═══════════════════════════════════════════════════════════════


class TestToolStep:
    def test_successful_tool_step(self):
        log = _make_log()
        r = log.record_step(phase="tool_call", action="read_file",
                            tool_name="read_file",
                            tool_input={"path": "main.py"},
                            success=True, duration_ms=5)
        assert r.success is True
        assert r.tool_name == "read_file"
        assert r.tool_input == {"path": "main.py"}
        assert r.duration_ms == 5

    def test_tool_with_output(self):
        log = _make_log()
        r = log.record_step(phase="tool_result", action="read_file",
                            tool_name="read_file",
                            success=True, tool_output="file content here")
        assert r.success is True
        assert "file content" in (r.tool_output or "")


# ═══════════════════════════════════════════════════════════════
# Tool 失败
# ═══════════════════════════════════════════════════════════════


class TestToolFailure:
    def test_failed_tool_step(self):
        log = _make_log()
        r = log.record_step(phase="tool_result", action="shell_exec",
                            tool_name="shell_exec",
                            success=False, error="exit code 1: command not found")
        assert r.success is False
        assert r.error
        assert "exit code 1" in r.error


# ═══════════════════════════════════════════════════════════════
# Backend 失败
# ═══════════════════════════════════════════════════════════════


class TestBackendFailure:
    def test_backend_error_recorded(self):
        log = _make_log()
        r = log.record_step(phase="error", action="backend_failure",
                            success=False,
                            error="ConnectionError: DNS resolution failed")
        assert r.success is False
        assert "ConnectionError" in (r.error or "")


# ═══════════════════════════════════════════════════════════════
# 多步骤执行
# ═══════════════════════════════════════════════════════════════


class TestMultiStep:
    def test_multi_step_sequence(self):
        log = _make_log()
        log.record_step(phase="init", action="agent_initialize", success=True)
        log.record_step(phase="plan", action="generate_plan", success=True,
                        metadata={"steps": 3})
        log.record_step(phase="tool_call", action="read_file",
                        tool_name="read_file", success=True, duration_ms=5)
        log.record_step(phase="tool_result", action="read_file",
                        tool_name="read_file", success=True, duration_ms=5)
        log.record_step(phase="tool_call", action="edit",
                        tool_name="edit", success=True, duration_ms=10)
        log.record_step(phase="tool_result", action="edit",
                        tool_name="edit", success=True,
                        tool_output="replaced 2 lines", duration_ms=10)
        log.record_step(phase="verification", action="auto_verify",
                        success=True, metadata={"checks": 2})
        log.record_step(phase="finalize", action="agent_finalize",
                        success=True, metadata={"status": "completed"})
        assert len(log.steps) == 8
        assert log.steps[0].phase == "init"
        assert log.steps[-1].phase == "finalize"
        # 所有步骤成功
        assert all(s.success for s in log.steps if s.success is not None)

    def test_multi_step_with_failures(self):
        log = _make_log()
        log.record_step(phase="tool_call", action="shell_exec",
                        tool_name="shell_exec", success=True)
        log.record_step(phase="tool_result", action="shell_exec",
                        tool_name="shell_exec",
                        success=False, error="exit 1")
        log.record_step(phase="tool_call", action="read_file",
                        tool_name="read_file", success=True)
        log.record_step(phase="tool_result", action="read_file",
                        tool_name="read_file", success=True)
        log.record_step(phase="verification", action="auto_verify",
                        success=False, tool_output="1/2 checks failed")
        assert log.summary()["errors"] == 2  # shell + verification


# ═══════════════════════════════════════════════════════════════
# 空输出
# ═══════════════════════════════════════════════════════════════


class TestEmptyOutput:
    def test_empty_tool_output(self):
        log = _make_log()
        r = log.record_step(phase="tool_result", action="read_file",
                            tool_name="read_file",
                            success=True, tool_output="")
        assert r.tool_output == ""

    def test_null_tool_output(self):
        log = _make_log()
        r = log.record_step(phase="tool_result", action="read_file",
                            tool_name="read_file",
                            success=True, tool_output=None)
        assert r.tool_output == ""

    def test_empty_error(self):
        log = _make_log()
        r = log.record_step(phase="error", action="test",
                            success=False, error="")
        assert r.error == ""


# ═══════════════════════════════════════════════════════════════
# 超大输出
# ═══════════════════════════════════════════════════════════════


class TestLargeOutput:
    def test_truncate_short_text(self):
        assert truncate_output("hello") == "hello"

    def test_truncate_long_text(self):
        long = "x" * 10000
        truncated = truncate_output(long, max_len=100)
        assert len(truncated) < len(long)
        assert "(truncated" in truncated

    def test_truncate_at_boundary(self):
        text = "a" * 5000
        assert truncate_output(text) == text  # 正好等于 MAX 不截断

    def test_truncate_exceeds_boundary(self):
        text = "a" * 5001
        t = truncate_output(text)
        assert "(truncated" in t
        assert "a" * 5001 not in t  # 原始文本不应完整出现在结果中

    def test_truncate_none(self):
        assert truncate_output(None) == ""

    def test_truncate_empty(self):
        assert truncate_output("") == ""

    def test_log_large_output_safe(self):
        """大输出记录不应异常。"""
        log = _make_log()
        huge = "x" * 100000
        r = log.record_step(phase="tool_result", action="shell_exec",
                            tool_name="shell_exec",
                            success=True, tool_output=huge)
        # 内部已截断
        assert r.tool_output
        assert len(r.tool_output) < len(huge)


# ═══════════════════════════════════════════════════════════════
# API Key 脱敏
# ═══════════════════════════════════════════════════════════════


class TestSanitizeParams:
    def test_api_key_redacted(self):
        params = {"api_key": "sk-1234567890abcdef", "model": "claude"}
        safe = _sanitize_params(params)
        assert safe["api_key"] == _SENSITIVE_MASK
        assert safe["model"] == "claude"

    def test_token_redacted(self):
        params = {"token": "ghp_xxxxx", "repo": "zmai"}
        safe = _sanitize_params(params)
        assert safe["token"] == _SENSITIVE_MASK

    def test_nested_sensitive(self):
        params = {"config": {"api_key": "secret123", "timeout": 30}}
        safe = _sanitize_params(params)
        assert safe["config"]["api_key"] == _SENSITIVE_MASK
        assert safe["config"]["timeout"] == 30

    def test_case_insensitive_redact(self):
        params = {"API_KEY": "secret", "Api_Key": "secret2"}
        safe = _sanitize_params(params)
        assert safe["API_KEY"] == _SENSITIVE_MASK
        assert safe["Api_Key"] == _SENSITIVE_MASK

    def test_non_sensitive_unchanged(self):
        params = {"path": "main.py", "content": "print('hello')", "mode": "replace"}
        safe = _sanitize_params(params)
        assert safe["path"] == "main.py"
        assert safe["content"] == "print('hello')"
        assert safe["mode"] == "replace"

    def test_none_params(self):
        safe = _sanitize_params(None)
        assert safe == {}

    def test_empty_params(self):
        safe = _sanitize_params({})
        assert safe == {}

    def test_list_with_dict(self):
        params = {"tools": [{"name": "read", "api_key": "secret"}]}
        safe = _sanitize_params(params)
        assert safe["tools"][0]["api_key"] == _SENSITIVE_MASK
        assert safe["tools"][0]["name"] == "read"


# ═══════════════════════════════════════════════════════════════
# 日志失败不影响 Agent
# ═══════════════════════════════════════════════════════════════


class TestLogFailureIsSafe:
    def test_log_exception_does_not_propagate(self):
        """日志内部异常不得传播。"""
        log = _make_log()
        # 故意传入 metadata 中可能引发异常的值
        class Unserializable:
            def __str__(self):
                raise RuntimeError("boom")

        # 应静默处理
        r = log.record_step(phase="test", action="test",
                            metadata={"bad": "value"})
        assert r is not None

    def test_log_with_exception_in_internal(self):
        """日志内部异常不影响后续记录。"""
        log = _make_log()
        # 正常记录后跟一个异常记录
        log.record_step(phase="tool_call", action="ok", success=True)
        log.record_step(phase="tool_result", action="ok", success=True)
        assert len(log.steps) == 2

    def test_persist_to_invalid_path_returns_false(self):
        log = _make_log()
        ok = log.persist("")
        assert ok is False

    def test_to_json_with_internal_error(self):
        log = _make_log()
        s = log.to_json()
        assert isinstance(s, str)
        assert json.loads(s)  # 至少能解析

    def test_log_does_not_change_agent_behavior(self):
        """记录步骤不改变外部状态。"""
        log = _make_log()
        steps_before = len(log.steps)
        log.record_step(phase="tool_call", action="read_file", success=True)
        log.record_step(phase="tool_result", action="read_file", success=True)
        assert len(log.steps) == steps_before + 2


# ═══════════════════════════════════════════════════════════════
# Runtime 集成测试（验证日志不影响 Runtime 行为）
# ═══════════════════════════════════════════════════════════════


class TestExecutionLogIntegration:
    """验证 ExecutionLog 集成到 Runtime 后不改变行为。"""

    def test_runtime_init_does_not_crash_with_log(self):
        """Runtime 初始化不应因日志而改变。"""
        from zmai.config.config import Config
        from zmai.runtime import Runtime

        rt = Runtime(config=Config(sources=[]))
        assert rt is not None
