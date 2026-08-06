# ZMAI Architecture

Version: 1.0

> 本文档定义 ZMAI Runtime 的完整架构设计。所有实现必须遵循此架构。

---

## 1. Design Philosophy

ZMAI 的设计围绕三个核心原则展开：

```
┌─────────────────────────────────────────────────────┐
│                    Runtime Core                      │
│  (Lifecycle · Scheduler · State · Memory · Plugin)  │
├─────────────────────────────────────────────────────┤
│                    Gateway Layer                     │
│         (Backend Abstraction · Tool Router)          │
├─────────────────────────────────────────────────────┤
│                    Backend Layer                     │
│           (Claude · OpenAI · Custom ...)             │
└─────────────────────────────────────────────────────┘
```

1. **Runtime First** — Runtime 是核心，Prompt 只是输入。所有业务逻辑由 Runtime 编排，不由 Prompt 承担。
2. **Layered Separation** — 严格的三层架构，下层不知道上层的存在。
3. **Plugin Everything** — 所有可扩展点都设计为 Plugin 接口。

---

## 2. Architecture Overview

### 2.1 System Context

```
┌──────────────────────────────────────────────────────────┐
│                        User                              │
│              (CLI / API / IDE Extension)                 │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                    ZMAI CLI / API                        │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │                  Runtime                         │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │   │
│  │  │Lifecycle │ │Scheduler │ │  State Manager   │ │   │
│  │  └──────────┘ └──────────┘ └──────────────────┘ │   │
│  │  ┌──────────────────┐ ┌──────────────────────┐ │   │
│  │  │  Memory Manager  │ │   Plugin Manager     │ │   │
│  │  └──────────────────┘ └──────────────────────┘ │   │
│  │  ┌──────────────────────────────────────────┐ │   │
│  │  │          Workflow Engine                 │ │   │
│  │  └──────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────┘   │
│                          ↕                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │                  Gateway                         │   │
│  │  ┌──────────────┐ ┌──────────┐ ┌──────────────┐ │   │
│  │  │BackendRegisry│ │ToolRouter│ │  MCP Client  │ │   │
│  │  └──────────────┘ └──────────┘ └──────────────┘ │   │
│  └──────────────────────────────────────────────────┘   │
│                          ↕                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │             Backend (Claude / ...)               │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │               Workspace (Sandbox)                │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │               Config System                      │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                  External World                          │
│       (File System · Shell · Network · APIs)             │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Layers

| Layer | Responsibility | Dependencies |
|---|---|---|
| **CLI/API** | User interaction, command parsing, output formatting | Runtime |
| **Runtime** | Agent lifecycle, scheduling, state, memory, plugins, workflow | Gateway |
| **Gateway** | Backend abstraction, tool routing, MCP communication | Backend implementations |
| **Backend** | Model invocation, tool execution, result formatting | — |
| **Workspace** | File system sandbox, I/O isolation | — |
| **Config** | Unified configuration management | — |

---

## 3. Module Map

```
src/zmai/
├── runtime/     # Core runtime — lifecycle, scheduler, state
├── memory/      # Memory system — working & long-term
├── gateway/     # Backend abstraction layer
├── agent/       # Agent base classes & lifecycle
├── tool/        # Tool system & registry
├── workflow/    # Workflow engine
├── plugin/      # Plugin system
├── workspace/   # Sandboxed execution environment
├── config/      # Configuration management
├── cli/         # Command-line interface
└── errors/      # Shared error types
```

### Dependency Graph

```
cli → runtime → gateway ─→ backend implementations
  ↕      ↕          ↕
config  agent    tool
  ↕      ↕        ↕
plugin  workflow  errors
  ↕
workspace
memory
```

严格单向依赖：`cli → runtime → gateway`。禁止反向依赖。

---

## 4. Runtime Architecture

### 4.1 Runtime Core

Runtime 是 ZMAI 的心脏。它不执行业务逻辑，而是编排整个执行流程。

```
Runtime
├── LifecycleManager    # Agent 生命周期管理
├── Scheduler           # 任务调度
├── StateManager        # 统一状态管理
├── MemoryManager       # 记忆系统
├── PluginManager       # 插件管理
└── WorkflowEngine      # 工作流引擎
```

### 4.2 Runtime Loop

```
┌──────────┐
│  IDLE    │ ← Agent 创建完成，等待启动
└────┬─────┘
     │ start()
     ▼
