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

from zmai.swe.loop_guard import (
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
        # 5 步无修改，且**确实在重复**（main.py 读了两次）：这是真正的空转。
        files = ["main.py", "utils.py", "config.py", "test.py", "main.py"]
        for f in files:
            _ok_call(guard, "read_file", f)
            guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"

    def test_first_pass_investigation_is_not_no_progress(self):
        """首次通读一批**新**文件不得被判成 no_progress。

        实测（benchmarks/results/swebench_lite）：7 个真实 run 中 6 个的首次
        LoopGuard 触发时，全历史精确重复调用数为 0 —— pylint-5859 在第 7 次调用
        （grep ×3 + git log + read_file ×3，全部是新目标）就被判 no_progress，
        运行时随即注入"检测到重复的无进展动作 / 不要重复上述任何 read/shell"。
        """
        guard = LoopGuard(threshold=5)
        files = ["main.py", "utils.py", "config.py", "test.py", "app.py"]
        for f in files:
            _ok_call(guard, "read_file", f)
            guard.record_no_modification()
        result = guard.check()
        assert not result.blocked, (
            "5 步读了 5 个互不相同的新文件，没有任何重复证据，不得判 no_progress"
        )

    def test_repeat_after_investigation_still_blocked(self):
        """先读新文件、随后开始重复时，no_progress 仍必须触发。"""
        guard = LoopGuard(threshold=5)
        for f in ["main.py", "utils.py", "config.py"]:
            _ok_call(guard, "read_file", f)
            guard.record_no_modification()
        assert not guard.check().blocked
        for _ in range(2):  # 第 4、5 步开始重复 utils.py
            _ok_call(guard, "read_file", "utils.py")
            guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"

    def test_modification_resets_duplicate_evidence(self):
        """改动过代码后，新一轮调查的重复证据从零开始计。"""
        guard = LoopGuard(threshold=5)
        for _ in range(3):
            _ok_call(guard, "read_file", "main.py")
            guard.record_no_modification()
        _write_call(guard)  # 真实进展 → 本轮停滞的重复证据清零
        for f in ["a.py", "b.py", "c.py", "d.py", "e.py"]:
            _ok_call(guard, "read_file", f)
            guard.record_no_modification()
        assert not guard.check().blocked

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
        from collections.abc import Iterator

        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendRequest,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

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

    def test_no_loop_during_normal_operation(self, tmp_path):
        """正常的工具调用序列不应触发循环检测。"""
        from collections.abc import Iterator

        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendRequest,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

        class NormalBackend(Backend):
            """每次返回不同的工具调用。"""
            name = "normal_backend"
            _call_idx = 0
            _commands = [
                [ToolCall(id="c1", name="read_file", params={"path": "main.py"})],
                [ToolCall(id="c2", name="grep", params={"pattern": "def "})],
                [ToolCall(id="c3", name="shell_exec", params={"command": "echo ok"})],
                [ToolCall(id="c4", name="write_file", params={"path": "main.py", "content": "fix"})],  # noqa: E501
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

        # 工作区必须自包含：write_file 修改的是**已有文件**才算进展。
        # 依赖 cwd 里恰好存在 main.py 会让本用例在干净 checkout（CI）上失败。
        (tmp_path / "main.py").write_text("original", encoding="utf-8")

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
                config={"loop_guard.threshold": 3, "project_path": str(tmp_path)},
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


# ═══════════════════════════════════════════════════════════════════
# 测试: 创建临时文件不算修复进展
# ═══════════════════════════════════════════════════════════════════


def _write_temp_call(guard: LoopGuard, path: str = "_dbg_plugin.py") -> None:
    """模拟创建临时调试文件：write_file 成功但 ws_changed=False（仅新增文件）。"""
    guard.record_tool_call(
        name="write_file",
        params={"path": path, "content": "debug code"},
        success=True,
        output="written",
        error=None,
        ws_changed=False,
    )


def _write_existing_call(guard: LoopGuard, path: str = "app.py") -> None:
    """模拟修改已有源码文件：write_file 成功且 ws_changed=True。"""
    guard.record_tool_call(
        name="write_file",
        params={"path": path, "content": "fixed code"},
        success=True,
        output="written",
        error=None,
        ws_changed=True,
    )


class TestTempFileNotProgress:
    """创建临时调试插件等新文件不应被当成修复进展。

    根因：write_file 成功曾无条件触发 _record_modification()，导致创建
    _dbg_plugin.py 等临时文件也会重置 no_progress 计数、清除 force_edit。
    修复：ws_changed=False 时覆盖工具名判断，不记录进展。
    """

    def test_temp_file_write_does_not_reset_no_progress(self):
        """创建临时文件（ws_changed=False）不应重置 no_progress 计数。"""
        guard = LoopGuard(threshold=5)
        # 先累积 3 步无修改
        for _ in range(3):
            _ok_call(guard, "read_file", "app.py")
            guard.record_no_modification()
        # 创建临时文件：不应重置计数
        _write_temp_call(guard)
        guard.record_no_modification()  # 这一步仍算无修改
        # 再累积 1 步达到阈值
        _ok_call(guard, "read_file", "app.py")
        guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"

    def test_existing_file_write_resets_no_progress(self):
        """修改已有源码（ws_changed=True）应正常重置 no_progress 计数。"""
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _ok_call(guard, "read_file", "app.py")
            guard.record_no_modification()
        # 修改已有文件：重置计数
        _write_existing_call(guard)
        # 不需要 record_no_modification
        result = guard.check()
        assert not result.blocked

    def test_consecutive_temp_files_trigger_no_progress(self):
        """连续创建临时文件应触发 no_progress 阻塞（不算进展）。"""
        guard = LoopGuard(threshold=5)
        for i in range(5):
            _write_temp_call(guard, path=f"_dbg_plugin_{i}.py")
            guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"

    def test_temp_file_then_real_edit_still_progress(self):
        """先创建临时文件，再修改已有源码：真实修改应重置计数。"""
        guard = LoopGuard(threshold=5)
        for _ in range(3):
            _write_temp_call(guard)
            guard.record_no_modification()
        # 真实修改：重置计数
        _write_existing_call(guard)
        # 再读 4 步也不会阻塞（因为计数已重置）
        for _ in range(4):
            _ok_call(guard, "read_file", "app.py")
            guard.record_no_modification()
        assert not guard.check().blocked

    def test_edit_tool_with_ws_changed_false_not_progress(self):
        """edit 成功但 ws_changed=False（理论上不应发生，但防御性测试）。"""
        guard = LoopGuard(threshold=5)
        for _ in range(4):
            _ok_call(guard, "read_file", "app.py")
            guard.record_no_modification()
        # edit 成功但 ws_changed=False：不应重置
        guard.record_tool_call(
            name="edit",
            params={"path": "app.py", "old": "x", "new": "y"},
            success=True,
            output="replaced",
            error=None,
            ws_changed=False,
        )
        guard.record_no_modification()
        result = guard.check()
        assert result.blocked
        assert result.reason == "no_progress"


# ═══════════════════════════════════════════════════════════════════
# 测试: soft_reset 衰减式重置
# ═══════════════════════════════════════════════════════════════════


class TestSoftReset:
    """LoopGuard 拦截后用 soft_reset 替代 reset，保留 no_progress 历史压力。

    根因：每次拦截后 guard.reset() 无条件清零所有计数器，模型每 N 步
    获得一次完整免费预算，形成"每 5 步拦一次"的固定点。
    修复：soft_reset 清零瞬时模式计数器，但 no_progress 只减去 threshold-2，
    使连续拦截的间隔逐步缩短。真实修改仍会完全清零。
    """

    def test_soft_reset_preserves_no_progress_pressure(self):
        """soft_reset 后 no_progress 计数应保留部分历史，不全清零。"""
        guard = LoopGuard(threshold=5)
        # 累积到阈值（5）
        for _ in range(5):
            _ok_call(guard, "read_file", "a.py")
            guard.record_no_modification()
        assert guard.check().blocked
        # soft_reset：no_progress 应保留 5 - (5-2) = 2
        guard.soft_reset()
        status = guard.get_status()
        assert status["steps_without_change"] == 2
        # 再累积 3 步（2+3=5）应再次阻塞，而不是需要 5 步
        for _ in range(3):
            _ok_call(guard, "read_file", "b.py")
            guard.record_no_modification()
        assert guard.check().blocked

    def test_soft_reset_clears_transient_counters(self):
        """soft_reset 应清零 identical_calls 和 identical_failures。"""
        guard = LoopGuard(threshold=5)
        # 累积 identical_calls
        for _ in range(3):
            _ok_call(guard, "read_file", "same.py")
        # 累积 identical_failures
        for _ in range(3):
            _call(guard, "shell_exec", "cmd", success=False, error="fail")
        guard.soft_reset()
        status = guard.get_status()
        assert status["consecutive_identical"] == 0
        assert status["consecutive_failures"] == 0

    def test_real_modification_clears_no_progress_after_soft_reset(self):
        """soft_reset 后真实修改应完全清零 no_progress（不误拦截）。"""
        guard = LoopGuard(threshold=5)
        for _ in range(5):
            _ok_call(guard, "read_file", "a.py")
            guard.record_no_modification()
        guard.soft_reset()  # 保留 2 步压力
        # 真实修改：完全清零
        _write_existing_call(guard)
        status = guard.get_status()
        assert status["steps_without_change"] == 0
        # 再读 4 步也不会阻塞
        for _ in range(4):
            _ok_call(guard, "read_file", "b.py")
            guard.record_no_modification()
        assert not guard.check().blocked

    def test_soft_reset_with_low_count_not_negative(self):
        """no_progress 计数低于 threshold-2 时，soft_reset 后不应为负数。"""
        guard = LoopGuard(threshold=5)
        for _ in range(2):
            _ok_call(guard, "read_file", "a.py")
            guard.record_no_modification()
        guard.soft_reset()
        status = guard.get_status()
        assert status["steps_without_change"] == 0  # 2 - 3 = max(0, -1) = 0

    def test_reset_still_full_clear(self):
        """原有 reset() 方法应保持完全清零行为（FixDriving 触发时使用）。"""
        guard = LoopGuard(threshold=5)
        for _ in range(5):
            _ok_call(guard, "read_file", "a.py")
            guard.record_no_modification()
        guard.reset()
        status = guard.get_status()
        assert status["steps_without_change"] == 0
        assert status["consecutive_identical"] == 0
        assert status["consecutive_failures"] == 0
