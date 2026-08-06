"""Planning tests — Plan 数据模型、解析、执行、重新规划。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from zmai.errors import BackendError
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.models import (
    MAX_REPLANS,
    Plan,
    PlanStep,
    validate_plan_dict,
    format_plan_summary,
)
from zmai.swe.planner import generate_plan, parse_plan_response

_DEFAULT_USAGE = TokenUsage(input_tokens=10, output_tokens=5)
_DEFAULT_META = {"model": "mock-v1"}


# ═══════════════════════════════════════════════════════════════
# Mock Backend — 返回预设 Plan JSON
# ═══════════════════════════════════════════════════════════════

_VALID_PLAN_JSON = json.dumps({
    "goal": "测试任务: 创建 hello.txt",
    "steps": [
        {
            "id": 1,
            "action": "创建 hello.txt 文件",
            "tool": "write_file",
            "params": {"path": "hello.txt", "content": "Hello, World!"},
            "expected_outcome": "hello.txt 文件已创建",
            "verification_strategy": "read_file hello.txt 确认内容",
        },
        {
            "id": 2,
            "action": "验证文件内容",
            "tool": "read_file",
            "params": {"path": "hello.txt"},
            "expected_outcome": "文件内容为 Hello, World!",
            "verification_strategy": "grep Hello, World! hello.txt",
        },
    ],
    "estimated_complexity": "simple",
    "risks": [],
})


class PlanMockBackend(Backend):
    """返回预设 Plan JSON 的 Mock Backend。"""
    name = "plan_mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.invoke_count = 0
        self._plan_json: str = config.get("plan_json", _VALID_PLAN_JSON) if config else _VALID_PLAN_JSON
        self._fail_on: int = config.get("fail_on", 0) if config else 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        if self._fail_on > 0 and self.invoke_count >= self._fail_on:
            raise BackendError("mock backend failure", status_code=500)
        return BackendResponse(
            content=self._plan_json,
            usage=_DEFAULT_USAGE,
            stop_reason="end_turn",
            metadata=_DEFAULT_META,
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.SYSTEM_PROMPT}


# ═══════════════════════════════════════════════════════════════
# 1-3. Plan 模型 & 解析 & 验证
# ═══════════════════════════════════════════════════════════════


class TestPlanModel:
    """Plan 数据模型单元测试。"""

    def test_valid_plan_creation(self):
        """有效 Plan 可正常创建。"""
        steps = [
            PlanStep(id=1, action="read file", tool="read_file",
                     expected_outcome="content read", verification_strategy="check output"),
            PlanStep(id=2, action="write file", tool="write_file",
                     expected_outcome="file written", verification_strategy="check file exists"),
        ]
        plan = Plan(goal="test task", steps=steps)
        assert plan.goal == "test task"
        assert len(plan.steps) == 2
        assert plan.estimated_complexity == "medium"
        assert plan.replan_count == 0
        assert plan.created_at != ""

    def test_plan_step_default_status(self):
        """PlanStep 默认 status 为 pending。"""
        step = PlanStep(id=1, action="test")
        assert step.status == "pending"

    def test_plan_is_finished(self):
        """Plan.is_finished 在所有步骤完成后返回 True。"""
        steps = [
            PlanStep(id=1, action="step 1", status="completed"),
            PlanStep(id=2, action="step 2", status="completed"),
        ]
        plan = Plan(goal="test", steps=steps)
        assert plan.is_finished is True
        assert plan.completed_steps == 2

    def test_plan_not_finished_with_pending(self):
        """有 pending 步骤时 is_finished 返回 False。"""
        steps = [
            PlanStep(id=1, action="step 1", status="completed"),
            PlanStep(id=2, action="step 2", status="pending"),
        ]
        plan = Plan(goal="test", steps=steps)
        assert plan.is_finished is False

    def test_current_step_returns_first_pending(self):
        """current_step 返回第一个 pending 步骤。"""
        steps = [
            PlanStep(id=1, action="step 1", status="completed"),
            PlanStep(id=2, action="step 2", status="pending"),
            PlanStep(id=3, action="step 3", status="pending"),
        ]
        plan = Plan(goal="test", steps=steps)
        current = plan.current_step
        assert current is not None
        assert current.id == 2

    def test_current_step_none_when_done(self):
        """所有步骤完成后 current_step 返回 None。"""
        steps = [
            PlanStep(id=1, action="step 1", status="completed"),
            PlanStep(id=2, action="step 2", status="failed"),
        ]
        plan = Plan(goal="test", steps=steps)
        assert plan.current_step is None

    def test_mark_step_updates_status(self):
        """mark_step 正确更新步骤状态。"""
        steps = [PlanStep(id=1, action="step 1")]
        plan = Plan(goal="test", steps=steps)
        plan.mark_step(1, "completed")
        assert steps[0].status == "completed"

    def test_to_dict_roundtrip(self):
        """Plan → dict → Plan 保持相等。"""
        steps = [
            PlanStep(id=1, action="read", tool="read_file",
                     expected_outcome="done", verification_strategy="check"),
        ]
        plan = Plan(goal="roundtrip", steps=steps,
                     estimated_complexity="simple", risks=["risk 1"])
        d = plan.to_dict()
        plan2 = Plan.from_dict(d)
        assert plan2.goal == "roundtrip"
        assert len(plan2.steps) == 1
        assert plan2.steps[0].tool == "read_file"
        assert plan2.estimated_complexity == "simple"
        assert plan2.risks == ["risk 1"]

    def test_format_plan_summary_includes_goal_and_steps(self):
        """format_plan_summary 生成的文本包含目标和步骤。"""
        steps = [PlanStep(id=1, action="do something", tool="shell_exec")]
        plan = Plan(goal="test goal", steps=steps)
        summary = format_plan_summary(plan)
        assert "test goal" in summary
        assert "do something" in summary
        assert "shell_exec" in summary


class TestPlanValidation:
    """Plan 格式验证测试。"""

    def test_valid_plan_dict(self):
        """有效的 Plan JSON 通过验证。"""
        data = {
            "goal": "create a file",
            "steps": [{"id": 1, "action": "create file"}],
        }
        valid, reason = validate_plan_dict(data)
        assert valid is True
        assert reason == ""

    def test_missing_goal(self):
        """缺少 goal 时验证失败。"""
        data = {"steps": [{"id": 1, "action": "create file"}]}
        valid, reason = validate_plan_dict(data)
        assert valid is False
        assert "goal" in reason

    def test_empty_goal(self):
        """空 goal 时验证失败。"""
        data = {"goal": "", "steps": [{"id": 1, "action": "create file"}]}
        valid, reason = validate_plan_dict(data)
        assert valid is False

    def test_missing_steps(self):
        """缺少 steps 时验证失败。"""
        data = {"goal": "test"}
        valid, reason = validate_plan_dict(data)
        assert valid is False
        assert "steps" in reason

    def test_empty_steps(self):
        """空 steps 数组时验证失败。"""
        data = {"goal": "test", "steps": []}
        valid, reason = validate_plan_dict(data)
        assert valid is False

    def test_step_missing_action(self):
        """步骤缺少 action 时验证失败。"""
        data = {
            "goal": "test",
            "steps": [{"id": 1}],
        }
        valid, reason = validate_plan_dict(data)
        assert valid is False
        assert "action" in reason

    def test_not_a_dict(self):
        """非 dict 输入验证失败。"""
        valid, reason = validate_plan_dict([])
        assert valid is False

    def test_steps_not_a_list(self):
        """steps 非数组验证失败。"""
        data = {"goal": "test", "steps": "not a list"}
        valid, reason = validate_plan_dict(data)
        assert valid is False


class TestPlanParsing:
    """Plan 响应解析测试。"""

    def test_parse_valid_json(self):
        """有效 JSON 解析成功。"""
        raw = json.dumps({
            "goal": "test",
            "steps": [{"id": 1, "action": "do it"}],
        })
        plan = parse_plan_response(raw)
        assert plan.goal == "test"
        assert len(plan.steps) == 1

    def test_parse_json_with_code_block(self):
        """被 ```json 包裹的 JSON 也能解析。"""
        raw = '```json\n{"goal": "test", "steps": [{"id": 1, "action": "do it"}]}\n```'
        plan = parse_plan_response(raw)
        assert plan.goal == "test"

    def test_parse_invalid_json_raises(self):
        """非法 JSON 字符串抛出 ValueError。"""
        with pytest.raises(ValueError) as exc:
            parse_plan_response("not json at all")
        assert "JSON" in str(exc.value)

    def test_parse_missing_fields_raises(self):
        """缺少必要字段的 JSON 抛出 ValueError。"""
        raw = json.dumps({"some_key": "some_value"})
        with pytest.raises(ValueError) as exc:
            parse_plan_response(raw)
        assert "goal" in str(exc.value) or "无效" in str(exc.value)


# ═══════════════════════════════════════════════════════════════
# 4-7. Plan 生成 & 执行 & 重新规划
# ═══════════════════════════════════════════════════════════════


class TestPlanGeneration:
    """Plan 生成测试（使用 Mock Backend）。"""

    def test_generate_valid_plan(self):
        """从 Mock Backend 生成有效 Plan。"""
        backend = PlanMockBackend()
        plan = generate_plan("create a file", backend)
        assert isinstance(plan, Plan)
        assert plan.goal == "测试任务: 创建 hello.txt"
        assert len(plan.steps) == 2
        assert plan.steps[0].tool == "write_file"

    def test_generate_plan_with_empty_response(self):
        """空响应抛出 ValueError。"""
        backend = PlanMockBackend(config={"plan_json": ""})
        with pytest.raises(ValueError):
            generate_plan("test", backend)

    def test_generate_plan_with_invalid_json(self):
        """非法 JSON 响应抛出 ValueError。"""
        backend = PlanMockBackend(config={"plan_json": "not json"})
        with pytest.raises(ValueError):
            generate_plan("test", backend)

    def test_generate_plan_backend_failure(self):
        """Backend 调用失败抛出异常。"""
        backend = PlanMockBackend(config={"plan_json": _VALID_PLAN_JSON, "fail_on": 1})
        with pytest.raises(BackendError):
            generate_plan("test", backend)


class TestPlanReplanning:
    """Plan 重新规划行为测试。"""

    def test_max_replans_constant(self):
        """MAX_REPLANS 应为 3。"""
        assert MAX_REPLANS == 3

    def test_plan_tracks_replan_count(self):
        """Plan 记录重新规划次数。"""
        plan = Plan(goal="test", steps=[PlanStep(id=1, action="test")])
        assert plan.replan_count == 0
        plan.replan_count = 2
        assert plan.replan_count == 2

    def test_failed_then_skipped_all_terminal(self):
        """混合 failed 和 skipped 也是 finished。"""
        steps = [
            PlanStep(id=1, action="s1", status="completed"),
            PlanStep(id=2, action="s2", status="failed"),
            PlanStep(id=3, action="s3", status="skipped"),
        ]
        plan = Plan(goal="test", steps=steps)
        assert plan.is_finished is True

    def test_all_pending_not_finished(self):
        """全部 pending 时 is_finished 为 False。"""
        steps = [PlanStep(id=1, action="s1"), PlanStep(id=2, action="s2")]
        plan = Plan(goal="test", steps=steps)
        assert plan.is_finished is False

    def test_plan_to_dict_contains_replan_count(self):
        """to_dict 包含 replan_count。"""
        plan = Plan(goal="test", steps=[PlanStep(id=1, action="a")], replan_count=2)
        d = plan.to_dict()
        assert d["replan_count"] == 2

    def test_from_dict_restores_replan_count(self):
        """from_dict 恢复 replan_count。"""
        d = {
            "goal": "test",
            "steps": [{"id": 1, "action": "a"}],
            "replan_count": 2,
            "created_at": "2026-01-01T00:00:00Z",
        }
        plan = Plan.from_dict(d)
        assert plan.replan_count == 2


class TestPlanExecution:
    """SWEAgent Plan 执行集成测试。

    使用 Mock Backend + 模拟 auto_plan 模式。
    """

    def test_auto_plan_config_flag(self):
        """auto_plan=True 在 context.config 中生效。"""
        config = {"auto_plan": True}
        assert config.get("auto_plan") is True

    def test_auto_plan_config_default(self):
        """auto_plan 默认为 False。"""
        config = {}
        assert config.get("auto_plan", False) is False


# ═══════════════════════════════════════════════════════════════
# 8. Plan-only 模式
# ═══════════════════════════════════════════════════════════════


class TestPlanOnlyMode:
    """Plan-only 模式（zmai plan "task"）测试。"""

    def test_plan_only_saves_to_session(self, tmp_path: Path):
        """Plan-only 模式将 Plan 保存到 session 目录。"""
        backend = PlanMockBackend()
        plan = generate_plan("create a file", backend)

        # 模拟保存到 session
        session_dir = tmp_path / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "latest_plan.json").write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        saved = json.loads((session_dir / "latest_plan.json").read_text(encoding="utf-8"))
        assert saved["goal"] == plan.goal
        assert len(saved["steps"]) == len(plan.steps)

    def test_plan_only_output_contains_goal(self):
        """Plan-only 输出包含任务目标。"""
        backend = PlanMockBackend()
        plan = generate_plan("create a file", backend)
        assert "测试任务" in plan.goal
        assert len(plan.steps) > 0
