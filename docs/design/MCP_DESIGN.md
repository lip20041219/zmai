# ZMAI MCP Design

> 设计日期: 2026-07-17
> 状态: 设计阶段（MCPClient 已存在，集成未完成）
> 范围: MCP Client、Server 发现、Tool 注册、Resource 管理、插件机制

---

## 一、执行摘要

MCP（Model Context Protocol）为 ZMAI 提供与外部工具服务器的标准化通信能力。

**当前状态**：`MCPClient` 类已实现（`gateway/mcp.py`，204 行），支持 `list_tools`、`call_tool`、`list_resources`、`read_resource` 四个操作。但 Client 与 Runtime **完全未集成**——没有任何代码调用 `MCPClient`。

**设计目标**：MCP 通过 Plugin 机制成为可选能力。用户安装 `zmai-plugin-mcp` 后，ZMAI 自动发现 MCP 服务器并将其工具注入到 Agent 可用工具列表中。

---

## 二、当前实现分析

### 2.1 已实现

```
MCPClient (gateway/mcp.py) ✅ 完整实现
├── __init__(server_url, config)    ✅
├── list_tools() → MCPToolDefinition[] ✅
├── call_tool(name, params) → ToolResult ✅
├── list_resources() → MCPResourceDefinition[] ✅
├── read_resource(uri) → bytes     ✅
├── close()                        ✅
├── _request(method, params)       ✅ HTTP JSON-RPC
└── MCPConnectionError             ✅

MCPToolDefinition (dataclass)      ✅
MCPResourceDefinition (dataclass)  ✅

测试 (test_gateway.py):
├── test_init                      ✅
├── test_init_custom_config        ✅
├── test_init_strips_trailing_slash ✅
├── test_mcp_connection_error      ✅
```

### 2.2 未实现

```
MCP → ToolRegistry 集成       ❌ MCP 工具未注入 Agent
MCP → Runtime 集成            ❌ Runtime 不加载 MCP
MCP 多服务器管理              ❌ 只能手动创建多个 Client
MCP 服务器发现                ❌ 无配置驱动发现
MCP SSE/Streaming 传输        ❌ 只有 HTTP POST
MCP 认证                       ❌ 无认证机制
MCP 心跳/重连                 ❌ 无连接管理
```

---

## 三、架构设计

### 3.1 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Runtime                                │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────┐  │
│  │ ToolRegistry │  │ BackendGateway │  │ MCPServerPool  │  │
│  │              │  │                │  │                │  │
│  │ read_file    │  │ ClaudeBackend  │  │ localhost:8080 │  │
│  │ write_file   │  │ DeepSeekBackend│  │ localhost:9090 │  │
│  │ ...          │  │                │  │                │  │
│  │ mcp:tool_a ◀─┼──┤ ← 自动注入    │  │ MCPClient     │  │
│  │ mcp:tool_b ◀─┼──┤               │  │ MCPClient     │  │
│  └──────────────┘  └────────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
    MCP Server (外部)
    ┌─────────────────┐
    │ filesystem       │
    │ database_query   │
    │ web_search       │
    │ ...              │
    └─────────────────┘
```

### 3.2 核心概念

| 概念 | 说明 |
|------|------|
| **MCPClient** | 单个 MCP 服务器的客户端连接（已实现） |
| **MCPServerPool** | 多服务器管理器（新增） |
| **MCPPlugin** | 通过 Plugin 机制加载 MCP 配置 |
| **mcp: 前缀工具** | MCP 工具注册到 ToolRegistry 时加 `mcp:` 前缀避免名称冲突 |

---

## 四、MCPServerPool

```python
# gateway/mcp_pool.py（新增）

