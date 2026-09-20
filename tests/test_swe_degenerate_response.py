"""SWE Degenerate Response — 退化 LLM 响应不得被当成正常回合消费（P1-B）。

观测（pylint-5859 / 7228）：连续 4 次 `content="" tool_calls=[]` 被 Runtime 当成
一次正常模型决策消费掉 —— 完成门禁累计 3 次后 fail，全程没有任何机制重试或标注它。
`stop_reason=length/max_tokens` 同理：内容被截断，却被当作正常结束。

本文件覆盖：
  1. 空响应 → 复用现有 retry budget 重试
  2. 空响应重试耗尽 → fail-closed，且错误可诊断（[DEGENERATE_RESPONSE]）
  3. stop_reason=length（且**无** tool_calls）→ 重试
  4. 正常纯文本（content 非空 + 无 tool_calls）→ **不**重试（回归护栏）
  5. 正常 tool-call → **不**重试（回归护栏）
  6. stop_reason=length **带完整 tool_calls** → **不**重试（回归护栏）

用真实文件系统；backend 逐次返回预置响应，工具真实执行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from zmai.agent import AgentContext
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
)
from zmai.swe.agent import SWEAgent
from zmai.tool import ToolCall, ToolRegistry

EMPTY = BackendResponse(content="", tool_calls=None, stop_reason="end_turn")
TEXT_DONE = BackendResponse(content="done", tool_calls=None, stop_reason="end_turn")
TRUNCATED = BackendResponse(content="partial wor", tool_calls=None,
                            stop_reason="length")
READ_CALL = ToolCall(id="r1", name="read_file", params={"path": "app.py"})


class _RawBackend(Backend):
    """逐次返回预置 BackendResponse；用尽后重复最后一项。记录 invoke 次数。"""

    name = "scripted_raw"

    def __init__(self, responses: list[BackendResponse]):
        self._responses = responses
        self.invokes = 0

    def invoke(self, request: BackendRequest) -> BackendResponse:
        i = min(self.invokes, len(self._responses) - 1)
        self.invokes += 1
        return self._responses[i]

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _ctx(tmp_path: Path, backend: Backend, **config: Any) -> AgentContext:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    return AgentContext(
        agent_id="degenerate_test",
        task="修复 app.py",
        backend=backend,
        tools=ToolRegistry(),
        config={"project_path": str(tmp_path), "timeout": 30,
                "retry.max_attempts": 3, **config},
        metadata={},
    )


async def _one_step(ctx: AgentContext) -> Any:
    """跑 agent.step 一次（不进入外层循环），只观察这一回合的 retry 行为。"""
    agent = SWEAgent("degenerate_test")
    await agent.initialize(ctx)
    return await agent.step(ctx)


# ═══════════════════════════════════════════════════════════════════
# 1 / 3 — 退化响应被重试
# ═══════════════════════════════════════════════════════════════════


class TestDegenerateIsRetried:
    def test_empty_response_is_retried(self, tmp_path: Path):
        """空响应 → 重试；下一次拿到可用响应则正常继续（不 fail）。"""
        backend = _RawBackend([EMPTY, EMPTY,
                               BackendResponse(content="", tool_calls=[READ_CALL],
                                               stop_reason="tool_use")])
        ctx = _ctx(tmp_path, backend)
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 3, f"空响应应被重试到第 3 次: {backend.invokes}"
        assert action.type != "fail", f"拿到可用响应后不应失败: {action.error}"
        assert ctx.metadata["swe_stats"].get("degenerate_responses") == 2, \
            "两次退化响应都应被计数（可诊断）"

    def test_truncated_response_is_retried(self, tmp_path: Path):
        """stop_reason=length → 重试，不当作正常结束。"""
        backend = _RawBackend([TRUNCATED,
                               BackendResponse(content="", tool_calls=[READ_CALL],
                                               stop_reason="tool_use")])
        ctx = _ctx(tmp_path, backend)
        asyncio.run(_one_step(ctx))

        assert backend.invokes == 2, f"截断响应应被重试: {backend.invokes}"
        assert ctx.metadata["swe_stats"].get("degenerate_responses") == 1

    def test_max_tokens_stop_reason_is_retried(self, tmp_path: Path):
        """stop_reason=max_tokens（claude/gemini 归一化值）同样重试。"""
        backend = _RawBackend([BackendResponse(content="abc", stop_reason="max_tokens"),
                               BackendResponse(content="", tool_calls=[READ_CALL],
                                               stop_reason="tool_use")])
        ctx = _ctx(tmp_path, backend)
        asyncio.run(_one_step(ctx))

        assert backend.invokes == 2, f"max_tokens 截断应被重试: {backend.invokes}"


# ═══════════════════════════════════════════════════════════════════
# 2 — 重试耗尽 → fail-closed 且可诊断
# ═══════════════════════════════════════════════════════════════════


class TestRetryExhaustedFailsClosed:
    def test_empty_response_exhausted_fails_closed(self, tmp_path: Path):
        """重试耗尽后必须明确失败，不得把空响应当回合消费或静默完成。"""
        backend = _RawBackend([EMPTY])          # 永远返回空响应
        ctx = _ctx(tmp_path, backend)
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 3, f"应耗尽 retry budget(3): {backend.invokes}"
        assert action.type == "fail", f"必须 fail-closed, 实际 {action.type}"
        assert "DEGENERATE_RESPONSE" in (action.error or ""), \
            f"错误必须可诊断: {action.error}"
        assert "empty response" in (action.error or ""), \
            f"错误应说明退化类型: {action.error}"

    def test_exhausted_truncation_reports_truncation(self, tmp_path: Path):
        backend = _RawBackend([TRUNCATED])
        ctx = _ctx(tmp_path, backend)
        action = asyncio.run(_one_step(ctx))

        assert action.type == "fail"
        assert "truncated" in (action.error or ""), \
            f"截断耗尽应报告截断原因: {action.error}"


# ═══════════════════════════════════════════════════════════════════
# 4 / 5 — 正常响应不得被误判（回归护栏）
# ═══════════════════════════════════════════════════════════════════


class TestNormalResponseNotRetried:
    def test_plain_text_response_is_not_retried(self, tmp_path: Path):
        """content 非空 + 无 tool_calls 是合法收尾，绝不能重试。"""
        backend = _RawBackend([TEXT_DONE])
        ctx = _ctx(tmp_path, backend)
        asyncio.run(_one_step(ctx))

        assert backend.invokes == 1, \
            f"正常纯文本响应不得重试: {backend.invokes}"
        assert ctx.metadata.get("swe_stats", {}).get("degenerate_responses") is None

    def test_tool_call_response_is_not_retried(self, tmp_path: Path):
        """带 tool_calls 的回合完全不受影响。"""
        backend = _RawBackend([BackendResponse(content="", tool_calls=[READ_CALL],
                                               stop_reason="tool_use")])
        ctx = _ctx(tmp_path, backend)
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 1, f"工具回合不得重试: {backend.invokes}"
        assert action.type == "continue", f"工具回合应正常推进: {action.type}"

    def test_truncated_with_tool_calls_is_not_retried(self, tmp_path: Path):
        """截断但 tool_calls 完整 → 照常消费。

        重试复用的是同一个请求（prompt 与 max_tokens 都没变），截断是确定性复现的：
        重试只会烧完 retry budget 再 fail-closed，把一次"还能继续"的回合变成硬失败。
        截断只影响尾随文本，不影响已完整的 tool_calls。
        """
        backend = _RawBackend([BackendResponse(content="partial wor",
                                               tool_calls=[READ_CALL],
                                               stop_reason="length")])
        ctx = _ctx(tmp_path, backend)
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 1, \
            f"带 tool_calls 的截断回合不得重试: {backend.invokes}"
        assert action.type == "continue", f"应正常推进: {action.type}"
        assert ctx.metadata.get("swe_stats", {}).get("degenerate_responses") is None
