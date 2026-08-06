"""Requirements-section regression tests.

验证原始 bug：多行用户提示中的 "Requirements:" 段被错误当作独立任务。

验收标准（对应 issue）:
  Input: "Fix bugs.
          Requirements:
          1. Run tests.
          2. Stop after success."
  Expected:
    - Agent 只执行 ONE 个任务（不把 Requirements 每行拆成独立任务）。
    - 测试通过后：无额外 shell 调用、无额外 read_file、无额外 agent loop。
    - backend.invoke 仅被调用 1 次（若被当作多任务，会调用多次）。
    - execution status 为 completed。

同时验证测试命令规范化：Agent 引导使用 `python -m pytest`，非裸 `pytest`。
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

_MULTILINE_TASK = (
    "Fix bugs.\n"
    "Requirements:\n"
    "1. Run tests.\n"
    "2. Stop after success."
)


class RequirementsThenRunBackend(Backend):
    """每轮都返回一次测试运行（若 agent 误把需求当多任务，会多次 invoke）。

    测试期望：测试通过后 Agent 必须短路，invoke 仅 1 次 ——
    证明 Requirements 段没有产生额外任务循环。
    """

    name = "requirements_run"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.calls += 1
        return BackendResponse(
            content="running tests",
            tool_calls=[ToolCall(
                id=f"c{self.calls}",
                name="shell_exec",
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


def test_requirements_section_is_not_tasks(tmp_path):
    """多行 Requirements 任务：测试通过后只执行一个任务，立即停止。"""
    (tmp_path / "test_pass.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    backend = RequirementsThenRunBackend()

    async def run():
        agent = SWEAgent("req_constraints_test")
        registry = ToolRegistry()
        ctx = AgentContext(
            agent_id="req_constraints_test",
            task=_MULTILINE_TASK,
            backend=backend,
            tools=registry,
            workspace=tmp_path,
            config={"project_path": str(tmp_path)},
            metadata={"loop_guard": LoopGuard(threshold=3)},
        )
        await agent.initialize(ctx)

        # 第 1 轮：跑测试（唯一真正的任务）→ 通过 → 立即 complete
        a1 = await agent.step(ctx)
        assert a1.type == "complete", (
            f"Requirements 段不应产生额外任务; 测试通过后应 complete, 实际: {a1.type}"
        )
        assert backend.calls == 1, (
            f"只应执行一个任务（1 次工具调用）, 实际 backend 被调用 {backend.calls} 次"
        )

        # 第 2 轮：即使后端还想跑, Agent 必须短路 —— 无额外 shell/read/loop
        a2 = await agent.step(ctx)
        assert a2.type == "complete", (
            f"已绿后不得进入新的 agent loop, 实际: {a2.type}"
        )
        assert backend.calls == 1, (
            f"测试通过后不得再调用任何工具, 实际 {backend.calls} 次"
        )

        result = await agent.finalize(ctx)
        assert result.status == AgentState.COMPLETED, (
            f"执行状态应为 completed, 实际: {result.status}"
        )

    asyncio.run(run())


def test_system_prompt_normalizes_test_command():
    """提示词必须引导 python -m pytest, 禁止裸 pytest 作为测试命令。"""
    from zmai.swe.agent import _BASE_SYSTEM_PROMPT

    assert "python -m pytest" in _BASE_SYSTEM_PROMPT, (
        "Phase 2 应使用 `python -m pytest` 而非裸 `pytest`"
    )
    # 不应把裸 `pytest` 当作可执行测试命令引导（仅允许出现在 `python -m pytest` 中）
    for line in _BASE_SYSTEM_PROMPT.splitlines():
        stripped = line.strip()
        if stripped.startswith("- Run") or stripped.startswith("1. RUN"):
            # 命令引导行若提到运行测试，必须用 python -m pytest，不得用裸 pytest
            if "pytest" in stripped and "python -m pytest" not in stripped:
                assert False, (
                    f"测试命令规范应指向 python -m pytest, 违规行: {stripped}"
                )
