"""DeepSeek Backend — OpenAI-compatible format API implementation."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from zmai.errors import BackendError
from zmai.gateway.base import (
    Backend,
    BackendCapability,
    BackendEvent,
    BackendRequest,
    BackendResponse,
    TokenUsage,
)
from zmai.gateway.errors import validate_backend_response
from zmai.tool import ToolCall
from zmai.tool.base import remaining_wall_seconds

logger = logging.getLogger("zmai.gateway.backends.deepseek")


def _validate_tool_history(messages: list[dict[str, Any]]) -> None:
    """Fail before HTTP if context pruning broke an assistant/tool exchange."""
    pending: set[str] = set()
    for message in messages:
        if pending and message.get("role") != "tool":
            raise BackendError("DeepSeek tool history incomplete before next message")
        if message.get("role") == "assistant" and message.get("tool_calls"):
            pending = {call["id"] for call in message["tool_calls"]}
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise BackendError("DeepSeek tool response has no matching call")
            pending.remove(call_id)
    if pending:
        raise BackendError("DeepSeek tool history missing result")

class DeepSeekBackend(Backend):
    """DeepSeek API Backend (OpenAI 兼容格式)。

    统一配置字段（所有 Backend 共用）:
        api_key:     API Key（默认从 DEEPSEEK_API_KEY 环境变量读取）。
        model:       模型名称（默认 deepseek-flash）。
        base_url:    API 基础 URL（默认 https://api.deepseek.com/v1）。
        timeout:     请求超时秒数（默认 120）。
        max_tokens:  最大生成 token 数（默认 4096）。
        temperature: 采样温度（默认 0.7）。
    """

    name: str = "deepseek"
    provider: str = "deepseek"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        c = self._config
        self._env_key: str = "DEEPSEEK_API_KEY"
        # api_key 由 PluginRegistry._build_config() 通过 CredentialResolver 装配
        self._api_key: str = c.get("api_key", "")
        if not self._api_key:
            logger.warning("%s 未设置，DeepSeekBackend 无法正常工作", self._env_key)

        # 统一配置字段（所有 Backend 共用同一套 key 名）
        self._model: str = c.get("model", "deepseek-flash")
        self._thinking: dict[str, str] = c.get("thinking", {"type": "enabled"})
        self._reasoning_effort: str = c.get("reasoning_effort", "high")
        self._base_url: str = c.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
        self._timeout: int = int(c.get("timeout", 120))
        self._max_tokens: int = int(c.get("max_tokens", 4096))
        self._temperature: float = float(c.get("temperature", 0.7))
        logger.info("DeepSeekBackend 初始化: model=%s", self._model)

    @property
    def model(self) -> str:
        return self._model

    def invoke(self, request: BackendRequest) -> BackendResponse:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for message in request.messages:
            messages.append({key: message[key] for key in (
                "role", "content", "reasoning_content", "tool_calls", "tool_call_id")
                if key in message})
        _validate_tool_history(messages)

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

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens or self._max_tokens,
            "thinking": self._thinking,
            "reasoning_effort": self._reasoning_effort,
        }
        if tools:
            body["tools"] = tools

        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        remaining = remaining_wall_seconds(request.metadata)
        if remaining is not None and remaining <= 2:
            raise BackendError("task wall budget exhausted before model request")
        http_timeout = min(self._timeout, max(1.0, remaining - 1)) if remaining is not None else self._timeout
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.debug("%s API HTTP %d: %s", self.name, e.code, err_body)
            from zmai.gateway.errors import friendly_http_error
            raise BackendError(
                friendly_http_error(e.code, self.name, self._model, self._env_key),
            )
        except urllib.error.URLError as e:
            raise BackendError(f"{self.name} API 网络错误: {e}")
        except json.JSONDecodeError as e:
            raise BackendError(f"{self.name} API 响应解析失败: {e}")

        # 统一响应验证 — 拦截 {} / {"error":...} / 缺字段
        validate_backend_response(
            result, provider="deepseek",
            required_fields=["choices"],
        )
        choices = result["choices"]
        if not choices:
            from zmai.errors import BackendInvalidResponse
            raise BackendInvalidResponse(
                "choices 为空列表", provider="deepseek",
            )

        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls_raw = msg.get("tool_calls")

        tool_calls = None
        if tool_calls_raw:
            tool_calls = []
            for tc in tool_calls_raw:
                try:
                    params = json.loads(tc["function"].get("arguments", "{}"))
                except (json.JSONDecodeError, KeyError):
                    raw = tc["function"].get("arguments", "{}")
                    try:
                        fixed = raw.replace("\n", "\\n").replace("\r", "\\r")
                        params = json.loads(fixed)
                    except Exception:
                        params = {"raw": raw[:200]}
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=tc["function"]["name"],
                    params=params,
                ))

        usage_raw = result.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
        )

        finish = choice.get("finish_reason", "stop")
        stop_reason = "tool_use" if finish == "tool_calls" else finish

        reported_model = result.get("model")
        # Official compatibility alias: the old V4 Flash name routes to Flash.
        equivalent = (body["model"] == "deepseek-flash"
                      and reported_model == "deepseek-v4-flash")
        mismatch = reported_model is not None and reported_model != body["model"] and not equivalent
        identity_status = ("unreported" if reported_model is None else
                           "equivalent" if equivalent else
                           "mismatch" if mismatch else "verified")
        if mismatch:
            logger.warning("DeepSeek model identity mismatch: requested=%s reported=%s",
                           body["model"], reported_model)
        return BackendResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
            metadata={"model": reported_model or self._model,
                      "configured_model": self._model,
                      "requested_model": body["model"],
                      "reported_model": reported_model,
                      "model_identity_mismatch": mismatch,
                      "model_identity_status": identity_status,
                      "thinking": self._thinking,
                      "reasoning_effort": self._reasoning_effort,
                      "reasoning_content": msg.get("reasoning_content"),
                      "tool_calls_raw": tool_calls_raw},
        )

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError("DeepSeekBackend 不支持流式输出")

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.TOOL_USE, BackendCapability.SYSTEM_PROMPT}


# ── Plugin descriptor ─────────────────────────────────────

from zmai.gateway.plugin import BackendPlugin as _BackendPlugin  # noqa: E402

plugin = _BackendPlugin(
    name="deepseek",
    backend_class=DeepSeekBackend,
    label="DeepSeek",
    default_model="deepseek-flash",
    default_base_url="https://api.deepseek.com/v1",
    default_timeout=120,
    default_max_tokens=4096,
    default_temperature=0.7,
    env_api_key="DEEPSEEK_API_KEY",
    env_model="DEEPSEEK_MODEL",
    verify_url="https://api.deepseek.com/v1/models",
    verify_method="GET",
)
