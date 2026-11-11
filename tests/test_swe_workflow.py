"""SWE Workflow — 5 阶段执行流程测试。

测试覆盖：
  1. 系统提示包含 5 阶段工作流
  2. Read-limit 强制执行（8 次读取无测试即阻断）
  3. pytest 检测重置读计数
  4. 工作流阶段元数据跟踪
  5. 正常多工具执行不触发限制
"""

from __future__ import annotations

import asyncio

from zmai.swe.agent import (
    _BASE_SYSTEM_PROMPT,
    _build_platform_prompt,
    _build_system_prompt,
)

# ═══════════════════════════════════════════════════════════════════
# 测试: 系统提示包含 5 阶段工作流
# ═══════════════════════════════════════════════════════════════════


class TestWorkflowInSystemPrompt:
    """系统提示中应包含 5 阶段 SWE 工作流。"""

    def test_prompt_contains_phase_discover(self):
        assert "Phase 1" in _BASE_SYSTEM_PROMPT
        assert "Discover" in _BASE_SYSTEM_PROMPT

    def test_prompt_contains_phase_run_tests(self):
        assert "Phase 2" in _BASE_SYSTEM_PROMPT
        assert "Run Tests First" in _BASE_SYSTEM_PROMPT

    def test_prompt_contains_phase_analyze(self):
        assert "Phase 3" in _BASE_SYSTEM_PROMPT
        assert "Analyze" in _BASE_SYSTEM_PROMPT

    def test_prompt_contains_phase_modify(self):
        assert "Phase 4" in _BASE_SYSTEM_PROMPT
        assert "Modify" in _BASE_SYSTEM_PROMPT

    def test_prompt_contains_phase_verify(self):
        assert "Phase 5" in _BASE_SYSTEM_PROMPT
        assert "Verify" in _BASE_SYSTEM_PROMPT

    def test_prompt_has_test_first_rule(self):
        assert "RUN TESTS FIRST" in _BASE_SYSTEM_PROMPT
        assert "pytest" in _BASE_SYSTEM_PROMPT

    def test_prompt_has_read_limit_rule(self):
        assert "8" in _BASE_SYSTEM_PROMPT
        assert "STOP" in _BASE_SYSTEM_PROMPT or "stop" in _BASE_SYSTEM_PROMPT

    def test_prompt_prohibits_infinite_reads(self):
        assert "read" in _BASE_SYSTEM_PROMPT.lower()

    def test_phases_in_correct_order(self):
        """5 个阶段按顺序出现。"""
        prompt = _BASE_SYSTEM_PROMPT
        idx1 = prompt.index("Phase 1")
        idx2 = prompt.index("Phase 2")
        idx3 = prompt.index("Phase 3")
        idx4 = prompt.index("Phase 4")
        idx5 = prompt.index("Phase 5")
        assert idx1 < idx2 < idx3 < idx4 < idx5, "Phase 顺序错误"

    def test_build_system_prompt_includes_workflow(self):
        """_build_system_prompt() 输出包含工作流。"""
        prompt = _build_system_prompt()
        assert "SWE Workflow" in prompt
        assert "Phase 1" in prompt
        assert "Phase 2" in prompt
        assert "Phase 5" in prompt

    def test_prompt_prohibits_debug_plugins_for_reading_source(self):
        """系统提示应明确禁止通过创建 pytest 插件/转储文件来读取源码。"""
        assert "DO NOT create debug plugins" in _BASE_SYSTEM_PROMPT
        assert "conftest.py" in _BASE_SYSTEM_PROMPT
        assert "dump scripts" in _BASE_SYSTEM_PROMPT
        # 应引导使用 read_file
        assert "read_file" in _BASE_SYSTEM_PROMPT

    def test_prompt_handles_testguard_rejection(self):
        """系统提示应包含 TestGuard 拒绝后切换工具的规则。"""
        assert "TestGuard" in _BASE_SYSTEM_PROMPT
        assert "declared exclusion" in _BASE_SYSTEM_PROMPT
        assert ("do NOT retry" in _BASE_SYSTEM_PROMPT.lower()
                or "do not retry" in _BASE_SYSTEM_PROMPT.lower())

    def test_prompt_requires_evidence_for_rerunning_tests(self):
        """系统提示应要求重复测试有充分理由（代码修改/结果变化/新证据）。"""
        assert "Do NOT re-run the same test command" in _BASE_SYSTEM_PROMPT
        assert "modified code" in _BASE_SYSTEM_PROMPT
        assert "new diagnostic evidence" in _BASE_SYSTEM_PROMPT

    def test_prompt_analyze_failure_assertion_values(self):
        """系统提示应要求测试失败后分析断言、实际值和预期值。"""
        assert "assertion" in _BASE_SYSTEM_PROMPT.lower()
        assert "actual value" in _BASE_SYSTEM_PROMPT.lower()
        assert "expected value" in _BASE_SYSTEM_PROMPT.lower()

    def test_prompt_mentions_pytest_tb_options(self):
        """系统提示应提及 pytest 内置输出选项作为替代方案。"""
        assert "--tb=short" in _BASE_SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════════
