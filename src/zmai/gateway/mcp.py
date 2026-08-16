"""MCP (Model Context Protocol) 客户端。

提供与 MCP 服务器通信的基础能力：工具发现、工具调用、资源读写。
MCP 支持作为插件机制，不在核心 Runtime 中强制依赖。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from zmai.tool import ToolResult

logger = logging.getLogger("zmai.gateway.mcp")


@dataclass
class MCPToolDefinition:
    """MCP 服务器暴露的工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResourceDefinition:
    """MCP 服务器暴露的资源定义。"""

    uri: str
    name: str
    description: str = ""
    mime_type: str = "application/octet-stream"


class MCPClient:
    """MCP 协议客户端。

    与 MCP 服务器通信，提供工具调用和资源访问能力。
    默认使用 HTTP SSE 传输方式（可通过子类覆盖 _transport）。

    使用方式:
        client = MCPClient("http://localhost:8080/mcp")
        result = client.call_tool("read_file", {"path": "/tmp/test.txt"})
        client.close()
    """

    def __init__(
        self,
        server_url: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._config: dict[str, Any] = config or {}
        self._timeout: int = self._config.get("timeout", 30)
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            **self._config.get("headers", {}),
        }
        self._session_id: str | None = None
        logger.info("MCPClient 初始化: server=%s", server_url)

    def list_tools(self) -> list[MCPToolDefinition]:
        """获取 MCP 服务器提供的工具列表。

        Returns:
            工具定义列表。
        """
        raw = self._request("tools/list", {})
        tools_raw = raw if isinstance(raw, list) else raw.get("tools", [])
        return [
            MCPToolDefinition(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools_raw
        ]

    def call_tool(self, name: str, params: dict[str, Any] | None = None) -> ToolResult:
        """调用 MCP 服务器上的工具。

        Args:
            name: 工具名称。
            params: 工具参数。

        Returns:
            工具执行结果。
        """
        raw = self._request("tools/call", {"name": name, "arguments": params or {}})
        if isinstance(raw, dict) and "content" in raw:
            content = raw["content"]
            is_error = raw.get("isError", False)
            if isinstance(content, list):
                text_parts = [
                    c.get("text", "") for c in content if c.get("type") == "text"
                ]
                output = "\n".join(text_parts)
            else:
                output = str(content)
            if is_error:
                return ToolResult.err(output)
            return ToolResult.ok(output)
        return ToolResult.ok(str(raw))

    def list_resources(self) -> list[MCPResourceDefinition]:
        """获取 MCP 服务器提供的资源列表。

        Returns:
            资源定义列表。
        """
        raw = self._request("resources/list", {})
        resources_raw = raw if isinstance(raw, list) else raw.get("resources", [])
        return [
            MCPResourceDefinition(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", "application/octet-stream"),
            )
            for r in resources_raw
        ]

    def read_resource(self, uri: str) -> bytes:
        """读取 MCP 服务器上的资源。

        Args:
            uri: 资源 URI。

        Returns:
            资源内容（二进制）。
        """
        raw = self._request("resources/read", {"uri": uri})
        if isinstance(raw, dict):
            contents = raw.get("contents", [])
            for c in contents:
                if c.get("uri") == uri:
                    text = c.get("text", "")
                    blob = c.get("blob", "")
                    if blob:
                        import base64
                        return base64.b64decode(blob)
                    return text.encode("utf-8")
        return str(raw).encode("utf-8")

    def close(self) -> None:
        """关闭 MCP 客户端连接。"""
        logger.info("MCPClient 已关闭: server=%s", self._server_url)

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        """向 MCP 服务器发送请求。

        基类使用 HTTP POST 请求。子类可覆盖实现不同传输方式（如 SSE、WebSocket）。

        Args:
            method: MCP 方法名。
            params: 请求参数。

        Returns:
            响应数据。

        Raises:
            MCPConnectionError: 连接失败时抛出。
        """
        import urllib.error
        import urllib.request

        url = f"{self._server_url}/{method.replace('/', '.')}"
        body = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers=self._headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "error" in data and data["error"]:
                    err = data["error"]
                    raise MCPConnectionError(
                        f"MCP 服务器返回错误 [{err.get('code', -1)}]: {err.get('message', '')}"
                    )
                return data.get("result", {})
        except urllib.error.URLError as e:
            raise MCPConnectionError(f"MCP 连接失败: {e}") from e
        except json.JSONDecodeError as e:
            raise MCPConnectionError(f"MCP 响应解析失败: {e}") from e


class MCPConnectionError(Exception):
    """MCP 连接异常。"""
    pass