┌──────────┐
│INIT      │ ← 初始化 Memory、Tool、Backend
└────┬─────┘
     │ on_ready()
     ▼
┌──────────┐     pause()    ┌──────────┐
│ RUNNING  │ ─────────────→ │ PAUSED   │
│          │ ←───────────── │          │
│          │   resume()     │          │
└────┬─────┘                └──────────┘
     │ complete() / fail() / cancel()
     ▼
┌──────────┐
│ TERMINAL │ ← 最终状态：completed / failed / cancelled
└──────────┘
```

### 4.3 State Management

所有状态统一为 JSON，单数据源。

```python
# State schema (conceptual)
{
    "agent_id": "str",
    "status": "idle | initializing | running | paused | completed | failed | cancelled",
    "created_at": "ISO8601",
    "updated_at": "ISO8601",
    "metadata": {},
    "current_step": "str | null",
    "error": "str | null"
}
```

---

## 5. Memory Architecture

### 5.1 Memory System

```
MemoryManager
├── WorkingMemory (ephemeral)
│   ├── Current conversation context
│   ├── Current task state
│   └── Scratch pad
│
└── LongTermMemory (persistent)
    ├── Project knowledge
    ├── User preferences
    ├── Historical sessions
    └── Key learnings
```

### 5.2 Memory Interface

```python
# Memory ABC (conceptual)
class Memory(ABC):
    def store(self, key: str, value: Any, namespace: str) -> None: ...
    def read(self, key: str, namespace: str) -> Any: ...
    def update(self, key: str, value: Any, namespace: str) -> None: ...
    def delete(self, key: str, namespace: str) -> None: ...
    def search(self, query: str, namespace: str) -> list[MemoryEntry]: ...
    def clear(self, namespace: str) -> None: ...
```

### 5.3 Data Flow

```
Agent Runtime
    │
    ├─ Working Memory ──→ In-memory dict (fast, volatile)
    │       │
    │       └── Serialize on pause/complete → JSON
    │
    └─ LongTermMemory ──→ File/DB (slow, persistent)
            │
            └── Deserialize on init → Working Memory
```

### 5.4 Namespace Isolation

每个 Agent 实例拥有独立的 Memory Namespace，默认以 `agent_id` 隔离。Plugin 可以注册额外的 Namespace。

---

## 6. Gateway Architecture

### 6.1 Gateway Layer

Gateway 是 Runtime 与 LLM Backend 之间的桥梁。

```
Gateway
├── BackendRegistry      # Backend 注册与发现
├── Backend(ABC)         # 所有 Backend 的抽象接口
├── ToolRouter           # 工具调用路由
├── MCPClient            # MCP 协议通信（未来）
└── ClaudeBackend        # 默认实现
```

### 6.2 Backend Interface

```python
# Backend ABC (conceptual)
class Backend(ABC):
    """所有 LLM Backend 必须实现此接口。"""
    
    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse: ...
    
    @abstractmethod
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]: ...
    
    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]: ...
```

### 6.3 Tool Router

```python
# Tool ABC (conceptual)
class Tool(ABC):
    """所有工具必须实现此接口。"""
    name: str
    description: str
    parameters: JSONSchema
    
    @abstractmethod
    def execute(self, context: ToolContext, params: dict) -> ToolResult: ...
```

ToolRouter 负责：
1. 将 Agent 发起的工具调用路由到正确的 Tool 实现
2. 提供工具列表供 Backend 注入到模型调用中
3. 格式化执行结果为 Backend 可消费的格式

### 6.4 MCP

MCP (Model Context Protocol) 支持作为插件实现，不在核心 Runtime 中强制依赖。

---

## 7. Workspace

### 7.1 Purpose

Workspace 为 Agent 提供隔离的文件系统沙箱。

### 7.2 Structure

```
workspace/
└── <agent_id>/
    ├── input/          # Agent 输入文件
    ├── output/         # Agent 输出产物
    ├── temp/           # 临时文件（运行结束后清理）
    └── .state/         # Workspace 内部状态