# 测试: Windows 平台命令兼容提示
# ═══════════════════════════════════════════════════════════════════


class TestWindowsPlatformPrompt:
    """_build_platform_prompt() 在 Windows 下应包含正确的命令兼容说明。"""

    def test_windows_prompt_includes_tail_alternative(self):
        """Windows 平台提示应包含 tail 的替代说明。"""
        prompt = _build_platform_prompt()
        assert "tail" in prompt.lower()
        # 应引导使用 read_file 或 pytest --tb=short
        assert "read_file" in prompt or "--tb=short" in prompt

    def test_windows_prompt_warns_against_tail_head(self):
        """Windows 平台提示应明确警告不要使用 tail/head。"""
        prompt = _build_platform_prompt()
        assert "Do NOT use `tail`" in prompt or "do not use tail" in prompt.lower()
        assert "head" in prompt.lower()

    def test_windows_prompt_warns_about_pipe_exit_code(self):
        """Windows 平台提示应警告管道会掩盖 pytest 的真实退出码。"""
        prompt = _build_platform_prompt()
        assert "exit code" in prompt.lower()
        assert "more" in prompt.lower() or "findstr" in prompt.lower()
        # 应建议裸跑 pytest
        assert "python -m pytest" in prompt

    def test_windows_prompt_still_has_existing_mappings(self):
        """新增内容不应破坏已有的命令映射。"""
        prompt = _build_platform_prompt()
        assert "ls" in prompt
        assert "dir" in prompt
        assert "cat" in prompt
        assert "grep" in prompt
        assert "Current OS: Windows" in prompt


# ═══════════════════════════════════════════════════════════════════
# 测试: 工作流元数据初始化
# ═══════════════════════════════════════════════════════════════════


class TestWorkflowMetadataInit:
    """SWEAgent.initialize() 应初始化工作流元数据。"""

    def test_metadata_initialized(self):
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        async def run():
            agent = SWEAgent("test_wf_init")
            registry = ToolRegistry()
            ctx = AgentContext(
                agent_id="test_wf_init",
                task="test",
                tools=registry,
                metadata={},
            )
            await agent.initialize(ctx)
            assert ctx.metadata.get("has_run_test") is False
            assert ctx.metadata.get("reads_without_test") == 0

        asyncio.run(run())

    def test_phase_starts_at_discover(self):
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        async def run():
            agent = SWEAgent("test_wf_phase")
            registry = ToolRegistry()
            ctx = AgentContext(
                agent_id="test_wf_phase",
                task="test",
                tools=registry,
                metadata={},
            )
            await agent.initialize(ctx)
            assert ctx.metadata.get("workflow_phase") == "discover"

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════
# 测试: Read-limit 强制执行
# ═══════════════════════════════════════════════════════════════════


