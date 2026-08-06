"""Claude Backend — Anthropic Claude API 默认实现。

支持 Claude API 的 invoke 和 stream 调用，支持工具调用、系统提示、多轮对话。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

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
from zmai.tool import ToolCall, ToolDefinition

logger = logging.getLogger("zmai.gateway.backends.claude")

CLAUDE_API_VERSION = "2023-06-01"


class ClaudeBackend(Backend):
    """Anthropic Claude API Backend。

    统一配置字段（所有 Backend 共用）:
        api_key:     API Key（默认从 ANTHROPIC_API_KEY 环境变量读取）。
        model:       模型名称（默认 claude-sonnet-4-6）。
        base_url:    API 基础 URL（默认 https://api.anthropic.com/v1）。
        timeout:     请求超时秒数（默认 300）。
        max_tokens:  最大生成 token 数（默认 4096）。
        temperature: 采样温度（默认 0.7）。
        max_retries: 最大重试次数（默认 3，Claude 特有）。
    """

    name: str = "claude"
    provider: str = "anthropic"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._env_key: str = "ANTHROPIC_API_KEY"
        # api_key 由 PluginRegistry._build_config() 通过 CredentialResolver 装配
        self._api_key: str = self._config.get("api_key", "")
        if not self._api_key:
            logger.warning("%s 未设置，ClaudeBackend 无法正常工作", self._env_key)

        # 统一配置字段（所有 Backend 共用同一套 key 名）
        self._model: str = self._config.get("model", "claude-sonnet-4-6")
        self._base_url: str = self._config.get("base_url", "https://api.anthropic.com/v1").rstrip("/")
        self._timeout: int = int(self._config.get("timeout", 300))
        self._max_tokens: int = int(self._config.get("max_tokens", 4096))
        self._temperature: float = float(self._config.get("temperature", 0.7))

        # Backend 特有字段
        self._max_retries: int = int(self._config.get("max_retries", 3))

        logger.info(
            "ClaudeBackend 初始化: model=%s, base_url=%s",
            self._model,
            self._base_url,
        )

    @property
    def model(self) -> str:
        return self._model

    def invoke(self, request: BackendRequest) -> BackendResponse:
        """调用 Claude API，返回完整响应。

        Args:
            request: Backend 调用请求。

        Returns:
            Claude API 响应。

        Raises:
            BackendError: API 调用失败时抛出。
        """
        body = self._build_request_body(request)
        url = f"{self._base_url}/messages"

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                data = self._post(url, body)
                # 统一响应验证 — 拦截 {} / {"error":...} / 缺字段
                validate_backend_response(
                    data, provider="claude",
                    required_fields=["content"],
                )
                return self._parse_response(data)
            except BackendError:
                raise
            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Claude API 调用失败 (attempt %d/%d): %s, 重试等待 %ds",
                        attempt + 1, self._max_retries, e, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "Claude API 调用最终失败 (attempt %d/%d): %s",
                        attempt + 1, self._max_retries, e,
                    )

        raise BackendError(f"{self.name} API 调用失败: {last_error}")

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        """流式调用，逐个产生事件。"""
        body = self._build_request_body(request)
        body["stream"] = True
        url = f"{self._base_url}/messages"

        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self._api_key,
                    "anthropic-version": CLAUDE_API_VERSION,
                },
                method="POST",
            )

            index = 0
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            event_data = line[6:]
                            if event_data == "[DONE]":
                                yield BackendEvent(type="done", data="", index=index)
                                index += 1
                                return
                            try:
                                parsed = json.loads(event_data)
                                event_type = parsed.get("type", "")
                                if event_type == "content_block_delta":
                                    delta = parsed.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        yield BackendEvent(
                                            type="text",
                                            data=delta.get("text", ""),
                                            index=index,
                                        )
                                        index += 1
                                elif event_type == "content_block_start":
                                    block = parsed.get("content_block", {})
                                    if block.get("type") == "tool_use":
                                        yield BackendEvent(
                                            type="tool_call",
                                            data={
                                                "id": block.get("id", ""),
                                                "name": block.get("name", ""),
                                                "input": block.get("input", {}),
                                            },
                                            index=index,
                                        )
                                        index += 1
                                elif event_type == "message_delta":
                                    usage = parsed.get("usage", {})
                                    if usage:
                                        yield BackendEvent(
                                            type="usage",
                                            data=usage,
                                            index=index,
                                        )
                                        index += 1
                                elif event_type == "error":
                                    yield BackendEvent(
                                        type="error",
                                        data=parsed.get("error", {}),
                                        index=index,
                                    )
                                    index += 1
                            except json.JSONDecodeError:
                                continue

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.debug("%s API HTTP %d: %s", self.name, e.code, error_body)
            from zmai.gateway.errors import friendly_http_error
            raise BackendError(
                friendly_http_error(e.code, self.name, self._model, self._env_key),
            )
        except urllib.error.URLError as e:
            raise BackendError(f"{self.name} API 网络错误: {e}")
        except Exception as e:
            raise BackendError(f"{self.name} API 流式调用失败: {e}")

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {
            BackendCapability.STREAMING,
            BackendCapability.TOOL_USE,
            BackendCapability.SYSTEM_PROMPT,
            BackendCapability.MULTI_TURN,
        }

    # ── 内部方法 ──────────────────────────────────────────

    def _build_request_body(self, request: BackendRequest) -> dict[str, Any]:
        """构建 Claude API 请求体。"""
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens or CLAUDE_DEFAULT_MAX_TOKENS,
            "messages": request.messages,
        }

        if request.system_prompt:
            body["system"] = request.system_prompt

        if request.temperature is not None:
            body["temperature"] = request.temperature

        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        if request.tools:
            body["tools"] = [
                self._tool_def_to_claude(t) for t in request.tools
            ]

        return body

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """发送 HTTP POST 请求到 Claude API。"""
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": CLAUDE_API_VERSION,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.debug("%s API HTTP %d: %s", self.name, e.code, error_body)
            from zmai.gateway.errors import friendly_http_error
            raise BackendError(
                friendly_http_error(e.code, self.name, self._model, self._env_key),
                status_code=e.code,
            )
        except urllib.error.URLError as e:
            raise BackendError(f"{self.name} API 网络错误: {e}")
        except json.JSONDecodeError as e:
            raise BackendError(f"{self.name} API 响应解析失败: {e}")

    def _parse_response(self, data: dict[str, Any]) -> BackendResponse:
        """解析 Claude API 响应为 BackendResponse。"""
        content = data.get("content", [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in content:
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    params=block.get("input", {}),
                ))

        usage_raw = data.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
            cache_read_tokens=usage_raw.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage_raw.get("cache_creation_input_tokens", 0),
        )

        stop_reason = data.get("stop_reason", "end_turn")
        # Claude stop_reason mapping: "end_turn" | "max_tokens" | "stop_sequence" | "tool_use"
        if stop_reason == "tool_use":
            stop_reason = "tool_use"
        elif stop_reason == "max_tokens":
            stop_reason = "max_tokens"
        elif stop_reason == "end_turn":
            stop_reason = "end_turn"
        elif stop_reason == "stop_sequence":
            stop_reason = "stop_sequence"

        return BackendResponse(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            usage=usage,
            stop_reason=stop_reason,
            metadata={
                "model": data.get("model", self._model),
                "id": data.get("id", ""),
            },
        )

    @staticmethod
    def _tool_def_to_claude(td: ToolDefinition) -> dict[str, Any]:
        """将 ToolDefinition 转换为 Claude API 工具格式。"""
        return {
            "name": td.name,
            "description": td.description,
            "input_schema": td.input_schema,
        }


# ── Plugin descriptor ─────────────────────────────────────
# 供 PluginRegistry 自动发现

from zmai.gateway.plugin import BackendPlugin as _BackendPlugin

plugin = _BackendPlugin(
    name="claude",
    backend_class=ClaudeBackend,
    label="Claude (Anthropic)",
    default_model="claude-sonnet-4-6",
    default_base_url="https://api.anthropic.com/v1",
    default_timeout=300,
    default_max_tokens=4096,
    default_temperature=0.7,
    env_api_key="ANTHROPIC_API_KEY",
    env_model="ANTHROPIC_MODEL",
    verify_url="https://api.anthropic.com/v1/messages",
    verify_method="POST",
    verify_headers={"anthropic-version": "2023-06-01"},
)