```

### 7.3 Rules

- 每个 Agent 运行实例拥有独立的 Workspace 目录
- Workspace 路径通过 Runtime Context 传递，Agent 不直接访问系统路径
- 临时文件在 Agent 生命周期结束后清理
- 输出文件保留，供用户审查

---

## 8. Plugin System

### 8.1 Plugin Architecture

```
PluginManager
├── Plugin(ABC)           # 所有插件必须实现的接口
├── HookRegistry          # 事件钩子注册
├── PluginDiscovery       # 插件发现（entry_points）
└── PluginIsolation       # 插件隔离（可选）
```

### 8.2 Plugin Lifecycle

```
DISCOVERED → LOADED → ENABLED → DISABLED → UNLOADED
                ↓                          ↑
              FAILED ──────────────────────┘
```

### 8.3 Plugin Hooks

Plugin 可以在以下生命周期点注入钩子：

| Hook | Trigger | Mutates |
|---|---|---|
| `on_runtime_start` | Runtime 启动 | Yes |
| `on_runtime_stop` | Runtime 停止 | No |
| `on_agent_init` | Agent 初始化 | Yes |
| `on_agent_step` | Agent 每步执行 | Yes |
| `on_agent_complete` | Agent 完成 | No |
| `on_agent_error` | Agent 出错 | No |
| `on_memory_read` | Memory 读取 | No |
| `on_memory_write` | Memory 写入 | Yes |
| `on_tool_execute` | Tool 执行前 | Yes |
| `on_tool_result` | Tool 执行后 | Yes |

### 8.4 Plugin Isolation

Plugin 运行在 Runtime 的隔离上下文中：
- Plugin 不得阻塞 Runtime 主循环
- Plugin 的异常不得传播到 Runtime
- Plugin 可以通过 Hook 返回值影响 Runtime，但不得直接修改 Runtime 内部状态

---

## 9. CLI

### 9.1 Commands

```
zmai                      # 交互式模式
zmai run <agent>          # 运行 Agent
zmai init                 # 初始化 ZMAI 项目
zmai config               # 管理配置
zmai plugin install       # 安装插件
zmai plugin list          # 列出插件
zmai plugin uninstall     # 卸载插件
zmai version              # 版本信息
```

### 9.2 CLI Architecture

```
CLI
├── main.py          # Entry point, arg parsing
├── commands/
│   ├── run.py       # zmai run
│   ├── init.py      # zmai init
│   ├── config.py    # zmai config
│   └── plugin.py    # zmai plugin
└── formatters/      # Output formatting
    ├── table.py
    └── json.py
```

---

## 10. Agent Lifecycle

### 10.1 Lifecycle States

```
                  ┌──────────┐
                  │   IDLE   │
                  └────┬─────┘
                       │ initialize()
                       ▼
                  ┌──────────┐
                  │  INIT    │──→ FAILED
                  └────┬─────┘
                       │ on_ready()
                       ▼
                  ┌──────────┐
     pause() ───→ │ RUNNING  │ ───→ COMPLETED
                  │          │ ───→ FAILED
     resume() ←── │          │
                  └──────────┘
                       │ cancel()
                       ▼
                  ┌──────────┐
                  │CANCELLED │
                  └──────────┘