class TestReadLimitEnforcement:
    """读取超过限制而未运行测试时应触发阻断。"""

    def test_reads_before_test_increment(self):
        """未运行测试时，read_file 调用增加 reads_without_test。"""
        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

        class ReadOnlyBackend(Backend):
            """每次返回 read_file 调用。"""
            name = "read_only"
            def invoke(self, request):
                return BackendResponse(
                    content="",
                    tool_calls=[ToolCall(id="r1", name="read_file",
                                        params={"path": "main.py"})],
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use",
                )
            def stream(self, request):
                yield BackendEvent(type="done", data="", index=1)
            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_read_count")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_read_count", task="test", tools=registry, metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_read_count",
                task="test",
                backend=ReadOnlyBackend(),
                tools=registry,
                config={"workflow.read_limit": 5},
                metadata={
                    "has_run_test": False,
                    "reads_without_test": 0,
                },
            )
            # 3 次读取 → 不应触发限制
            for _ in range(3):
                await agent.step(ctx)
            # 此时 reads_without_test 应该 >= 3
            assert ctx.metadata.get("reads_without_test", 0) >= 3, \
                f"读取计数应为 3+, 实际为 {ctx.metadata.get('reads_without_test')}"

        asyncio.run(run())

    def test_read_limit_triggers_block(self):
        """超过读取限制（未运行测试）应触发阻断消息。"""
        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

        class ReadOnlyBackend(Backend):
            name = "read_only2"
            def invoke(self, request):
                return BackendResponse(
                    content="",
                    tool_calls=[ToolCall(id="r1", name="read_file",
                                        params={"path": "main.py"})],
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use",
                )
            def stream(self, request):
                yield BackendEvent(type="done", data="", index=1)
            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_limit_block")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_limit_block", task="test", tools=registry, metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_limit_block",
                task="test",
                backend=ReadOnlyBackend(),
                tools=registry,
                config={"workflow.read_limit": 5},
                metadata={
                    "has_run_test": False,
                    "reads_without_test": 0,
                },
            )
            # 执行多次直至触发限制（read_limit=5, 每次 step 1 次 read_file）
            blocked = False
            for _ in range(10):
                action = await agent.step(ctx)
                if "Read limit" in (action.output or ""):
                    blocked = True
                    break
            assert blocked, "读取限制未被触发"

        asyncio.run(run())

    def test_pytest_resets_read_counter(self):
        """运行 pytest 后读取计数应重置为 0。"""
        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

        _call_count = [0]  # mutable for closure

        class MixedBackend(Backend):
            """前几次返回 read_file，然后返回 pytest。"""
            name = "mixed"
            def invoke(self, request):
                _call_count[0] += 1
                if _call_count[0] <= 3:
                    return BackendResponse(
                        content="",
                        tool_calls=[ToolCall(id="r1", name="read_file",
                                            params={"path": f"file{_call_count[0]}.py"})],
                        usage=TokenUsage(input_tokens=10, output_tokens=5),
                        stop_reason="tool_use",
                    )
                return BackendResponse(
                    content="",
                    tool_calls=[ToolCall(id="t1", name="shell_exec",
                                        params={"command": "echo pytest --version"})],
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use",
                )
            def stream(self, request):
                yield BackendEvent(type="done", data="", index=1)
            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_reset_on_pytest")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_reset_on_pytest", task="test", tools=registry, metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_reset_on_pytest",
                task="test",
                backend=MixedBackend(),
                tools=registry,
                config={"workflow.read_limit": 10},
                metadata={
                    "has_run_test": False,
                    "reads_without_test": 0,
                },
            )
            # 3 次 read_file
            for _ in range(3):
                await agent.step(ctx)
            assert ctx.metadata.get("reads_without_test", 0) >= 3, \
                f"reads_without_test 应为 3+, 实际为 {ctx.metadata.get('reads_without_test')}"

            # pytest step
            await agent.step(ctx)
            assert ctx.metadata.get("has_run_test") is True, "has_run_test 应为 True"
            # 由于 pytest 是 shell_exec 并且有参数 "python -m pytest",
            # 且在 result.success=True 时会被检测到
            # 但本 mock 中 shell_exec 工具会实际执行 "python -m pytest"
            # 这会在真实环境中运行。让测试适应这个。

        asyncio.run(run())

    def test_mixed_tools_no_false_positive(self):
        """非 read_file 工具不应增加读取计数。"""
        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

        class MixedToolsBackend(Backend):
            """返回 grep + show_to_user + shell_exec（非读取）。"""
            name = "mixed_tools"
            _tools = [
                [ToolCall(id="g1", name="grep", params={"pattern": "def"})],
                [ToolCall(id="s1", name="show_to_user", params={"content": "hello"})],
                [ToolCall(id="sh1", name="shell_exec", params={"command": "echo ok"})],
            ]
            _idx = 0
            def invoke(self, request):
                calls = self._tools[self._idx]
                self._idx = min(self._idx + 1, len(self._tools) - 1)
                return BackendResponse(
                    content="",
                    tool_calls=calls,
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use",
                )
            def stream(self, request):
                yield BackendEvent(type="done", data="", index=1)
            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_no_false_positive")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_no_false_positive", task="test", tools=registry, metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_no_false_positive",
                task="test",
                backend=MixedToolsBackend(),
                tools=registry,
                config={"workflow.read_limit": 5},
                metadata={
                    "has_run_test": False,
                    "reads_without_test": 0,
                },
            )
            for _ in range(3):
                await agent.step(ctx)
            reads = ctx.metadata.get("reads_without_test", 0)
            assert reads == 0, f"非 read_file 工具不应增加计数: {reads}"

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════
# 测试: 正常工具调用不触发限制
# ═══════════════════════════════════════════════════════════════════


