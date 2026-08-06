# ZMAI Modules

Version: 1.0

> 本文档定义 ZMAI 的所有模块划分、职责、依赖关系和公共 API 契约。

---

## 1. Module Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                           CLI                                    │
│                    (用户交互入口)                                  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ depends on
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│   │ Runtime  │  │  Agent   │  │ Workflow │  │    Plugin     │  │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│        │             │             │                │          │
│   ┌────▼─────────────▼─────────────▼────────────────▼───────┐  │
│   │                      Memory                              │  │
│   └─────────────────────────────────────────────────────────┘  │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                     Gateway                              │  │
│   └─────────────────────────────────────────────────────────┘  │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                    Workspace                             │  │
│   └─────────────────────────────────────────────────────────┘  │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                     Config                               │  │
│   └─────────────────────────────────────────────────────────┘  │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                     Errors                               │  │
│   └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Module: `zmai.runtime`

**Package:** `src/zmai/runtime/`

**职责:** ZMAI 的核心运行时，负责 Agent 的生命周期管理、任务调度、状态管理。

**依赖:** `memory`, `gateway`, `plugin`, `agent`, `workspace`, `config`, `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `runtime.py` | Runtime 主类，编排整个执行流程 |
| `lifecycle.py` | Agent 生命周期状态机管理 |
| `scheduler.py` | 任务调度（同步/异步执行） |
| `state.py` | 统一状态管理器（JSON 单数据源） |

### 公共 API

```python
class Runtime:
    """ZMAI 运行时主类。"""
    
    def __init__(self, config: Config) -> None: ...
    
    async def run(
        self,
        agent_id: str,
        task: str,
        backend: str = "claude",
        **kwargs: Any,
    ) -> AgentResult: ...
    
    async def pause(self, agent_id: str) -> None: ...
    async def resume(self, agent_id: str) -> None: ...
    async def cancel(self, agent_id: str) -> None: ...
    
    def get_status(self, agent_id: str) -> AgentStatus: ...
    def list_agents(self) -> list[AgentInfo]: ...

class LifecycleManager:
    """Agent 生命周期状态机。"""
    
    def initialize(self, agent_id: str, config: dict) -> None: ...
    def transition(self, agent_id: str, to: AgentState) -> None: ...
    def get_state(self, agent_id: str) -> AgentState: ...
    def is_terminal(self, agent_id: str) -> bool: ...

class StateManager:
    """统一 JSON 状态管理。"""
    
    def get(self, agent_id: str) -> AgentStateData: ...
    def update(self, agent_id: str, data: dict) -> None: ...
    def delete(self, agent_id: str) -> None: ...
    def list(self) -> list[AgentStateData]: ...

class Scheduler:
    """任务调度器。"""
    
    async def schedule(self, agent_id: str, coro: Coroutine) -> TaskHandle: ...
    async def cancel(self, agent_id: str) -> None: ...
    async def wait(self, agent_id: str) -> AgentResult: ...
```

---

## 3. Module: `zmai.memory`

**Package:** `src/zmai/memory/`

**职责:** Agent 的记忆系统，提供工作记忆和长期记忆的存储、读取、更新能力。

**依赖:** `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `base.py` | Memory 抽象基类和 MemoryEntry 数据类 |
| `working.py` | Working Memory 实现（内存存储） |
| `long_term.py` | Long-term Memory 实现（文件持久化） |

### 公共 API

```python
class MemoryEntry:
    key: str
    value: Any
    namespace: str
    timestamp: datetime
    ttl: int | None  # seconds, None = no expiry

class Memory(ABC):
    """Memory 抽象基类。所有 Memory 实现必须继承此类。"""
    
    @abstractmethod
    def store(self, key: str, value: Any, namespace: str = "default") -> None: ...
    @abstractmethod
    def read(self, key: str, namespace: str = "default") -> Any: ...
    @abstractmethod
    def update(self, key: str, value: Any, namespace: str = "default") -> None: ...
    @abstractmethod
    def delete(self, key: str, namespace: str = "default") -> None: ...
    @abstractmethod
    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]: ...
    @abstractmethod
    def clear(self, namespace: str = "default") -> None: ...

class MemoryManager:
    """统一 Memory 入口，管理 Working 和 Long-term Memory。"""
    
    def __init__(self, config: MemoryConfig) -> None: ...
    def working(self, agent_id: str) -> Memory: ...
    def long_term(self, agent_id: str) -> Memory: ...
    def persist(self, agent_id: str) -> None: ...
    def restore(self, agent_id: str) -> None: ...
    def cleanup(self, agent_id: str) -> None: ...
```

