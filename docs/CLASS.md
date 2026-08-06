# ZMAI Class Design

Version: 1.0

> 本文档定义 ZMAI 所有模块的类层次、继承关系、数据结构、状态机。
>
> **前置阅读:** ARCHITECTURE.md, SPECIFICATION.md
> **读者:** 实现者

---

## 1. Package Overview

```
src/zmai/
├── errors/       → ZMAIError + 7 subclasses
├── config/       → Config, ConfigSource(ABC), FileSource, EnvSource, CLISource
├── tool/         → Tool(ABC), ToolRegistry, ToolContext, ToolResult
├── memory/       → Memory(ABC), WorkingMemory, LongTermMemory, MemoryManager, MemoryEntry
├── plugin/       → Plugin(ABC), PluginManager, HookRegistry, HookPoint
├── gateway/      → Backend(ABC), BackendRegistry, ToolRouter, BackendRequest, BackendResponse
│   └── backends/ → ClaudeBackend
├── workspace/    → Workspace
├── agent/        → Agent(ABC), AgentContext, AgentResult, AgentState, AgentAction
├── workflow/     → Workflow(ABC), WorkflowEngine, WorkflowStep, WorkflowResult
├── runtime/      → Runtime, LifecycleManager, StateManager, Scheduler, RuntimeInfo
├── cli/          → CLI entry + Command classes
└── __init__.py   → Public API re-exports
```

---

## 2. Module: errors — Class Hierarchy

```
Exception
└── ZMAIError
    ├── RuntimeError
    ├── BackendError
    ├── MemoryError
    ├── PluginError
    ├── ConfigError
    ├── WorkspaceError
    ├── ToolError
    └── AgentError
```

### ZMAIError

```python
@dataclass(frozen=True)
class ZMAIError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "type": type(self).__name__}
```

### 子类定义

所有子类都是 `ZMAIError` 的简单特化，通过 `super().__init__(code, message)` 构造：

| 类 | code 值 | 添加字段 |
|---|---|---|
| `RuntimeError` | `"RUNTIME_ERROR"` | — |
| `BackendError` | `"BACKEND_ERROR"` | `status_code: int \| None` |
| `MemoryError` | `"MEMORY_ERROR"` | — |
| `PluginError` | `"PLUGIN_ERROR"` | `plugin_name: str \| None` |
| `ConfigError` | `"CONFIG_ERROR"` | `key: str \| None` |
| `WorkspaceError` | `"WORKSPACE_ERROR"` | `path: str \| None` |
| `ToolError` | `"TOOL_ERROR"` | `tool_name: str \| None` |
| `AgentError` | `"AGENT_ERROR"` | `agent_id: str \| None` |

### 设计说明

- 使用 `@dataclass(frozen=True)` 确保异常实例不可变
- `to_dict()` 支持 JSON 序列化，用于 CLI JSON 输出和日志
- 统一继承 `ZMAIError`，调用方可使用 `except ZMAIError` 捕获所有 ZMAI 异常
- 子类可添加额外字段以携带更多上下文

---

## 3. Module: config — Class Design

```
ConfigSource (ABC)
    ├── FileSource
    ├── EnvSource
    └── CLISource

Config
    └── _sources: list[ConfigSource]
```

### ConfigSource (ABC)

```python
class ConfigSource(ABC):
    """配置源抽象基类。"""

    @abstractmethod
    def load(self) -> dict:
        """加载配置源的全部键值对。返回扁平 dict（点号分隔键）。"""
        ...

    @abstractmethod
    def name(self) -> str:
        """配置源名称（用于日志和调试）。"""
        ...
```

### FileSource

```python
class FileSource(ConfigSource):
    """从 JSON 文件加载配置。"""

    _path: Path

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> dict:
        # 读取 JSON，展平为点号分隔键
        # 例: {"runtime": {"timeout": 30}} → {"runtime.timeout": 30}
        ...

    def name(self) -> str:
        return f"file:{self._path}"
```

### EnvSource

```python
class EnvSource(ConfigSource):
    """从环境变量加载配置。"""

    _prefix: str
    _separator: str

    def __init__(self, prefix: str = "ZMAI_", separator: str = "__") -> None:
        # prefix="ZMAI_", separator="__"
        # ZMAI_RUNTIME__TIMEOUT=30 → {"runtime.timeout": "30"}
        ...

    def load(self) -> dict: ...
    def name(self) -> str: ...
```

### CLISource

```python
class CLISource(ConfigSource):
    """从 CLI 参数 --key=value 加载配置。"""

    _args: list[str]
    _prefix: str

    def __init__(self, args: list[str] | None = None, prefix: str = "--") -> None:
        # --runtime.timeout=30 → {"runtime.timeout": "30"}
        ...

    def load(self) -> dict: ...
    def name(self) -> str: ...
```

