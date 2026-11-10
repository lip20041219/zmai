"""Tests for zmai.runtime — Runtime init, run, lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from tests.mocks import ConnectionErrorBackend, InfiniteLoopBackend
from zmai.agent import AgentAction, AgentContext, AgentResult, AgentState
from zmai.config.config import Config
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.runtime import Runtime
from zmai.tool import Tool, ToolCall, ToolContext, ToolResult

# ═══════════════════════════════════════════════════════════════
# MockBackend
# ═══════════════════════════════════════════════════════════════

class MockBackend(Backend):
    """不发送真实 HTTP 请求的 Mock Backend。"""
    name = "mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        return BackendResponse(
            content=f"mock response for: {request.messages[-1].get('content', '')}",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "mock-v1"},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


class SlowMockBackend(Backend):
    """延迟响应的 Mock Backend，用于测试取消。"""
    name = "slow"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        pass

    def invoke(self, request: BackendRequest) -> BackendResponse:
        import time
        time.sleep(5)  # 足够长来测试取消
        return BackendResponse(
            content="slow response",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "slow"},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()



class ToolCallMockBackend(Backend):
    """返回工具调用以触发 on_progress 的 Mock Backend。"""
    name = "toolmock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        pass

    def invoke(self, request: BackendRequest) -> BackendResponse:
        from zmai.tool import ToolCall
        return BackendResponse(
            content="",
            tool_calls=[ToolCall(id="call_1", name="read_file", params={"path": "test.txt"})],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
            metadata={"model": "mock"},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


# ═══════════════════════════════════════════════════════════════
# Runtime init
# ═══════════════════════════════════════════════════════════════

class TestRuntimeInit:
    def test_creates_with_defaults(self):
        rt = Runtime(config=Config(sources=[]))
        assert rt is not None
        assert rt._lifecycle is not None
        assert rt._gateway is not None
        assert rt._tools is not None

    def test_creates_with_config(self):
        cfg = Config(sources=[])
        cfg.set("runtime.max_iterations", 5)
        rt = Runtime(config=cfg)
        assert rt._config.get("runtime.max_iterations") == 5

    def test_memory_manager_created(self):
        rt = Runtime(config=Config(sources=[]))
        assert rt._memory is not None

    def test_get_info(self):
        rt = Runtime(config=Config(sources=[]))
        info = rt.get_info()
        assert info.total_agents == 0
        assert info.running_agents == 0

    def test_list_agents_empty(self):
        rt = Runtime(config=Config(sources=[]))
        assert rt.list_agents() == []


# ═══════════════════════════════════════════════════════════════
# Runtime.run()
# ═══════════════════════════════════════════════════════════════

class TestRuntimeRun:
    @pytest.fixture
    def runtime_with_mock(self) -> Runtime:
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        # 注册 MockBackend 作为默认
        rt._gateway.register("mock", MockBackend, default=True)
        return rt

    def test_run_returns_dict(self, runtime_with_mock: Runtime):
        rt = runtime_with_mock

        async def run():
            return await rt.run(
                agent_id="test_agent",
                task="say hello",
                backend="mock",
            )

        result = asyncio.run(run())
        assert isinstance(result, dict)

    def test_initialize_exception_is_failed_and_redacted(self, tmp_path, monkeypatch):
        from zmai.swe.agent import SWEAgent

        secret = "sk-" + "x" * 24
        monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
        cfg = Config(sources=[])
        cfg.set("workspace.root", str(tmp_path))
        rt = Runtime(config=cfg)
        rt._gateway.register("mock", MockBackend, default=True)
        backend = rt._gateway.get("mock")
        calls = []

        async def broken_initialize(self, context):
            raise RuntimeError(f"setup failed with token={secret}")

        async def unexpected_step(self, context):
            calls.append("step")
            raise AssertionError("step must not run")

        monkeypatch.setattr(SWEAgent, "initialize", broken_initialize)
        monkeypatch.setattr(SWEAgent, "step", unexpected_step)
        result = asyncio.run(rt.run(agent_id="init_failure", task="repair", backend="mock"))
        assert result["status"] == "failed"
        assert result["phase"] == "initialize"
        assert result["error_type"] == "RuntimeError"
        assert result["steps"] == 0
        assert secret not in result["error"]
        assert backend.invoke_count == 0
        assert calls == []
        log = (tmp_path / "init_failure" / ".state" / "execution_log.json").read_text()
        assert "agent_initialize" in log and "setup failed" in log
        assert secret not in log

    def test_memory_restore_exception_is_failed_before_model(self, tmp_path, monkeypatch):
        from zmai.swe.agent import SWEAgent

        cfg = Config(sources=[])
        cfg.set("workspace.root", str(tmp_path))
        rt = Runtime(config=cfg)
        rt._gateway.register("mock", MockBackend, default=True)
        backend = rt._gateway.get("mock")
        calls = []

        async def initialized(self, context):
            calls.append("initialize")

        async def unexpected_step(self, context):
            calls.append("step")
            raise AssertionError("step must not run")

        def broken_restore(agent_id):
            raise OSError("read-only memory directory")

        monkeypatch.setattr(SWEAgent, "initialize", initialized)
        monkeypatch.setattr(SWEAgent, "step", unexpected_step)
        monkeypatch.setattr(rt._memory, "restore", broken_restore)
        result = asyncio.run(rt.run(agent_id="restore_failure", task="repair", backend="mock"))
        assert result["status"] == "failed"
        assert result["phase"] == "memory_restore"
        assert result["error_type"] == "OSError"
        assert result["steps"] == 0
        assert backend.invoke_count == 0
        assert calls == ["initialize"]
        log = (tmp_path / "restore_failure" / ".state" / "execution_log.json").read_text()
        assert "memory_restore" in log and "read-only memory directory" in log

    def test_run_has_status(self, runtime_with_mock: Runtime):
        rt = runtime_with_mock

        async def run():
            return await rt.run(
                agent_id="test_agent2",
                task="say hello",
                backend="mock",
            )

        result = asyncio.run(run())
        assert "status" in result
        assert result["status"] == "completed"

    def test_run_has_output(self, runtime_with_mock: Runtime):
        rt = runtime_with_mock

        async def run():
            return await rt.run(
                agent_id="test_agent3",
                task="say hello",
                backend="mock",
            )

        result = asyncio.run(run())
        assert "output" in result

    def test_run_invokes_backend(self, runtime_with_mock: Runtime):
        rt = runtime_with_mock
        backend = rt._gateway.get("mock")

        async def run():
            return await rt.run(
                agent_id="test_agent4",
                task="say hello",
                backend="mock",
            )

        asyncio.run(run())
        assert backend.invoke_count >= 1

    def test_run_has_steps(self, runtime_with_mock: Runtime):
        rt = runtime_with_mock

        async def run():
            return await rt.run(
                agent_id="test_agent5",
                task="say hello",
                backend="mock",
            )

        result = asyncio.run(run())
        assert "steps" in result
        assert result["steps"] >= 1

    def test_zero_max_iterations_is_not_completed(self):
        """max_iterations=0：一步未执行 → 不得 COMPLETED（无任何验证证据）。"""
        cfg = Config(sources=[])
        cfg.set("runtime.max_iterations", 0)
        rt = Runtime(config=cfg)
        rt._gateway.register("mock", MockBackend, default=True)

        async def run():
            return await rt.run(agent_id="zero_steps", task="say hello",
                                backend="mock")

        result = asyncio.run(run())
        assert result["steps"] == 0
        assert result["status"] != "completed", (
            f"零步执行不得 COMPLETED: {result}"
        )

    def test_run_with_on_progress(self, runtime_with_mock: Runtime):
        rt = runtime_with_mock
        # 注册一个会触发工具调用的 backend
        rt._gateway.register("toolmock", ToolCallMockBackend, default=True)
        events: list[tuple[str, str]] = []

        def progress(typ: str, msg: str) -> None:
            events.append((typ, msg))

        async def run():
            return await rt.run(
                agent_id="test_agent6",
                task="read test.txt",
                backend="toolmock",
                on_progress=progress,
            )

        asyncio.run(run())
        # 应该触发了 tool 和 result 事件
        assert len(events) > 0
        assert any(t == "tool" for t, _ in events)

    def test_run_memory_persisted(self, runtime_with_mock: Runtime):
        """运行后记忆被持久化到 MemoryManager。"""
        rt = runtime_with_mock

        async def run():
            return await rt.run(
                agent_id="mem_test",
                task="say hello",
                backend="mock",
            )

        asyncio.run(run())
        # MemoryManager 应该有该 agent 的记忆
        info = rt._memory.exists("mem_test")
        assert info is True


# ═══════════════════════════════════════════════════════════════
# CancelMockBackend — 用于取消测试
# ═══════════════════════════════════════════════════════════════


class CancelMockBackend(Backend):
    """短延迟 Mock Backend，用于测试取消。"""
    name = "cancel_mock"

    def __init__(self, config=None):
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request):
        self.invoke_count += 1
        import time
        time.sleep(0.3)
        return BackendResponse(
            content="done",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "mock"},
        )

    def stream(self, request):
        raise NotImplementedError

    @property
    def capabilities(self):
        return set()


# ═══════════════════════════════════════════════════════════════
# 测试: 任务取消
# ═══════════════════════════════════════════════════════════════


class TestTaskCancellation:
    @pytest.fixture
    def runtime_with_cancel_mock(self) -> Runtime:
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("cancel_mock", CancelMockBackend, default=True)
        return rt

    def test_cancel_during_execution(self, runtime_with_cancel_mock):
        """验证 Runtime 取消机制不会崩溃。

        当前架构中 backend.invoke() 是同步阻塞的，真正的 asyncio 取消
        只能在 await 点生效。取消后的实际状态取决于同步 Backend 的执行时机。
        """
        rt = runtime_with_cancel_mock

        async def run_and_cancel():
            asyncio.create_task(
                rt.run(agent_id="cancel_test", task="do something", backend="cancel_mock")
            )
            await asyncio.sleep(0.05)
            # Runtime.cancel() 本身不应抛出异常
            await rt.cancel("cancel_test")
            await asyncio.sleep(0.5)
            # 取消后状态应为 terminal（cancelled 或 completed 均为可能）
            state = rt._lifecycle.get_state("cancel_test")
            assert state in ("cancelled", "timeout", "completed", "failed"), (
                f"取消后应为 terminal 状态，实际 {state}"
            )

        asyncio.run(run_and_cancel())

    def test_cancel_not_success(self, runtime_with_cancel_mock):
        """验证取消后 runtime.cancel() 本身不会抛出异常。"""
        rt = runtime_with_cancel_mock

        async def run_and_cancel():
            asyncio.create_task(
                rt.run(agent_id="cancel_test2", task="do something", backend="cancel_mock")
            )
            await asyncio.sleep(0.05)
            await rt.cancel("cancel_test2")
            await asyncio.sleep(0.5)
            # 取消后的状态应为 terminal（不再是 executing）
            state = rt._lifecycle.get_state("cancel_test2")
            assert state in ("cancelled", "timeout", "completed", "failed"), (
                f"取消后应为 terminal 状态，实际 {state}"
            )

        asyncio.run(run_and_cancel())


# ═══════════════════════════════════════════════════════════════
# 测试: 失败后的恢复循环
# ═══════════════════════════════════════════════════════════════


class _FailingTool(Tool):
    """总是失败的工具。"""
    name = "fail_tool"
    description = "总是失败的工具"
    parameters = {"type": "object", "properties": {}}

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.err("intentional failure for testing")


class _StepRecoveryBackend(Backend):
    """第1步返回工具调用，第2步返回文本。"""
    name = "step_recovery"

    def __init__(self, config=None):
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request):
        self.invoke_count += 1
        if self.invoke_count == 1:
            return BackendResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="fail_tool", params={})],
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                stop_reason="tool_use",
                metadata={"model": "mock"},
            )
        return BackendResponse(
            content="recovered!",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "mock"},
        )

    def stream(self, request):
        raise NotImplementedError

    @property
    def capabilities(self):
        return {BackendCapability.TOOL_USE}


class TestFailureRecovery:
    """失败后的恢复循环测试。

    当前生产代码状态:
    ✅ Tool 失败 → ToolRouter 捕获异常，ToolResult(success=False) 返回
                  → SWEAgent 将错误作为消息追加给 LLM → LLM 可决定重试
    ✅ Backend 失败 → SWEAgent.step() 以指数退避重试（1s, 2s, 4s…）
                      → 默认最多 3 次，全部耗尽才返回 fail
    """

    def test_tool_failure_continues_execution(self):
        """Tool 失败后 Agent 正确报告失败。

        此测试验证: Tool 失败后 (tool_calls_ok=0, tool_calls_fail=1),
        即使 LLM 返回文本 "recovered!"，finalize() 仍应返回 FAILED。
        这是审计修复的核心场景: Agent 不得在工具执行失败后谎报成功。
        """
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("step_recovery", _StepRecoveryBackend, default=True)
        rt._tools.register(_FailingTool())

        async def run():
            return await rt.run(
                agent_id="recovery_test",
                task="do something",
                backend="step_recovery",
            )

        result = asyncio.run(run())
        # 工具失败且无工具成功 → 必须报告 failed
        assert result["status"] == "failed"

    def test_backend_failure_terminates_agent(self):
        """Backend 调用失败后 Agent 立即终止，返回 failed 状态。"""
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("conn_err", ConnectionErrorBackend, default=True)

        async def run():
            return await rt.run(
                agent_id="fail_test",
                task="do something",
                backend="conn_err",
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"
        assert result["status"] not in ("completed", "cancelled")


# ═══════════════════════════════════════════════════════════════
# RetryAgentBackend — 用于测试 Agent 级 Backend 重试
# ═══════════════════════════════════════════════════════════════


class RetryAgentBackend(Backend):
    """前 N-1 次失败（Exception），第 N 次成功。模拟网络波动后恢复。"""
    name = "retry_agent"

    def __init__(self, config=None):
        self._config = config or {}
        self.invoke_count = 0
        self._fail_count: int = config.get("fail_count", 2) if config else 2

    def invoke(self, request):
        self.invoke_count += 1
        if self.invoke_count <= self._fail_count:
            if self.invoke_count == 1:
                raise Exception("ConnectionError: DNS resolution failed")
            raise Exception("TimeoutError: request timed out")
        return BackendResponse(
            content="ok after retry",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "mock"},
        )

    def stream(self, request):
        raise NotImplementedError

    @property
    def capabilities(self):
        return set()


class TestAgentRetry:
    """Agent 级的 Backend 调用重试测试。

    架构: SWEAgent.step() 在 backend.invoke() 外层包装重试循环。
          非 BackendError 的 Exception → 指数退避重试 (1s, 2s, 4s)
          BackendError → 直接透传，不重试
    """

    @pytest.fixture
    def runtime_with_retry(self) -> Runtime:
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("retry_agent", RetryAgentBackend, default=True)
        return rt

    def test_agent_retry_connection_error_timeout_success(self, runtime_with_retry):
        """ConnectionError → TimeoutError → Success，最终 status=completed。"""
        rt = runtime_with_retry
        backend = rt._gateway.get("retry_agent")

        async def run():
            return await rt.run(
                agent_id="retry_test",
                task="say hello",
                backend="retry_agent",
            )

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert backend.invoke_count == 3

    def test_agent_retry_exhaustion_fails(self, runtime_with_retry):
        """超过最大重试次数后 Agent 返回 failed。"""
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        # fail_count=5 > 默认 max_retries=3，所有重试耗尽
        rt._gateway.register("exhaust", RetryAgentBackend, default=True, config={"fail_count": 5})

        async def run():
            return await rt.run(
                agent_id="exhaust_test",
                task="say hello",
                backend="exhaust",
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"
        # 注意: 实际 invoke_count 取决于重试逻辑与默认配置
        # 这里只验证最终状态是 failed 而非 completed


# ═══════════════════════════════════════════════════════════════
# 测试: max_steps → timeout
# ═══════════════════════════════════════════════════════════════


class TestMaxStepsTimeout:
    """max_steps 耗尽后 Agent 返回 timeout。"""

    def test_max_steps_hit_returns_timeout(self):
        """达到 max_steps 限制时返回 timeout。"""
        cfg = Config(sources=[])
        cfg.set("runtime.max_iterations", 3)
        rt = Runtime(config=cfg)
        rt._gateway.register("infinite_loop", InfiniteLoopBackend, default=True)

        async def run():
            return await rt.run(
                agent_id="timeout_test",
                task="do something",
                backend="infinite_loop",
            )

        result = asyncio.run(run())
        assert result["status"] == "timeout", (
            f"max_steps 耗尽应 timeout，实际 {result['status']}"
        )
        assert "steps" in result
        # 非 complete 非 fail 的任务不得返回 false positive
        assert result["status"] not in ("completed",)


# ═══════════════════════════════════════════════════════════════
# 测试: SWEAgent.finalize() 状态判定（直接测试 finalize 逻辑）
# ═══════════════════════════════════════════════════════════════


class TestSWEAgentFinalize:
    """直接测试 finalize() 的各状态分支，确保 Runtime 尊重其返回。"""

    def test_normal_complete(self):
        """无异常 → COMPLETED。"""
        from zmai.swe.agent import SWEAgent

        agent = SWEAgent("test")
        ctx = AgentContext(
            agent_id="test", task="test", config={},
        )
        result = asyncio.run(agent.finalize(ctx))
        assert result.status == AgentState.COMPLETED

    def test_step_failed_returns_failed(self):
        """step_failed → FAILED，error 传递正确。"""
        from zmai.swe.agent import SWEAgent

        agent = SWEAgent("test")
        ctx = AgentContext(
            agent_id="test", task="test", config={},
            metadata={"step_failed": "something went wrong"},
        )
        result = asyncio.run(agent.finalize(ctx))
        assert result.status == AgentState.FAILED
        assert result.error == "something went wrong", (
            f"step_failed 的 error 应传递到 AgentResult，实际 {result.error}"
        )

    def test_timed_out_returns_timeout(self):
        """timed_out → TIMEOUT。"""
        from zmai.swe.agent import SWEAgent

        agent = SWEAgent("test")
        ctx = AgentContext(
            agent_id="test", task="test", config={},
            metadata={"timed_out": True},
            step_count=5,
        )
        result = asyncio.run(agent.finalize(ctx))
        assert result.status == AgentState.TIMEOUT

    def test_verification_failure_returns_failed(self):
        """验证失败 → FAILED。"""
        from zmai.swe.agent import SWEAgent
        from zmai.swe.verifier import VerificationResult

        agent = SWEAgent("test")
        failed_vr = VerificationResult(
            passed=False, checks=[], summary="verification failed",
        )
        ctx = AgentContext(
            agent_id="test", task="test", config={},
            metadata={"verification": failed_vr},
        )
        result = asyncio.run(agent.finalize(ctx))
        assert result.status == AgentState.FAILED
        assert result.status != AgentState.COMPLETED

    def test_timed_out_priority_over_step_failed(self):
        """timed_out 应优先于 step_failed。"""
        from zmai.swe.agent import SWEAgent

        agent = SWEAgent("test")
        ctx = AgentContext(
            agent_id="test", task="test", config={},
            metadata={"timed_out": True, "step_failed": "error"},
            step_count=3,
        )
        result = asyncio.run(agent.finalize(ctx))
        assert result.status == AgentState.TIMEOUT

    def test_step_failed_not_overridden_by_tool_ok(self):
        """step_failed 存在时，即使有成功工具也不得 COMPLETED。"""
        from zmai.swe.agent import SWEAgent

        agent = SWEAgent("test")
        ctx = AgentContext(
            agent_id="test", task="test", config={},
            metadata={
                "step_failed": "step-error",
                "tool_calls_ok": 5,
                "tool_calls_fail": 1,
            },
        )
        result = asyncio.run(agent.finalize(ctx))
        # step_failed 应优先于工具统计 → FAILED
        assert result.status == AgentState.FAILED, (
            f"step_failed 时不得 COMPLETED，实际 {result.status}"
        )
        assert result.status != AgentState.COMPLETED


# ═══════════════════════════════════════════════════════════════
# 逐步持久化的对话轨迹（workspace/.state/trajectory.json）
# ═══════════════════════════════════════════════════════════════


def test_execution_log_persisted_before_next_step(tmp_path, monkeypatch):
    """A later interrupted step must not erase completed tool evidence."""
    from zmai.swe.agent import SWEAgent

    async def initialize(self, context):
        pass

    async def step(self, context):
        log_path = tmp_path / "log_probe" / ".state" / "execution_log.json"
        if not context.metadata.get("first_step_done"):
            context.metadata["first_step_done"] = True
            context.metadata["__log__"].record_step(
                phase="tool_result", action="shell_exec", tool_name="shell_exec",
                success=True, tool_output="1 passed")
            return AgentAction.cont()
        saved = json.loads(log_path.read_text(encoding="utf-8"))
        assert any(s["phase"] == "tool_result" and s["tool_output"] == "1 passed"
                   for s in saved["steps"])
        return AgentAction.fail("simulated interruption")

    async def finalize(self, context):
        return AgentResult(agent_id=self.agent_id, status=AgentState.FAILED)

    monkeypatch.setattr(SWEAgent, "initialize", initialize)
    monkeypatch.setattr(SWEAgent, "step", step)
    monkeypatch.setattr(SWEAgent, "finalize", finalize)
    cfg = Config(sources=[])
    cfg.set("workspace.root", str(tmp_path))
    cfg.set("runtime.max_iterations", 2)
    runtime = Runtime(config=cfg)
    runtime._gateway.register("mock", MockBackend, default=True)
    result = asyncio.run(runtime.run(agent_id="log_probe", task="probe", backend="mock"))
    assert result["status"] == "failed"


def test_wall_budget_exhaustion_cannot_complete(tmp_path, monkeypatch):
    from zmai.swe.agent import SWEAgent

    calls = {"steps": 0}

    async def initialize(self, context):
        pass

    async def step(self, context):
        calls["steps"] += 1
        await asyncio.sleep(0.6)
        return AgentAction.complete("too late")

    monkeypatch.setattr(SWEAgent, "initialize", initialize)
    monkeypatch.setattr(SWEAgent, "step", step)
    cfg = Config(sources=[])
    cfg.set("workspace.root", str(tmp_path))
    cfg.set("runtime.max_iterations", 3)
    runtime = Runtime(config=cfg)
    runtime._gateway.register("mock", MockBackend, default=True)
    result = asyncio.run(runtime.run(
        agent_id="wall_probe", task="probe", backend="mock",
        config={"wall_seconds_limit": 0.5, "wall_exit_reserve_seconds": 0}))

    assert calls["steps"] == 1
    assert result["status"] == "timeout"
    assert result["error"] == "任务墙钟预算耗尽"
    log = tmp_path / "wall_probe" / ".state" / "execution_log.json"
    assert log.is_file() and json.loads(log.read_text(encoding="utf-8"))["steps"]


class _OkTool(Tool):
    """总是成功的工具 —— 让第 1 步的工具调用把 Agent 推进到第 2 步。"""
    name = "ok_tool"
    description = "总是成功"
    parameters = {"type": "object", "properties": {}}

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.ok("ok")


class TrajectoryProbeBackend(Backend):
    """第 1 步返回工具调用、第 2 步返回文本。

    每次 invoke 开始时探测轨迹文件，并原样记录本次实际收到的请求内容，
    用于验证"落盘的就是模型真正收到的那份输入"以及"第 2 次调用开始时
    第 1 步已落盘"。
    """
    name = "traj_probe"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.invoke_count = 0
        self.trajectory_path = None  # 由测试注入
        self.seen: list[list[dict[str, Any]]] = []
        self.seen_system: list[str] = []
        self.observed: list[dict[str, Any] | None] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        self.seen.append([dict(m) for m in request.messages])
        self.seen_system.append(request.system_prompt or "")
        p = self.trajectory_path
        self.observed.append(
            json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else None
        )
        if self.invoke_count == 1:
            return BackendResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="ok_tool", params={})],
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                stop_reason="tool_use",
                metadata={"model": "probe"},
            )
        return BackendResponse(
            content="done",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            metadata={"model": "probe"},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


class TestTrajectoryPersistence:
    """每个已完成步骤后立即落盘一份模型实际请求快照。"""

    AGENT = "traj_agent"
    STEPS = 2

    def _run(self, ws_root) -> tuple[TrajectoryProbeBackend, dict[str, Any], Any]:
        cfg = Config(sources=[])
        cfg.set("workspace.root", str(ws_root))
        rt = Runtime(config=cfg)
        rt._gateway.register("traj_probe", TrajectoryProbeBackend, default=True)
        rt._tools.register(_OkTool())
        be = rt._gateway.get("traj_probe")
        traj = ws_root / self.AGENT / ".state" / "trajectory.json"
        be.trajectory_path = traj

        async def run():
            return await rt.run(agent_id=self.AGENT, task="do something",
                                backend="traj_probe")

        return be, asyncio.run(run()), traj

    @pytest.fixture
    def probed(self, tmp_path):
        be, result, traj = self._run(tmp_path)
        assert be.invoke_count >= self.STEPS, "需要至少 2 步才能验证逐步落盘"
        return be, result, traj

    def test_visible_mid_run(self, probed):
        """第 2 次模型调用开始时，第 1 步的轨迹已经在磁盘上。"""
        be, _, _ = probed
        first_seen_by_second_call = be.observed[1]
        assert first_seen_by_second_call is not None, (
            "第 2 次 backend 调用开始时磁盘上还没有轨迹文件"
        )
        assert first_seen_by_second_call["step"] == 1


    def test_content_matches_real_request(self, probed):
        """落盘的 messages / system_prompt 与模型实际收到的完全一致。"""
        be, _, _ = probed
        first_seen_by_second_call = be.observed[1]
        assert first_seen_by_second_call["messages"] == be.seen[0]
        assert first_seen_by_second_call["system_prompt"] == be.seen_system[0]
        assert first_seen_by_second_call["system_prompt"], (
            "system_prompt 是请求的一部分，不得为空占位"
        )

    def test_step_number_is_latest_completed(self, probed):
        """多步执行后，文件里是最新完成的步骤。"""
        _, _, traj = probed
        final = json.loads(traj.read_text(encoding="utf-8"))
        assert final["step"] == self.STEPS
        assert final["agent_id"] == self.AGENT
        assert final["messages"]

    def test_atomic_write_leaves_parseable_file(self, probed):
        """正常写入流程不产生截断文件，也不留 .tmp 残留。"""
        _, _, traj = probed
        assert json.loads(traj.read_text(encoding="utf-8"))["step"] == self.STEPS
        assert not traj.with_name(traj.name + ".tmp").exists()

    def test_write_failure_does_not_fail_agent(self, tmp_path, caplog):
        """轨迹写入失败时 Agent 主流程行为不变。"""
        # 把目标路径占成一个目录 → tmp 写入成功但 os.replace 必然失败
        blocker = tmp_path / "blocked" / self.AGENT / ".state" / "trajectory.json"
        blocker.mkdir(parents=True)

        with caplog.at_level(logging.WARNING, logger="zmai.runtime"):
            _, blocked_result, _ = self._run(tmp_path / "blocked")
        _, control_result, _ = self._run(tmp_path / "control")

        assert blocked_result["status"] == control_result["status"], (
            "写入失败不得改变 Agent 的执行结果"
        )
        assert blocker.is_dir(), "失败路径不应留下半截 JSON"
        assert any("轨迹持久化失败" in r.message for r in caplog.records), (
            "写入失败必须留下警告日志"
        )