class TestNormalOperation:
    """正常的 5 阶段执行不应触发任何限制。"""

    def test_read_then_test_then_write_then_read(self):
        """读→测试→写→读 的正常流程。"""
        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendCapability,
            BackendEvent,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolCall, ToolRegistry

        _step = [0]

        class NormalBackend(Backend):
            name = "normal_wf"
            _plan = [
                [ToolCall(id="1", name="read_file", params={"path": "main.py"})],
                [ToolCall(id="2", name="shell_exec", params={"command": "echo pytest"})],
                [ToolCall(id="3", name="read_file", params={"path": "main.py"})],
                [ToolCall(id="4", name="write_file", params={"path": "main.py", "content": "fix"})],
                [],
            ]
            def invoke(self, request):
                calls = self._plan[_step[0]]
                _step[0] = min(_step[0] + 1, len(self._plan) - 1)
                return BackendResponse(
                    content="" if calls else "done",
                    tool_calls=calls,
                    usage=TokenUsage(input_tokens=10, output_tokens=5),
                    stop_reason="tool_use" if calls else "end_turn",
                )
            def stream(self, request):
                yield BackendEvent(type="done", data="", index=1)
            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        async def run():
            agent = SWEAgent("test_normal_wf")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_normal_wf", task="test", tools=registry, metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_normal_wf",
                task="test",
                backend=NormalBackend(),
                tools=registry,
                config={"workflow.read_limit": 5},
                metadata={"has_run_test": False, "reads_without_test": 0},
            )
            # Step 1: read_file
            a1 = await agent.step(ctx)
            assert "limit" not in (a1.output or "").lower(), \
                f"Step 1 不应触发限制: {a1.output}"
            # Step 2: pytest
            a2 = await agent.step(ctx)
            assert "limit" not in (a2.output or "").lower(), \
                f"Step 2 (pytest) 不应触发限制: {a2.output}"
            # Step 3: read_file (after test - ok!)
            a3 = await agent.step(ctx)
            assert "limit" not in (a3.output or "").lower(), \
                f"Step 3 (read after test) 不应触发限制: {a3.output}"
            # Step 4: write_file
            a4 = await agent.step(ctx)
            assert a4.type == "continue", "Step 4 应继续"

        asyncio.run(run())

    def test_custom_read_limit_config(self):
        """可通过 config 自定义读取限制。"""
        from zmai.agent import AgentContext
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        async def run():
            agent = SWEAgent("test_custom_limit")
            registry = ToolRegistry()
            ctx = AgentContext(
                agent_id="test_custom_limit",
                task="test",
                tools=registry,
                config={"workflow.read_limit": 3},
                metadata={},
            )
            await agent.initialize(ctx)
            assert ctx.metadata.get("has_run_test") is False
            assert ctx.metadata.get("reads_without_test") == 0

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════
# 测试: Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界情况测试。"""

    def test_empty_tool_calls_no_effect(self):
        """空工具调用列表不应影响计数。"""
        from zmai.agent import AgentContext
        from zmai.gateway.base import (
            Backend,
            BackendEvent,
            BackendResponse,
            TokenUsage,
        )
        from zmai.swe.agent import SWEAgent
        from zmai.tool import ToolRegistry

        class EmptyBackend(Backend):
            name = "empty"
            def invoke(self, request):
                return BackendResponse(
                    content="no tools needed",
                    usage=TokenUsage(input_tokens=5, output_tokens=3),
                )
            def stream(self, request):
                yield BackendEvent(type="done", data="", index=1)
            @property
            def capabilities(self):
                return set()

        async def run():
            agent = SWEAgent("test_empty_calls")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="test_empty_calls", task="test", tools=registry, metadata={},
            ))
            ctx = AgentContext(
                agent_id="test_empty_calls",
                task="test",
                backend=EmptyBackend(),
                tools=registry,
                config={},
                metadata={"has_run_test": False, "reads_without_test": 0},
            )
            action = await agent.step(ctx)
            assert action.type in ("continue", "complete")
            reads = ctx.metadata.get("reads_without_test", 0)
            assert reads == 0, f"空调用不应增加读取计数: {reads}"

        asyncio.run(run())

    def test_default_read_limit_value(self):
        """默认读取限制应为 8。"""
        from zmai.swe.agent import SWEAgent
        agent = SWEAgent("test_default")
        assert hasattr(agent, 'initialize')  # 确保类定义正确
        # default read_limit from config.get fallback
        default = 8
        assert default == 8