### Config

```python
class Config:
    """统一配置管理器。多源合并，按优先级覆盖。"""

    _data: dict[str, Any]      # 合并后的扁平配置
    _sources: list[ConfigSource]

    def __init__(self, sources: list[ConfigSource] | None = None) -> None:
        # 默认: [FileSource("zmai.json"), EnvSource(), CLISource()]
        # sources 顺序决定了合并优先级，后者覆盖前者
        ...

    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def has(self, key: str) -> bool: ...
    def export(self) -> dict: ...
    def reload(self) -> None: ...

    def _flatten(self, d: dict, parent_key: str = "") -> dict:
        """将嵌套 dict 展平为点号分隔键的扁平 dict。"""
        ...

    def _merge(self, sources: list[ConfigSource]) -> dict:
        """按优先级合并所有配置源。后者覆盖前者。"""
        ...
```

---

## 4. Module: tool — Class Design

```
Tool (ABC)
    └── <concrete tools>

ToolRegistry
    └── _tools: dict[str, Tool]

ToolContext (dataclass)
ToolResult (dataclass)
ToolCall (dataclass)
ToolDefinition (dataclass)
```

### Tool (ABC)

```python
class Tool(ABC):
    """工具抽象基类。"""

    name: str
    description: str
    parameters: dict  # JSON Schema

    @abstractmethod
    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        ...

    def validate(self, params: dict) -> bool:
        """校验参数是否符合 JSON Schema。"""
        ...

    def to_definition(self) -> ToolDefinition:
        """生成 LLM 可消费的工具定义。"""
        ...
```

### ToolContext (dataclass)

```python
@dataclass(frozen=True)
class ToolContext:
    agent_id: str
    workspace_path: Path
    config: dict
    timeout: int = 30
    env: dict = field(default_factory=dict)
    logger: logging.Logger | None = None
```

### ToolResult (dataclass)

```python
@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict: ...
```

### ToolCall (dataclass)

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    params: dict
```

### ToolDefinition (dataclass)

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict  # JSON Schema
```

### ToolRegistry

```python
class ToolRegistry:
    """工具注册表。线程安全。"""

    _tools: dict[str, Tool]
    _lock: threading.Lock

    def register(self, tool: Tool) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> Tool: ...
    def list(self) -> list[Tool]: ...
    def definitions(self) -> list[ToolDefinition]: ...
    def execute(self, name: str, params: dict, context: ToolContext) -> ToolResult: ...
```

---

## 5. Module: memory — Class Design

```
Memory (ABC)
    ├── WorkingMemory
    └── LongTermMemory

MemoryManager
    └── _working: dict[str, WorkingMemory]
    └── _long_term: dict[str, LongTermMemory]

MemoryEntry (dataclass)
MemoryConfig (dataclass)
```

### MemoryEntry (dataclass)

```python
@dataclass
class MemoryEntry:
    key: str
    value: Any
    namespace: str = "default"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ttl: int | None = None  # seconds, None = 永不过期

    @property
    def is_expired(self) -> bool:
        """检查条目是否已过期。"""
        ...

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> MemoryEntry: ...
```

### Memory (ABC)

```python
class Memory(ABC):
    """记忆抽象基类。"""

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
    @abstractmethod
    def list_namespaces(self) -> list[str]: ...
```

### WorkingMemory

```python
class WorkingMemory(Memory):
    """工作记忆。纯内存存储，Agent 生命周期结束后自动销毁。"""

    _data: dict[str, dict[str, MemoryEntry]]  # namespace → {key → entry}
    _lock: threading.Lock
    _max_size: int

    def __init__(self, max_size: int = 1000) -> None: ...

    def store(self, key: str, value: Any, namespace: str = "default") -> None:
        # 检查 max_size，检查 JSON 可序列化
        ...

    def read(self, key: str, namespace: str = "default") -> Any:
        # 检查 TTL 过期
        ...

    def update(self, key: str, value: Any, namespace: str = "default") -> None:
        # 更新 updated_at
        ...

    def delete(self, key: str, namespace: str = "default") -> None: ...
    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]: ...
    def clear(self, namespace: str = "default") -> None: ...
    def list_namespaces(self) -> list[str]: ...
    def _cleanup_expired(self) -> None: ...
```

### LongTermMemory

