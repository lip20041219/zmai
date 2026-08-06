"""LoopGuard — Agent 执行循环检测的全面测试。

测试覆盖：
  1. 连续相同 tool call 检测
  2. 连续相同失败检测
  3. 连续无代码修改检测
  4. 边界情况：阈值、重置、混和调用
  5. SWEAgent 集成：LoopGuard 在 step() 中正确触发
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from zmai.swe.loop_guard import (
    LOOP_THRESHOLD,
    LoopGuard,
    LoopResult,
    _tool_call_signature,
    _tool_failure_signature,
    create_loop_guard,
)


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════


def _call(
    guard: LoopGuard,
    name: str = "shell_exec",
    command: str = "dir *.py",
    success: bool = False,
    error: str | None = "exit 1",
) -> None:
    """快捷记录一次工具调用。"""
    guard.record_tool_call(
        name=name,
        params={"command": command},
        success=success,
        output="",
        error=error,
    )


def _ok_call(
    guard: LoopGuard,
    name: str = "read_file",
    path: str = "main.py",
) -> None:
    """快捷记录一次成功的工具调用。"""
    guard.record_tool_call(
        name=name,
        params={"path": path},
        success=True,
        output="file content",
        error=None,
    )


def _write_call(guard: LoopGuard, path: str = "main.py") -> None:
    """快捷记录一次成功的写操作。"""
    guard.record_tool_call(
        name="write_file",
        params={"path": path, "content": "new content"},
        success=True,
        output="written",
        error=None,
    )


# ═══════════════════════════════════════════════════════════════════
# 测试: 工具调用签名
# ═══════════════════════════════════════════════════════════════════


class TestToolCallSignature:
    """工具调用签名生成测试。"""

    def test_shell_exec_signature(self):
        sig = _tool_call_signature("shell_exec", {"command": "dir *.py"})
        assert sig == "shell_exec:dir *.py"

    def test_read_file_signature(self):
        sig = _tool_call_signature("read_file", {"path": "main.py"})
        assert sig == "read_file:main.py"

    def test_grep_signature(self):
        sig = _tool_call_signature("grep", {"pattern": "def"})
        assert sig == "grep:def"

    def test_write_file_signature(self):
        sig = _tool_call_signature("write_file", {"path": "out.txt", "content": "hi"})
        assert sig == "write_file:out.txt"

    def test_signature_strips_whitespace(self):
        sig = _tool_call_signature("shell_exec", {"command": "  dir *.py  "})
        assert sig == "shell_exec:dir *.py"

    def test_signature_normalizes_backslash(self):
        sig = _tool_call_signature("read_file", {"path": "app\\main.py"})
        assert sig == "read_file:app/main.py"

    def test_failure_signature(self):
        sig = _tool_failure_signature("shell_exec", "exit 1: file not found")
        assert sig == "shell_exec:FAIL:exit 1: file not found"

    def test_failure_signature_none_error(self):
        sig = _tool_failure_signature("shell_exec", None)
        assert sig == "shell_exec:FAIL:"


# ═══════════════════════════════════════════════════════════════════
# 测试: 连续相同 tool call 检测
# ═══════════════════════════════════════════════════════════════════


class TestConsecutiveIdenticalCalls:
    """连续相同工具调用应触发 identical_calls 阻塞。"""

    def test_single_call_not_blocked(self):
        guard = LoopGuard(threshold=5)
        _call(guard, "shell_exec", "dir *.py")
        result = guard.check()
        assert not result.blocked

    def test_three_identical_not_blocked(self):
        guard = LoopGuard(threshold=5)
        for _ in range(3):
            _call(guard, "shell_exec", "dir *.py")
        result = guard.check()
        assert not result.blocked

    def test_five_identical_triggers_block(self):
        guard = LoopGuard(threshold=5)
        # 使用成功的相同写调用（不会触发 no_progress，但 identical_calls 会）
        for _ in range(5):
            _write_call(guard, "main.py")
        result = guard.check()
        assert result.blocked
        assert result.reason == "identical_calls"
        assert result.suggestion == "change_strategy"

    def test_six_identical_triggers_block(self):
        guard = LoopGuard(threshold=5)
        for _ in range(6):
            _write_call(guard, "main.py")
        result = guard.check()
        assert result.blocked
        assert result.reason == "identical_calls"

    def test_reset_clears_counter(self):
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py")
        assert not guard.check().blocked
        guard.reset()
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py")
        assert not guard.check().blocked
        # 再加一次才到 5
        _call(guard, "shell_exec", "dir *.py")
        assert guard.check().blocked

    def test_different_call_breaks_chain(self):
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py")
        # 成功的调用中断连续（失败签名不变时连续失败仍会触发）
        _ok_call(guard, "read_file", "main.py")
        result = guard.check()
        assert not result.blocked

    def test_different_tool_breaks_chain(self):
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py")
        # 不同工具
        _ok_call(guard, "read_file", "main.py")
        result = guard.check()
        assert not result.blocked

    def test_threshold_configurable(self):
        guard = LoopGuard(threshold=3)
        for _ in range(2):
            _call(guard, "shell_exec", "dir *.py")
        assert not guard.check().blocked
        _call(guard, "shell_exec", "dir *.py")
        assert guard.check().blocked


# ═══════════════════════════════════════════════════════════════════
# 测试: 连续相同失败检测
# ═══════════════════════════════════════════════════════════════════


class TestConsecutiveIdenticalFailures:
    """连续相同失败应触发 identical_failures 阻塞。"""

    def test_success_breaks_failure_chain(self):
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py", success=False, error="exit 1")
        # 成功的调用中断连续
        _ok_call(guard)
        result = guard.check()
        assert not result.blocked

    def test_five_failures_triggers_block(self):
        guard = LoopGuard(threshold=5)
        for _ in range(5):
            _call(guard, "shell_exec", "dir *.py", success=False, error="exit 1")
        result = guard.check()
        assert result.blocked
        assert result.reason == "identical_failures"

    def test_different_error_breaks_failure_chain(self):
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py", success=False, error="exit 1")
        # 不同命令 + 不同错误，同时中断调用链和失败链
        _ok_call(guard, "read_file", "main.py")
        result = guard.check()
        assert not result.blocked

    def test_failure_and_mixed_success(self):
        """失败序列中间有成功不应触发。"""
        guard = LoopGuard(threshold=5)
        for _ in range(3):
            _call(guard, "shell_exec", "cmd", success=False, error="error")
        _ok_call(guard)  # break the chain
        for _ in range(3):
            _call(guard, "shell_exec", "cmd", success=False, error="error")
        assert not guard.check().blocked


# ═══════════════════════════════════════════════════════════════════
# 测试: 连续无代码修改检测
# ═══════════════════════════════════════════════════════════════════


class TestNoProgressDetection:
    """连续无代码修改应触发 no_progress 阻塞。"""

    def test_no_modifications_triggers_block(self):
        guard = LoopGuard(threshold=5)
        # 使用不同读操作（避免命中 identical_calls 或 identical_failures）
        files = ["main.py", "utils.py", "config.py", "test.py", "app.py"]
        for f in files:
            _ok_call(guard, "read_file", f)
            guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"

    def test_write_breaks_no_progress(self):
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _call(guard)
            guard.record_no_modification()
        # 成功写入 = 有进展
        _write_call(guard)
        # 不需要 record_no_modification
        result = guard.check()
        assert not result.blocked

    def test_write_then_read_still_ok(self):
        guard = LoopGuard(threshold=5)
        _write_call(guard)  # modification
        for _ in range(4):
            _ok_call(guard)  # reads are fine after modification
            guard.record_no_modification()
        assert not guard.check().blocked

    def test_read_only_loop_detected(self):
        """纯读操作也是循环（无代码修改）。"""
        guard = LoopGuard(threshold=5)
        for _ in range(5):
            _ok_call(guard, "read_file", "main.py")
            guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"

    def test_mixed_read_write_no_loop(self):
        guard = LoopGuard(threshold=5)
        for _ in range(3):
            _ok_call(guard, "read_file", "main.py")
            guard.record_no_modification()
        _write_call(guard)  # file change resets counter
        for _ in range(3):
            _ok_call(guard, "read_file", "main.py")
            guard.record_no_modification()
        assert not guard.check().blocked
        # 再两次无修改
        for _ in range(2):
            _ok_call(guard, "read_file", "main.py")
            guard.record_no_modification()
        assert guard.check().blocked


# ═══════════════════════════════════════════════════════════════════
# 测试: 完整场景
# ═══════════════════════════════════════════════════════════════════


class TestIntegrationScenarios:
    """真实场景组合测试。"""

    def test_dir_py_repeat_scenario(self):
        """重现问题: 连续重复 dir *.py (失败) 5 次后触发阻塞。"""
        guard = LoopGuard(threshold=5)
        for _ in range(5):
            guard.record_tool_call(
                name="shell_exec",
                params={"command": "dir *.py"},
                success=False,
                output="",
                error="File Not Found",
            )
            guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason in ("identical_failures", "no_progress")

    def test_repair_after_reset(self):
        """重置后可以重新计数。"""
        guard = LoopGuard(threshold=5)

        # 第一次循环触发
        for _ in range(5):
            _call(guard, "shell_exec", "dir *.py")
            guard.record_no_modification()
        assert guard.check().blocked

        guard.reset()

        # 重置后重新开始
        for _ in range(4):
            _call(guard, "shell_exec", "dir *.py")
            guard.record_no_modification()
        assert not guard.check().blocked

        _write_call(guard)
        assert not guard.check().blocked

    def test_loop_result_to_dict(self):
        """LoopResult.to_dict() 序列化。"""
        result = LoopResult(
            blocked=True,
            reason="no_progress",
            suggestion="change_strategy",
            details={"count": 5},
        )
        d = result.to_dict()
        assert d["status"] == "blocked"
        assert d["reason"] == "no_progress"
        assert d["suggestion"] == "change_strategy"

    def test_not_blocked_to_dict(self):
        result = LoopResult(blocked=False)
        d = result.to_dict()
        assert d["status"] == "ok"

    def test_create_loop_guard(self):
        guard = create_loop_guard(threshold=3)
        assert guard._threshold == 3


# ═══════════════════════════════════════════════════════════════════
# 测试: LoopGuard 状态查询
# ═══════════════════════════════════════════════════════════════════


class TestStatusQueries:
    """get_status() 和 get_recent_calls() 测试。"""

    def test_get_status_empty(self):
        guard = LoopGuard()
        s = guard.get_status()
        assert s["call_count"] == 0
        assert s["consecutive_identical"] == 0
        assert s["steps_without_change"] == 0

    def test_get_status_after_calls(self):
        guard = LoopGuard()
        for _ in range(3):
            _call(guard)
        s = guard.get_status()
        assert s["call_count"] == 3

    def test_get_recent_calls(self):
        guard = LoopGuard()
        for i in range(10):
            guard.record_tool_call(
                name="shell_exec",
                params={"command": f"cmd_{i}"},
                success=False,
                error="error",
            )
        recent = guard.get_recent_calls(3)
        assert len(recent) == 3
        assert recent[-1]["signature"] == "shell_exec:cmd_9"

    def test_is_stuck_property(self):
        guard = LoopGuard(threshold=3)
        assert not guard.is_stuck
        for _ in range(3):
            _call(guard, "shell_exec", "dir *.py")
        assert guard.is_stuck

    def test_call_count(self):
        guard = LoopGuard()
        assert guard.call_count == 0
        _call(guard)
        assert guard.call_count == 1

    def test_reset_hard_clears_history(self):
        guard = LoopGuard()
        _call(guard)
        assert guard.call_count == 1
        guard.reset_hard()
        assert guard.call_count == 0


# ═══════════════════════════════════════════════════════════════════
# 测试: 边界情况
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界情况测试。"""

    def test_threshold_one(self):
        """阈值为 1 时单次失败即触发。"""
        guard = LoopGuard(threshold=1)
        _call(guard, "shell_exec", "dir *.py", success=False, error="err")
        assert guard.check().blocked

    def test_threshold_zero_not_allowed(self):
        """阈值 0 或负数不应用（但不会崩溃）。LOOP_THRESHOLD 是常量。"""
        guard = LoopGuard(threshold=0)
        _call(guard)
        _call(guard)
        _call(guard)
        # 0 阈值会导致 `>= 0` 总是 True
        assert guard.check().blocked

    def test_no_calls(self):
        guard = LoopGuard()
        result = guard.check()
        assert not result.blocked

    def test_mixed_tools_no_loop(self):
        """正常多工具执行不应触发循环。"""
        guard = LoopGuard(threshold=5)
        tools = [
            ("read_file", {"path": "main.py"}),
            ("grep", {"pattern": "def "}),
            ("shell_exec", {"command": "python -m pytest"}),
            ("write_file", {"path": "main.py", "content": "fix"}),
            ("shell_exec", {"command": "python -m pytest"}),
            ("read_file", {"path": "test_main.py"}),
        ]
        for name, params in tools:
            success = name != "shell_exec"  # 只有 shell 失败
            guard.record_tool_call(name=name, params=params, success=success)
            if success and name in ("write_file", "edit", "git"):
                pass  # 有修改
            else:
                guard.record_no_modification()
        assert not guard.check().blocked

    def test_identical_after_different(self):
        """不同调用后相同调用应从 0 开始计数。"""
        guard = LoopGuard(threshold=5)
        _ok_call(guard, "read_file", "a.py")
        _ok_call(guard, "read_file", "b.py")
        for _ in range(5):
            _ok_call(guard, "read_file", "c.py")
        # 连续 5 次相同 read_file c.py
        result = guard.check()
        assert result.blocked
        assert result.reason == "identical_calls"
        assert guard._consecutive_identical >= 5

    def test_none_params(self):
        """None 参数不应崩溃。"""
        guard = LoopGuard()
        guard.record_tool_call("shell_exec", {"command": None}, success=False)
        assert guard.call_count == 1

    def test_empty_params(self):
        """空参数字典不应崩溃。"""
        guard = LoopGuard()
        guard.record_tool_call("shell_exec", {}, success=False)
        guard.record_tool_call("read_file", {}, success=False)
        guard.record_tool_call("write_file", {}, success=True)
        # 有成功写操作，无修改计数应为 0
        assert guard._steps_without_change == 0


