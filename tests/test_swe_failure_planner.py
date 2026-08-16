"""P1/P2 — Failure Parser 与 Fix Planner 单测。

覆盖：
  - P2: pytest traceback → 语义化问题（404→路由缺失、KeyError→字段缺失、依赖缺失）
  - P1: 语义化失败 → 有序修复计划（NotFound→路由步骤，MissingField→字段步骤）
  - 端到端：Agent 修复失败时注入的 [Repair Plan] 含语义分析与计划
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zmai.swe.failure import format_failure, parse_test_failure
from zmai.swe.fix_planner import format_plan, generate_fix_plan

# ═══════════════════════════════════════════════════════════════════
# P2 — Failure Parser
# ═══════════════════════════════════════════════════════════════════


class TestFailureParser:
    def test_parse_404_as_route_missing(self):
        tb = (
            "FAILED test_app.py::test_home_returns_200 - AssertionError: "
            "assert 404 == 200\n"
            "+  where 404 = <WrapperTestResponse streamed [404 NOT FOUND]>.status_code"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.test_name == "test_home_returns_200"
        assert issue.issue_type == "NotFound"
        assert "404" in issue.semantic or "路由" in issue.semantic or "状态码" in issue.semantic
        assert issue.hints, "404 语义应给出修复提示"

    def test_parse_keyerror_as_missing_field(self):
        tb = (
            "FAILED test_app.py::test_api_returns_username - KeyError: 'username'\n"
            "data = client.get('/api/user').get_json()\n"
            "E   KeyError: 'username'"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.issue_type == "MissingField"
        assert "username" in issue.semantic
        assert issue.hints

    def test_parse_missing_dependency(self):
        tb = (
            "test_app.py:2: in <module>\n"
            "from app import app\n"
            "ModuleNotFoundError: No module named 'flask'"
        )
        issue = parse_test_failure(tb)
        assert issue is not None
        assert issue.issue_type == "MissingDependency"
        assert "flask" in issue.semantic

    def test_parse_empty_returns_none(self):
        assert parse_test_failure("") is None
        assert parse_test_failure("   ") is None

    def test_format_failure_contains_semantic(self):
        issue = parse_test_failure("assert 404 == 200")
        text = format_failure(issue)
        assert "语义化根因" in text
        assert "修复提示" in text


# ═══════════════════════════════════════════════════════════════════
# P1 — Fix Planner
# ═══════════════════════════════════════════════════════════════════


class TestFixPlanner:
    def test_route_failure_generates_route_plan(self):
        issue = parse_test_failure("assert 404 == 200")
        plan = generate_fix_plan(issue)
        assert not plan.is_empty
        assert plan.issue_type == "NotFound"
        assert any("route" in s.lower() or "路由" in s for s in plan.steps), \
            f"404 计划应含路由步骤: {plan.steps}"

    def test_field_failure_generates_field_plan(self):
        issue = parse_test_failure("KeyError: 'username'")
        plan = generate_fix_plan(issue)
        assert not plan.is_empty
        assert plan.issue_type == "MissingField"
        assert any("字段" in s for s in plan.steps)

    def test_generic_failure_gets_default_steps(self):
        issue = parse_test_failure("assert 1 == 2")
        plan = generate_fix_plan(issue)
        assert not plan.is_empty
        # 未知断言 → 通用计划，仍包含 修改→验证 步骤
        assert any("edit" in s or "write_file" in s for s in plan.steps)

    def test_format_plan(self):
        issue = parse_test_failure("assert 404 == 200")
        plan = generate_fix_plan(issue)
        text = format_plan(plan)
        assert "Fix Plan" in text
        assert "1." in text


# ═══════════════════════════════════════════════════════════════════
# 端到端：Agent 修复失败时注入语义化计划
# ═══════════════════════════════════════════════════════════════════


def _messages_text(ctx) -> str:
    parts = []
    for m in ctx.metadata.get("messages", []):
        if isinstance(m, dict):
            parts.append(m.get("content", "") or "")
        else:
            parts.append(getattr(m, "content", "") or "")
    return " ".join(parts)


class TestRepairPlanEndToEnd:
    def test_failure_injects_semantic_analysis_and_plan(self, tmp_path: Path):
        """Agent 首次失败时应注入 [Repair Plan]，且含语义分析与修复计划。"""
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

        # 真实 Flask 项目（缺路由 → 404）
        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n\n"
            "def index():\n    return 'Hello'\n",
            encoding="utf-8",
        )
        (tmp_path / "test_app.py").write_text(
            "import pytest\nfrom app import app\n\n"
            "@pytest.fixture\ndef client():\n"
            "    app.config['TESTING'] = True\n"
            "    with app.test_client() as c:\n"
            "        yield c\n\n"
            "def test_home_returns_200(client):\n"
            "    assert client.get('/').status_code == 200\n",
            encoding="utf-8",
        )

        class B(Backend):
            name = "e2e_fix"

            def __init__(self):
                self._i = 0

            def invoke(self, request: BackendRequest) -> BackendResponse:
                self._i += 1
                if self._i == 1:
                    tc = [ToolCall(id="1", name="shell_exec",
                                   params={"command": "python -m pytest -q"})]
                else:
                    tc = None
                return BackendResponse(content="", tool_calls=tc,
                                       usage=TokenUsage(1, 1),
                                       stop_reason="tool_use" if tc else "end_turn")

            def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
                yield BackendEvent(type="done", data="", index=1)

            @property
            def capabilities(self):
                return {BackendCapability.TOOL_USE}

        agent = SWEAgent("e2e_fix")
        ctx = AgentContext(
            agent_id="e2e_fix",
            task=f"项目在 {tmp_path}。修复 bug 使测试通过。",
            backend=B(),
            tools=ToolRegistry(),
            config={"project_path": str(tmp_path), "timeout": 30},
            metadata={},
        )
        asyncio.run(agent.initialize(ctx))
        asyncio.run(agent.step(ctx))  # step1: pytest 失败 → 注入计划

        joined = _messages_text(ctx)
        assert "[Repair Plan]" in joined
        # P2 语义化：识别出 404/路由
        assert "404" in joined or "状态码" in joined or "路由" in joined, \
            f"应含语义化分析: {joined}"
        # P1 计划：包含 Fix Plan 步骤
        assert "Fix Plan" in joined, f"应含修复计划: {joined}"
        assert "edit" in joined or "write_file" in joined
