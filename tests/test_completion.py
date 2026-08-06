"""CompletionState regression tests.

验证: 一旦测试全绿（exit 0 + 通过），后续轮次不得再调用 backend /
工具 —— 这是原始 bug（pytest 通过后仍重复跑测试/读文件/验证）。
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
from zmai.swe.completion import CompletionState
from zmai.swe.loop_guard import LoopGuard
from zmai.tool import ToolCall, ToolRegistry


class EverLoopsBackend(Backend):
    """每次被调用都返回一个 pytest 运行（永远想复检/重跑）。

    测试期望: 第 1 轮 pytest 通过后，Agent 在进入第 2 轮 step() 时
    必须短路，invoke 仅被调用 1 次。
    """

    name = "ever_loops"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.calls += 1
        return BackendResponse(
            content="re-running tests",
            tool_calls=[ToolCall(
                id=f"c{self.calls}", name="shell_exec",
                params={"command": "python -m pytest test_pass.py -q"},
            )],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def test_green_tests_short_circuit_next_round(tmp_path):
    """测试已绿后，下一轮 step() 不调用 backend，直接 complete。"""
    (tmp_path / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    backend = EverLoopsBackend()

    async def run():
        agent = SWEAgent("completion_test")
        registry = ToolRegistry()
        # 模拟生产 runtime：initialize 与 step 共享同一 ctx
        ctx = AgentContext(
            agent_id="completion_test",
            task="fix the bug",
            backend=backend,
            tools=registry,
            workspace=tmp_path,
            config={"project_path": str(tmp_path)},
            metadata={"loop_guard": LoopGuard(threshold=3)},
        )
        await agent.initialize(ctx)

        # 第 1 轮: 跑测试, 通过 → 应 complete
        a1 = await agent.step(ctx)
        assert a1.type == "complete", f"测试通过后应 complete, 实际: {a1.type}"
        assert backend.calls == 1, f"第 1 轮应恰好调用 1 次 backend, 实际 {backend.calls}"

        # 第 2 轮: 即使后端还想跑, Agent 必须短路, 不调用 backend
        a2 = await agent.step(ctx)
        assert a2.type == "complete", f"已绿后第 2 轮应短路 complete, 实际: {a2.type}"
        assert backend.calls == 1, (
            f"已绿后不得再调用 backend, 实际调用 {backend.calls} 次"
        )

        result = await agent.finalize(ctx)
        assert result.status == AgentState.COMPLETED

    asyncio.run(run())


def test_completion_state_conditions():
    """CompletionState 逐项条件验证。"""
    c = CompletionState()
    assert not c.should_complete()  # 未做任何事

    c.record_test_result(exit_code=0, passed=True, step=1)
    assert c.should_complete()  # exit 0 + 通过 + 无后续修改

    # 修改使测试结果失效 → 不得完成
    c.record_modification(step=2)
    assert not c.should_complete()

    # 再次绿 → 可完成
    c.record_test_result(exit_code=0, passed=True, step=3)
    assert c.should_complete()

    # 失败 → 不得完成
    c.record_test_result(exit_code=1, passed=False, step=4)
    assert not c.should_complete()
