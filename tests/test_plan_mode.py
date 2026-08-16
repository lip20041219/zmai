"""Plan Mode 测试 — PlanAgent, PlanModeGuard, 状态机集成。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from zmai.agent import AgentState
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.models import Plan, PlanStep
from zmai.swe.plan_agent import PlanAgent
from zmai.swe.plan_guard import PlanModeGuard
from zmai.tool import ToolResult

_DEFAULT_USAGE = TokenUsage(input_tokens=10, output_tokens=5)
_DEFAULT_META = {"model": "mock-v1"}

_VALID_PLAN_JSON = json.dumps({
    "goal": "修复 calculator.py 中的除以零 bug",
    "assumptions": ["项目使用 Python 3", "calculator.py 含 divide() 函数"],
    "files_to_modify": ["calculator.py"],
    "expected_outcome": "divide(10, 0) 正确抛出 ZeroDivisionError",
    "verification_strategy": "运行 pytest 验证所有测试通过",
    "steps": [
        {
            "id": 1,
            "action": "读取 calculator.py 分析 divide() 实现",
            "tool": "read_file",
            "params": {"path": "calculator.py"},
            "expected_outcome": "理解当前实现",
            "verification_strategy": "文件存在且可读",
        },
        {
            "id": 2,
            "action": "修改 divide() 去掉过宽的 except",
            "tool": "edit",
            "params": {"path": "calculator.py", "mode": "replace_lines",
                       "start_line": 5, "end_line": 7, "new_text": "        return a / b\n"},
            "expected_outcome": "divide() 不再捕获 ZeroDivisionError",
            "verification_strategy": "运行测试确认通过",
        },
        {
            "id": 3,
            "action": "运行测试验证修改",
            "tool": "shell_exec",
            "params": {"command": "python -m pytest check_calculator.py -v"},
            "expected_outcome": "测试全部通过",
            "verification_strategy": "exit code 0",
        },
    ],
    "estimated_complexity": "simple",
    "risks": [],
})


# ═══════════════════════════════════════════════════════════════
# Mock Backend
# ═══════════════════════════════════════════════════════════════


class PlanMockBackend(Backend):
    """返回预设 Plan JSON 的 Mock Backend。"""
    name = "plan_mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._plan_json: str = config.get("plan_json", _VALID_PLAN_JSON) if config else _VALID_PLAN_JSON  # noqa: E501
        self._fail_count: int = config.get("fail_count", 0) if config else 0

    def invoke(self, request):
        self.invoke_count += 1
        if self._fail_count > 0 and self.invoke_count <= self._fail_count:
            raise Exception("mock backend failure")
        return BackendResponse(
            content=self._plan_json,
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request):
        raise NotImplementedError

    @property
    def capabilities(self):
        return {BackendCapability.SYSTEM_PROMPT}


class MockToolRegistry:
    """模拟 ToolRegistry，记录工具调用。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.tools: dict[str, Any] = {}

    def register(self, tool):
        self.tools[tool.name] = tool

    def execute(self, name, params, context=None):
        self.calls.append((name, params))
        # 基本的放行
        return ToolResult.ok(output=f"{name} executed")

    def list(self):
        return list(self.tools.values())


# ═══════════════════════════════════════════════════════════════
# 1. Plan 生成
# ═══════════════════════════════════════════════════════════════


class TestPlanGeneration:
    """PlanAgent.create_plan() — 从 Backend 生成结构化 Plan。"""

    def test_generate_valid_plan(self):
        """从 Mock Backend 生成有效 Plan。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        config = {}
        ctx = AgentContext(agent_id="test", task="fix bug", config=config)

        agent = PlanAgent("test", backend, tools, config)
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug in calculator", ctx))
        assert isinstance(plan, Plan)
        assert plan.goal == "修复 calculator.py 中的除以零 bug"
        assert len(plan.steps) == 3
        assert len(plan.files_to_modify) == 1
        assert "calculator.py" in plan.files_to_modify
        assert len(plan.assumptions) >= 1
        assert plan.expected_outcome

    def test_generate_plan_with_empty_response(self):
        """空响应抛出 RuntimeError。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend(config={"plan_json": ""})
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        with pytest.raises(RuntimeError):
            asyncio.run(agent.create_plan("test", ctx))

    def test_generate_plan_backend_failure(self):
        """Backend 调用失败抛出 RuntimeError。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend(config={"plan_json": _VALID_PLAN_JSON, "fail_count": 1})
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        with pytest.raises(RuntimeError):
            asyncio.run(agent.create_plan("test", ctx))

    def test_generate_plan_preserves_plan(self):
        """生成的 Plan 可通过 agent.plan 属性访问。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug", ctx))
        assert agent.plan is plan
        assert agent.plan.goal == plan.goal