---

## 4. Module: `zmai.gateway`

**Package:** `src/zmai/gateway/`

**职责:** Backend 抽象层，提供模型无关的调用接口。负责 Backend 注册发现、工具调用路由。

**依赖:** `tool`, `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `base.py` | Backend 抽象基类 |
| `registry.py` | Backend 注册表 |
| `tool_router.py` | 工具调用路由 |
| `backends/claude.py` | Claude Backend 默认实现 |

### 公共 API

```python
class BackendRequest:
    messages: list[Message]
    tools: list[ToolDefinition] | None
    max_tokens: int
    temperature: float

class BackendResponse:
    content: str
    tool_calls: list[ToolCall]
    usage: TokenUsage | None
    metadata: dict

class Backend(ABC):
    """LLM Backend 抽象基类。"""
    
    name: str
    
    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse: ...
    @abstractmethod
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]: ...
    
    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]: ...

class BackendRegistry:
    """Backend 注册表。"""
    
    def register(self, name: str, backend: type[Backend]) -> None: ...
    def get(self, name: str) -> Backend: ...
    def list(self) -> dict[str, type[Backend]]: ...
    def default(self) -> Backend: ...

class ToolRouter:
    """工具调用路由。"""
    
    def register(self, tool: Tool) -> None: ...
    def execute(self, name: str, params: dict, context: ToolContext) -> ToolResult: ...
    def definitions(self) -> list[ToolDefinition]: ...
```

---

## 5. Module: `zmai.agent`

**Package:** `src/zmai/agent/`

**职责:** Agent 基础抽象和生命周期定义。提供 Agent 基类供具体实现继承。

**依赖:** `memory`, `tool`, `gateway`, `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `base.py` | Agent 抽象基类 |
| `lifecycle.py` | Agent 生命周期状态和事件定义 |

### 公共 API

```python
class AgentState(Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class AgentEvent:
    agent_id: str
    state: AgentState
    timestamp: datetime
    data: dict | None

class Agent(ABC):
    """Agent 抽象基类。所有 Agent 实现必须继承此类。"""
    
    agent_id: str
    name: str
    description: str
    
    @abstractmethod
    async def initialize(self, context: AgentContext) -> None: ...
    @abstractmethod
    async def step(self, context: AgentContext) -> AgentAction: ...
    @abstractmethod
    async def finalize(self, context: AgentContext) -> AgentResult: ...
```

---

## 6. Module: `zmai.tool`

**Package:** `src/zmai/tool/`

**职责:** 工具系统，提供 Tool 抽象和注册表。工具是 Agent 与外部世界交互的主要方式。

**依赖:** `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `base.py` | Tool 抽象基类和 ToolResult 定义 |
| `registry.py` | Tool 注册表 |

### 公共 API

```python
class ToolContext:
    agent_id: str
    workspace_path: Path
    config: dict
    timeout: int

class ToolResult:
    success: bool
    output: str
    error: str | None
    metadata: dict

class Tool(ABC):
    """Tool 抽象基类。"""
    
    name: str
    description: str
    parameters: JSONSchema
    
    @abstractmethod
    def execute(self, context: ToolContext, params: dict) -> ToolResult: ...
    
    def validate(self, params: dict) -> bool: ...

class ToolRegistry:
    """Tool 注册表。"""
    
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    def list(self) -> list[Tool]: ...
    def remove(self, name: str) -> None: ...
```

---

## 7. Module: `zmai.workflow`

**Package:** `src/zmai/workflow/`

**职责:** 工作流引擎，支持多步骤 Agent Workflow 的定义和执行。

**依赖:** `agent`, `memory`, `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `base.py` | Workflow 抽象基类 |
| `engine.py` | Workflow 执行引擎 |

### 公共 API