```

### 10.2 State Transitions

| From | To | Trigger | Description |
|---|---|---|---|
| `idle` | `initializing` | `initialize()` | 启动初始化流程 |
| `initializing` | `running` | `on_ready()` | 初始化完成，开始运行 |
| `initializing` | `failed` | Error | 初始化失败 |
| `running` | `paused` | `pause()` | 暂停（等待外部输入） |
| `paused` | `running` | `resume()` | 恢复执行 |
| `running` | `completed` | Natural end | 正常完成 |
| `running` | `failed` | Unhandled error | 运行时错误 |
| `running` | `cancelled` | `cancel()` | 用户取消 |
| `paused` | `cancelled` | `cancel()` | 用户取消 |
| Any | `failed` | Unhandled error | 错误终止 |

### 10.3 Lifecycle Events

Runtime 在 Agent 生命周期关键点释放事件。Plugin 可以通过 Hook 订阅：

```
Agent Created     → Event(agent_id, "created")
Agent Init        → Event(agent_id, "initializing")
Agent Ready       → Event(agent_id, "running")
Agent Step        → Event(agent_id, "step", step_num, action)
Agent Pause       → Event(agent_id, "paused")
Agent Resume      → Event(agent_id, "running")
Agent Complete    → Event(agent_id, "completed", result)
Agent Fail        → Event(agent_id, "failed", error)
Agent Cancel      → Event(agent_id, "cancelled")
```

---

## 11. Configuration System

### 11.1 Design

```
Config
├── ConfigSource(ABC)     # 配置源抽象
├── FileSource            # 文件配置 (JSON/YAML/TOML)
├── EnvSource             # 环境变量
├── CLISource             # CLI 参数
└── ConfigValidator       # 配置校验
```

### 11.2 Precedence

```
CLI args  >  Environment Variables  >  Config File  >  Defaults
(highest)                                         (lowest)
```

### 11.3 Config Namespace

```yaml
# config.yaml (conceptual)
runtime:
  max_iterations: 100
  timeout: 300
  log_level: "INFO"

gateway:
  default_backend: "claude"
  backends:
    claude:
      api_key: "${CLAUDE_API_KEY}"
      model: "claude-sonnet-4-6"

workspace:
  root: "./workspace"
  cleanup_temp: true

memory:
  working_size: 100
  long_term_dir: "./memory"
```

---

## 12. Error Handling

### 12.1 Error Hierarchy

```
ZMAIError (base)
├── RuntimeError        # Runtime 内部错误
├── BackendError        # Backend 调用错误
├── MemoryError         # Memory 操作错误
├── PluginError         # Plugin 加载/执行错误
├── ConfigError         # 配置错误
├── WorkspaceError      # Workspace 错误
├── ToolError           # 工具执行错误
└── AgentError          # Agent 生命周期错误
```

### 12.2 Rules

- 所有自定义异常继承自 `ZMAIError`
- Error 必须包含 `code` (机器可读) 和 `message` (人类可读)
- Runtime 层捕获所有下层异常，包装为 `RuntimeError`
- Plugin 异常不得传播到 Runtime 主循环

---

## 13. Data Flow: End-to-End

```
User Input
    │
    ▼
CLI → Runtime.run(agent_id, task)
    │
    ▼
Runtime → LifecycleManager.initialize()
    │
    ├── MemoryManager.init(agent_id)       # 加载记忆
    ├── PluginManager.trigger(on_agent_init)
    ├── Gateway.get_backend("claude")      # 获取 Backend
    └── Workspace.prepare(agent_id)        # 准备沙箱
    │
    ▼
Runtime → LifecycleManager.transition("running")
    │
    ▼
Runtime Loop (until complete/fail/cancel):
    │
    ├── 1. MemoryManager → build_context()     # 构建上下文
    ├── 2. Gateway → backend.invoke(request)   # 调用模型
    ├── 3. Response Parser:
    │   ├── Text/Message → continue loop
    │   ├── Tool Call → ToolRouter.execute()
    │   │   ├── PluginManager.trigger(on_tool_execute)
    │   │   ├── Tool.execute()
    │   │   └── PluginManager.trigger(on_tool_result)
    │   └── Complete → exit loop
    ├── 4. MemoryManager → update()            # 更新记忆
    └── 5. PluginManager.trigger(on_agent_step)
    │
    ▼
Runtime → LifecycleManager.transition("completed" | "failed" | "cancelled")
    │
    ├── Workspace.cleanup()
    ├── MemoryManager.persist()
    ├── PluginManager.trigger(on_agent_complete | on_agent_error)
    └── Result → CLI → User