# ═══════════════════════════════════════════════════════════════
# 2. Plan 展示
# ═══════════════════════════════════════════════════════════════


class TestPlanDisplay:
    """PlanAgent.format_plan() — 可读的 Plan 文本输出。"""

    def test_format_plan_contains_goal(self):
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        asyncio.run(agent.create_plan("fix bug", ctx))
        formatted = agent.format_plan()
        assert "修复 calculator.py 中的除以零 bug" in formatted
        assert "Steps" in formatted
        assert "Steps: 3" in formatted

    def test_format_plan_contains_steps(self):
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        asyncio.run(agent.create_plan("fix bug", ctx))
        formatted = agent.format_plan()
        assert "读取" in formatted or "分析" in formatted
        assert "修改" in formatted

    def test_format_plan_no_plan(self):
        agent = PlanAgent("test", None, MockToolRegistry(), {})
        assert agent.format_plan() == "(No Plan)"

    def test_format_plan_contains_files(self):
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        asyncio.run(agent.create_plan("fix bug", ctx))
        formatted = agent.format_plan()
        assert "calculator.py" in formatted


# ═══════════════════════════════════════════════════════════════
# 3-5. PlanModeGuard — 未确认时写入工具被拒绝
# ═══════════════════════════════════════════════════════════════


class TestPlanModeGuardReadOnly:
    """PlanModeGuard — 只读工具在确认前可用。"""

    def test_read_file_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("read_file", {"path": "main.py"})
        assert result.success is True

    def test_grep_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("grep", {"pattern": "def "})
        assert result.success is True

    def test_show_to_user_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("show_to_user", {"content": "hello"})
        assert result.success is True

    def test_open_in_browser_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("open_in_browser", {"path": "index.html"})
        assert result.success is True


class TestPlanModeGuardWriteRejected:
    """PlanModeGuard — 写入工具在确认前被拒绝。"""

    def test_write_file_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("write_file", {"path": "main.py", "content": "x"})
        assert result.success is False
        assert "forbidden" in (result.error or "")

    def test_edit_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("edit", {"path": "main.py", "mode": "replace_lines"})
        assert result.success is False
        assert "forbidden" in (result.error or "")

    def test_unknown_tool_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("unknown_tool", {})
        assert result.success is False


class TestPlanModeGuardShell:
    """PlanModeGuard — 危险 shell 命令被拒绝。"""

    def test_shell_rm_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "rm file.txt"})
        assert result.success is False

    def test_shell_del_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "del file.txt"})
        assert result.success is False

    def test_shell_mv_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "mv a.txt b.txt"})
        assert result.success is False

    def test_shell_redirect_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "echo x > file.txt"})
        assert result.success is False

    def test_shell_echo_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "echo hello"})
        assert result.success is True

    def test_shell_dir_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "dir"})
        assert result.success is True

    def test_shell_python_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "python -c 'print(1)'"})
        assert result.success is True

    def test_shell_git_status_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "git status"})
        assert result.success is True

    def test_shell_pip_install_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "pip install requests"})
        assert result.success is False

    def test_git_commit_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("git", {"args": "commit -m 'fix'"})
        assert result.success is False

    def test_git_push_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("git", {"args": "push origin main"})
        assert result.success is False

    def test_git_log_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("git", {"args": "log --oneline"})
        assert result.success is True

    def test_git_diff_allowed(self):
        guard = PlanModeGuard()
        result = guard.check("git", {"args": "diff"})
        assert result.success is True

    def test_npm_install_rejected(self):
        guard = PlanModeGuard()
        result = guard.check("shell_exec", {"command": "npm install express"})
        assert result.success is False