class MCPServerPool:
    """MCP 服务器连接池。管理多个 MCPClient 实例。"""

    def __init__(self) -> None:
        self._servers: dict[str, MCPClient] = {}
        self._mcp_tools: dict[str, MCPToolDefinition] = {}  # flat name → def

    def add_server(self, name: str, url: str, config: dict | None = None) -> None:
        """添加 MCP 服务器并发现其工具。"""
        client = MCPClient(url, config=config)
        tools = client.list_tools()
        for t in tools:
            self._mcp_tools[f"mcp:{t.name}"] = t
        self._servers[name] = client

    def remove_server(self, name: str) -> None:
        """移除 MCP 服务器。"""
        client = self._servers.pop(name, None)
        if client:
            # 移除该服务器注册的所有工具
            keys = [k for k in self._mcp_tools if k.startswith(f"mcp:{name}:")]
            for k in keys:
                del self._mcp_tools[k]
            client.close()

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """将所有 MCP 工具转为 ToolDefinition，注入 ToolRegistry。"""
        return [
            ToolDefinition(
                name=f"mcp:{t.name}",
                description=t.description,
                input_schema=t.input_schema,
            )
            for t in self._mcp_tools.values()
        ]

    def execute_tool(self, name: str, params: dict) -> ToolResult:
        """执行 MCP 工具。name 格式: mcp:<tool_name>。"""
        # 从 mcp: 前缀解析工具名
        tool_name = name[len("mcp:"):] if name.startswith("mcp:") else name
        for server_name, client in self._servers.items():
            # 简单策略：遍历所有服务器查找工具
            try:
                return client.call_tool(tool_name, params)
            except Exception:
                continue
        return ToolResult.err(f"MCP 工具未找到: {tool_name}")
```

---

## 五、MCPPlugin（通过 Plugin 机制集成）

通过 Plugin 系统将 MCP 集成到 Runtime 中：

```python
# zmai_plugin_mcp.py（插件项目）

from zmai.plugin import Plugin, HookPoint
from zmai.gateway.mcp_pool import MCPServerPool


class MCPPlugin(Plugin):
    name = "mcp"
    version = "0.1.0"
    description = "MCP 服务器集成"

    def on_load(self) -> None:
        self._pool = MCPServerPool()

    def register_hooks(self, registry) -> None:
        registry.register(HookPoint.ON_RUNTIME_START, self.on_start, priority=10)
        registry.register(HookPoint.ON_TOOL_EXECUTE, self.on_tool, priority=50)

    def on_start(self, config: dict, **kw) -> None:
        """从配置加载 MCP 服务器列表。"""
        mcp_configs = config.get("mcp", {}).get("servers", [])
        for srv in mcp_configs:
            self._pool.add_server(
                name=srv["name"],
                url=srv["url"],
                config=srv.get("config"),
            )
            logger.info("MCP 服务器已连接: %s (%s)", srv["name"], srv["url"])

        # 将 MCP 工具注入 ToolRegistry
        tools = self._pool.get_tool_definitions()
        self._inject_tools(tools)

    def on_tool(self, tool_name: str, params: dict, **kw) -> dict | None:
        """拦截 mcp: 前缀的工具调用。"""
        if tool_name.startswith("mcp:"):
            result = self._pool.execute_tool(tool_name, params)
            return {"result": result}
        return None  # 非 MCP 工具，放行
```

---

## 六、配置

```json
// zmai.json
{
    "mcp": {
        "servers": [
            {
                "name": "filesystem",
                "url": "http://localhost:8080/mcp",
                "config": {
                    "timeout": 30
                }
            },
            {
                "name": "web_search",
                "url": "http://localhost:9090/mcp",
                "config": {
                    "timeout": 60,
                    "headers": {
                        "Authorization": "Bearer ${MCP_API_KEY}"
                    }
                }
            }
        ]
    }
}
```

Agent 看到的工具列表：
```
read_file             ← 内置工具
write_file            ← 内置工具
mcp:read_file         ← MCP filesystem 工具
mcp:write_file        ← MCP filesystem 工具
mcp:search            ← MCP web_search 工具
```

---

## 七、传输层扩展

当前只实现了 HTTP JSON-RPC。未来可支持：

```python
class MCPClient:
    """基类使用 HTTP POST。子类覆盖 _transport 实现其他协议。"""

    def _transport(self, url: str, body: bytes) -> bytes:
        """可被子类覆盖以实现 SSE、WebSocket 等。"""
        # 默认实现: urllib.request.urlopen
        ...