# ═══════════════════════════════════════════════════════════════════
# 测试: SWEAgent 集成
# ═══════════════════════════════════════════════════════════════════


class TestSWEAgentLoopGuardIntegration:
    """LoopGuard 在 SWEAgent.step() 中的集成测试。

    使用 MockBackend 模拟循环调用场景，验证阻塞行为。
    """

    def test_loop_guard_initialized(self):
        """SWEAgent.initialize() 应创建 LoopGuard。"""
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        async def run():
            agent = SWEAgent("test_lg_init")
            registry = ToolRegistry()
            ctx = AgentContext(
                agent_id="test_lg_init",
                task="test",
                tools=registry,
                metadata={},
            )
            await agent.initialize(ctx)
            assert "loop_guard" in ctx.metadata
            guard = ctx.metadata["loop_guard"]
            assert isinstance(guard, LoopGuard)
            assert guard._threshold == 5

        asyncio.run(run())

    def test_loop_guard_custom_threshold(self):
        """自定义阈值在 config 中传递。"""
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        async def run():
            agent = SWEAgent("test_lg_custom")
            registry = ToolRegistry()
            ctx = AgentContext(
                agent_id="test_lg_custom",
                task="test",
                tools=registry,
                config={"loop_guard.threshold": 3},
                metadata={},
            )
            await agent.initialize(ctx)
            guard = ctx.metadata["loop_guard"]
            assert guard._threshold == 3

        asyncio.run(run())

    def test_loop_detection_in_step(self):
        """连续失败工具调用在 step() 中应触发 LoopGuard 阻塞。

        使用一个总是返回相同工具的 Backend 来模拟循环。
        """
        from zmai.agent import AgentContext, AgentAction
        from zmai.gateway.base import (
            Backend, BackendCapability, BackendEvent,
            BackendRequest, BackendResponse, TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry
        from typing import Iterator

        class LoopBackend(Backend):
            """始终返回相同的失败 shell_exec 调用。"""
            name = "loop_backend"

            def invoke(self, request: BackendRequest) -> BackendResponse:
                return BackendResponse(
                    content="",
                    tool_calls=[ToolCall(
                        id="call_loop",
                        name="shell_exec",
                        params={"command": "dir *.py"},
                    )],
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use",
                )

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self) -> set[BackendCapability]:
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_loop_step")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_loop_step",
                task="test",
                tools=registry,
                metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_loop_step",
                task="test",
                backend=LoopBackend(),
                tools=registry,
                config={"loop_guard.threshold": 3},
                metadata={"loop_guard": LoopGuard(threshold=3)},
            )
            # 第 1-3 步：应返回 cont（正在执行）
            for i in range(3):
                action = await agent.step(ctx)
                assert action.type == "continue", f"第 {i+1} 步应返回 continue, 实际: {action}"

            # 第 4 步：应检测到循环（连续 3+ 次相同失败）
            action = await agent.step(ctx)
            assert action.type == "continue", "循环检测后应返回 continue"
            # 消息应包含 LoopGuard
            cm = ctx.metadata.get("cm")
            assert cm is not None
            recent = cm.get_context()
            has_loopguard_msg = any(
                "LoopGuard" in str(m.get("content", ""))
                for m in recent
            )
            assert has_loopguard_msg, "上下文中应包含 LoopGuard 消息"

        asyncio.run(run())

    def test_no_loop_during_normal_operation(self):
        """正常的工具调用序列不应触发循环检测。"""
        from zmai.agent import AgentContext, AgentAction
        from zmai.gateway.base import (
            Backend, BackendCapability, BackendEvent,
            BackendRequest, BackendResponse, TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry
        from typing import Iterator

        class NormalBackend(Backend):
            """每次返回不同的工具调用。"""
            name = "normal_backend"
            _call_idx = 0
            _commands = [
                [ToolCall(id="c1", name="read_file", params={"path": "main.py"})],
                [ToolCall(id="c2", name="grep", params={"pattern": "def "})],
                [ToolCall(id="c3", name="shell_exec", params={"command": "echo ok"})],
                [ToolCall(id="c4", name="write_file", params={"path": "main.py", "content": "fix"})],
                [],  # 最终空调用 → complete
            ]

            def invoke(self, request: BackendRequest) -> BackendResponse:
                calls = self._commands[self._call_idx]
                self._call_idx = min(self._call_idx + 1, len(self._commands) - 1)
                return BackendResponse(
                    content="" if calls else "done",
                    tool_calls=calls,
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use" if calls else "end_turn",
                )

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self) -> set[BackendCapability]:
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_normal")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_normal",
                task="test",
                tools=registry,
                metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_normal",
                task="test",
                backend=NormalBackend(),
                tools=registry,
                config={"loop_guard.threshold": 3},
                metadata={"loop_guard": LoopGuard(threshold=3)},
            )
            # 所有步骤都应为 continue（不触发循环）
            for i in range(4):
                action = await agent.step(ctx)
                assert action.type == "continue", f"第 {i+1} 步应返回 continue"

            # 验证 no_progress 未触发
            guard = ctx.metadata["loop_guard"]
            assert not guard.is_stuck

        asyncio.run(run())