```python
class LongTermMemory(Memory):
    """长期记忆。文件持久化，JSON Lines 格式。"""

    _root_dir: Path
    _max_file_size: int
    _cache: dict[str, dict[str, MemoryEntry]]  # namespace → {key → entry}

    def __init__(self, root_dir: Path, max_file_size: int = 10 * 1024 * 1024) -> None: ...

    def store(self, key: str, value: Any, namespace: str = "default") -> None:
        # 追加写入 JSONL 文件
        ...

    def read(self, key: str, namespace: str = "default") -> Any:
        # 从缓存读取，缓存未命中时重建
        ...

    def update(self, key: str, value: Any, namespace: str = "default") -> None:
        # 更新缓存 + 重写文件
        ...

    def delete(self, key: str, namespace: str = "default") -> None: ...
    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]: ...
    def clear(self, namespace: str = "default") -> None: ...
    def list_namespaces(self) -> list[str]: ...

    def _file_path(self, namespace: str) -> Path: ...
    def _rebuild_cache(self, namespace: str) -> None:
        """从 JSONL 文件重建内存缓存。"""
        ...

    def _append_to_file(self, entry: MemoryEntry) -> None: ...
    def _rewrite_file(self, namespace: str) -> None:
        """重写整个 JSONL 文件（update/delete 后需要）。"""
        ...
```

### MemoryManager

```python
class MemoryManager:
    """统一记忆管理器。管理 Working 和 Long-term Memory 的配对。"""

    _working_store: dict[str, WorkingMemory]   # agent_id → WorkingMemory
    _long_term_store: dict[str, LongTermMemory] # agent_id → LongTermMemory
    _config: MemoryConfig

    def __init__(self, config: MemoryConfig | None = None) -> None: ...

    def working(self, agent_id: str) -> WorkingMemory:
        """获取 Agent 的工作记忆实例。按需创建。"""
        ...

    def long_term(self, agent_id: str) -> LongTermMemory:
        """获取 Agent 的长期记忆实例。按需创建。"""
        ...

    def persist(self, agent_id: str) -> None:
        """将 Working Memory 同步到 Long-term Memory。"""
        ...

    def restore(self, agent_id: str) -> None:
        """从 Long-term Memory 恢复到 Working Memory。"""
        ...

    def cleanup(self, agent_id: str) -> None:
        """清理 Agent 的所有记忆。"""
        ...

    def exists(self, agent_id: str) -> bool:
        """检查 Agent 是否有已持久化的记忆。"""
        ...
```

### MemoryConfig (dataclass)

```python
@dataclass
class MemoryConfig:
    working_max_size: int = 1000
    working_cleanup_interval: int = 300
    long_term_root: str = "./zmai_memory"
    long_term_max_file_size: int = 10 * 1024 * 1024
```

---

## 6. Module: plugin — Class Design

```
Plugin (ABC)
    └── <concrete plugins>

PluginManager
    └── _plugins: dict[str, PluginInfo]
    └── _hook_registry: HookRegistry

HookRegistry
    └── _hooks: dict[HookPoint, list[HookHandler]]

PluginInfo (dataclass)
PluginMetadata (dataclass)
HookResult (dataclass)
HookPoint (Enum)
```

### HookPoint (Enum)

```python
class HookPoint(Enum):
    ON_RUNTIME_START = "on_runtime_start"
    ON_RUNTIME_STOP = "on_runtime_stop"
    ON_AGENT_INIT = "on_agent_init"
    ON_AGENT_STEP = "on_agent_step"
    ON_AGENT_COMPLETE = "on_agent_complete"
    ON_AGENT_ERROR = "on_agent_error"
    ON_MEMORY_READ = "on_memory_read"
    ON_MEMORY_WRITE = "on_memory_write"
    ON_TOOL_EXECUTE = "on_tool_execute"
    ON_TOOL_RESULT = "on_tool_result"
```

### Plugin (ABC)

```python
class Plugin(ABC):
    """插件抽象基类。"""

    name: str
    version: str
    description: str
    hooks: list[tuple[HookPoint, Callable, int]]  # (hook_point, handler, priority)

    @abstractmethod
    def on_load(self) -> None: ...
    @abstractmethod
    def on_unload(self) -> None: ...

    def on_enable(self) -> None: ...
    def on_disable(self) -> None: ...

    def register_hooks(self) -> None:
        """向 HookRegistry 注册所有 hook。"""
        ...
```

### HookRegistry

```python
class HookRegistry:
    """Hook 注册表。管理插件的 hook 注册和触发。"""

    _hooks: dict[HookPoint, list[_HookEntry]]

    def register(self, hook_point: HookPoint, handler: Callable, priority: int = 0) -> None:
        """注册 hook 处理器。priority 越高越先执行。"""
        ...

    def unregister_all(self, plugin_name: str) -> None:
        """卸载指定插件的所有 hook。"""
        ...

    def trigger(self, hook_point: HookPoint, **context: Any) -> list[HookResult]:
        """触发指定 hook 点。捕获所有异常，不传播。"""
        ...
```

