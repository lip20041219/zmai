"""ZMAI exception type definitions."""

from __future__ import annotations

from typing import Any


class ZMAIError(Exception):
    """所有 ZMAI 异常的基类。"""

    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "type": type(self).__name__,
        }


class WorkspaceError(ZMAIError):
    """Workspace 操作错误。"""

    code = "WORKSPACE_ERROR"

    def __init__(self, message: str, path: str | None = None) -> None:
        self.path = path
        super().__init__(self.code, message)


class ConfigError(ZMAIError):
    """配置错误。"""

    code = "CONFIG_ERROR"

    def __init__(self, message: str, key: str | None = None) -> None:
        self.key = key
        super().__init__(self.code, message)


class RuntimeError(ZMAIError):
    """Runtime 内部错误。"""

    code = "RUNTIME_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(self.code, message)


class MemoryError(ZMAIError):
    """Memory 操作错误。"""

    code = "MEMORY_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(self.code, message)


class BackendError(ZMAIError):
    """Backend 调用错误。"""

    code = "BACKEND_ERROR"

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(self.code, message)


class BackendInvalidResponse(BackendError):
    """Backend 返回无效/意外响应（空、错误、缺字段）。

    覆盖：
      - API 返回 {} 空对象
      - API 返回 {"error": ...}
      - API 返回 {"choices": []} 或缺少必要字段
      - API 返回的结构无法解析为 BackendResponse

    触发场景：
      choice = result.get("choices", [{}])[0]  ← IndexError 的根源
    """

    code = "BACKEND_INVALID_RESPONSE"

    def __init__(self, message: str, provider: str = "", raw: str = "") -> None:
        self.provider = provider
        self.raw = raw[:500]
        prefix = f"[{provider}] " if provider else ""
        ZMAIError.__init__(self, self.code, f"{prefix}{message}")


class PluginError(ZMAIError):
    """Plugin 加载/执行错误。"""

    code = "PLUGIN_ERROR"

    def __init__(self, message: str, plugin_name: str | None = None) -> None:
        self.plugin_name = plugin_name
        super().__init__(self.code, message)


class ToolError(ZMAIError):
    """工具执行错误。"""

    code = "TOOL_ERROR"

    def __init__(self, message: str, tool_name: str | None = None) -> None:
        self.tool_name = tool_name
        super().__init__(self.code, message)


class AgentError(ZMAIError):
    """Agent 生命周期错误。"""

    code = "AGENT_ERROR"

    def __init__(self, message: str, agent_id: str | None = None) -> None:
        self.agent_id = agent_id
        super().__init__(self.code, message)


class CredentialError(ZMAIError):
    """凭据文件操作错误（加密/解密/格式）。"""

    code = "CREDENTIAL_ERROR"

    def __init__(self, message: str, reason: str = "") -> None:
        self.reason = reason
        super().__init__(self.code, message)


__all__ = [
    "ZMAIError",
    "WorkspaceError",
    "ConfigError",
    "RuntimeError",
    "MemoryError",
    "BackendError",
    "BackendInvalidResponse",
    "PluginError",
    "ToolError",
    "AgentError",
    "CredentialError",
]