```python
class WorkflowStep:
    id: str
    name: str
    agent: type[Agent]
    input_mapping: dict
    output_mapping: dict
    next_on_success: str | None
    next_on_failure: str | None

class Workflow(ABC):
    """Workflow 抽象基类。"""
    
    name: str
    description: str
    steps: list[WorkflowStep]
    
    @abstractmethod
    def build(self) -> list[WorkflowStep]: ...

class WorkflowEngine:
    """Workflow 执行引擎。"""
    
    def __init__(self, runtime: Runtime) -> None: ...
    async def execute(self, workflow: Workflow, context: dict) -> WorkflowResult: ...
    def get_progress(self, workflow_id: str) -> WorkflowProgress: ...
    async def cancel(self, workflow_id: str) -> None: ...
```

---

## 8. Module: `zmai.plugin`

**Package:** `src/zmai/plugin/`

**职责:** 插件系统，管理插件的发现、加载、启用、禁用、卸载生命周期。

**依赖:** `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `base.py` | Plugin 抽象基类 |
| `manager.py` | Plugin 管理器 |
| `hooks.py` | Hook 点和 Hook 注册表定义 |

### 公共 API

```python
class PluginHook:
    """Hook 装饰器/注册器。"""
    point: HookPoint
    priority: int
    handler: Callable

class Plugin(ABC):
    """Plugin 抽象基类。"""
    
    name: str
    version: str
    description: str
    
    @abstractmethod
    def on_load(self) -> None: ...
    @abstractmethod
    def on_unload(self) -> None: ...
    
    def on_enable(self) -> None: ...
    def on_disable(self) -> None: ...

class PluginManager:
    """Plugin 管理器。"""
    
    def discover(self) -> list[PluginMetadata]: ...
    def load(self, name: str) -> Plugin: ...
    def unload(self, name: str) -> None: ...
    def enable(self, name: str) -> None: ...
    def disable(self, name: str) -> None: ...
    def list(self) -> list[PluginInfo]: ...
    def trigger(self, point: HookPoint, **context: Any) -> list[HookResult]: ...
```

---

## 9. Module: `zmai.workspace`

**Package:** `src/zmai/workspace/`

**职责:** 工作空间管理，为 Agent 提供隔离的文件系统沙箱。

**依赖:** `config`, `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `workspace.py` | Workspace 管理器 |

### 公共 API

```python
class Workspace:
    """Workspace 管理器。"""
    
    def __init__(self, root: Path) -> None: ...
    def prepare(self, agent_id: str) -> Path: ...
    def cleanup(self, agent_id: str) -> None: ...
    def read(self, agent_id: str, path: str) -> bytes: ...
    def write(self, agent_id: str, path: str, data: bytes) -> Path: ...
    def list(self, agent_id: str, pattern: str = "*") -> list[Path]: ...
    def exists(self, agent_id: str, path: str) -> bool: ...
    def temp(self, agent_id: str) -> Path: ...
```

---

## 10. Module: `zmai.config`

**Package:** `src/zmai/config/`

**职责:** 统一的配置管理，支持多配置源（文件、环境变量、CLI 参数）。

**依赖:** `errors`

### 子模块

| 模块 | 职责 |
|---|---|
| `config.py` | 配置管理器 |
| `sources.py` | 配置源实现 |

### 公共 API

```python
class Config:
    """配置管理器。"""
    
    def __init__(self, sources: list[ConfigSource]) -> None: ...
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def has(self, key: str) -> bool: ...
    def export(self) -> dict: ...

class ConfigSource(ABC):
    """配置源抽象基类。"""
    
    @abstractmethod
    def load(self) -> dict: ...
    @abstractmethod
    def watch(self, callback: Callable) -> None: ...

class FileSource(ConfigSource):
    """文件配置源（JSON）。"""
    def __init__(self, path: Path) -> None: ...

class EnvSource(ConfigSource):
    """环境变量配置源。"""
    def __init__(self, prefix: str = "ZMAI_") -> None: ...

class CLISource(ConfigSource):
    """CLI 参数配置源。"""
    def __init__(self, args: list[str]) -> None: ...
```

---

## 11. Module: `zmai.cli`

**Package:** `src/zmai/cli/`

**职责:** 命令行界面，用户交互入口。

**依赖:** `runtime`, `config`, `plugin`