### PluginManager

```python
class PluginManager:
    """插件管理器。"""

    _registry: HookRegistry
    _plugins: dict[str, _PluginState]
    _config: dict

    def __init__(self, config: dict | None = None) -> None: ...

    def discover(self) -> list[PluginMetadata]:
        """通过 entry_points 发现插件。"""
        ...

    def load(self, name: str) -> Plugin: ...
    def unload(self, name: str) -> None: ...
    def enable(self, name: str) -> None: ...
    def disable(self, name: str) -> None: ...
    def list(self) -> list[PluginInfo]: ...
    def get(self, name: str) -> Plugin | None: ...
    def trigger(self, point: HookPoint, **context: Any) -> list[HookResult]: ...

    @property
    def hook_registry(self) -> HookRegistry: ...
```

### PluginInfo (dataclass)

```python
@dataclass
class PluginInfo:
    name: str
    version: str
    description: str
    status: str  # "loaded" | "enabled" | "disabled" | "unloaded" | "failed"

@dataclass
class PluginMetadata:
    name: str
    version: str
    description: str
    entry_point: str

@dataclass
class HookResult:
    hook_point: HookPoint
    plugin_name: str
    success: bool
    data: Any = None
    error: str | None = None
```

---

## 7. Module: gateway — Class Design

```
Backend (ABC)
    └── ClaudeBackend
    └── <future backends>

BackendRegistry
ToolRouter

BackendRequest (dataclass)
BackendResponse (dataclass)
BackendEvent (dataclass)
BackendCapability (Enum)
TokenUsage (dataclass)
```

### BackendCapability (Enum)

```python
class BackendCapability(Enum):
    STREAMING = "streaming"
    TOOL_USE = "tool_use"
    SYSTEM_PROMPT = "system_prompt"
    MULTI_TURN = "multi_turn"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
```

### Backend (ABC)

```python
class Backend(ABC):
    """LLM Backend 抽象基类。"""

    name: str

    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse:
        """同步调用，返回完整响应。"""
        ...

    @abstractmethod
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        """流式调用，逐个产生事件。"""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]:
        """该 Backend 支持的能力集。"""
        ...

    def supports(self, capability: BackendCapability) -> bool:
        """检查是否支持某个能力。"""
        ...
```

### BackendRequest (dataclass)

```python
@dataclass
class BackendRequest:
    messages: list[dict]             # [{"role": str, "content": str}, ...]
    tools: list[ToolDefinition] | None = None
    system_prompt: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7
    stop_sequences: list[str] | None = None
    metadata: dict = field(default_factory=dict)
```

### BackendResponse (dataclass)

```python
@dataclass
class BackendResponse:
    content: str
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage | None = None
    stop_reason: str = "end_turn"
    metadata: dict = field(default_factory=dict)
```

### BackendEvent (dataclass)

```python
@dataclass
class BackendEvent:
    type: str  # "text", "tool_call", "error", "done"
    data: Any
    index: int = 0
```

### TokenUsage (dataclass)

```python
@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens
```

### BackendRegistry

```python
class BackendRegistry:
    """Backend 注册表。"""

    _backends: dict[str, type[Backend]]
    _instances: dict[str, Backend]
    _default: str | None

    def __init__(self) -> None: ...

    def register(self, name: str, backend_cls: type[Backend], *, default: bool = False) -> None: ...
    def get(self, name: str | None = None) -> Backend:
        """获取 Backend 实例。name=None 时返回默认 Backend。"""
        ...
    def list(self) -> list[str]: ...
    def set_default(self, name: str) -> None: ...

    def _create_instance(self, name: str) -> Backend:
        """创建 Backend 实例（传入配置）。"""
        ...
```

### ToolRouter

```python
class ToolRouter:
    """工具路由。"""

    _registry: ToolRegistry
    _config: dict

    def __init__(self, registry: ToolRegistry, config: dict | None = None) -> None: ...

    def execute(self, tool_call: ToolCall, context: ToolContext) -> ToolResult:
        ...
    def definitions(self) -> list[ToolDefinition]:
        ...
    def execute_with_timeout(self, tool_call: ToolCall, context: ToolContext, timeout: int) -> ToolResult:
        ...
```

### ClaudeBackend