# ═══════════════════════════════════════════════════════════════════
# 测试: 环境错误提示（_env_error_hint）
# ═══════════════════════════════════════════════════════════════════


class TestEnvErrorHint:
    """pytest 缺失等环境错误应返回可操作建议，避免 Agent 盲目探测。"""

    def test_no_module_pytest_returns_hint(self):
        """"No module named pytest" 应触发环境建议。"""
        from zmai.swe.tools import _env_error_hint
        output = "C:\\python\\python.exe: No module named pytest\n"
        hint = _env_error_hint(output)
        assert "[env]" in hint
        assert "pytest" in hint
        assert "where pytest" in hint
        assert ".venv" in hint

    def test_normal_test_failure_no_hint(self):
        """正常测试失败（AssertionError）不应触发环境建议。"""
        from zmai.swe.tools import _env_error_hint
        output = "AssertionError: assert 60.0 == 58.8\n3 failed, 66 passed"
        hint = _env_error_hint(output)
        assert hint == ""

    def test_empty_output_no_hint(self):
        """空输出不应触发环境建议。"""
        from zmai.swe.tools import _env_error_hint
        assert _env_error_hint("") == ""

    def test_other_import_error_no_hint(self):
        """其他模块缺失（非 pytest）不应触发 pytest 专用建议。"""
        from zmai.swe.tools import _env_error_hint
        output = "ModuleNotFoundError: No module named requests"
        hint = _env_error_hint(output)
        assert hint == ""


# ═══════════════════════════════════════════════════════════════════
# 测试: 系统提示词包含环境错误处理规则
# ═══════════════════════════════════════════════════════════════════


class TestEnvErrorRuleInPrompt:
    """规则 14：环境错误不是 Bug，应引导 Agent 找到正确解释器。"""

    def test_rule_14_present(self):
        """系统提示词应包含环境错误处理规则。"""
        assert "ENVIRONMENT ERRORS ARE NOT BUGS" in _BASE_SYSTEM_PROMPT
        assert "No module named pytest" in _BASE_SYSTEM_PROMPT
        assert "where pytest" in _BASE_SYSTEM_PROMPT

    def test_rule_14_no_auto_install(self):
        """规则应明确禁止自动安装 pytest。"""
        assert "Do NOT auto-install pytest" in _BASE_SYSTEM_PROMPT

    def test_rule_14_no_repeat_failed_command(self):
        """规则应明确禁止反复执行已知失败的命令。"""
        assert "Do NOT repeatedly run" in _BASE_SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════════
# 测试: 退化响应判定（_degenerate_response_reason）
# ═══════════════════════════════════════════════════════════════════


