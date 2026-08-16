"""Agent 生命周期状态模型测试。

覆盖 8 个测试场景:
  1. 正常生命周期                      — created → executing → completed
  2. Backend 失败                      — created → executing → failed
  3. Tool 失败                         — created → executing → failed
  4. max_steps                         — created → executing → timeout
  5. Timeout（wall-clock 超时）         — created → executing → timeout
  6. Cancellation                      — executing  → cancelled
  7. 非法状态转换                       — created → completed 被拒绝
  8. 完成后重复执行                     — completed → executing 被拒绝
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.mocks import (
    ConnectionErrorBackend,
    InfiniteLoopBackend,
)
from zmai.agent import AgentState
from zmai.config.config import Config
from zmai.errors import RuntimeError
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.runtime import LifecycleManager, Runtime
from zmai.tool import Tool, ToolCall, ToolContext, ToolResult

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

_DEFAULT_USAGE = TokenUsage(input_tokens=10, output_tokens=5)
_DEFAULT_META = {"model": "mock-v1"}


class _FailingTool(Tool):
    """总是失败的工具。"""
    name = "fail_tool"
    description = "总是失败的工具"
    parameters = {"type": "object", "properties": {}}

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        return ToolResult.err("intentional failure for testing")


# ═══════════════════════════════════════════════════════════════
# 1. LifecycleManager 直接测试
# ═══════════════════════════════════════════════════════════════


class TestLifecycleManagerUnit:
    """LifecycleManager 状态机单元测试。"""

    # ── 1. 正常生命周期 ─────────────────────────────────

    def test_normal_lifecycle(self):
        """正常路径: created → executing → completed。"""
        lm = LifecycleManager()
        lm.create("agent_1")
        assert lm.get_state("agent_1") == "created"

        lm.execute("agent_1")
        assert lm.get_state("agent_1") == "executing"

        lm.complete("agent_1")
        assert lm.get_state("agent_1") == "completed"
        assert lm.is_terminal("agent_1") is True

    def test_normal_lifecycle_with_verify(self):
        """正常路径含验证: created → executing → verifying → completed。"""
        lm = LifecycleManager()
        lm.create("agent_2")
        lm.execute("agent_2")
        lm.verify("agent_2")
        assert lm.get_state("agent_2") == "verifying"
        lm.complete("agent_2")
        assert lm.is_terminal("agent_2") is True

    def test_normal_lifecycle_with_planning(self):
        """正常路径含规划: created → planning → plan_ready → executing → completed。"""
        lm = LifecycleManager()
        lm.create("agent_3")
        lm.plan("agent_3")
        assert lm.get_state("agent_3") == "planning"
        lm.plan_ready("agent_3")
        assert lm.get_state("agent_3") == "plan_ready"
        lm.execute("agent_3")
        assert lm.get_state("agent_3") == "executing"
        lm.complete("agent_3")
        assert lm.is_terminal("agent_3") is True

    # ── 2. Backend 失败 ─────────────────────────────────

    def test_backend_failure(self):
        """Backend 失败: created → executing → failed。"""
        lm = LifecycleManager()
        lm.create("agent_4")
        lm.execute("agent_4")
        lm.fail("agent_4")
        assert lm.get_state("agent_4") == "failed"
        assert lm.is_terminal("agent_4") is True

    # ── 3. Tool 失败 ────────────────────────────────────

    def test_tool_all_fail(self):
        """所有 Tool 失败: created → executing → failed。"""
        lm = LifecycleManager()
        lm.create("agent_5")
        lm.execute("agent_5")
        lm.fail("agent_5")
        assert lm.get_state("agent_5") == "failed"

    # ── 4. max_steps / 5. Timeout ───────────────────────

    def test_timeout(self):
        """超时: created → executing → timeout。"""
        lm = LifecycleManager()
        lm.create("agent_6")
        lm.execute("agent_6")
        lm.timeout("agent_6")
        assert lm.get_state("agent_6") == "timeout"
        assert lm.is_terminal("agent_6") is True

    # ── 6. Cancellation ─────────────────────────────────

    def test_cancellation_from_executing(self):
        """取消: executing → cancelled。"""
        lm = LifecycleManager()
        lm.create("agent_7")
        lm.execute("agent_7")
        lm.cancel("agent_7")
        assert lm.get_state("agent_7") == "cancelled"
        assert lm.is_terminal("agent_7") is True

    def test_cancellation_from_created(self):
        """创建后取消: created → cancelled。"""
        lm = LifecycleManager()
        lm.create("agent_8")
        lm.cancel("agent_8")
        assert lm.get_state("agent_8") == "cancelled"

    def test_cancel_idempotent(self):
        """重复取消: 终态取消被忽略。"""
        lm = LifecycleManager()
        lm.create("agent_9")
        lm.execute("agent_9")
        lm.cancel("agent_9")  # first: executing → cancelled
        assert lm.get_state("agent_9") == "cancelled"
        lm.cancel("agent_9")  # second: terminal → no-op
        assert lm.get_state("agent_9") == "cancelled"

    # ── 7. 非法状态转换 ────────────────────────────────

    @pytest.mark.parametrize("from_state,to_state", [
        ("created", "completed"),    # 跳过了 executing
        ("created", "timeout"),      # 跳过了 executing
        ("created", "verifying"),    # 跳过了 executing
        ("executing", "planning"),   # 回退
        ("completed", "executing"),  # 终态再执行
        ("completed", "failed"),     # 终态再转换
        ("failed", "completed"),     # 终态再转换
        ("cancelled", "executing"),  # 终态再执行
        ("timeout", "completed"),    # 终态再转换
    ])
    def test_illegal_transitions_raise(self, from_state, to_state):
        """非法状态转换被拒绝并抛出 RuntimeError。"""
        lm = LifecycleManager()

        # 设置初始状态
        if from_state == "created":
            lm.create("agent_x")
        elif from_state == "executing":
            lm.create("agent_x")
            lm.execute("agent_x")
        elif from_state == "completed":
            lm.create("agent_x")
            lm.execute("agent_x")
            lm.complete("agent_x")
        elif from_state == "failed":
            lm.create("agent_x")
            lm.fail("agent_x")
        elif from_state == "cancelled":
            lm.create("agent_x")
            lm.cancel("agent_x")
        elif from_state == "timeout":
            lm.create("agent_x")
            lm.execute("agent_x")
            lm.timeout("agent_x")

        # 尝试非法转换
        trans_map = {
            "completed": lm.complete,
            "failed": lm.fail,
            "cancelled": lm.cancel,
            "timeout": lm.timeout,
            "executing": lm.execute,
            "planning": lm.plan,
            "verifying": lm.verify,
        }
        with pytest.raises(RuntimeError) as exc:
            trans_map[to_state]("agent_x")
        assert "非法" in str(exc.value) or "终态" in str(exc.value)

    # ── 8. 完成后重复执行 ──────────────────────────────

    def test_create_after_completed_rejected(self):
        """完成后不可再次创建同一 Agent。"""
        lm = LifecycleManager()
        lm.create("agent_r")
        lm.execute("agent_r")
        lm.complete("agent_r")
        assert lm.is_terminal("agent_r") is True

        with pytest.raises(RuntimeError) as exc:
            lm.create("agent_r")
        assert "终态" in str(exc.value) or "已存在" in str(exc.value)

    def test_execute_after_terminal_rejected(self):
        """终态后执行被拒绝。"""
        lm = LifecycleManager()
        # 状态名 → 方法名映射
        method_map = {
            "completed": lm.complete,
            "failed": lm.fail,
            "cancelled": lm.cancel,
            "timeout": lm.timeout,
        }

        for terminal_state, meth in method_map.items():
            aid = f"agent_term_{terminal_state}"
            lm.create(aid)
            if terminal_state != "failed":
                lm.execute(aid)
            meth(aid)
            assert lm.is_terminal(aid) is True

            with pytest.raises(RuntimeError):
                lm.execute(aid)

    # ── 验证辅助方法 ─────────────────────────────────

    def test_is_active(self):
        """is_active 对 PLANNING/PLAN_READY/EXECUTING/VERIFYING 返回 True。"""
        lm = LifecycleManager()

        lm.create("a1")
        assert lm.is_active("a1") is False

        lm.plan("a1")
        assert lm.is_active("a1") is True

        lm.plan_ready("a1")
        assert lm.is_active("a1") is True

        lm.execute("a1")
        assert lm.is_active("a1") is True

        lm.verify("a1")
        assert lm.is_active("a1") is True

        lm.complete("a1")
        assert lm.is_active("a1") is False

    def test_has_and_remove(self):
        """has() 和 remove() 正确工作。"""
        lm = LifecycleManager()
        lm.create("agent_h")
        assert lm.has("agent_h") is True
        lm.remove("agent_h")
        assert lm.has("agent_h") is False

    def test_list(self):
        """list() 返回所有 Agent 状态。"""
        lm = LifecycleManager()
        lm.create("agent_l1")
        lm.create("agent_l2")
        states = lm.list()
        assert len(states) == 2
        assert states["agent_l1"] == "created"
        assert states["agent_l2"] == "created"


# ═══════════════════════════════════════════════════════════════
# 2-6. Runtime 集成测试
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# Properly defined Mock Backends for integration tests
# ═══════════════════════════════════════════════════════════════


class _AuthErrorBackend(Backend):
    """模拟 API Key 认证失败（401）。"""
    name = "auth_error"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        from zmai.errors import BackendError
        raise BackendError(
            "auth_error API HTTP 401: authentication_error",
            status_code=401,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


class _AllFailBackend(Backend):
    """每次调用都返回同一个会失败的 tool_call。"""
    name = "all_fail"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        return BackendResponse(
            content="",
            tool_calls=[ToolCall(id="c1", name="fail_tool", params={})],
            usage=_DEFAULT_USAGE,
            stop_reason="tool_use",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


class _FailOnceThenOkBackend(Backend):
    """第一次调用返回失败的工具调用，第二次返回完成文本。

    用于测试: 工具失败后 Agent 正确报告 FAILED（而非假成功）。
    """
    name = "fail_once"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        if self.invoke_count == 1:
            return BackendResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="fail_tool", params={})],
                usage=_DEFAULT_USAGE,
                stop_reason="tool_use",
                metadata=_DEFAULT_META,
            )
        # 第二次返回文本 → Agent 结束
        return BackendResponse(
            content="completed",
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


class _SlowBackend(Backend):
    """延迟响应的 Backend，用于测试取消。"""
    name = "slow"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        time.sleep(0.5)
        return BackendResponse(
            content="done",
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


class _StateTestBackend(Backend):
    """返回预设完成响应，用于简单状态测试。"""
    name = "state_test"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        return BackendResponse(
            content="task done",
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return set()


class TestRuntimeLifecycleIntegration:
    """Runtime 集成测试 — 验证完整执行路径的状态正确性。"""

    @pytest.fixture
    def runtime(self) -> Runtime:
        cfg = Config(sources=[])
        return Runtime(config=cfg)

    # ── 1. 正常生命周期 ─────────────────────────────────

    def test_normal_completed(self, runtime: Runtime):
        """正常路径: Runtime 返回 status=completed。"""
        runtime._gateway.register("ok", _StateTestBackend, default=True)

        async def run():
            return await runtime.run(
                agent_id="integ_ok", task="test", backend="ok",
            )

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert "output" in result
        assert result.get("steps", 0) >= 1

        # Lifecycle 状态是 completed
        state = runtime._lifecycle.get_state("integ_ok")
        assert state == "completed"

    # ── 2. Backend 失败 ─────────────────────────────────

    def test_backend_failure(self, runtime: Runtime):
        """Backend 401 失败: Runtime 返回 status=failed。"""
        runtime._gateway.register("auth_err", _AuthErrorBackend, default=True)

        async def run():
            return await runtime.run(
                agent_id="integ_fail", task="test", backend="auth_err",
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"

    def test_backend_connection_error(self, runtime: Runtime):
        """连接错误: Runtime 返回 status=failed。"""
        runtime._gateway.register("conn_err", ConnectionErrorBackend, default=True)

        async def run():
            return await runtime.run(
                agent_id="integ_conn", task="test", backend="conn_err",
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"

    # ── 3. Tool 失败 ────────────────────────────────────

    def test_all_tools_fail(self, runtime: Runtime):
        """所有工具反复失败 → max_steps 耗尽 → timeout。"""
        runtime._gateway.register("all_fail", _AllFailBackend, default=True)
        runtime._tools.register(_FailingTool())

        async def run():
            return await runtime.run(
                agent_id="integ_toolfail", task="test", backend="all_fail",
            )

        result = asyncio.run(run())
        # 所有工具都反复失败，直到 max_steps → timeout
        assert result["status"] == "timeout"

    def test_tool_fail_once_reports_failed(self, runtime: Runtime):
        """工具失败一次且无成功 → 正确报告 failed。"""
        runtime._gateway.register("fail_once", _FailOnceThenOkBackend, default=True)
        runtime._tools.register(_FailingTool())

        async def run():
            return await runtime.run(
                agent_id="integ_toolfail_once", task="test", backend="fail_once",
            )

        result = asyncio.run(run())
        # 工具失败 1 次，成功 0 次 → FAILED
        assert result["status"] == "failed"

    # ── 4. max_steps / 5. Timeout ───────────────────────

    def test_max_steps_timeout(self, runtime: Runtime):
        """max_steps 耗尽: Runtime 返回 status=timeout。"""
        cfg = Config(sources=[])
        cfg.set("runtime.max_iterations", 2)
        rt = Runtime(config=cfg)
        rt._gateway.register("infinite", InfiniteLoopBackend, default=True)

        async def run():
            return await rt.run(
                agent_id="integ_timeout", task="test", backend="infinite",
            )

        result = asyncio.run(run())
        assert result["status"] == "timeout"
        assert "最大执行步数" in result.get("error", "")

        # Lifecycle 状态是 timeout
        state = rt._lifecycle.get_state("integ_timeout")
        assert state == "timeout"

    def test_normal_not_timeout(self, runtime: Runtime):
        """正常完成的任务不得报告 timeout。"""
        runtime._gateway.register("state_ok", _StateTestBackend, default=True)

        async def run():
            return await runtime.run(
                agent_id="integ_notimeout", task="test", backend="state_ok",
            )

        result = asyncio.run(run())
        assert result["status"] != "timeout"
        assert result["status"] == "completed"

    # ── 6. Cancellation ────────────────────────────────

    def test_cancellation_does_not_crash(self, runtime: Runtime):
        """取消任务不会崩溃。取消后的状态可能是 timeout（因同步 Backend
        的 CancelledError 只能在 await 点交付，若错过窗口则进入 TIMEOUT）。
        """
        runtime._gateway.register("infinite", InfiniteLoopBackend, default=True)

        async def run_and_cancel():
            _task = asyncio.create_task(
                runtime.run(agent_id="integ_cancel", task="test", backend="infinite")
            )
            await asyncio.sleep(0.05)
            await runtime.cancel("integ_cancel")
            await asyncio.sleep(0.5)
            # 取消后的合理状态: cancelled 或 timeout（取决于取消窗口）
            state = runtime._lifecycle.get_state("integ_cancel")
            assert state in ("cancelled", "timeout", "failed"), (
                f"取消后预期 terminal 状态，实际 {state}"
            )

        asyncio.run(run_and_cancel())

    # ── AgentState 枚举属性 ────────────────────────────

    def test_agent_state_properties(self):
        """AgentState 的 is_terminal 和 is_active 属性正确。"""
        assert AgentState.CREATED.is_terminal is False
        assert AgentState.CREATED.is_active is False

        assert AgentState.PLANNING.is_terminal is False
        assert AgentState.PLANNING.is_active is True

        assert AgentState.EXECUTING.is_terminal is False
        assert AgentState.EXECUTING.is_active is True

        assert AgentState.VERIFYING.is_terminal is False
        assert AgentState.VERIFYING.is_active is True

        assert AgentState.COMPLETED.is_terminal is True
        assert AgentState.COMPLETED.is_active is False

        assert AgentState.FAILED.is_terminal is True
        assert AgentState.FAILED.is_active is False

        assert AgentState.CANCELLED.is_terminal is True
        assert AgentState.CANCELLED.is_active is False

        assert AgentState.TIMEOUT.is_terminal is True
        assert AgentState.TIMEOUT.is_active is False


# ═══════════════════════════════════════════════════════════════
# 7-8. 非法转换 & 终态约束 — Runtime 级别
# ═══════════════════════════════════════════════════════════════


class TestRuntimeStateEnforcement:
    """Runtime 级别的状态约束测试。"""

    def test_cannot_reuse_completed_agent_id(self):
        """同一 agent_id 在完成后再次使用被拒绝。"""
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("ok", _StateTestBackend, default=True)

        async def run_twice():
            r1 = await rt.run(
                agent_id="reuse_test", task="test", backend="ok",
            )
            assert r1["status"] == "completed"

            r2 = await rt.run(
                agent_id="reuse_test", task="test again", backend="ok",
            )
            assert r2["status"] == "failed"

        asyncio.run(run_twice())

    def test_finalize_failed_not_completed(self):
        """所有工具失败时 finalize 必须返回 FAILED，Runtime 不可返回 completed。"""
        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("finalize_fail", _FailOnceThenOkBackend, default=True)
        rt._tools.register(_FailingTool())

        async def run():
            return await rt.run(
                agent_id="finalize_test", task="test", backend="finalize_fail",
            )

        result = asyncio.run(run())
        # 核心断言: tool 失败 1 次且无成功 → 不得返回 completed
        assert result["status"] != "completed"
        assert result["status"] == "failed", (
            f"预期 failed，实际 {result['status']}: 工具失败后不得假成功"
        )
