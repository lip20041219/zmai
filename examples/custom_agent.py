"""custom_agent.py — 创建自定义 Agent。

功能：
  继承 zmai.Agent 创建自定义 Agent，手动执行其生命周期。

运行方式：
  # 有 API Key:
  export DEEPSEEK_API_KEY=sk-xxx
  python examples/custom_agent.py

  # 无 API Key（内置 Mock 演示流程）:
  python examples/custom_agent.py

需要配置：
  至少一个 Backend 的 API Key。
  无 API Key 时使用 MockBackend 演示。

预期结果：
  终端输出自定义 Agent 的执行过程和结果。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── 1. 定义一个自定义 Agent ──────────────────────────────

from zmai.agent import Agent, AgentAction, AgentContext, AgentResult, AgentState
from zmai.tool import Tool, ToolContext, ToolResult


class GreetingTool(Tool):
    """一个简单的工具：返回问候语。"""

    name = "greet"
    description = "向用户问好"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "用户名称"},
        },
        "required": ["name"],
    }

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        name = params.get("name", "World")
        return ToolResult.ok(output=f"你好, {name}!")


class CounterTool(Tool):
    """一个简单的工具：模拟计数。"""

    name = "count"
    description = "计数到指定数字"
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "目标数字"},
        },
        "required": ["limit"],
    }

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        limit = params.get("limit", 5)
        counts = [str(i) for i in range(1, limit + 1)]
        return ToolResult.ok(output=f"计数: {', '.join(counts)}")


class DemoAgent(Agent):
    """一个自定义 Agent：执行固定步骤后完成。

    实际场景中，Agent 的 step() 方法通常调用 LLM Backend 来决定下一步。
    这里为演示 Agent 生命周期，使用固定逻辑。
    """

    name = "demo_agent"
    description = "演示自定义 Agent"

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self._step_count = 0

    async def initialize(self, context: AgentContext) -> None:
        """初始化：注册自定义工具。"""
        if context.tools:
            context.tools.register(GreetingTool())
            context.tools.register(CounterTool())
        print(f"  [Agent] 初始化完成: {context.agent_id}")

    async def step(self, context: AgentContext) -> AgentAction:
        """每步执行一个工具调用，4 步后完成。"""
        self._step_count += 1
        context.step_count = self._step_count

        if self._step_count == 1:
            return AgentAction.cont(output="开始执行...")

        elif self._step_count == 2:
            result = context.tools.execute("greet", {"name": "ZMAI"}, ToolContext(
                agent_id=context.agent_id,
                workspace_path=context.workspace or os.getcwd(),
            ))
            return AgentAction.cont(output=f"greet: {result.output}")

        elif self._step_count == 3:
            result = context.tools.execute("count", {"limit": 3}, ToolContext(
                agent_id=context.agent_id,
                workspace_path=context.workspace or os.getcwd(),
            ))
            return AgentAction.cont(output=f"count: {result.output}")

        else:
            return AgentAction.complete(
                output=f"自定义 Agent 执行完毕，共 {self._step_count} 步。"
            )

    async def finalize(self, context: AgentContext) -> AgentResult:
        """最终化：汇总执行结果。"""
        return AgentResult(
            agent_id=self.agent_id,
            status=AgentState.COMPLETED,
            output=context.metadata.get("final_output", "done"),
            steps=self._step_count,
        )


# ── 2. 主流程：手动执行 Agent 生命周期 ──────────────────


async def main() -> None:
    from zmai.gateway import Backend
    from zmai.tool import ToolRegistry

    # 检测真实 Backend
    has_key = any(
        os.environ.get(v, "").strip()
        for v in ["DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]
    )

    # 2a. 创建 Agent 实例
    agent = DemoAgent(agent_id="demo_custom")

    # 2b. 创建 ToolRegistry 并注册基础工具
    tool_registry = ToolRegistry()

    # 2c. 创建 Backend（真实或 Mock）
    if has_key:
        from zmai.config import Config
        from zmai.gateway import PluginRegistry

        config = Config()
        gateway = PluginRegistry(config=config)
        backend = gateway.get()
        print(f"  [使用真实 Backend: {backend.name} ({backend.model})]")
    else:
        from zmai.gateway.base import (
            Backend as B,
            BackendCapability,
            BackendResponse,
        )

        class MockB(B):
            name = "mock"
            def __init__(self, config=None):
                self._config = config or {}
                self._model = "mock-model"
            @property
            def model(self):
                return self._model
            @property
            def capabilities(self):
                return {BackendCapability.SYSTEM_PROMPT}
            def invoke(self, request):
                return BackendResponse(content="[mock]")
            def stream(self, request):
                yield from ()

        backend = MockB()
        print("  [使用 MockBackend — 无 API Key 检测到]")

    # 2d. 构建 AgentContext
    context = AgentContext(
        agent_id="demo_custom",
        task="执行自定义 Agent 演示",
        backend=backend,
        tools=tool_registry,
        workspace=None,
        max_steps=10,
    )

    # 2e. 执行 Agent 生命周期
    print()
    print("  ── Agent 生命周期开始 ──")

    await agent.initialize(context)

    step_count = 0
    while step_count < context.max_steps:
        action = await agent.step(context)
        step_count += 1
        print(f"  第 {step_count} 步: type={action.type}, output={action.output}")
        if action.type in ("complete", "fail"):
            break

    result = await agent.finalize(context)
    print()
    print(f"  ── Agent 生命周期结束 ──")
    print(f"  状态: {result.status.value}")
    print(f"  总步数: {result.steps}")


if __name__ == "__main__":
    asyncio.run(main())
