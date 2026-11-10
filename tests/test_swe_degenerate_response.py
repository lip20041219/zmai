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
    TokenUsage,
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
    def test_retry_counts_tokens_from_truncated_response(self, tmp_path: Path):
        backend = _RawBackend([
            BackendResponse(content="partial", stop_reason="length",
                            usage=TokenUsage(input_tokens=100, output_tokens=4096)),
            BackendResponse(content="", tool_calls=[READ_CALL], stop_reason="tool_use",
                            usage=TokenUsage(input_tokens=110, output_tokens=20)),
        ])
        ctx = _ctx(tmp_path, backend)
        action = asyncio.run(_one_step(ctx))

        assert action.type == "continue"
        assert backend.invokes == 2
        assert ctx.metadata["token_usage"]["input_tokens"] == 210
        assert ctx.metadata["token_usage"]["output_tokens"] == 4116

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


# ═══════════════════════════════════════════════════════════════════
# 7 — 截断恢复消息注入与 retry.max_attempts 行为（A/B 实验配置验证）
# ═══════════════════════════════════════════════════════════════════


class _RecordingBackend(Backend):
    """逐次返回预置响应，同时记录每次 invoke 收到的 request.messages 快照。

    注意：生产代码在截断恢复时会原地修改 request.messages，因此必须在记录时
    深拷贝 messages，否则事后检查会看到被修改后的内容。
    """

    name = "recording_raw"

    def __init__(self, responses: list[BackendResponse]):
        self._responses = responses
        self.invokes = 0
        self.request_snapshots: list[dict] = []

    def invoke(self, request: BackendRequest) -> BackendResponse:
        # 记录时快照 messages，避免后续原地修改影响断言
        import copy
        self.request_snapshots.append({
            "messages": copy.deepcopy(request.messages or []),
            "system_prompt": request.system_prompt or "",
        })
        i = min(self.invokes, len(self._responses) - 1)
        self.invokes += 1
        return self._responses[i]

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        yield BackendEvent(type="done", data="", index=1)

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE}


def _messages_text(snapshot: dict) -> str:
    """拼接 request snapshot 中 messages 的所有 content 字段。"""
    parts = []
    for msg in snapshot.get("messages", []) or []:
        c = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if c:
            parts.append(str(c))
    return "\n".join(parts)


class TestTruncationRecoveryInjection:
    """验证截断恢复消息确实进入第二次实际发送的请求（方案 A 核心路径）。"""

    def test_recovery_message_enters_second_request(self, tmp_path: Path):
        """retry.max_attempts=2：第一次截断 → 注入恢复消息 → 第二次请求包含它。"""
        backend = _RecordingBackend([
            TRUNCATED,
            BackendResponse(content="", tool_calls=[READ_CALL], stop_reason="tool_use"),
        ])
        ctx = _ctx(tmp_path, backend, **{"retry.max_attempts": 2})
        asyncio.run(_one_step(ctx))

        assert backend.invokes == 2, f"应重试一次: {backend.invokes}"
        # 第一次请求不应包含恢复消息（截断尚未发生）
        first_text = _messages_text(backend.request_snapshots[0])
        assert "[Recovery]" not in first_text, "第一次请求不应含恢复消息"
        # 第二次请求必须包含恢复消息
        second_text = _messages_text(backend.request_snapshots[1])
        assert "[Recovery]" in second_text, \
            f"第二次请求应含截断恢复消息，实际 messages 文本:\n{second_text[:500]}"
        assert "stop_reason=length" in second_text
        assert ctx.metadata["swe_stats"].get("degenerate_responses") == 1

    def test_recovery_then_success_continues_normally(self, tmp_path: Path):
        """恢复后第二次拿到可用响应 → 正常继续，不 fail。"""
        backend = _RecordingBackend([
            TRUNCATED,
            BackendResponse(content="", tool_calls=[READ_CALL], stop_reason="tool_use"),
        ])
        ctx = _ctx(tmp_path, backend, **{"retry.max_attempts": 2})
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 2
        assert action.type != "fail", f"恢复成功后不应失败: {getattr(action, 'error', '')}"

    def test_consecutive_truncation_fails_closed_with_max_2(self, tmp_path: Path):
        """retry.max_attempts=2：两次都截断 → 明确失败，错误可诊断。"""
        backend = _RecordingBackend([TRUNCATED, TRUNCATED])
        ctx = _ctx(tmp_path, backend, **{"retry.max_attempts": 2})
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 2, f"应耗尽 2 次预算: {backend.invokes}"
        assert action.type == "fail", f"必须 fail-closed: {action.type}"
        assert "DEGENERATE_RESPONSE" in (action.error or "")
        assert "truncated" in (action.error or "")
        # 第二次请求应包含恢复消息（第一次截断后注入）
        second_text = _messages_text(backend.request_snapshots[1])
        assert "[Recovery]" in second_text, "第二次请求应含恢复消息"

    def test_max_attempts_1_truncation_fails_immediately(self, tmp_path: Path):
        """retry.max_attempts=1：第一次截断 → 无重试、无恢复消息、立即失败。

        这是上次实验的实际配置：截断恢复消息条件 attempt < max_retries - 1
        在 max_retries=1 时恒为 False，恢复消息被构造但永不发送。
        """
        backend = _RecordingBackend([TRUNCATED])
        ctx = _ctx(tmp_path, backend, **{"retry.max_attempts": 1})
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 1, f"max_attempts=1 不应重试: {backend.invokes}"
        assert action.type == "fail", f"必须立即失败: {action.type}"
        assert "DEGENERATE_RESPONSE" in (action.error or "")
        assert "truncated" in (action.error or "")
        # 唯一一次请求不应包含恢复消息（没有第二次机会发送它）
        only_text = _messages_text(backend.request_snapshots[0])
        assert "[Recovery]" not in only_text, \
            "max_attempts=1 时恢复消息不应出现在任何请求中"

    def test_max_attempts_1_empty_response_fails_immediately(self, tmp_path: Path):
        """retry.max_attempts=1：空响应同样立即失败，无重试。"""
        backend = _RecordingBackend([EMPTY])
        ctx = _ctx(tmp_path, backend, **{"retry.max_attempts": 1})
        action = asyncio.run(_one_step(ctx))

        assert backend.invokes == 1
        assert action.type == "fail"
        assert "empty response" in (action.error or "")