```python
class ClaudeBackend(Backend):
    """Claude API Backend 默认实现。"""

    name: str = "claude"
    _api_key: str
    _model: str
    _http_client: httpx.Client | None
    _base_url: str
    _max_retries: int

    def __init__(self, config: dict) -> None:
        # 从 config 读取 api_key, model, base_url, max_retries
        ...

    def invoke(self, request: BackendRequest) -> BackendResponse:
        # 构造 /v1/messages API 请求
        # 处理响应 → BackendResponse
        ...

    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
        # 流式调用 /v1/messages?stream=true
        # 逐个产生 BackendEvent
        ...

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {STREAMING, TOOL_USE, SYSTEM_PROMPT, MULTI_TURN}
```

---

## 8. Module: workspace — Class Design

```
Workspace
    └── _root: Path
```

### Workspace

```python
class Workspace:
    """工作空间管理器。为 Agent 提供隔离的文件系统沙箱。"""

    _root: Path
    _config: dict
    _locks: dict[str, threading.Lock]

    def __init__(self, root: Path | str, config: dict | None = None) -> None: ...

    def prepare(self, agent_id: str) -> Path:
        """创建 Agent 工作目录。返回路径。"""
        ...

    def cleanup(self, agent_id: str, *, keep_output: bool = True) -> None:
        """清理 Agent 工作目录。"""
        ...

    def read(self, agent_id: str, path: str) -> bytes: ...
    def write(self, agent_id: str, path: str, data: bytes) -> Path: ...
    def list(self, agent_id: str, pattern: str = "**/*") -> list[Path]: ...
    def exists(self, agent_id: str, path: str) -> bool: ...
    def delete(self, agent_id: str, path: str) -> None: ...

    def temp(self, agent_id: str) -> Path: ...
    def output(self, agent_id: str) -> Path: ...
    def input_dir(self, agent_id: str) -> Path: ...

    def _agent_path(self, agent_id: str) -> Path: ...
    def _validate_path(self, agent_id: str, path: str) -> Path:
        """校验路径合法性，防止路径穿越。"""
        ...

    def _check_disk_space(self) -> None:
        """检查磁盘剩余空间。"""
        ...
```

---

## 9. Module: agent — Class Design

```
Agent (ABC)
    └── <concrete agents>

AgentState (Enum)
AgentAction (dataclass)
AgentContext (dataclass)
AgentResult (dataclass)
AgentInfo (dataclass)
```

### AgentState (Enum)

```python
class AgentState(Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self in (AgentState.INITIALIZING, AgentState.RUNNING, AgentState.PAUSED)
```

### Agent (ABC)

```python
class Agent(ABC):
    """Agent 抽象基类。所有 Agent 实现必须继承此类。"""

    agent_id: str
    name: str
    description: str
    state: AgentState

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState.IDLE
        ...

    @abstractmethod
    async def initialize(self, context: AgentContext) -> None:
        """初始化 Agent。"""
        ...

    @abstractmethod
    async def step(self, context: AgentContext) -> AgentAction:
        """执行一步。返回下一个动作。"""
        ...

    @abstractmethod
    async def finalize(self, context: AgentContext) -> AgentResult:
        """结束 Agent，返回结果。"""
        ...

    def on_pause(self, reason: str | None = None) -> None:
        """暂停回调。子类可覆盖。"""
        self.state = AgentState.PAUSED

    def on_resume(self, input: str | None = None) -> None:
        """恢复回调。子类可覆盖。"""
        self.state = AgentState.RUNNING
```

### AgentAction (dataclass)

```python
@dataclass
class AgentAction:
    type: Literal["continue", "pause", "complete", "fail"]
    output: str | None = None
    metadata: dict = field(default_factory=dict)
    pause_reason: str | None = None
    error: str | None = None

    @classmethod
    def cont(cls, output: str = "", metadata: dict | None = None) -> AgentAction: ...
    @classmethod
    def pause(cls, reason: str, metadata: dict | None = None) -> AgentAction: ...
    @classmethod
    def complete(cls, output: str = "", metadata: dict | None = None) -> AgentAction: ...
    @classmethod
    def fail(cls, error: str, metadata: dict | None = None) -> AgentAction: ...
```

### AgentContext (dataclass)

```python
@dataclass
class AgentContext:
    agent_id: str
    task: str
    config: dict
    backend: Backend
    memory: MemoryManager
    workspace: Path
    tools: ToolRegistry
    logger: logging.Logger
    max_steps: int = 100
    step_count: int = 0
```

### AgentResult (dataclass)

```python
@dataclass
class AgentResult:
    agent_id: str
    status: AgentState
    output: str = ""
    steps: int = 0
    usage: TokenUsage | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict: ...
```

### AgentInfo (dataclass)

```python
@dataclass
class AgentInfo:
    agent_id: str
    name: str
    status: AgentState
    task: str
    created_at: datetime
    updated_at: datetime
    step_count: int
```