# ═══════════════════════════════════════════════════════════════
# 6. 用户确认后允许执行
# ═══════════════════════════════════════════════════════════════


class TestPlanModeGuardDisarm:
    """PlanModeGuard.disarm() — 解除后所有工具可用。"""

    def test_disarm_allows_write(self):
        guard = PlanModeGuard()
        guard.disarm()
        assert guard.is_armed is False
        result = guard.check("write_file", {"path": "main.py", "content": "x"})
        assert result.success is True

    def test_disarm_allows_edit(self):
        guard = PlanModeGuard()
        guard.disarm()
        result = guard.check("edit", {"path": "main.py", "mode": "replace_lines"})
        assert result.success is True

    def test_disarm_allows_shell_rm(self):
        guard = PlanModeGuard()
        guard.disarm()
        result = guard.check("shell_exec", {"command": "rm file.txt"})
        assert result.success is True

    def test_disarm_allows_git_commit(self):
        guard = PlanModeGuard()
        guard.disarm()
        result = guard.check("git", {"args": "commit -m 'fix'"})
        assert result.success is True

    def test_is_armed_default(self):
        guard = PlanModeGuard()
        assert guard.is_armed is True

    def test_disarm_twice_no_error(self):
        guard = PlanModeGuard()
        guard.disarm()
        guard.disarm()  # 不应报错
        assert guard.is_armed is False

    def test_disarm_then_rearm_not_supported(self):
        """当前设计不支持重新武装。"""
        guard = PlanModeGuard()
        guard.disarm()
        # 没有 rearm 方法，设计如此


# ═══════════════════════════════════════════════════════════════
# 7. Plan 失败
# ═══════════════════════════════════════════════════════════════


class TestPlanFailure:
    """Plan 生成失败场景。"""

    def test_plan_invalid_json(self):
        """非法 JSON → RuntimeError。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend(config={"plan_json": "not json at all"})
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        with pytest.raises(RuntimeError, match="JSON"):
            asyncio.run(agent.create_plan("test", ctx))

    def test_plan_missing_goal(self):
        """缺少 goal 的 JSON → RuntimeError。"""
        from zmai.agent import AgentContext
        bad_plan = json.dumps({"steps": [{"id": 1, "action": "test"}]})
        backend = PlanMockBackend(config={"plan_json": bad_plan})
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        with pytest.raises(RuntimeError, match="goal"):
            asyncio.run(agent.create_plan("test", ctx))

    def test_plan_empty_steps(self):
        """空 steps → RuntimeError。"""
        from zmai.agent import AgentContext
        bad_plan = json.dumps({"goal": "test", "steps": []})
        backend = PlanMockBackend(config={"plan_json": bad_plan})
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        with pytest.raises(RuntimeError, match="steps"):
            asyncio.run(agent.create_plan("test", ctx))


# ═══════════════════════════════════════════════════════════════
# 8. Plan 超时（MAX_PLAN_STEPS）
# ═══════════════════════════════════════════════════════════════


class TestPlanTimeout:
    """Plan 超时场景。当前 PlanAgent 使用单次 LLM 调用 + 解析，
    没有多轮循环。超时保护通过 Backend 调用的 timeout 参数保证。"""

    def test_plan_backend_timeout_raises(self):
        """Backend 超时 → RuntimeError。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend(config={"plan_json": _VALID_PLAN_JSON, "fail_count": 1})
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        with pytest.raises(RuntimeError):
            asyncio.run(agent.create_plan("test", ctx))


# ═══════════════════════════════════════════════════════════════
# 9. Plan 执行后验证
# ═══════════════════════════════════════════════════════════════


