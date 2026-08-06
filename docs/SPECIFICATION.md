# ZMAI Specification

Version: 1.0

> 本文档为 ZMAI 所有模块的软件规格说明（Software Specification）。
>
> 遵循 Phase 1 [ARCHITECTURE.md](ARCHITECTURE.md) 定义的三层架构。
>
> **读者:** 实现者、测试者、Reviewer。
> **前置阅读:** ARCHITECTURE.md, MODULES.md

---

## 目录

1. [Module: errors](#1-module-errors)
2. [Module: config](#2-module-config)
3. [Module: tool](#3-module-tool)
4. [Module: memory](#4-module-memory)
5. [Module: plugin](#5-module-plugin)
6. [Module: gateway](#6-module-gateway)
7. [Module: workspace](#7-module-workspace)
8. [Module: agent](#8-module-agent)
9. [Module: workflow](#9-module-workflow)
10. [Module: runtime](#10-module-runtime)
11. [Module: cli](#11-module-cli)

---

## 1. Module: errors

### 职责

提供 ZMAI 全局统一的异常类型体系。所有模块抛出的异常必须继承自 `ZMAIError`。

### 输入

无。异常由各模块在检测到错误条件时构造抛出。

### 输出

预定义的异常类层级。每个异常包含 `code`（机器可读字符串）和 `message`（人类可读字符串）。

### 异常层级

```
ZMAIError
├── RuntimeError      (code: "RUNTIME_ERROR")
├── BackendError      (code: "BACKEND_ERROR")
├── MemoryError       (code: "MEMORY_ERROR")
├── PluginError       (code: "PLUGIN_ERROR")
├── ConfigError       (code: "CONFIG_ERROR")
├── WorkspaceError    (code: "WORKSPACE_ERROR")
├── ToolError         (code: "TOOL_ERROR")
└── AgentError        (code: "AGENT_ERROR")
```

所有异常必须：
- 继承自 `ZMAIError`
- 接受 `code: str` 和 `message: str` 作为构造参数
- 实现 `__str__` 返回 `f"[{self.code}] {self.message}"`
- 可以被 `json.dumps` 序列化（通过 `to_dict()` 方法）

### 生命周期

模块本身无生命周期。异常在被抛出时创建，在 `except` 块中被捕获处理。

### 依赖

无。仅使用 Python 标准库。

### 配置

无。

### 异常（元异常）

本模块定义的异常自身不应抛出异常。构造函数必须保持简单（不执行 I/O，不调用外部逻辑）。

### 测试要求

- 每个异常类可被独立构造
- 继承关系正确（`isinstance` 检查）
- `code` 字段值与类名对应
- `to_dict()` 正确序列化
- 字符串表示格式符合 `f"[{code}] {message}"`
- 异常可以被 `except ZMAIError` 统一捕获

---

## 2. Module: config

### 职责

提供统一的配置管理入口。支持从 JSON 文件、环境变量、CLI 参数三个来源加载配置，按优先级合并。

### 输入

| 来源 | 格式 | 示例 |
|---|---|---|
| JSON 文件 | `{ "runtime": { "max_iterations": 100 } }` | `config.json` |
| 环境变量 | `ZMAI_RUNTIME_MAX_ITERATIONS=100` | 前缀 `ZMAI_` |
| CLI 参数 | `--runtime.max_iterations=100` | 点号分隔路径 |

### 输出

`Config` 实例，提供 `get(key, default)` / `set(key, value)` / `has(key)` / `export()` 接口。

### 配置优先级（从高到低）

1. CLI 参数
2. 环境变量
3. JSON 配置文件
4. 硬编码默认值

### 配置键命名规则

- 使用点号分隔层级：`runtime.max_iterations`
- 全部小写
- 单词间下划线连接

### 生命周期

```
Config.__init__(sources)
    │
    ├── FileSource.load()    # 加载 JSON 文件
    ├── EnvSource.load()     # 加载环境变量
    └── CLISource.load()     # 加载 CLI 参数
    │
    └── merge(all_sources)   # 按优先级合并
         │
         ▼
      Config 实例可用
```

### 依赖

- `zmai.errors` (`ConfigError`)

### 配置

本模块的配置（Config 自身的配置）：
- `config.file` — 配置文件的路径，默认 `./zmai.json`

### 异常

| 异常 | 触发条件 |
|---|---|
| `ConfigError` | 配置文件不存在 |
| `ConfigError` | 配置文件格式错误（非 JSON） |
| `ConfigError` | 配置键路径冲突 |
| `ConfigError` | 环境变量前缀长度为 0 |

### 测试要求

- 空配置返回默认值
- JSON 文件加载正确
- 环境变量正确映射（`ZMAI_RUNTIME_TIMEOUT=30` → `get("runtime.timeout") == 30`）
- CLI 参数正确解析
- 高优先级覆盖低优先级
- 不存在的键返回 `default`
- 配置文件不存在时使用默认配置而非抛出异常
- 配置文件格式错误时抛出 `ConfigError`

---

## 3. Module: tool

### 职责

提供 Tool 抽象基类和注册表。Tool 是 Agent 与外部世界交互的唯一方式。

### 输入

`ToolContext` 包含：
- `agent_id: str` — 调用方 Agent ID
- `workspace_path: Path` — Agent 工作目录
- `config: dict` — Tool 自定义配置
- `timeout: int` — 执行超时（秒）
- `env: dict` — 环境变量（可选）

`params: dict` — 工具参数，必须符合 JSON Schema 定义。

### 输出

`ToolResult` 包含：
- `success: bool` — 执行是否成功
- `output: str` — 执行输出
- `error: str | None` — 错误信息（`success=False` 时必须提供）
- `metadata: dict` — 额外元数据（执行时间、文件路径等）

### Tool 定义契约

每个 Tool 必须声明：
- `name: str` — 工具名称，唯一标识
- `description: str` — 工具描述，供 LLM 理解用途
- `parameters: JSONSchema` — 参数 Schema，供 LLM 生成参数

### 生命周期

```
注册阶段:
  tool = MyTool()
  ToolRegistry.register(tool)

执行阶段:
  context = ToolContext(agent_id="...", ...)
  result = tool.execute(context, params)

注销阶段:
  ToolRegistry.remove("my_tool")
```

### 依赖

- `zmai.errors` (`ToolError`)

### 配置

无模块级配置。Tool 的自定义配置通过 `ToolContext.config` 传递。

### 异常

| 异常 | 触发条件 |
|---|---|
| `ToolError` | 工具执行超时（超过 `context.timeout`） |
| `ToolError` | 工具参数校验失败 |
| `ToolError` | 工具执行时内部错误 |
| `ToolError` | 执行被外部取消 |

### 测试要求

- Tool ABC 不可直接实例化
- 实现 Tool 子类后所有抽象方法可调用
- 参数校验正确拒绝非法参数
- `ToolResult` 在成功/失败时字段正确
- 注册表可注册、获取、列出、移除
- 注册表获取不存在的 Tool 抛出明确错误
- 注册表防止重复注册同名 Tool
- Tool 执行超时机制可触发

---

## 4. Module: memory

### 职责

Agent 记忆系统。提供 Working Memory（当前会话，内存存储）和 Long-term Memory（跨会话，文件持久化）两种实现。统一通过 `MemoryManager` 访问。

### 输入

| 操作 | 输入 |
|---|---|
| `store(key, value, namespace)` | `key: str`, `value: Any(JSON-serializable)`, `namespace: str` |
| `read(key, namespace)` | `key: str`, `namespace: str` |
| `update(key, value, namespace)` | `key: str`, `value: Any`, `namespace: str` |
| `search(query, namespace)` | `query: str`, `namespace: str` |
| `delete(key, namespace)` | `key: str`, `namespace: str` |

### 输出

| 操作 | 输出 |
|---|---|
| `store` | `None` |
| `read` | `Any | None`（不存在时返回 `None`） |
| `update` | `None` |
| `search` | `list[MemoryEntry]` |
| `delete` | `None` |

### MemoryEntry 结构

```
{
    "key": str,
    "value": Any,
    "namespace": str,
    "created_at": ISO8601,
    "updated_at": ISO8601,
    "ttl": int | None      // seconds, None = 永不过期
}
```

### 生命周期

```
初始化:
  MemoryManager(config)
    ├── working_memory = WorkingMemory()      # 内存存储
    └── long_term_memory = LongTermMemory()   # 文件存储

运行时:
  manager.working(agent_id).store("key", value)
  manager.long_term(agent_id).read("key")

持久化:
  manager.persist(agent_id)    # Working → Long-term 同步
  manager.restore(agent_id)    # Long-term → Working 重建

清理:
  manager.cleanup(agent_id)    # 删除 Agent 的所有 Memory
```

### Working Memory 规则

- 纯内存存储，不持久化
- Agent 生命周期结束后自动销毁
- 支持 TTL（过期自动清理）
- 适合存储当前上下文、中间结果

### Long-term Memory 规则

- 文件系统存储（JSON Lines 格式）
- 每个 Agent 独立的存储文件：`<memory_root>/<namespace>/<agent_id>.jsonl`
- 支持增量追加写
- 支持全量重建（load all → filter → rewrite）
- 适合存储跨会话的知识、偏好、历史

### 依赖

- `zmai.errors` (`MemoryError`)
- 标准库: `json`, `pathlib`, `time`, `threading`

### 配置

```json
{
    "memory": {
        "working": {
            "max_size": 1000,          // 最大条目数
            "cleanup_interval": 300    // TTL 清理间隔（秒）
        },
        "long_term": {
            "root_dir": "./zmai_memory", // 存储根目录
            "max_file_size": 10485760   // 单文件最大字节（10MB）
        }
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `MemoryError` | 写入值不可 JSON 序列化 |
| `MemoryError` | Long-term 存储 I/O 错误 |
| `MemoryError` | Working Memory 超出 `max_size` |
| `MemoryError` | `namespace` 包含非法字符（路径遍历） |

### 测试要求

- Working Memory: store → read 返回正确值
- Working Memory: 读取不存在的 key 返回 `None`
- Working Memory: TTL 过期后返回 `None`
- Working Memory: `clear` 清空指定 namespace
- Long-term Memory: store → read 正确（跨实例重建后仍正确）
- Long-term Memory: 搜索支持前缀/关键词匹配
- Long-term Memory: 大文件读写不崩溃
- MemoryManager: `persist` 后 `restore` 数据一致
- 非法 namespace 拒绝操作
- 并发读写不出现数据竞争

---

## 5. Module: plugin

### 职责

提供 Plugin 系统的抽象基类、生命周期管理和 Hook 机制。

### 输入

| 操作 | 输入 |
|---|---|
| `load(name)` | Plugin 名称 |
| `enable(name)` | Plugin 名称 |
| `trigger(point, **context)` | Hook 点 + 上下文数据 |

### 输出

| 操作 | 输出 |
|---|---|
| `discover()` | `list[PluginMetadata]` |
| `load()` | `Plugin` 实例 |
| `enable()` | `None` |
| `trigger()` | `list[HookResult]` |

### Hook 点定义

| Hook 点 | 触发时机 | context 内容 | 预期返回值 |
|---|---|---|---|
| `ON_RUNTIME_START` | Runtime 启动时 | `{config}` | `None` |
| `ON_RUNTIME_STOP` | Runtime 停止时 | `{exit_code}` | `None` |
| `ON_AGENT_INIT` | Agent 初始化时 | `{agent_id, config}` | `dict | None`（修改配置） |
| `ON_AGENT_STEP` | Agent 每步执行后 | `{agent_id, step, action, result}` | `dict | None`（修改下一步） |
| `ON_AGENT_COMPLETE` | Agent 完成时 | `{agent_id, result}` | `None` |
| `ON_AGENT_ERROR` | Agent 出错时 | `{agent_id, error}` | `None` |
| `ON_MEMORY_READ` | Memory 读取前 | `{agent_id, key, namespace}` | `Any | None`（覆盖返回值） |
| `ON_MEMORY_WRITE` | Memory 写入前 | `{agent_id, key, value, namespace}` | `dict | None`（修改值） |
| `ON_TOOL_EXECUTE` | Tool 执行前 | `{agent_id, tool_name, params}` | `dict | None`（修改参数或阻止） |
| `ON_TOOL_RESULT` | Tool 执行后 | `{agent_id, tool_name, result}` | `ToolResult | None`（修改结果） |

### 生命周期

```
PluginManager 初始化
    │
    ├── discover()          # 扫描 entry_points, 发现插件
    ├── load(name)          # 加载插件代码 → PLUGIN_LOADED
    ├── enable(name)        # 启用插件 → PLUGIN_ENABLED
    │
    运行时:
    │   trigger(point, context)  # 触发 Hook
    │
    ├── disable(name)       # 禁用插件 → PLUGIN_DISABLED
    └── unload(name)        # 卸载插件 → PLUGIN_UNLOADED

状态: DISCOVERED → LOADED → ENABLED ↔ DISABLED → UNLOADED
                                         ↓
                                       FAILED
```

### Plugin 隔离规则

1. Plugin 的异常不得传播到调用方；`trigger()` 应捕获并包装为 `HookResult(error=...)`
2. Plugin 不得阻塞 Runtime 主循环（单 Plugin 执行超过 5 秒应被警告）
3. Plugin 不得直接修改 Runtime 内部状态，只能通过 Hook 返回值影响行为
4. 同一 Hook 点支持多 Plugin 注册，按 `priority` 顺序执行

### 依赖

- `zmai.errors` (`PluginError`)
- 标准库: `importlib.metadata` (entry_points)

### 配置

```json
{
    "plugin": {
        "enabled": ["plugin_a", "plugin_b"],
        "disabled": [],
        "paths": []  // 额外插件搜索路径
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `PluginError` | 插件入口点不存在 |
| `PluginError` | 插件加载失败（import error） |
| `PluginError` | 插件执行 Hook 超时 |
| `PluginError` | 重复加载同名插件 |
| `PluginError` | 禁用时触发卸载未加载的插件 |

### 测试要求

- Plugin ABC 不可直接实例化
- 实现子类后生命周期方法可调用
- `discover` 可扫描 entry_points
- `load/unload` 正确触发 `on_load`/`on_unload`
- `enable/disable` 正确切换状态
- `trigger` 正确调用已启用插件的 Hook
- `trigger` 不调用已禁用插件的 Hook
- Plugin Hook 异常不传播到 Runtime（被包装为 HookResult）
- 重复加载抛出 `PluginError`

---

## 6. Module: gateway

### 职责

Backend 抽象层。提供模型无关的调用接口、Backend 注册发现、工具调用路由。

### 输入

`BackendRequest`：
- `messages: list[Message]` — 消息列表（role, content）
- `tools: list[ToolDefinition] | None` — 可用工具定义
- `max_tokens: int` — 最大生成 token 数
- `temperature: float` — 采样温度
- `stop_sequences: list[str] | None` — 停止序列

### 输出

`BackendResponse`：
- `content: str` — 模型生成的文本
- `tool_calls: list[ToolCall] | None` — 模型请求的工具调用
- `usage: TokenUsage | None` — Token 用量（input_tokens, output_tokens）
- `stop_reason: str` — 停止原因（"end_turn", "max_tokens", "stop_sequence", "tool_use"）
- `metadata: dict` — Backend 特定元数据

### Backend 接口

```python
class Backend(ABC):
    name: str                           # Backend 唯一名称

    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse:
        """同步调用模型，返回完整响应。"""
        ...

    @abstractmethod
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        """流式调用模型，逐个事件返回。"""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]:
        """声明 Backend 支持的能力集。"""
        ...
```

### BackendCapability

```python
class BackendCapability(Enum):
    STREAMING = "streaming"              # 支持流式输出
    TOOL_USE = "tool_use"                # 支持工具调用
    SYSTEM_PROMPT = "system_prompt"      # 支持 System Prompt
    MULTI_TURN = "multi_turn"           # 支持多轮对话
    VISION = "vision"                    # 支持图片输入
    STRUCTURED_OUTPUT = "structured_output"  # 支持结构化输出
```

### ToolRouter 规则

1. Agent 执行过程中 Backend 请求工具调用 → Runtime 解析为 `ToolCall`
2. Runtime 将 `ToolCall` 交给 `ToolRouter.execute(name, params, context)`
3. `ToolRouter` 查找已注册的 Tool 并执行
4. 执行结果格式化为 `ToolResult` 并注入回下一轮 Backend 调用
5. Tool 执行失败不终止流程，将错误信息返回 Backend 处理

### 生命周期

```
启动时:
  BackendRegistry.register("claude", ClaudeBackend)
  ToolRouter.register(read_tool)
  ToolRouter.register(write_tool)

运行时:
  backend = registry.get("claude")        # 获取 Backend 实例
  response = backend.invoke(request)       # 调用模型
  if response.tool_calls:
      for tc in response.tool_calls:
          result = tool_router.execute(tc.name, tc.params)
          # 将 result 合并到下一轮 request

扩展时:
  registry.register("openai", OpenAIBackend)  # 新增 Backend
```

### Claude Backend 约束（默认实现）

- 默认使用 Claude API
- 必须从配置读取 API Key（`gateway.backends.claude.api_key`）
- 支持 `STREAMING`, `TOOL_USE`, `SYSTEM_PROMPT`, `MULTI_TURN`
- 环境变量 `ANTHROPIC_API_KEY` 作为 fallback
- API 版本、模型名称可配置

### 依赖

- `zmai.errors` (`BackendError`)
- `zmai.tool` (`Tool`, `ToolRegistry`, `ToolContext`, `ToolResult`)
- 标准库: `json`, `abc`, `dataclasses`
- 第三方: `httpx` (HTTP 客户端，可选)

### 配置

```json
{
    "gateway": {
        "default_backend": "claude",
        "timeout": 300,
        "max_retries": 3,
        "backends": {
            "claude": {
                "api_key": "${ANTHROPIC_API_KEY}",
                "model": "claude-sonnet-4-6",
                "max_tokens": 4096,
                "temperature": 0.7
            }
        }
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `BackendError` | Backend API 调用失败（网络错误） |
| `BackendError` | Backend 返回错误状态码 |
| `BackendError` | 请求超时（超过 `timeout`） |
| `BackendError` | 达到 `max_retries` 仍失败 |
| `BackendError` | 请求的 Backend 不存在 |
| `BackendError` | 请求的 Tool 未注册 |

### 测试要求

- Backend ABC 不可直接实例化
- BackendRegistry 可注册、获取、列出、移除
- 注册已存在的 Backend 覆盖旧实例（log warning）
- ToolRouter 正确执行已注册 Tool
- ToolRouter 执行未注册 Tool 抛出 `BackendError`
- 模拟 Claude API 调用（mock HTTP）验证 invoke/stream
- 流式调用正确逐个产生事件
- 超时机制可触发
- 重试机制在临时错误时自动重试
- 不同 Backend 之间完全隔离

---

## 7. Module: workspace

### 职责

为 Agent 提供隔离的文件系统沙箱。管理工作目录的创建、文件读写、临时文件清理。

### 输入

| 操作 | 输入 |
|---|---|
| `prepare(agent_id)` | `agent_id: str` |
| `read(agent_id, path)` | `path: str`（相对于 workspace root） |
| `write(agent_id, path, data)` | `path: str`, `data: bytes` |
| `list(agent_id, pattern)` | `pattern: str` (glob) |
| `cleanup(agent_id)` | `agent_id: str` |

### 输出

| 操作 | 输出 |
|---|---|
| `prepare` | `Path` — Agent 工作目录的绝对路径 |
| `read` | `bytes` — 文件内容 |
| `write` | `Path` — 写入文件的绝对路径 |
| `list` | `list[Path]` — 匹配的文件列表 |
| `exists` | `bool` |
| `cleanup` | `None` |

### 目录结构

```
<workspace_root>/
└── <agent_id>/
    ├── input/          # Agent 输入文件（只读）
    ├── output/         # Agent 输出产物（持久化保留）
    ├── temp/           # 临时文件（cleanup 时删除）
    └── .state/         # Workspace 内部状态（非 Agent 可见）
```

### 安全规则

1. **路径穿越防护** — 任何涉及路径的输入必须校验，禁止 `../` 逃逸到 workspace 之外
2. **文件大小限制** — 单文件写入超过 `max_file_size` 时拒绝操作
3. **文件数量限制** — 单 Agent 文件数超过 `max_files` 时拒绝创建新文件
4. **二进制安全** — 所有文件以二进制形式读写，不做编码假设
5. **磁盘空间检查** — 写入前检查剩余空间，低于 `min_disk_space` 时拒绝写入

### 生命周期

```
创建 Agent → Workspace.prepare(agent_id)
    │
    ├── 创建 <workspace_root>/<agent_id>/ 目录
    ├── 创建 input/, output/, temp/, .state/ 子目录
    └── 返回工作目录路径
    │
运行时 → Workspace.read/write/list 操作
    │
Agent 完成/失败/取消 → Workspace.cleanup(agent_id)
    │
    ├── 删除 temp/ 目录内容
    ├── 保留 output/ 和 .state/（可配置清理）
    └── 可选：保留 input/（供后续审查）
```

### 依赖

- `zmai.errors` (`WorkspaceError`)
- `zmai.config` (读取 workspace 配置)
- 标准库: `pathlib`, `shutil`, `tempfile`

### 配置

```json
{
    "workspace": {
        "root": "./workspace",
        "max_file_size": 10485760,
        "max_files": 1000,
        "min_disk_space": 104857600,
        "cleanup_temp": true,
        "cleanup_output": false,
        "cleanup_input": false
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `WorkspaceError` | 路径穿越攻击（`../`） |
| `WorkspaceError` | 写入文件超出 `max_file_size` |
| `WorkspaceError` | 文件数超出 `max_files` |
| `WorkspaceError` | 磁盘空间不足 |
| `WorkspaceError` | 读取不存在的文件 |
| `WorkspaceError` | Workspace root 不可写 |
| `WorkspaceError` | 写入路径不是文件（是目录） |

### 测试要求

- `prepare` 正确创建目录结构
- `write` 后 `read` 返回相同内容
- `list` 正确返回 glob 匹配结果
- 路径穿越攻击被拒绝（`../` 逃逸）
- 超大文件写入被拒绝
- `cleanup` 正确删除 temp 目录
- Agent 之间目录隔离（agent_a 无法读写 agent_b 的文件）
- `exists` 对存在/不存在文件返回正确
- 并发操作不同 Agent 的 Workspace 不冲突

---

## 8. Module: agent

### 职责

提供 Agent 抽象基类和生命周期定义。Agent 是 ZMAI 中执行任务的基本单元。

### 输入

`AgentContext`：
- `agent_id: str` — Agent 唯一标识
- `task: str` — 任务描述
- `config: dict` — Agent 自定义配置
- `backend: Backend` — Gateway 传入的 Backend 实例
- `memory: MemoryManager` — Memory 实例
- `workspace: Path` — Workspace 路径
- `tools: ToolRegistry` — 可用工具注册表

### 输出

`AgentResult`：
- `agent_id: str`
- `status: AgentState` — 最终状态
- `output: str` — 最终输出
- `steps: int` — 执行步数
- `usage: TokenUsage | None` — Token 用量汇总
- `error: str | None` — 错误信息
- `metadata: dict` — 额外元数据

### AgentState

```python
class AgentState(Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### Agent 接口

```python
class Agent(ABC):
    agent_id: str
    name: str
    description: str

    @abstractmethod
    async def initialize(self, context: AgentContext) -> None:
        """初始化 Agent。设置上下文、加载记忆、注册工具。"""
        ...

    @abstractmethod
    async def step(self, context: AgentContext) -> AgentAction:
        """执行一步。返回 AgentAction 指示下一步行为。"""
        ...

    @abstractmethod
    async def finalize(self, context: AgentContext) -> AgentResult:
        """结束 Agent。清理资源、保存结果、返回最终输出。"""
        ...
```

### AgentAction

Agent 的 `step()` 返回 `AgentAction`，指示 Runtime 下一步行为：

```python
class AgentAction:
    type: Literal["continue", "pause", "complete", "fail"]
    output: str | None           # 当前步的输出
    metadata: dict               # 额外信息
    pause_reason: str | None     # type="pause" 时必填
    error: str | None            # type="fail" 时必填
```

### 生命周期

```
Runtime 创建 Agent 实例
    │
    ├── agent_id = generate_uuid()
    ├── name = class attribute
    └── description = class attribute
    │
Runtime 调用 agent.initialize(context)
    │
    ├── 加载/恢复记忆
    ├── 注册 Agent 特定工具
    ├── 设置初始上下文
    └── 触发 ON_AGENT_INIT hook
    │
Runtime 进入主循环：
    │
    while True:
        │
        action = await agent.step(context)
        │
        ├── "continue" → Runtime 处理 → 继续循环
        ├── "pause"    → Runtime 暂停 → 等待 resume
        ├── "complete" → break → 调用 finalize
        └── "fail"     → break → 调用 finalize(with error)
    │
Runtime 调用 agent.finalize(context)
    │
    ├── 保存结果
    ├── 持久化记忆
    ├── 清理资源
    ├── 触发 ON_AGENT_COMPLETE / ON_AGENT_ERROR hook
    └── 返回 AgentResult
```

### 依赖

- `zmai.errors` (`AgentError`)
- `zmai.memory` (`MemoryManager`)
- `zmai.tool` (`ToolRegistry`)
- `zmai.gateway` (`Backend`)

### 配置

```json
{
    "agent": {
        "default_max_steps": 100,
        "default_timeout": 600
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `AgentError` | `initialize` 抛出未处理异常 |
| `AgentError` | `step` 抛出未处理异常 |
| `AgentError` | `finalize` 抛出未处理异常 |
| `AgentError` | Agent 执行超过 `max_steps` |
| `AgentError` | Agent 执行超时 |

### 测试要求

- Agent ABC 不可直接实例化
- 实现子类后三阶段方法可调
- `AgentState` 所有状态值正确
- `AgentAction` 各类型构造正确
- Agent 基础执行流程（initialize → step(complete) → finalize）
- 超过 `max_steps` 时被终止
- Agent 异常被包装为 `AgentError`
- `AgentContext` 包含所有必要字段

---

## 9. Module: workflow

### 职责

提供 Workflow 定义和执行能力。支持将多个 Agent 编排为一个多步骤工作流。

### 输入

`Workflow` 定义包含：
- `name: str` — 工作流名称
- `description: str` — 描述
- `steps: list[WorkflowStep]` — 步骤列表

`WorkflowStep`：
- `id: str` — 步骤唯一 ID
- `name: str` — 步骤名称
- `agent_type: type[Agent]` — 执行此 Step 的 Agent 类
- `input_mapping: dict` — 从全局输入映射到 Agent 输入
- `output_mapping: dict` — 从 Agent 输出映射到全局输出
- `next_on_success: str | None` — 成功后下一步 ID（None = 终止）
- `next_on_failure: str | None` — 失败后下一步 ID（None = 终止）
- `max_retries: int` — 失败重试次数，默认 0

### 输出

`WorkflowResult`：
- `workflow_id: str`
- `name: str`
- `status: WorkflowStatus` — `RUNNING | COMPLETED | FAILED | CANCELLED`
- `steps: list[StepResult]` — 每步执行结果
- `started_at: ISO8601`
- `completed_at: ISO8601 | None`
- `error: str | None`

`StepResult`：
- `step_id: str`
- `status: StepStatus` — `PENDING | RUNNING | SUCCESS | FAILED | SKIPPED`
- `agent_result: AgentResult | None`
- `error: str | None`
- `retries: int`

### 生命周期

```
WorkflowEngine.execute(workflow, context)
    │
    ├── 创建 workflow_id
    ├── 初始化 Workflow 状态（所有步骤 PENDING）
    │
    ├── 遍历 steps（按顺序）:
    │   ├── step → RUNNING
    │   ├── 应用 input_mapping → 创建 AgentContext
    │   ├── Runtime.run(agent)  ← 委托 Runtime 执行 Agent
    │   │   ├── on_success → 应用 output_mapping → 下一步
    │   │   ├── on_failure → 检查 max_retries → 重试 / 跳转到失败处理步骤
    │   │   └── on_cancel  → 停止所有步骤 → CANCELLED
    │   └── step → SUCCESS / FAILED / SKIPPED
    │
    └── 汇总结果 → WorkflowResult
```

### Workflow 编排模式

**线性（默认）：**
```
Step1 → Step2 → Step3 → Complete
```

**条件分支（通过 next_on_success/next_on_failure）：**
```
Step1 → success → Step2 → success → Complete
  │                               │
  └── failure → ErrorHandler ─────┘
```

### 约束

- Workflow v1 仅支持 DAG 结构（不支持循环）
- Step 按拓扑顺序执行
- 同一 Workflow 内 Agent 实例互不共享 Memory（除非通过 `output_mapping` 显式传递）
- Workflow 支持取消（取消当前正在执行的 Step）

### 依赖

- `zmai.errors` (`RuntimeError` 复用)
- `zmai.agent` (`Agent`, `AgentContext`, `AgentResult`)
- `zmai.runtime` (`Runtime`) — 委托 Runtime 执行 Agent

### 配置

```json
{
    "workflow": {
        "max_steps": 50,
        "default_max_retries": 2
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | Workflow 步骤循环引用 |
| `RuntimeError` | Step 的 `agent_type` 不可实例化 |
| `RuntimeError` | `input_mapping` 中引用的 key 不存在 |
| `RuntimeError` | Workflow 执行超时 |
| `RuntimeError` | 取消时正在执行的 Step 无法终止 |

### 测试要求

- 线性 Workflow 按顺序执行所有步骤
- 条件分支正确跟随 success/failure 路径
- step 失败后重试机制正确
- 失败的步骤正确跳转到 `next_on_failure`
- Workflow 可被取消
- 取消后正在执行的 Agent 被终止
- `input_mapping`/`output_mapping` 正确传递数据
- 循环引用检测（A→B→A）在启动时拒绝
- 所有步骤成功时 Workflow 状态为 COMPLETED
- 步骤失败时 Workflow 状态为 FAILED

---

## 10. Module: runtime

### 职责

ZMAI 的核心运行时。编排 Agent 的完整生命周期，协调 Memory、Gateway、Plugin、Workspace 等子模块。

### 输入

`Runtime.run()` 参数：
- `agent_id: str` — Agent 标识
- `task: str` — 任务描述
- `backend: str` — Backend 名称（默认 `"claude"`）
- `config: dict | None` — 运行时配置覆盖

`Runtime.pause()` 参数：
- `agent_id: str`
- `reason: str | None` — 暂停原因

`Runtime.resume()` 参数：
- `agent_id: str`
- `input: str | None` — 恢复时注入的输入

`Runtime.cancel()` 参数：
- `agent_id: str`
- `reason: str | None` — 取消原因

### 输出

`AgentResult`（同 `zmai.agent` 定义）：
- `agent_id`, `status`, `output`, `steps`, `usage`, `error`, `metadata`

### 核心执行循环

```
Runtime.run(agent_id, task, backend):
    │
    1. 初始化阶段
    │   ├── Config 加载
    │   ├── BackendRegistry.get(backend) → Backend
    │   ├── MemoryManager.init(agent_id)
    │   ├── Workspace.prepare(agent_id)
    │   ├── PluginManager.trigger(ON_RUNTIME_START)
    │   └── PluginManager.trigger(ON_AGENT_INIT)
    │
    2. 构建 AgentContext
    │   ├── agent_id, task, config
    │   ├── backend, memory, workspace, tools
    │   └── 传递给 agent.initialize(context)
    │
    3. 执行阶段
    │   StateManager → status = RUNNING
    │   Scheduler → 启动异步执行
    │   loop (until complete/fail/cancel):
    │   │
    │   ├── 1. Memory → build_context()
    │   ├── 2. agent.step() → AgentAction
    │   ├── 3. PluginManager.trigger(ON_AGENT_STEP)
    │   │
    │   ├── if action.type == "continue":
    │   │   ├── Gateway → backend.invoke(request)
    │   │   ├── if tool_call → ToolRouter.execute()
    │   │   └── Memory.update()
    │   │
    │   ├── if action.type == "pause":
    │   │   └── StateManager → status = PAUSED → 等待 resume
    │   │
    │   ├── if action.type == "complete":
    │   │   └── break
    │   │
    │   └── if action.type == "fail":
    │       └── break
    │
    4. 结束阶段
    │   ├── StateManager → status = terminal_state
    │   ├── agent.finalize()
    │   ├── PluginManager.trigger(ON_AGENT_COMPLETE | ON_AGENT_ERROR)
    │   ├── Memory.persist()
    │   ├── Workspace.cleanup()
    │   └── PluginManager.trigger(ON_RUNTIME_STOP)
    │
    5. 返回 AgentResult
```

### Runtime 状态监控

```python
class RuntimeInfo:
    running_agents: list[AgentInfo]      # 正在运行的 Agent
    completed_agents: list[AgentInfo]    # 已完成的 Agent
    total_agents: int                    # 总计
    uptime: float                        # Runtime 运行时间（秒）
    memory_usage: dict                   # Memory 使用统计
```

### 并发模型

- Runtime 使用 `asyncio` 事件循环
- 每个 Agent 在一个独立的 Task 中运行
- `run()` 是 async 的，调用方 await 执行结果
- `pause/resume/cancel` 是线程安全的，通过事件驱动
- Runtime 维护一个 `dict[str, asyncio.Task]` 管理所有 Agent Task

### 状态持久化

- Agent 状态在 `pause` 和 `complete` 时持久化
- 持久化位置：`<workspace_root>/<agent_id>/.state/state.json`
- 状态文件包含 Agent 元数据、当前步骤、Memory 快照引用
- Runtime 重启后可恢复已暂停的 Agent

### Runtime 规则

1. Runtime **不得**直接实现业务逻辑。业务逻辑在 Agent 中实现。
2. Runtime **不得**直接调用 Backend。通过 Gateway 调用。
3. Runtime **不得**处理 Prompt。Prompt 是 Agent 的职责。
4. Runtime **必须**捕获所有下层异常（Agent, Memory, Gateway），包装为 `RuntimeError`。
5. Runtime **必须**记录关键生命周期事件（启动、停止、Agent 状态变化）到日志。

### 依赖

- `zmai.errors` (`RuntimeError`)
- `zmai.config` (`Config`)
- `zmai.gateway` (`BackendRegistry`, `ToolRouter`, `Backend`)
- `zmai.memory` (`MemoryManager`)
- `zmai.workspace` (`Workspace`)
- `zmai.plugin` (`PluginManager`)
- `zmai.agent` (`Agent`, `AgentContext`, `AgentResult`)
- 标准库: `asyncio`, `logging`, `uuid`, `datetime`

### 配置

```json
{
    "runtime": {
        "max_iterations": 100,
        "timeout": 300,
        "log_level": "INFO",
        "max_concurrent_agents": 10,
        "state_auto_persist": true,
        "state_persist_interval": 30
    }
}
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | Agent 超出 `max_iterations` |
| `RuntimeError` | Agent 执行超时 |
| `RuntimeError` | Memory 操作失败 |
| `RuntimeError` | Gateway 调用失败（包装 BackendError） |
| `RuntimeError` | Plugin Hook 阻塞超时 |
| `RuntimeError` | 并发 Agent 数超出 `max_concurrent_agents` |
| `RuntimeError` | 对不存在的 Agent 执行 pause/resume/cancel |

### 测试要求

- Runtime 可正常启动和停止
- Agent 从 initialize → run → complete 完整周期通过
- Agent 可被 pause → resume
- Agent 可被 cancel
- 超过 `max_iterations` 时自动终止
- 并发运行多个 Agent
- 不存在的 Agent ID 的 pause/resume/cancel 抛出明确错误
- Plugin Hook 在生命周期点被触发
- 异常被正确包装为 `RuntimeError`
- 状态持久化后重启可恢复（在支持恢复的版本中验证）
- 所有模块集成：Runtime + Memory + Gateway + Workspace

---

## 11. Module: cli

### 职责

提供命令行用户界面。解析用户命令，调用 Runtime 执行，格式化输出。

### 输入

命令行参数（由 `argparse` 解析）。

### 输出

终端输出（文本/表格/JSON 格式）或非零退出码。

### 命令定义

#### `zmai run <agent-type> [options]`

运行 Agent。

```
参数:
  agent-type              Agent 类型（必填）
  -t, --task TEXT         任务描述
  -b, --backend NAME     Backend 名称（默认: claude）
  -c, --config FILE      配置文件路径
  --max-steps N          最大步数
  --timeout N            超时秒数
  --json                 输出 JSON 格式

示例:
  zmai run swe --task "Fix bug in parser.py"
  zmai run swe --task "Add unit tests" --json
```

#### `zmai init [options]`

初始化 ZMAI 项目。

```
参数:
  --dir PATH              项目目录（默认: 当前目录）
  --force                 覆盖已有文件

示例:
  zmai init
  zmai init --dir ./my_project
```

#### `zmai config [options]`

管理配置。

```
子命令:
  zmai config get <key>              读取配置
  zmai config set <key> <value>      写入配置
  zmai config list                   列出所有配置
  zmai config path                   显示配置文件路径

示例:
  zmai config get gateway.default_backend
  zmai config set runtime.max_iterations 200
  zmai config list
```

#### `zmai plugin [options]`

管理插件。

```
子命令:
  zmai plugin list                   列出已安装插件
  zmai plugin install <name>         安装插件
  zmai plugin uninstall <name>       卸载插件
  zmai plugin enable <name>          启用插件
  zmai plugin disable <name>         禁用插件

示例:
  zmai plugin list
  zmai plugin install zmai-plugin-mcp
```

#### `zmai --version`

显示版本信息。

```
输出: ZMAI v0.1.0
```

### 输出格式

```json
// --json 模式下 AgentResult 的 JSON 输出
{
    "agent_id": "a1b2c3d4",
    "status": "completed",
    "output": "任务完成...",
    "steps": 15,
    "usage": {
        "input_tokens": 25000,
        "output_tokens": 8000
    },
    "error": null
}
```

### 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 通用错误 |
| 2 | 配置错误 |
| 3 | Agent 执行失败 |
| 4 | Plugin 错误 |
| 5 | 参数错误 |

### 生命周期

```
CLI.main()
    │
    ├── argparse.parse_args()
    ├── Config.load()
    │
    ├── if cmd == "run":
    │   ├── Runtime.create()
    │   ├── Runtime.run(agent_type, task, ...)
    │   ├── 格式化输出
    │   └── sys.exit(code)
    │
    ├── if cmd == "init":
    │   ├── 创建项目目录结构
    │   ├── 生成默认配置文件
    │   └── 打印成功消息
    │
    ├── if cmd == "config":
    │   ├── get/set/list 配置
    │   └── 打印结果
    │
    ├── if cmd == "plugin":
    │   ├── 调 PluginManager 对应操作
    │   └── 打印结果
    │
    └── if cmd == "--version":
        └── 打印版本号
```

### 输出着色规则

- 成功消息：绿色
- 错误消息：红色
- 警告消息：黄色
- 信息消息：白色
- JSON 模式：无着色

### 依赖

- `zmai.errors` (`ZMAIError`)
- `zmai.config` (`Config`)
- `zmai.runtime` (`Runtime`)
- `zmai.plugin` (`PluginManager`)
- 标准库: `argparse`, `sys`, `json`

### 配置

无模块级配置。CLI 使用 Runtime 配置。

### 异常

| 异常 | 触发条件 |
|---|---|
| `ZMAIError` | 所有下层异常由 CLI 捕获并打印友好错误信息 |
| `SystemExit` | 退出码通过 `sys.exit()` 返回 |

CLI 不应抛出未捕获异常。所有异常应在 `main()` 顶层被捕获并打印。

### 测试要求

- 所有命令可被 `argparse` 正确解析
- `zmai run` 正确传递参数到 Runtime
- `zmai run --json` 输出合法 JSON
- `zmai init` 创建正确的目录结构
- `zmai config get/set/list` 正确读写配置
- `zmai plugin list` 调用 PluginManager
- 无效命令返回非零退出码
- 缺少必填参数时打印帮助信息
- 错误路径打印友好错误信息（非 stack trace）
- 退出码符合规范

---

## 附录 A: 全局 Cross-Cutting Concerns

### A.1 日志规范

所有模块使用标准库 `logging`，命名格式：

```python
logger = logging.getLogger("zmai.<module_name>")
```

日志级别规范：

| 级别 | 使用场景 |
|---|---|
| `ERROR` | 异常、操作失败 |
| `WARNING` | 非预期但可恢复的情况 |
| `INFO` | 生命周期事件（start/stop/state change） |
| `DEBUG` | 执行细节（Tool 调用、Memory 操作） |

### A.2 序列化

- 所有跨模块传输的数据必须是 JSON 可序列化的
- `datetime` 统一使用 ISO8601 格式字符串
- `Path` 统一使用 `str`（`as_posix()`）传输

### A.3 模块边界

- 模块之间仅通过 `__init__.py` 暴露的公共 API 交互
- 私有模块（`_*.py`）不被其他模块导入
- 模块间调用必须通过接口（ABC），不直接使用具体类

### A.4 线程安全

- Runtime、MemoryManager、PluginManager 必须支持多线程安全
- 使用 `threading.Lock` 保护共享状态
- 所有 `store`/`update`/`delete` 操作必须是原子的

### A.5 错误传播

```
下层异常 → 上层捕获 → 包装为本层异常 → 向上层或外部抛出
```

示例：

```
ToolError → Runtime 捕获 → 包装为 RuntimeError → CLI 捕获 → 打印错误 → exit(1)
```

---

> **下一阶段:** Phase 2 — Design。根据本文档为每个模块设计实现方案。
