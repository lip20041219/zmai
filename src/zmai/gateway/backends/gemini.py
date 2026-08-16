"""Gemini Backend — Google Gemini API 实现。

支持 Gemini invoke 和 stream 调用，支持工具调用、系统提示、多轮对话。
API 文档: https://ai.google.dev/api/generate-content
"""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger("zmai.gateway.backends.gemini")


class GeminiBackend(Backend):
    """Google Gemini API Backend。

    统一配置字段（所有 Backend 共用）:
        api_key:     API Key（默认从 GEMINI_API_KEY 环境变量读取）。
        model:       模型名称（默认 gemini-2.0-flash）。
        base_url:    API 基础 URL（默认 https://generativelanguage.googleapis.com/v1beta）。
        timeout:     请求超时秒数（默认 120）。
        max_tokens:  最大生成 token 数（默认 4096）。
        temperature: 采样温度（默认 0.7）。
    """

    name: str = "gemini"
    provider: str = "gemini"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._env_key: str = "GEMINI_API_KEY"
        # api_key 由 PluginRegistry._build_config() 通过 CredentialResolver 装配
        self._api_key: str = self._config.get("api_key", "")
        if not self._api_key:
            logger.warning("%s 未设置，GeminiBackend 无法正常工作", self._env_key)

        # 统一配置字段（所有 Backend 共用同一套 key 名）
        self._model: str = self._config.get("model", "gemini-2.0-flash")
        self._base_url: str = self._config.get(
            "base_url", "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        self._timeout: int = int(self._config.get("timeout", 120))
        self._max_tokens: int = int(self._config.get("max_tokens", 4096))
        self._temperature: float = float(self._config.get("temperature", 0.7))

        logger.info(
            "GeminiBackend 初始化: model=%s, base_url=%s",
            self._model,
            self._base_url,
        )

    @property
    def model(self) -> str:
        return self._model

    def invoke(self, request: BackendRequest) -> BackendResponse:
        """调用 Gemini API，返回完整响应。"""
        body = self._build_request_body(request)
        url = f"{self._base_url}/models/{self._model}:generateContent?key={self._api_key}"

        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.debug("Gemini API HTTP %d: %s", e.code, err_body)
            from zmai.gateway.errors import friendly_http_error
            raise BackendError(
                friendly_http_error(e.code, "gemini", self._model, self._env_key),
            )
        except urllib.error.URLError as e:
            raise BackendError(f"Gemini API 网络错误: {e}")
        except json.JSONDecodeError as e:
            raise BackendError(f"Gemini API 响应解析失败: {e}")

        # 统一响应验证 — 拦截 {} / {"error":...} / 缺字段
        validate_backend_response(
            result, provider="gemini",
            required_fields=["candidates"],
        )
        if not result.get("candidates"):
            from zmai.errors import BackendInvalidResponse
            raise BackendInvalidResponse(
                "candidates 为空列表", provider="gemini",
            )
        return self._parse_response(result)

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        """流式调用 Gemini API。"""
        body = self._build_request_body(request)
        url = (
            f"{self._base_url}/models/{self._model}:streamGenerateContent"
            f"?key={self._api_key}&alt=sse"
        )

        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
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
                            if event_data.strip() == "[DONE]":
                                yield BackendEvent(type="done", data="", index=index)
                                index += 1
                                return
                            try:
                                parsed = json.loads(event_data)
                                yield from self._parse_stream_event(parsed, index)
                            except json.JSONDecodeError:
                                continue

            yield BackendEvent(type="done", data="", index=index)

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.debug("Gemini API HTTP %d: %s", e.code, err_body)
            from zmai.gateway.errors import friendly_http_error
            raise BackendError(
                friendly_http_error(e.code, "gemini", self._model, self._env_key),
                status_code=e.code,
            )
        except urllib.error.URLError as e:
            raise BackendError(f"Gemini API 网络错误: {e}")
        except Exception as e:
            raise BackendError(f"Gemini API 流式调用失败: {e}")

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
        """构建 Gemini API 请求体。

        Gemini 格式:
        {
          "contents": [{"role": "user", "parts": [{"text": "..."}]}],
          "systemInstruction": {"parts": [{"text": "..."}]},
          "generationConfig": {"maxOutputTokens": ..., "temperature": ...},
          "tools": [{"functionDeclarations": [...]}]
        }
        """
        # 转换消息格式: 标准 messages → Gemini contents
        contents = []
        for msg in request.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            # Gemini 使用 "model" 而不是 "assistant"
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}],
            })

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens or self._max_tokens,
                "temperature": request.temperature if request.temperature is not None else self._temperature,
            },
        }

        if request.system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}]
            }

        if request.stop_sequences:
            body["generationConfig"]["stopSequences"] = request.stop_sequences

        if request.tools:
            body["tools"] = [
                self._tool_defs_to_gemini(t) for t in request.tools
            ]

        return body

    def _parse_response(self, data: dict[str, Any]) -> BackendResponse:
        """解析 Gemini API 响应为 BackendResponse。"""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        candidates = data.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])

            for part in parts:
                if "text" in part:
                    text_parts.append(part["text"])
                if "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(ToolCall(
                        id=fc.get("name", ""),
                        name=fc.get("name", ""),
                        params=fc.get("args", {}),
                    ))

        # Token 用量
        usage_raw = data.get("usageMetadata", {})
        usage = TokenUsage(
            input_tokens=usage_raw.get("promptTokenCount", 0),
            output_tokens=usage_raw.get("candidatesTokenCount", 0),
        )

        # Stop reason mapping
        finish_reason = ""
        if candidates:
            finish_reason = candidates[0].get("finishReason", "").lower()
        stop_reason = self._map_stop_reason(finish_reason)

        return BackendResponse(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            usage=usage,
            stop_reason=stop_reason,
            metadata={
                "model": data.get("modelVersion", self._model),
            },
        )

    def _parse_stream_event(
        self, parsed: dict[str, Any], index: int
    ) -> Iterator[BackendEvent]:
        """解析流式事件块。"""
        candidates = parsed.get("candidates", [])
        if not candidates:
            return

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        for part in parts:
            if "text" in part:
                yield BackendEvent(type="text", data=part["text"], index=index)
                index += 1
            if "functionCall" in part:
                fc = part["functionCall"]
                yield BackendEvent(
                    type="tool_call",
                    data={
                        "id": fc.get("name", ""),
                        "name": fc.get("name", ""),
                        "input": fc.get("args", {}),
                    },
                    index=index,
                )
                index += 1

    @staticmethod
    def _map_stop_reason(finish_reason: str) -> str:
        """映射 Gemini finishReason 到标准 stop_reason。"""
        mapping = {
            "stop": "end_turn",
            "max_tokens": "max_tokens",
            "safety": "end_turn",
            "recitation": "end_turn",
            "other": "end_turn",
        }
        return mapping.get(finish_reason, "end_turn")

    @staticmethod
    def _tool_defs_to_gemini(td: ToolDefinition) -> dict[str, Any]:
        """将 ToolDefinition 转换为 Gemini functionDeclarations 格式。"""
        return {
            "functionDeclarations": [
                {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.input_schema,
                }
            ]
        }


# ── Plugin descriptor ─────────────────────────────────────

from zmai.gateway.plugin import BackendPlugin as _BackendPlugin  # noqa: E402

plugin = _BackendPlugin(
    name="gemini",
    backend_class=GeminiBackend,
    label="Gemini (Google)",
    default_model="gemini-2.0-flash",
    default_base_url="https://generativelanguage.googleapis.com/v1beta",
    default_timeout=120,
    default_max_tokens=4096,
    default_temperature=0.7,
    env_api_key="GEMINI_API_KEY",
    env_model="GEMINI_MODEL",
    verify_url="https://generativelanguage.googleapis.com/v1beta/models",
    verify_method="GET",
)