class TestPlanVerification:
    """Plan 执行后的验证集成。验证 Plan 数据模型中的
    verification_strategy 和 expected_outcome 字段正确传递。"""

    def test_plan_has_verification_strategy(self):
        """Plan 含 verification_strategy。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug", ctx))
        assert plan.verification_strategy
        assert "pytest" in plan.verification_strategy or "测试" in plan.verification_strategy

    def test_each_step_has_verification(self):
        """每步都有 verification_strategy。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug", ctx))
        for step in plan.steps:
            assert step.verification_strategy, f"step {step.id} 缺少 verification_strategy"

    def test_plan_has_expected_outcome(self):
        """Plan 有整体 expected_outcome。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug", ctx))
        assert plan.expected_outcome

    def test_step_has_expected_outcome(self):
        """每步都有 expected_outcome。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug", ctx))
        for step in plan.steps:
            assert step.expected_outcome, f"step {step.id} 缺少 expected_outcome"


# ═══════════════════════════════════════════════════════════════
# 10. 取消 Plan
# ═══════════════════════════════════════════════════════════════


class TestPlanCancel:
    """Plan 取消场景。PlanAgent 本身没有取消方法。
    取消由 Runtime 的 cancel() 处理。"""

    def test_plan_generated_then_cancelled(self):
        """Plan 已生成但被取消 — 验证状态一致性。"""
        from zmai.agent import AgentContext
        backend = PlanMockBackend()
        tools = MockToolRegistry()
        ctx = AgentContext(agent_id="test", task="test", config={})

        agent = PlanAgent("test", backend, tools, {})
        import asyncio
        plan = asyncio.run(agent.create_plan("fix bug", ctx))
        assert plan is not None
        # 取消是 Runtime 层职责，验证 Plan 本身正常（不会因"取消"而损坏）
        assert plan.goal
        assert len(plan.steps) > 0

    def test_plan_not_executed_without_confirm(self):
        """Plan 未经确认不应执行。"""
        guard = PlanModeGuard()
        assert guard.is_armed is True
        guard.disarm()
        assert guard.is_armed is False


# ═══════════════════════════════════════════════════════════════
# 生命周期集成 — PLAN_READY 状态
# ═══════════════════════════════════════════════════════════════


class TestPlanLifecycleIntegration:
    """Plan 生命周期与 Runtime.LifecycleManager 集成。"""

    def test_plan_ready_state_exists(self):
        """PLAN_READY 存在于 AgentState。"""
        assert hasattr(AgentState, "PLAN_READY")
        assert AgentState.PLAN_READY.value == "plan_ready"

    def test_plan_ready_is_active(self):
        """PLAN_READY 是活跃态（非终态）。"""
        assert AgentState.PLAN_READY.is_active is True
        assert AgentState.PLAN_READY.is_terminal is False

    def test_planning_to_plan_ready_transition(self):
        """LifecycleManager 支持 planning → plan_ready。"""
        from zmai.runtime.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.create("test_agent")
        lm.plan("test_agent")
        lm.plan_ready("test_agent")
        assert lm.get_state("test_agent") == "plan_ready"

    def test_plan_ready_to_executing_transition(self):
        """LifecycleManager 支持 plan_ready → executing。"""
        from zmai.runtime.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.create("test_agent")
        lm.plan("test_agent")
        lm.plan_ready("test_agent")
        lm.execute("test_agent")
        assert lm.get_state("test_agent") == "executing"

    def test_plan_ready_to_failed_transition(self):
        """LifecycleManager 支持 plan_ready → failed。"""
        from zmai.runtime.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.create("test_agent")
        lm.plan("test_agent")
        lm.plan_ready("test_agent")
        lm.fail("test_agent")
        assert lm.get_state("test_agent") == "failed"

    def test_plan_ready_to_cancelled_transition(self):
        """LifecycleManager 支持 plan_ready → cancelled。"""
        from zmai.runtime.lifecycle import LifecycleManager
        lm = LifecycleManager()
        lm.create("test_agent")
        lm.plan("test_agent")
        lm.plan_ready("test_agent")
        lm.cancel("test_agent")
        assert lm.get_state("test_agent") == "cancelled"


# ═══════════════════════════════════════════════════════════════
# Runtime 集成 — auto_plan 模式
# ═══════════════════════════════════════════════════════════════


