"""自主停止测试 — Agent 达成任务完成条件后必须立即停止整个执行循环。

回归场景（原始 bug）：pytest 全绿后，runtime loop 没有退出，
仍出现 "zmai>" 提示并再次调用 shell_exec / read_file / pytest。

本测试验证修复后：
  1. pytest 成功一次即终止。
  2. 最终状态为 completed。
  3. backend.invoke 恰好被调用 1 次（绝不进入第 2 轮）。
  4. 没有第 2 轮 shell 调用。
  5. 没有第 2 次 pytest 调用。
  6. workflow 正常退出（Runtime.run 返回，不再循环）。
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
from zmai.runtime import Runtime
from zmai.swe.agent import SWEAgent
from zmai.swe.loop_guard import LoopGuard
from zmai.tool import ToolCall, ToolRegistry

TASK = "Fix bugs.\nRequirements:\n1. Run pytest.\n2. Stop after success."


class LoopTilToldBackend(Backend):
    """永远想重跑/复检 pytest 的 backend。

    若 loop 未正确停止，它会无休止返回 shell_exec(pytest)，
    从而暴露"测试通过后仍继续"的原始 bug。
    """

    name = "autostop_ever_loops"

    def __init__(self, test_file: str) -> None:
        self.test_file = test_file
        self.calls = 0
        self.pytest_tool_calls = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.calls += 1
        self.pytest_tool_calls += 1  # 本轮总是请求跑一次 pytest
        return BackendResponse(
            content="re-running tests",
            tool_calls=[ToolCall(
                id=f"c{self.calls}", name="shell_exec",
                params={"command": f"python -m pytest {self.test_file} -q"},
            )],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def test_autostop_runtime_exits_after_one_green(tmp_path):
    """完整 Runtime.run：pytest 全绿一次后，workflow 退出、不再循环。"""
    (tmp_path / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    backend = LoopTilToldBackend(str(tmp_path / "test_pass.py"))

    async def run() -> dict:
        runtime = Runtime()
        runtime._gateway.register(backend.name, backend.__class__, default=True)
        runtime._gateway._instances[backend.name] = backend
        return await runtime.run(
            agent_id="autostop_run",
            task=TASK,
            backend=backend.name,
            config={"project_path": str(tmp_path)},
        )

    result = asyncio.run(run())

    # 1. workflow 正常退出并标记 completed
    assert result["status"] == "completed", f"应 completed, 实际: {result}"
    # 2. backend.invoke 恰好 1 次（绝无第 2 轮）
    assert backend.calls == 1, (
        f"pytest 全绿后不得再调用 backend, 实际调用 {backend.calls} 次"
    )
    # 5. 没有第 2 次 pytest 请求
    assert backend.pytest_tool_calls == 1, (
        f"不应有第 2 次 pytest, 实际 {backend.pytest_tool_calls}"
    )


def test_autostop_step_short_circuits_next_round(tmp_path):
    """直接驱动 step 循环：第 1 轮绿后，第 2 轮不得再调用 backend。"""
    (tmp_path / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    backend = LoopTilToldBackend(str(tmp_path / "test_pass.py"))

    async def run():
        agent = SWEAgent("autostop_step")
        registry = ToolRegistry()
        ctx = AgentContext(
            agent_id="autostop_step",
            task=TASK,
            backend=backend,
            tools=registry,
            workspace=tmp_path,
            config={"project_path": str(tmp_path)},
            metadata={"loop_guard": LoopGuard(threshold=3)},
        )
        await agent.initialize(ctx)

        # 第 1 轮：跑 pytest 全绿 → 必须 complete
        a1 = await agent.step(ctx)
        assert a1.type == "complete", (
            f"pytest 全绿后应立即 complete, 实际: {a1.type}"
        )
        assert backend.calls == 1, (
            f"第 1 轮应恰好调用 1 次 backend, 实际 {backend.calls}"
        )

        # 第 2 轮：即使 backend 还想跑，必须短路 → 不得再调用
        a2 = await agent.step(ctx)
        assert a2.type == "complete", (
            f"已绿后第 2 轮应短路 complete, 实际: {a2.type}"
        )
        assert backend.calls == 1, (
            f"已绿后不得再调用 backend, 实际调用 {backend.calls} 次"
        )

        result = await agent.finalize(ctx)
        assert result.status == AgentState.COMPLETED

    asyncio.run(run())


def test_autostop_green_never_runs_second_pytest(tmp_path):
    """防御验证：全绿后 backend 绝不被再次请求（无第 2 轮 shell/pytest）。"""
    (tmp_path / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    backend = LoopTilToldBackend(str(tmp_path / "test_pass.py"))

    async def run():
        agent = SWEAgent("autostop_pytest")
        registry = ToolRegistry()
        ctx = AgentContext(
            agent_id="autostop_pytest",
            task=TASK,
            backend=backend,
            tools=registry,
            workspace=tmp_path,
            config={"project_path": str(tmp_path)},
            metadata={"loop_guard": LoopGuard(threshold=3)},
        )
        await agent.initialize(ctx)

        a1 = await agent.step(ctx)
        assert a1.type == "complete"
        a2 = await agent.step(ctx)
        assert a2.type == "complete"

        # 第 2 轮绝不能再次请求 shell_exec(pytest)
        assert backend.calls == 1, (
            f"应仅 1 次 backend 调用（1 次 pytest），实际 {backend.calls}"
        )
        assert backend.pytest_tool_calls == 1

    asyncio.run(run())