class SSETransportMixin:
    """Server-Sent Events 传输。"""
    ...


class WebSocketTransportMixin:
    """WebSocket 传输。"""
    ...
```

---

## 八、工具的 mcp: 前缀策略

直接在 ToolRegistry 中注册 MCP 工具可能导致与内置工具**冲突**（例如 MCP 的 `read_file` 和 ZMAI 的 `ReadFileTool`）。

**方案**：MCP 工具注册时自动添加 `mcp:` 前缀：

```
内置工具名: read_file / write_file / shell_exec
MCP 工具名:  mcp:read_file / mcp:write_file / mcp:search
```

Agent 的系统提示中添加说明：
```
## MCP 工具（需 mcp: 前缀）
- mcp:read_file    读取文件（通过 MCP 服务器）
- mcp:search       搜索互联网（通过 MCP 服务器）
```

---

## 九、资源管理

当前 `MCPClient.list_resources()` 和 `read_resource()` 已实现但**无处使用**。

**建议集成方式**：MCP 资源作为 Workspace 的补充：

```python
class MCPResourceManager:
    """MCP 资源管理器。将 MCP 资源映射为 Workspace 中的虚拟文件。"""

    def __init__(self, pool: MCPServerPool) -> None:
        self._pool = pool

    def get_resource(self, uri: str) -> bytes:
        """读取 MCP 资源。"""
        return self._pool._servers[?].read_resource(uri)

    def inject_to_workspace(self, workspace: Workspace, agent_id: str) -> None:
        """将 MCP 资源注入到 Agent workspace（可选）。"""
        ...
```

---

## 十、安全与隔离

| 要点 | 设计 |
|------|------|
| **认证** | 通过 `config.headers` 传入 `Authorization` header，支持 `${ENV_VAR}` 模板 |
| **超时** | 每个服务器可配置独立 timeout |
| **错误隔离** | MCP 调用失败返回 `ToolResult.err()`，不阻塞 Agent |
| **沙箱** | MCP 服务器是外部进程，不会影响 ZMAI Runtime 内部 |

---

## 十一、文件清单

| 文件 | 操作 | 行数 | 内容 |
|------|------|------|------|
| `src/zmai/gateway/mcp.py` | **已有** | 204 | MCPClient 基础实现 |
| `src/zmai/gateway/mcp_pool.py` | **新增** | ~120 | MCPServerPool 多服务器管理 |
| `src/zmai/gateway/__init__.py` | 修改 | +2 | 导出 MCPServerPool |
| `tests/test_mcp.py` | **新增** | ~150 | MCPClient + MCPServerPool 测试 |

**外部（插件项目）**：

| 文件 | 内容 |
|------|------|
| `zmai-plugin-mcp/pyproject.toml` | 声明 `zmai.plugins` entry_point |
| `zmai-plugin-mcp/zmai_plugin_mcp.py` | MCPPlugin 实现 |

---

## 十二、实施路线图

| Phase | 内容 | 依赖 |
|-------|------|------|
| **Phase 1** | MCPClient 实现 | ✅ **已完成** |
| **Phase 2** | MCPServerPool + 配置驱动加载 | MCPClient |
| **Phase 3** | MCPPlugin via Plugin 系统 | Plugin 系统 + MCPServerPool |
| **Phase 4** | Agent 工具注入 + mcp: 前缀 | MCPPlugin |
| **Phase 5** | 资源管理 + 安全增强 | MCPPlugin |
| **Phase 6** | SSE/WebSocket 传输 | MCPClient |
| **Phase 7** | `zmai-plugin-mcp` 独立包 | MCPPlugin |

---

*Design by `claude` — 基于 `gateway/mcp.py` + Gateway 架构 + Plugin 设计*