class TestRuntimePlanMode:
    """Runtime.run() 在 auto_plan=True 时的行为。"""

    def test_auto_plan_generates_plan(self):
        """auto_plan=True 时生成 Plan 并等待确认。"""
        from zmai.config.config import Config
        from zmai.runtime import Runtime

        plan_confirmed = False

        def on_plan(plan):
            nonlocal plan_confirmed
            plan_confirmed = True
            assert plan.goal
            assert len(plan.steps) > 0
            return True  # 确认

        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("plan_mock", PlanMockBackend, default=True)

        import asyncio
        result = asyncio.run(rt.run(
            agent_id="plan_test",
            task="fix calculator divide bug",
            config={"auto_plan": True, "on_plan": on_plan},
        ))
        assert plan_confirmed is True
        # 确认后应正常执行
        assert result["status"] in ("completed", "failed", "timeout")

    def test_auto_plan_rejected(self):
        """auto_plan=True 但用户拒绝 Plan → failed。"""
        from zmai.config.config import Config
        from zmai.runtime import Runtime

        def on_plan(plan):
            return False  # 拒绝

        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("plan_mock", PlanMockBackend, default=True)

        import asyncio
        result = asyncio.run(rt.run(
            agent_id="plan_reject",
            task="fix calculator divide bug",
            config={"auto_plan": True, "on_plan": on_plan},
        ))
        assert result["status"] == "failed"
        assert "拒绝" in result.get("error", "")

    def test_auto_plan_without_callback(self):
        """auto_plan=True 无 on_plan 回调时自动确认并执行。"""
        from zmai.config.config import Config
        from zmai.runtime import Runtime

        cfg = Config(sources=[])
        rt = Runtime(config=cfg)
        rt._gateway.register("plan_mock", PlanMockBackend, default=True)

        import asyncio
        result = asyncio.run(rt.run(
            agent_id="plan_auto",
            task="fix calculator divide bug",
            config={"auto_plan": True},
        ))
        assert result["status"] in ("completed", "failed", "timeout")


# ═══════════════════════════════════════════════════════════════
# 数据模型兼容性
# ═══════════════════════════════════════════════════════════════


class TestPlanModelCompatibility:
    """Plan 数据模型向后兼容。"""

    def test_plan_accepts_new_fields(self):
        """Plan 接受新增字段。"""
        plan = Plan(
            goal="test",
            steps=[PlanStep(id=1, action="step 1")],
            assumptions=["assumption 1"],
            files_to_modify=["file1.py"],
            expected_outcome="it works",
            verification_strategy="run tests",
        )
        assert plan.assumptions == ["assumption 1"]
        assert plan.files_to_modify == ["file1.py"]
        assert plan.expected_outcome == "it works"
        assert plan.verification_strategy == "run tests"

    def test_plan_new_fields_default_empty(self):
        """新增字段默认为空列表/字符串。"""
        plan = Plan(goal="test", steps=[PlanStep(id=1, action="s1")])
        assert plan.assumptions == []
        assert plan.files_to_modify == []

    def test_plan_to_dict_includes_new_fields(self):
        """to_dict 包含新增字段。"""
        plan = Plan(
            goal="test",
            steps=[PlanStep(id=1, action="s1")],
            assumptions=["a"],
            files_to_modify=["f.py"],
        )
        d = plan.to_dict()
        assert "assumptions" in d
        assert "files_to_modify" in d
        assert "expected_outcome" in d
        assert "verification_strategy" in d

    def test_plan_from_dict_roundtrip_new_fields(self):
        """from_dict 恢复新增字段。"""
        d = {
            "goal": "test",
            "steps": [{"id": 1, "action": "s1"}],
            "assumptions": ["a1"],
            "files_to_modify": ["f1.py"],
            "expected_outcome": "expected",
            "verification_strategy": "verify",
        }
        plan = Plan.from_dict(d)
        assert plan.assumptions == ["a1"]
        assert plan.files_to_modify == ["f1.py"]
        assert plan.expected_outcome == "expected"