---

## 10. Module: workflow — Class Design

```
Workflow (ABC)
    └── <concrete workflows>

WorkflowEngine

WorkflowStep (dataclass)
WorkflowResult (dataclass)
StepResult (dataclass)
WorkflowStatus (Enum)
StepStatus (Enum)
```

### WorkflowStatus (Enum)

```python
class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### StepStatus (Enum)

```python
class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
```

### WorkflowStep (dataclass)

```python
@dataclass
class WorkflowStep:
    id: str
    name: str
    agent_type: type[Agent]
    input_mapping: dict = field(default_factory=dict)
    output_mapping: dict = field(default_factory=dict)
    next_on_success: str | None = None
    next_on_failure: str | None = None
    max_retries: int = 0
    config: dict = field(default_factory=dict)
```

### Workflow (ABC)

```python
class Workflow(ABC):
    """工作流抽象基类。"""

    name: str
    description: str

    @abstractmethod
    def build(self) -> list[WorkflowStep]: ...

    def validate(self) -> None:
        """校验工作流定义：检查循环引用、agent_type 可实例化。"""
        ...

    def _detect_cycle(self, steps: list[WorkflowStep]) -> bool:
        """检测是否有循环引用。"""
        ...
```

### WorkflowEngine

```python
class WorkflowEngine:
    """工作流执行引擎。"""

    _runtime: Runtime

    def __init__(self, runtime: Runtime) -> None: ...

    async def execute(self, workflow: Workflow, global_context: dict) -> WorkflowResult:
        """执行工作流。"""
        ...

    async def cancel(self, workflow_id: str) -> None: ...
    def get_progress(self, workflow_id: str) -> WorkflowProgress: ...

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict,
    ) -> StepResult: ...
