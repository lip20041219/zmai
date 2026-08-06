"""端到端 mock 对话测试 — 模拟 LLM 调用验证全流程。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

from zmai.agent import AgentAction, AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolContext, ToolRegistry


class MockBackend(Backend):
    """模拟 Backend，预置回复序列模拟 LLM 对话。"""

    name = "mock"

    def __init__(self, responses: list[dict] | None = None) -> None:
        self._responses = responses or [
            {"content": "我先读取文件再回复你。", "tool_calls": None},
        ]
        self._call_count = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        resp = self._responses[idx]
        self._call_count += 1

        # 如果预置回复指定了 tool_call，构造出来
        tool_calls = None
        if "tool" in resp:
            from zmai.tool import ToolCall
            tool_calls = [
                ToolCall(
                    id=f"call_{i}",
                    name=t["name"],
                    params=t.get("params", {}),
                )
                for i, t in enumerate(resp["tool"])
            ]

        return BackendResponse(
            content=resp.get("content", ""),
            tool_calls=tool_calls,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            stop_reason="end_turn" if not tool_calls else "tool_use",
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="text", data="mock stream")
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE, BackendCapability.SYSTEM_PROMPT}


class TestSWEAgentMockConversation:
    """用 mock backend 模拟完整 SWE Agent 对话流程。"""

    def test_agent_initializes_and_tools_available(self):
        async def run():
            agent = SWEAgent("swe_test_1")
            registry = ToolRegistry()
            backend = MockBackend()
            ctx = AgentContext(
                agent_id="swe_test_1",
                task="列出当前目录所有 Python 文件",
                backend=backend,
                tools=registry,
            )
            await agent.initialize(ctx)
            assert len(registry.list()) == 8
            names = [t.name for t in registry.list()]
            assert "shell_exec" in names
            assert "read_file" in names
            return agent, registry, ctx

        agent, registry, ctx = asyncio.run(run())

    def test_agent_step_with_tool_execution(self, tmp_path: Path):
        """模拟 Agent 调用 shell_exec 工具并返回结果。"""
        async def run():
            agent = SWEAgent("swe_test_2")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="swe_test_2", task="test", tools=registry,
            ))

            # 模拟 LLM 调用 shell_exec 来列出文件
            backend = MockBackend(responses=[
                {
                    "content": "我用 shell 命令查看文件列表。",
                    "tool": [{"name": "shell_exec", "params": {"command": "dir /b *.py"}}],
                },
                {
                    "content": "找到文件了，任务完成。",
                    "tool_calls": None,
                },
            ])

            ctx = AgentContext(
                agent_id="swe_test_2",
                task="列出 Python 文件",
                backend=backend,
                tools=registry,
                workspace=tmp_path,
                metadata={"messages": []},
            )

            # 第一步：预期有 tool_call
            action1 = await agent.step(ctx)
            assert action1.type == "continue", f"预期 continue, 实际 {action1.type}"
            assert "shell_exec" in ctx.metadata.get("messages", [])[-1]["content"]

            # 第二步：预期完成
            action2 = await agent.step(ctx)
            assert action2.type == "complete", f"预期 complete, 实际 {action2.type}"

        asyncio.run(run())

    def test_agent_full_cycle_initializes_tools(self):
        """Agent 初始化后 tools 正确注册。"""
        async def run():
            agent = SWEAgent("swe_test_3")
            registry = ToolRegistry()
            await agent.initialize(AgentContext(
                agent_id="swe_test_3", task="test", tools=registry,
            ))
            names = {t.name for t in registry.list()}
            names = {t.name for t in registry.list()}
            assert names == {"read_file", "write_file", "edit", "grep", "shell_exec", "git", "show_to_user", "open_in_browser"}

            result = await agent.finalize(AgentContext(
                agent_id="swe_test_3", task="test", tools=registry,
            ))
            assert result.agent_id == "swe_test_3"
            assert result.steps == 0

        asyncio.run(run())

    def test_agent_fails_without_backend(self):
        """没有 Backend 时 agent step 直接 fail。"""
        async def run():
            agent = SWEAgent("swe_test_4")
            registry = ToolRegistry()
            ctx = AgentContext(
                agent_id="swe_test_4",
                task="test",
                tools=registry,
                backend=None,
            )
            action = await agent.step(ctx)
            assert action.type == "fail"

        asyncio.run(run())
