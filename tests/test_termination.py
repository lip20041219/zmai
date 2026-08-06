"""Termination tests — 修复任务成功后 Agent 应自主停止。

验证:
  1. pytest 全部通过后，Agent 不再继续调用工具（即使模型仍想交付/复检）。
  2. 停止后 execution status 为 completed。
  3. pytest 失败时不会提前终止。
"""

from __future__ import annotations

import asyncio
from typing import Iterator

from zmai.agent import AgentContext, AgentState
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.agent import SWEAgent
from zmai.swe.loop_guard import LoopGuard
from zmai.tool import ToolCall, ToolRegistry


class PassThenDeliverBackend(Backend):
    """第 1 轮跑测试（通过）；若被允许，第 2 轮会发 show_to_user 交付。

    测试期望：因为测试已通过，Agent 必须在第 1 轮即终止，
    invoke 永远不被第 2 次调用。
    """

    name = "pass_then_deliver"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.calls.append(request.messages[-1].get("content", ""))
        if len(self.calls) == 1:
            return BackendResponse(
                content="Running tests...",
                tool_calls=[ToolCall(
                    id="c1", name="shell_exec",
                    params={"command": "python -m pytest test_pass.py -q"},
                )],
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                stop_reason="tool_use",
            )
        # 测试通过后不应到达这里
        return BackendResponse(
            content="done",
            tool_calls=[ToolCall(
                id="c2", name="show_to_user",
                params={"content": "All tests passed"},
            )],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


class FailBackend(Backend):
    """跑一个永远失败的测试——Agent 不应提前终止。"""

    name = "fail_backend"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.calls.append(request.messages[-1].get("content", ""))
        return BackendResponse(
            content="Running tests...",
            tool_calls=[ToolCall(
                id="c1", name="shell_exec",
                params={"command": "python -m pytest test_fail.py -q"},
            )],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


async def _build_ctx(tmp_path, backend, task="fix the bug"):
    agent = SWEAgent("term_test")
    registry = ToolRegistry()
    await agent.initialize(AgentContext(
        agent_id="term_test", task=task, tools=registry, metadata={},
    ))
    ctx = AgentContext(
        agent_id="term_test",
        task=task,
        backend=backend,
        tools=registry,
        workspace=tmp_path,
        config={"project_path": str(tmp_path)},
        metadata={"loop_guard": LoopGuard(threshold=3)},
    )
    return agent, ctx


def test_passing_tests_stop_and_complete(tmp_path):
    """pytest 全通过后：不再调用工具，execution status 为 completed。"""
    (tmp_path / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    backend = PassThenDeliverBackend()

    async def run():
        agent, ctx = await _build_ctx(tmp_path, backend)
        action = await agent.step(ctx)
        assert action.type == "complete", f"测试通过后应立即 complete, 实际: {action.type}"
        assert len(backend.calls) == 1, (
            f"测试通过后不应再调用工具, 实际调用 {len(backend.calls)} 次"
        )
        result = await agent.finalize(ctx)
        assert result.status == AgentState.COMPLETED

    asyncio.run(run())


def test_failing_tests_do_not_terminate(tmp_path):
    """pytest 失败时不应提前终止（返回 continue）。"""
    (tmp_path / "test_fail.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    backend = FailBackend()

    async def run():
        agent, ctx = await _build_ctx(tmp_path, backend)
        action = await agent.step(ctx)
        assert action.type == "continue", (
            f"测试失败时不应终止, 实际: {action.type}"
        )

    asyncio.run(run())