### 子模块

| 模块 | 职责 |
|---|---|
| `main.py` | CLI 入口，参数解析 |
| `commands/run.py` | `zmai run` 命令 |
| `commands/init.py` | `zmai init` 命令 |
| `commands/config.py` | `zmai config` 命令 |
| `commands/plugin.py` | `zmai plugin` 命令 |

### 公共 API

```python
# CLI 入口
def main() -> None: ...

# 命令函数
def cmd_run(args: Namespace) -> None: ...
def cmd_init(args: Namespace) -> None: ...
def cmd_config(args: Namespace) -> None: ...
def cmd_plugin(args: Namespace) -> None: ...
```

---

## 12. Module: `zmai.errors`

**Package:** `src/zmai/errors/`

**职责:** 共享错误类型定义。

**依赖:** 无（标准库仅依赖）

### 公共 API

```python
class ZMAIError(Exception):
    """所有 ZMAI 异常的基类。"""
    code: str
    message: str
    
    def __init__(self, code: str, message: str) -> None: ...

class RuntimeError(ZMAIError): ...    # code: "RUNTIME_ERROR"
class BackendError(ZMAIError): ...    # code: "BACKEND_ERROR"
class MemoryError(ZMAIError): ...     # code: "MEMORY_ERROR"
class PluginError(ZMAIError): ...     # code: "PLUGIN_ERROR"
class ConfigError(ZMAIError): ...     # code: "CONFIG_ERROR"
class WorkspaceError(ZMAIError): ...  # code: "WORKSPACE_ERROR"
class ToolError(ZMAIError): ...       # code: "TOOL_ERROR"
class AgentError(ZMAIError): ...      # code: "AGENT_ERROR"
```

---

## 13. Module Dependency Matrix

| Module | Depends On | Depended By | Is External |
|---|---|---|---|
| `errors` | — | all modules | No |
| `config` | `errors` | `runtime`, `workspace`, `cli`, `gateway` | No |
| `tool` | `errors` | `gateway`, `agent`, `runtime` | Yes (future plugins) |
| `memory` | `errors` | `runtime`, `agent`, `workflow` | Yes (future plugins) |
| `plugin` | `errors` | `runtime`, `cli` | Yes |
| `gateway` | `tool`, `errors` | `runtime` | Yes (future backends) |
| `workspace` | `config`, `errors` | `runtime` | Yes (future plugins) |
| `agent` | `memory`, `tool`, `gateway`, `errors` | `runtime`, `workflow` | Yes (future agents) |
| `workflow` | `agent`, `memory`, `errors` | `runtime` | No |
| `runtime` | `memory`, `gateway`, `plugin`, `agent`, `workspace`, `config`, `errors` | `cli` | No |
| `cli` | `runtime`, `config`, `plugin`, `errors` | — | No |

### Circular Dependency Check

```
cli → runtime → gateway → tool
cli → runtime → agent → tool
cli → runtime → workflow → agent
cli → runtime → memory
cli → runtime → plugin
cli → runtime → workspace → config
```

所有依赖为单向。无循环依赖。

---

## 14. Module Size Budget (ahead-of-time)

| Module | Files | Est. LOC | Comments |
|---|---|---|---|
| `errors` | 2 | ~80 | 异常类型定义 |
| `config` | 3 | ~200 | 配置管理器 + 配置源 |
| `tool` | 3 | ~150 | 基类 + 注册表 |
| `memory` | 4 | ~400 | 基类 + 两种实现 + Manager |
| `plugin` | 4 | ~350 | 基类 + 管理器 + Hook |
| `gateway` | 5 | ~500 | 基类 + 注册表 + 路由 + 默认 Backend |
| `workspace` | 2 | ~200 | 管理器 |
| `agent` | 3 | ~200 | 基类 + 生命周期定义 |
| `workflow` | 3 | ~300 | 基类 + 引擎 |
| `runtime` | 5 | ~600 | 主类 + 生命周期 + 调度 + 状态 |
| `cli` | 6 | ~400 | 入口 + 4 个命令 |
| **Total** | **~40** | **~3400** | |

---

> **下一次阅读：** [ROADMAP.md](ROADMAP.md) — 开发路线图与里程碑