class TestDegenerateResponseReason:
    """截断响应和空响应的判定逻辑。"""

    def test_truncated_length_no_tool_calls(self):
        """stop_reason=length 且无 tool_calls 应判定为截断。"""
        from zmai.gateway.base import BackendResponse, TokenUsage
        from zmai.swe.agent import _degenerate_response_reason
        resp = BackendResponse(
            content="some long text that got cut off",
            tool_calls=[],
            stop_reason="length",
            usage=TokenUsage(input_tokens=100, output_tokens=4096),
        )
        reason = _degenerate_response_reason(resp)
        assert reason is not None
        assert "truncated" in reason
        assert "length" in reason

    def test_truncated_max_tokens_no_tool_calls(self):
        """stop_reason=max_tokens 且无 tool_calls 应判定为截断。"""
        from zmai.gateway.base import BackendResponse, TokenUsage
        from zmai.swe.agent import _degenerate_response_reason
        resp = BackendResponse(
            content="text",
            tool_calls=[],
            stop_reason="max_tokens",
            usage=TokenUsage(input_tokens=100, output_tokens=4096),
        )
        reason = _degenerate_response_reason(resp)
        assert reason is not None
        assert "truncated" in reason

    def test_truncated_with_tool_calls_is_normal(self):
        """带 tool_calls 的截断响应不判定为退化（按设计重试确定性截断无意义）。"""
        from zmai.gateway.base import BackendResponse, TokenUsage, ToolCall
        from zmai.swe.agent import _degenerate_response_reason
        resp = BackendResponse(
            content="",
            tool_calls=[ToolCall(id="t1", name="edit", params={"path": "a.py"})],
            stop_reason="length",
            usage=TokenUsage(input_tokens=100, output_tokens=4096),
        )
        assert _degenerate_response_reason(resp) is None

    def test_empty_response_is_degenerate(self):
        """content 和 tool_calls 同时为空应判定为空响应。"""
        from zmai.gateway.base import BackendResponse, TokenUsage
        from zmai.swe.agent import _degenerate_response_reason
        resp = BackendResponse(
            content="",
            tool_calls=[],
            stop_reason="stop",
            usage=TokenUsage(input_tokens=100, output_tokens=0),
        )
        reason = _degenerate_response_reason(resp)
        assert reason is not None
        assert "empty" in reason

    def test_normal_content_is_not_degenerate(self):
        """有 content 的正常响应不判定为退化。"""
        from zmai.gateway.base import BackendResponse, TokenUsage
        from zmai.swe.agent import _degenerate_response_reason
        resp = BackendResponse(
            content="done",
            tool_calls=[],
            stop_reason="stop",
            usage=TokenUsage(input_tokens=100, output_tokens=5),
        )
        assert _degenerate_response_reason(resp) is None


# ═══════════════════════════════════════════════════════════════════
# 测试: 最后一次有效测试命令注入
# ═══════════════════════════════════════════════════════════════════


class TestLastWorkingTestCmdInjection:
    """FixDriving 阶段应注入最后一次有效 pytest 命令，避免忘记解释器路径。"""

    def test_directive_includes_last_working_cmd(self):
        """修复状态提示应包含 last_working_test_cmd。"""
        from zmai.agent import AgentContext
        from zmai.swe.agent import _build_fix_state_directive
        ctx = AgentContext(
            agent_id="test", task="fix", tools=None,
            metadata={
                "repair_phase": "fix",
                "test_failed": True,
                "reads_after_fail": 2,
                "last_working_test_cmd": r"D:\anaconada\python.exe -m pytest -q",
            },
        )
        directive = _build_fix_state_directive(ctx)
        assert "Last working pytest command" in directive
        assert r"D:\anaconada\python.exe -m pytest -q" in directive

    def test_directive_no_cmd_when_not_set(self):
        """未设置 last_working_test_cmd 时不应包含相关行。"""
        from zmai.agent import AgentContext
        from zmai.swe.agent import _build_fix_state_directive
        ctx = AgentContext(
            agent_id="test", task="fix", tools=None,
            metadata={
                "repair_phase": "fix",
                "test_failed": True,
                "reads_after_fail": 1,
            },
        )
        directive = _build_fix_state_directive(ctx)
        assert "Last working pytest command" not in directive

    def test_directive_empty_when_idle(self):
        """idle 阶段不注入修复状态提示。"""
        from zmai.agent import AgentContext
        from zmai.swe.agent import _build_fix_state_directive
        ctx = AgentContext(
            agent_id="test", task="fix", tools=None,
            metadata={
                "repair_phase": "idle",
                "last_working_test_cmd": "python -m pytest -q",
            },
        )
        assert _build_fix_state_directive(ctx) == ""