```

---

## 14. Directory Structure

```
src/zmai/
├── __init__.py
│
├── runtime/
│   ├── __init__.py
│   ├── runtime.py          # Runtime 主类
│   ├── lifecycle.py        # Agent 生命周期管理
│   ├── scheduler.py        # 任务调度
│   └── state.py            # 统一状态管理
│
├── memory/
│   ├── __init__.py
│   ├── base.py             # Memory 抽象基类
│   ├── working.py          # Working Memory 实现
│   └── long_term.py        # Long-term Memory 实现
│
├── gateway/
│   ├── __init__.py
│   ├── base.py             # Backend 抽象基类
│   ├── registry.py         # Backend 注册表
│   ├── tool_router.py      # 工具调用路由
│   └── backends/
│       ├── __init__.py
│       └── claude.py       # Claude Backend (默认)
│
├── agent/
│   ├── __init__.py
│   ├── base.py             # Agent 抽象基类
│   └── lifecycle.py        # Agent 生命周期定义
│
├── tool/
│   ├── __init__.py
│   ├── base.py             # Tool 抽象基类
│   └── registry.py         # Tool 注册表
│
├── workflow/
│   ├── __init__.py
│   ├── base.py             # Workflow 抽象基类
│   └── engine.py           # Workflow 引擎
│
├── plugin/
│   ├── __init__.py
│   ├── base.py             # Plugin 抽象基类
│   ├── manager.py          # Plugin 管理器
│   └── hooks.py            # Hook 定义
│
├── workspace/
│   ├── __init__.py
│   └── workspace.py        # Workspace 管理器
│
├── config/
│   ├── __init__.py
│   ├── config.py           # 配置管理器
│   └── sources.py          # 配置源
│
├── cli/
│   ├── __init__.py
│   ├── main.py             # CLI 入口
│   └── commands/
│       ├── __init__.py
│       ├── run.py          # zmai run
│       ├── init.py         # zmai init
│       ├── config.py       # zmai config
│       └── plugin.py       # zmai plugin
│
└── errors/
    ├── __init__.py
    └── errors.py           # 错误类型定义
```

---

## 15. Architecture Decision Records

### ADR-1: Gateway 作为独立层

**Decision:** Gateway 是 Runtime 与 Backend 之间的独立层，而非 Runtime 的一部分。

**Rationale:** 保持 Runtime 模型无关。Runtime 只通过 Gateway 接口与 Backend 通信，不感知具体 Backend 实现。

### ADR-2: Working Memory 与 Long-term Memory 分离

**Decision:** Memory 分为 Working 和 Long-term 两层。

**Rationale:** 职责分离。Working Memory 负责当前会话的快速读写，Long-term Memory 负责跨会话的持久化存储。两者接口一致但实现不同。

### ADR-3: Plugin 通过 Hook 与 Runtime 交互

**Decision:** Plugin 不直接调用 Runtime API，而是通过 Hook 机制在生命周期点注入行为。

**Rationale:** 避免 Plugin 对 Runtime 的强依赖。Runtime 不感知 Plugin 的存在，Plugin 通过 Hook 返回值影响行为但不直接修改状态。

### ADR-4: 统一 JSON 状态

**Decision:** 所有运行状态使用 JSON 表示，单数据源。

**Rationale:** 简化状态管理、序列化、调试。JSON 是通用格式，易于日志记录和跨语言交互。

### ADR-5: Workspace 独立于 Runtime

**Decision:** Workspace 是独立模块，Runtime 通过 Context 传递 Workspace 路径。

**Rationale:** Workspace 职责（I/O 管理）与 Runtime 职责（执行编排）正交。独立模块便于测试和替换。

---

## 16. Non-Goals (明确不做的事)

1. **不是聊天机器人** — ZMAI 不提供聊天 UI，只提供 Runtime
2. **不是 Prompt 工程平台** — 不优化 Prompt，只执行 Prompt
3. **不是数据标注平台** — 不处理训练数据
4. **不是模型训练框架** — 不涉及模型训练或微调
5. **不是知识库** — Memory 是存储运行时上下文，不是通用知识库
6. **不强制 MCP** — MCP 是可选能力，通过 Plugin 方式支持
7. **不绑定 Claude** — Claude 是默认 Backend，但架构不依赖 Claude

---

> **下一次阅读：** [MODULES.md](MODULES.md) — 详细模块定义与职责