```

### WorkflowResult (dataclass)

```python
@dataclass
class WorkflowResult:
    workflow_id: str
    name: str
    status: WorkflowStatus
    steps: list[StepResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    error: str | None = None
```

### StepResult (dataclass)

```python
@dataclass
class StepResult:
    step_id: str
    name: str
    status: StepStatus
    agent_result: AgentResult | None = None
    error: str | None = None
    retries: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
```

---

## 11. Module: runtime — Class Design

```
Runtime
LifecycleManager
StateManager
Scheduler

RuntimeInfo (dataclass)
```

### Runtime

```python
class Runtime:
    """ZMAI 运行时主类。编排 Agent 完整生命周期。"""

    _config: Config
    _lifecycle: LifecycleManager
    _state: StateManager
    _scheduler: Scheduler
    _memory: MemoryManager
    _workspace: Workspace
    _plugins: PluginManager
    _gateway: BackendRegistry
    _tools: ToolRegistry

    _tasks: dict[str, asyncio.Task]
    _lock: asyncio.Lock
    _started_at: datetime
    _logger: logging.Logger

    def __init__(self, config: Config | None = None) -> None:
        """初始化 Runtime。"""
        ...

    async def run(
        self,
        agent_id: str,
        agent_type: type[Agent],
        task: str,
        backend: str = "claude",
        config: dict | None = None,
    ) -> AgentResult:
        """运行 Agent。"""
        # 参见 SPECIFICATION.md §10 核心执行循环
        ...

    async def pause(self, agent_id: str, reason: str | None = None) -> None: ...
    async def resume(self, agent_id: str, input: str | None = None) -> None: ...
    async def cancel(self, agent_id: str, reason: str | None = None) -> None: ...

    def get_status(self, agent_id: str) -> AgentInfo: ...
    def list_agents(self) -> list[AgentInfo]: ...
    def get_info(self) -> RuntimeInfo: ...
    async def shutdown(self) -> None: ...

    async def _run_agent_loop(
        self,
        agent: Agent,
        context: AgentContext,
    ) -> AgentResult:
        """Agent 主循环（跨步骤）。"""
        ...

    async def _execute_step(
        self,
        agent: Agent,
        context: AgentContext,
        backend: Backend,
    ) -> AgentAction:
        """执行一步：调用模型 → 处理工具 → 更新记忆。"""
        ...
```

### LifecycleManager

```python
class LifecycleManager:
    """Agent 生命周期状态机。"""

    _states: dict[str, AgentState]
    _lock: threading.Lock

    def __init__(self) -> None: ...

    def initialize(self, agent_id: str) -> None:
        """IDLE → INITIALIZING"""
        ...

    def mark_ready(self, agent_id: str) -> None:
        """INITIALIZING → RUNNING"""
        ...

    def pause(self, agent_id: str) -> None:
        """RUNNING → PAUSED"""
        ...

    def resume(self, agent_id: str) -> None:
        """PAUSED → RUNNING"""
        ...

    def complete(self, agent_id: str) -> None:
        """RUNNING → COMPLETED"""
        ...

    def fail(self, agent_id: str) -> None:
        """Any non-terminal → FAILED"""
        ...

    def cancel(self, agent_id: str) -> None:
        """Any non-terminal → CANCELLED"""
        ...

    def get_state(self, agent_id: str) -> AgentState: ...
    def is_terminal(self, agent_id: str) -> bool: ...
    def list(self) -> dict[str, AgentState]: ...
    def remove(self, agent_id: str) -> None: ...

    def validate_transition(self, from_state: AgentState, to_state: AgentState) -> bool:
        """校验状态转换是否合法。"""
        ...

    @property
    def _transitions(self) -> dict[tuple[AgentState, AgentState], bool]:
        """状态转换矩阵。"""
        ...
```

### StateManager

```python
class StateManager:
    """统一 JSON 状态管理器。"""

    _states: dict[str, AgentStateData]
    _persist_path: Path
    _lock: threading.Lock

    def __init__(self, persist_path: Path | str | None = None) -> None: ...

    def get(self, agent_id: str) -> AgentStateData: ...
    def update(self, agent_id: str, **fields: Any) -> None: ...
    def delete(self, agent_id: str) -> None: ...
    def list(self) -> list[AgentStateData]: ...

    def persist(self) -> None:
        """将所有状态持久化到磁盘。"""
        ...

    def restore(self) -> None:
        """从磁盘恢复所有状态。"""
        ...

    def cleanup(self, max_age_days: int = 7) -> None:
        """清理过期的终端状态。"""
        ...


@dataclass
class AgentStateData:
    agent_id: str
    status: AgentState
    task: str
    created_at: datetime
    updated_at: datetime
    step_count: int = 0
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> AgentStateData: ...
```

### Scheduler

```python
class Scheduler:
    """任务调度器。管理 Agent 异步任务。"""

    _tasks: dict[str, asyncio.Task]
    _lock: asyncio.Lock
    _max_concurrent: int

    def __init__(self, max_concurrent: int = 10) -> None: ...

    async def schedule(self, agent_id: str, coro: Coroutine) -> asyncio.Task:
        """调度一个新的 Agent 任务。"""
        ...

    async def cancel(self, agent_id: str) -> None:
        """取消指定 Agent 的任务。"""
        ...

    async def wait(self, agent_id: str) -> AgentResult:
        """等待指定 Agent 完成。"""
        ...

    def is_running(self, agent_id: str) -> bool: ...
    def running_count(self) -> int: ...
    def list_active(self) -> list[str]: ...

    async def shutdown(self) -> None:
        """取消所有正在运行的任务。"""
        ...
```

### RuntimeInfo (dataclass)

```python
@dataclass
class RuntimeInfo:
    running_agents: int
    completed_agents: int
    total_agents: int
    uptime_seconds: float
    memory_usage: dict
    config: dict
```

---

## 12. Module: cli — Class Design

```
CLI 采用函数式设计，非类层次。

Command (命名空间函数):
├── cmd_run(args, runtime)
├── cmd_init(args)
├── cmd_config(args, config)
└── cmd_plugin(args, plugin_mgr)

Formatter (命名空间函数):
├── format_text(result)
├── format_json(result)
└── format_table(items)
```

### CLI 主要类

```python
# CLI 不需要复杂类层次，使用 argparse + 命令函数
# 以下是辅助类：

@dataclass
class CLIConfig:
    """CLI 配置（从 args 解析）。"""
    command: str
    format: str              # "text" | "json"
    verbose: bool
    config_path: str | None
```

### 命令函数签名

```python
def main(argv: list[str] | None = None) -> None:
    """CLI 入口。"""
    ...

def cmd_run(args: argparse.Namespace, runtime: Runtime) -> None: ...
def cmd_init(args: argparse.Namespace) -> None: ...
def cmd_config(args: argparse.Namespace, config: Config) -> None: ...
def cmd_plugin(args: argparse.Namespace, plugin_mgr: PluginManager) -> None: ...
```

---

## 13. 模块间组合关系

```
Runtime
├── has-a → LifecycleManager (组合)
├── has-a → StateManager (组合)
├── has-a → Scheduler (组合)
├── has-a → MemoryManager (组合)
├── has-a → Workspace (组合)
├── has-a → PluginManager (组合)
├── has-a → BackendRegistry (组合)
├── has-a → ToolRegistry (组合)
├── has-a → Config (组合)
│
├── uses-a → Agent (依赖，运行时传入)
├── uses-a → WorkflowEngine (依赖，按需创建)
└── uses-a → Backend (依赖，通过 Gateway 获取)

PluginManager
├── has-a → HookRegistry (组合)
├── has-many → Plugin (聚合，运行时加载)

MemoryManager
├── has-many → WorkingMemory (聚合，按 Agent 创建)
└── has-many → LongTermMemory (聚合，按 Agent 创建)

WorkflowEngine
└── uses-a → Runtime (依赖，委托执行 Agent)
```

---

## 14. 状态机总览

### AgentState 转换矩阵

```
                   ┌──────────────────────────────────────────────────┐
                   │                    TO                             │
                   │   IDLE    INIT    RUNNING  PAUSED  COMPL  FAILED  │
┌─────────┬────────┼──────────────────────────────────────────────────┤
│  F      │ IDLE   │   -       ✓       ✗       ✗      ✗      ✗      │
│  R      │ INIT   │   ✗       -       ✓       ✗      ✗      ✓      │
│  O      │ RUNNING│   ✗       ✗       -       ✓      ✓      ✓      │
│  M      │ PAUSED │   ✗       ✗       ✓       -      ✗      ✗      │
│         │ COMPL  │   ✗       ✗       ✗       ✗      -      ✗      │
│         │ FAILED │   ✗       ✗       ✗       ✗      ✗      -      │
│         │ CANCEL │   ✗       ✗       ✗       ✗      ✗      ✗      │
└─────────┴────────┴──────────────────────────────────────────────────┘

✓ = 合法转换  ✗ = 非法转换  - = 自环（同状态）
```

**说明：**
- `IDLE → INITIALIZING`：唯一入口
- `INITIALIZING → RUNNING | FAILED`：初始化成功或失败
- `RUNNING → PAUSED | COMPLETED | FAILED`：运行时可能暂停、成功、失败
- `PAUSED → RUNNING`：暂停后只能恢复运行
- 任何非终端状态 → `CANCELLED`（用户取消优先于其他转换）
- 终端状态（COMPLETED/FAILED/CANCELLED）不可转换

### WorkflowStatus 状态机

```
PENDING → RUNNING → COMPLETED
                 → FAILED
                 → CANCELLED
```

### PluginStatus 状态机

```
DISCOVERED → LOADED → ENABLED → DISABLED → UNLOADED
                          ↓          ↓
                       FAILED ←─────┘
```

---

## 15. Data Class 索引

| dataclass | Module | 用途 |
|---|---|---|
| `ZMAIError` | errors | 异常基类（frozen） |
| `ToolContext` | tool | 工具执行上下文 |
| `ToolResult` | tool | 工具执行结果 |
| `ToolCall` | tool | 模型请求的工具调用 |
| `ToolDefinition` | tool | 工具定义（给 LLM 的 Schema） |
| `MemoryEntry` | memory | 记忆条目 |
| `MemoryConfig` | memory | Memory 配置 |
| `PluginInfo` | plugin | 插件运行时信息 |
| `PluginMetadata` | plugin | 插件元数据 |
| `HookResult` | plugin | Hook 执行结果 |
| `BackendRequest` | gateway | Backend 请求 |
| `BackendResponse` | gateway | Backend 响应 |
| `BackendEvent` | gateway | 流式事件 |
| `TokenUsage` | gateway | Token 用量 |
| `AgentAction` | agent | Agent 动作指令 |
| `AgentContext` | agent | Agent 执行上下文 |
| `AgentResult` | agent | Agent 执行结果 |
| `AgentInfo` | agent | Agent 摘要信息 |
| `AgentStateData` | runtime | 持久化状态数据 |
| `RuntimeInfo` | runtime | Runtime 运行时信息 |
| `WorkflowStep` | workflow | 工作流步骤定义 |
| `WorkflowResult` | workflow | 工作流执行结果 |
| `StepResult` | workflow | 步骤执行结果 |
| `CLIConfig` | cli | CLI 配置 |

共 **25 个** `@dataclass` 类定义。

---

## 16. 工厂与构建器

### 工厂方法

| 工厂 | 位置 | 用途 |
|---|---|---|
| `AgentAction.cont()` | agent | 构建 "continue" 动作 |
| `AgentAction.pause()` | agent | 构建 "pause" 动作 |
| `AgentAction.complete()` | agent | 构建 "complete" 动作 |
| `AgentAction.fail()` | agent | 构建 "fail" 动作 |

### 构建器模式

无需构建器（Builder）。所有数据类使用 `@dataclass` 的构造函数，配合 `.cont()` 等工厂方法。

---

> **下一份阅读:** [API.md](API.md) — 公共接口详细设计
