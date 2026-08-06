"""Mock Backend 行为验证测试。

每个测试验证一个明确的 Mock Backend 行为。
不测试"Mock 名称存在"，只测试"Mock 在场景中表现正确"。

测试通过标准：
  - 每个 Mock 实现 Backend ABC 接口（name/invoke/stream/capabilities）
  - 每个 Mock 在预期场景中引发正确的异常类型或返回值
  - 每个 Mock 能被 Runtime.run() 消费（集成验证）
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator

import pytest

from zmai.config.config import Config
from zmai.errors import BackendError
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.gateway.registry import BackendRegistry
from zmai.runtime import Runtime

from tests.mocks import (
    AuthErrorBackend,
    ConnectionErrorBackend,
    FlakyBackend,
    InfiniteLoopBackend,
    InvalidResponseBackend,
    SuccessBackend,
    TimeoutBackend,
)


# ═════════════════════════════════════════════════════════════════════
# 辅助
# ═════════════════════════════════════════════════════════════════════

def _runtime_with(backend_cls: type[Backend], **kwargs: Any) -> Runtime:
    """创建已注册指定 Backend 的 Runtime 实例。"""
    cfg = Config(sources=[])
    rt = Runtime(config=cfg)
    rt._gateway.register(backend_cls.name, backend_cls, default=True, config=kwargs)
    return rt


def _run(rt: Runtime, task: str = "test") -> dict:
    """同步运行业务。"""
    async def run():
        return await rt.run(
            agent_id=f"test_{id(rt)}",
            task=task,
            backend=rt._gateway.default_name,
        )
    return asyncio.run(run())


# ═════════════════════════════════════════════════════════════════════
# 接口契约 — 所有 Mock 必须实现 Backend ABC
# ═════════════════════════════════════════════════════════════════════

class TestMockBackendInterface:
    """验证每个 Mock 正确实现 Backend ABC。"""

    @pytest.mark.parametrize("cls", [
        SuccessBackend,
        AuthErrorBackend,
        ConnectionErrorBackend,
        TimeoutBackend,
        InvalidResponseBackend,
        FlakyBackend,
        InfiniteLoopBackend,
    ])
    def test_has_name(self, cls: type[Backend]) -> None:
        assert cls.name, f"{cls.__name__} must define 'name'"

    @pytest.mark.parametrize("cls", [
        SuccessBackend,
        AuthErrorBackend,
        ConnectionErrorBackend,
        TimeoutBackend,
        InvalidResponseBackend,
        FlakyBackend,
        InfiniteLoopBackend,
    ])
    def test_implements_invoke(self, cls: type[Backend]) -> None:
        instance = cls()
        assert callable(instance.invoke)

    @pytest.mark.parametrize("cls", [
        SuccessBackend,
        AuthErrorBackend,
        ConnectionErrorBackend,
        TimeoutBackend,
        InvalidResponseBackend,
        FlakyBackend,
        InfiniteLoopBackend,
    ])
    def test_implements_stream(self, cls: type[Backend]) -> None:
        instance = cls()
        assert callable(instance.stream)

    @pytest.mark.parametrize("cls", [
        SuccessBackend,
        AuthErrorBackend,
        ConnectionErrorBackend,
        TimeoutBackend,
        InvalidResponseBackend,
        FlakyBackend,
        InfiniteLoopBackend,
    ])
    def test_implements_capabilities(self, cls: type[Backend]) -> None:
        instance = cls()
        caps = instance.capabilities
        assert isinstance(caps, set)
        for c in caps:
            assert isinstance(c, BackendCapability)


# ═════════════════════════════════════════════════════════════════════
# SuccessBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestSuccessBackend:
    def test_invoke_returns_response(self) -> None:
        """SuccessBackend.invoke() 应正常返回 BackendResponse。"""
        b = SuccessBackend()
        req = BackendRequest(messages=[{"role": "user", "content": "hello"}])
        resp = b.invoke(req)
        assert isinstance(resp, BackendResponse)
        assert resp.content != ""
        assert resp.stop_reason == "end_turn"

    def test_invoke_increments_count(self) -> None:
        """多次调用递增 invoke_count。"""
        b = SuccessBackend()
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        b.invoke(req)
        assert b.invoke_count == 1
        b.invoke(req)
        assert b.invoke_count == 2

    def test_stream_produces_events(self) -> None:
        """stream() 产生事件序列并以 done 结尾。"""
        b = SuccessBackend()
        req = BackendRequest(messages=[])
        events = list(b.stream(req))
        assert len(events) >= 1
        assert events[-1].type == "done"

    def test_runtime_completes(self) -> None:
        """SuccessBackend 驱动的完整 Runtime 运行应成功。"""
        rt = _runtime_with(SuccessBackend)
        result = _run(rt)
        assert result["status"] == "completed"

    def test_capabilities(self) -> None:
        """SuccessBackend 应声明基本能力。"""
        b = SuccessBackend()
        assert BackendCapability.TOOL_USE in b.capabilities
        assert BackendCapability.SYSTEM_PROMPT in b.capabilities


# ═════════════════════════════════════════════════════════════════════
# AuthErrorBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestAuthErrorBackend:
    def test_invoke_raises_backend_error(self) -> None:
        """AuthErrorBackend 应始终抛出 BackendError。"""
        b = AuthErrorBackend()
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendError) as exc_info:
            b.invoke(req)
        assert "401" in str(exc_info.value)

    def test_runtime_returns_failed(self) -> None:
        """AuthErrorBackend 驱动的 Runtime 应返回 failed 状态。"""
        rt = _runtime_with(AuthErrorBackend)
        result = _run(rt)
        assert result["status"] == "failed"

    def test_runtime_error_mentions_key(self) -> None:
        """AuthErrorBackend 的错误应提及 API Key 或 401。"""
        rt = _runtime_with(AuthErrorBackend)
        result = _run(rt)
        err = result.get("error", "").lower()
        assert "401" in err or "key" in err or "auth" in err

    def test_invoke_count_tracked(self) -> None:
        """即使抛出异常，invoke_count 也应递增。"""
        b = AuthErrorBackend()
        req = BackendRequest(messages=[])
        for _ in range(3):
            try:
                b.invoke(req)
            except BackendError:
                pass
        assert b.invoke_count == 3


# ═════════════════════════════════════════════════════════════════════
# ConnectionErrorBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestConnectionErrorBackend:
    def test_invoke_raises_connection_error(self) -> None:
        """ConnectionErrorBackend 应抛出 ConnectionError 含网络错误描述。"""
        b = ConnectionErrorBackend()
        req = BackendRequest(messages=[])
        with pytest.raises(ConnectionError) as exc_info:
            b.invoke(req)
        msg = str(exc_info.value).lower()
        assert "getaddrinfo" in msg or "connection" in msg

    def test_runtime_handles_connection_error(self) -> None:
        """Runtime 应处理 ConnectionError 而不崩溃（Agent 重试或失败）。"""
        rt = _runtime_with(ConnectionErrorBackend)
        result = _run(rt)
        assert result["status"] in ("completed", "failed")

    def test_custom_error_message(self) -> None:
        """支持自定义错误消息。"""
        b = ConnectionErrorBackend(config={"error_msg": "custom connect refused"})
        req = BackendRequest(messages=[])
        with pytest.raises(ConnectionError, match="custom connect refused"):
            b.invoke(req)

    def test_stream_also_raises(self) -> None:
        """stream() 同样抛出 ConnectionError。"""
        b = ConnectionErrorBackend()
        req = BackendRequest(messages=[])
        with pytest.raises(ConnectionError):
            list(b.stream(req))


# ═════════════════════════════════════════════════════════════════════
# TimeoutBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestTimeoutBackend:
    def test_invoke_raises_timeout_error_immediately(self) -> None:
        """TimeoutBackend 应立即抛出 TimeoutError，不应 sleep 延迟。"""
        import time
        b = TimeoutBackend()
        req = BackendRequest(messages=[])
        start = time.time()
        with pytest.raises(TimeoutError) as exc_info:
            b.invoke(req)
        elapsed = time.time() - start
        assert elapsed < 0.5, f"TimeoutBackend 不应 sleep，实际耗时 {elapsed:.2f}s"
        assert "timed out" in str(exc_info.value).lower()

    def test_runtime_handles_timeout(self) -> None:
        """Runtime 应处理 TimeoutError 而不崩溃。"""
        rt = _runtime_with(TimeoutBackend)
        result = _run(rt)
        assert result["status"] in ("completed", "failed")

    def test_multiple_calls_all_timeout(self) -> None:
        """每次调用都超时。"""
        b = TimeoutBackend()
        req = BackendRequest(messages=[])
        for _ in range(3):
            with pytest.raises(TimeoutError):
                b.invoke(req)
        assert b.invoke_count == 3

    def test_custom_error_message(self) -> None:
        """支持自定义超时消息。"""
        b = TimeoutBackend(config={"error_msg": "custom: request timed out after 10s"})
        req = BackendRequest(messages=[])
        with pytest.raises(TimeoutError, match="custom: request timed out after 10s"):
            b.invoke(req)


# ═════════════════════════════════════════════════════════════════════
# InvalidResponseBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestInvalidResponseBackend:
    def test_invoke_returns_empty_content_default(self) -> None:
        """默认返回 BackendResponse(content="")。"""
        b = InvalidResponseBackend()
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        resp = b.invoke(req)
        assert isinstance(resp, BackendResponse)
        assert resp.content == ""
        assert resp.stop_reason == "end_turn"

    def test_runtime_handles_empty_content(self) -> None:
        """Runtime 不应因空内容崩溃。"""
        rt = _runtime_with(InvalidResponseBackend)
        result = _run(rt)
        assert result["status"] in ("completed", "failed")

    def test_empty_response_triggers_invalid_response_error(self) -> None:
        """empty_response=True 时抛出 BackendInvalidResponse。"""
        from zmai.errors import BackendInvalidResponse
        b = InvalidResponseBackend(config={"empty_response": True})
        req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(BackendInvalidResponse, match="空响应体"):
            b.invoke(req)

    def test_tool_only_mode(self) -> None:
        """tool_only=True 时返回工具调用但无文本。"""
        b = InvalidResponseBackend(config={"tool_only": True})
        req = BackendRequest(messages=[])
        resp = b.invoke(req)
        assert resp.content == ""
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) >= 1

    def test_invoke_count_tracked(self) -> None:
        """调用计数。"""
        b = InvalidResponseBackend()
        req = BackendRequest(messages=[])
        b.invoke(req)
        b.invoke(req)
        b.invoke(req)
        assert b.invoke_count == 3


# ═════════════════════════════════════════════════════════════════════
# FlakyBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestFlakyBackend:
    def test_initial_calls_raise_exception(self) -> None:
        """前 fail_count 次调用抛出普通 Exception（非 BackendError）。"""
        b = FlakyBackend(config={"fail_count": 2})
        req = BackendRequest(messages=[])
        with pytest.raises(Exception) as exc_info:
            b.invoke(req)
        assert not isinstance(exc_info.value, BackendError)
        assert "503" in str(exc_info.value)

    def test_succeeds_after_fail_count(self) -> None:
        """第 fail_count+1 次调用返回成功。"""
        b = FlakyBackend(config={"fail_count": 2})
        req = BackendRequest(messages=[])

        # 前 2 次失败
        for _ in range(2):
            with pytest.raises(Exception):
                b.invoke(req)

        # 第 3 次成功
        resp = b.invoke(req)
        assert isinstance(resp, BackendResponse)
        assert resp.content == "ok after retry"

    def test_uses_regular_exception_not_backend_error(self) -> None:
        """FlakyBackend 抛出普通 Exception（触发真实 Backend 的重试逻辑）。"""
        b = FlakyBackend()
        req = BackendRequest(messages=[])
        try:
            b.invoke(req)
        except Exception as e:
            assert not isinstance(e, BackendError), \
                "FlakyBackend 应抛出 Exception 而非 BackendError，" \
                "以触发真实 Backend 的重试路径"

    def test_runtime_handles_flaky(self) -> None:
        """FlakyBackend 在 Runtime 中不应崩溃（Runtime 捕获未预期的异常）。"""
        rt = _runtime_with(FlakyBackend, fail_count=1)
        result = _run(rt)
        # Flaky 在 Agent 层面表现为 Backend 调用失败
        assert result["status"] in ("completed", "failed")

    def test_custom_fail_count(self) -> None:
        """自定义 fail_count 生效。"""
        b = FlakyBackend(config={"fail_count": 3})
        req = BackendRequest(messages=[])
        for _ in range(3):
            with pytest.raises(Exception):
                b.invoke(req)
        resp = b.invoke(req)
        assert resp.content == "ok after retry"


# ═════════════════════════════════════════════════════════════════════
# InfiniteLoopBackend 行为
# ═════════════════════════════════════════════════════════════════════

class TestInfiniteLoopBackend:
    def test_always_returns_tool_calls(self) -> None:
        """每次 invoke 都返回工具调用，无文本内容。"""
        b = InfiniteLoopBackend()
        req = BackendRequest(messages=[])
        for _ in range(5):
            resp = b.invoke(req)
            assert resp.content == "", "不应返回文本"
            assert resp.tool_calls is not None, "必须返回工具调用"
            assert resp.stop_reason == "tool_use"

    def test_tool_call_always_shell_exec(self) -> None:
        """工具调用始终是 shell_exec。"""
        b = InfiniteLoopBackend()
        req = BackendRequest(messages=[])
        resp = b.invoke(req)
        assert resp.tool_calls[0].name == "shell_exec"

    def test_invoke_count_increments(self) -> None:
        """每次调用递增计数。"""
        b = InfiniteLoopBackend()
        req = BackendRequest(messages=[])
        for _ in range(10):
            b.invoke(req)
        assert b.invoke_count == 10

    def test_stream_also_returns_tool_calls(self) -> None:
        """stream() 也产生工具调用事件。"""
        b = InfiniteLoopBackend()
        req = BackendRequest(messages=[])
        events = list(b.stream(req))
        assert any(e.type == "tool_call" for e in events)


# ═════════════════════════════════════════════════════════════════════
# Runtime 集成 — 真实场景验证
# ═════════════════════════════════════════════════════════════════════

class TestMockBackendRuntimeIntegration:
    """验证每个 Mock 能被 Runtime.run() 消费而不引起未处理的崩溃。"""

    @pytest.mark.parametrize("cls,expect_fail", [
        (SuccessBackend, False),
        (AuthErrorBackend, True),
        (ConnectionErrorBackend, True),
        (TimeoutBackend, True),
        (InvalidResponseBackend, False),  # 空响应不崩溃即可
        (FlakyBackend, False),            # Runtime 捕获异常
        (InfiniteLoopBackend, False),     # max_steps 终止循环
    ])
    def test_runtime_does_not_crash(self, cls: type[Backend], expect_fail: bool) -> None:
        """Runtime 使用任何 Mock Backend 都不应崩溃。"""
        rt = _runtime_with(cls)
        try:
            result = _run(rt)
            assert isinstance(result, dict)
            assert "status" in result
            if expect_fail:
                assert result["status"] == "failed", f"{cls.__name__} 应返回 failed"
        except Exception as e:
            pytest.fail(f"{cls.__name__} 导致 Runtime 未处理的异常: {e}")


class TestMockBackendIsolation:
    """每个 Runtime 实例独立使用 Mock Backend。"""

    def test_two_runtimes_independent(self) -> None:
        """两个 Runtime 实例各自使用自己的 Mock Backend。"""
        rt1 = _runtime_with(SuccessBackend)
        rt2 = _runtime_with(AuthErrorBackend)
        r1 = _run(rt1)
        r2 = _run(rt2)
        assert r1["status"] == "completed"
        assert r2["status"] == "failed"


class TestMockAlias:
    """兼容性别名。"""

    def test_mock_backend_alias_exists(self) -> None:
        """MockBackend 是 SuccessBackend 的别名。"""
        from tests.mocks import MockBackend
        assert MockBackend is SuccessBackend
