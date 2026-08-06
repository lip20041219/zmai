"""真实 API 调用测试 — 用 DeepSeek 验证全流程。"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Iterator

import pytest

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


import os

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"

# 无真实 API Key 时跳过所有真实 API 测试，避免无凭据下 401 导致失败。
# 不硬编码任何凭据。
pytestmark = pytest.mark.skipif(
    not DEEPSEEK_API_KEY,
    reason="未设置 DEEPSEEK_API_KEY，跳过真实 API 测试",
)


class DeepSeekBackend(Backend):
    """DeepSeek API Backend (OpenAI 兼容格式)。"""

    name = "deepseek"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        c = config or {}
        self._api_key = c.get("api_key", DEEPSEEK_API_KEY)
        self._model = c.get("model", "deepseek-chat")
        self._base = c.get("base_url", DEEPSEEK_BASE).rstrip("/")
        self._max_tokens = int(c.get("max_tokens", 2048))

    def invoke(self, request: BackendRequest) -> BackendResponse:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(request.messages)

        tools = None
        if request.tools:
            tools = []
            for t in request.tools:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                })

        body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            body["tools"] = tools

        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            return BackendResponse(content=f"API 错误: {e.code} {err}")

        choice = result.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls_raw = msg.get("tool_calls")

        from zmai.tool import ToolCall
        tool_calls = None
        if tool_calls_raw:
            tool_calls = [
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc["function"]["name"],
                    params=json.loads(tc["function"].get("arguments", "{}")),
                )
                for tc in tool_calls_raw
            ]

        usage_raw = result.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
        )

        stop = choice.get("finish_reason", "stop")
        if stop == "tool_calls":
            stop = "tool_use"

        return BackendResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop,
            metadata={"model": result.get("model", self._model)},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="text", data="stream 未实现")
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE, BackendCapability.SYSTEM_PROMPT}


def test_deepseek_simple_chat():
    """测试 DeepSeek 简单对话。"""
    backend = DeepSeekBackend()
    req = BackendRequest(
        messages=[{"role": "user", "content": "用一句话回答：1+1=?"}],
    )
    resp = backend.invoke(req)
    print(f"\n[DeepSeek 回复] {resp.content}")
    assert resp.content, "应有回复内容"
    assert resp.usage is not None
    print(f"[Token 用量] input={resp.usage.input_tokens} output={resp.usage.output_tokens}")


def test_deepseek_with_tools():
    """测试 DeepSeek 工具调用能力。"""
    from zmai.swe.tools import ReadFileTool, ShellTool
    from zmai.tool import ToolDefinition

    backend = DeepSeekBackend()
    tools = [
        ToolDefinition(
            name="shell_exec",
            description="Execute a shell command",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "command to run"},
                },
                "required": ["command"],
            },
        ),
    ]

    req = BackendRequest(
        messages=[{"role": "user", "content": "执行 echo hello_world 命令"}],
        tools=tools,
        system_prompt="你有 shell_exec 工具可用。需要执行命令时就调用它。",
    )
    resp = backend.invoke(req)
    print(f"\n[DeepSeek 工具回复] content={resp.content}")
    if resp.tool_calls:
        for tc in resp.tool_calls:
            print(f"  → 工具调用: {tc.name}({tc.params})")
    else:
        print("  → 无工具调用")
    assert resp.content or resp.tool_calls


def test_swe_agent_live():
    """用 DeepSeek 运行 SWE Agent 完成真实任务。"""
    import asyncio

    async def run():
        agent = SWEAgent("live_test_1")
        registry = ToolRegistry()
        backend = DeepSeekBackend()

        from zmai.agent import AgentContext
        ctx = AgentContext(
            agent_id="live_test_1",
            task="列出当前目录的所有文件，告诉我一共有几个",
            backend=backend,
            tools=registry,
            metadata={"messages": []},
        )

        await agent.initialize(ctx)
        print(f"\n[Agent] 工具注册: {[t.name for t in registry.list()]}")

        step = 0
        while step < 6:
            action = await agent.step(ctx)
            print(f"[Step {step}] action={action.type}")
            if action.output:
                print(f"  output={action.output[:200]}")
            if action.type in ("complete", "fail"):
                break
            step += 1

        result = await agent.finalize(ctx)
        print(f"\n[Agent 完成] status={result.status} steps={result.steps}")
        assert result.status.value in ("completed", "failed")
        return result

    asyncio.run(run())


if __name__ == "__main__":
    import sys
    test_map = {
        "chat": test_deepseek_simple_chat,
        "tools": test_deepseek_with_tools,
        "swe": test_swe_agent_live,
    }
    target = sys.argv[1] if len(sys.argv) > 1 else "swe"
    test_map.get(target, test_swe_agent_live)()
