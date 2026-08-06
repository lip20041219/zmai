# ZMAI Public API Reference

Version: 1.0

> 本文档定义 ZMAI 所有模块的公共接口。
>
> **读者:** Agent 开发者、Plugin 开发者、Backend 集成者。
> **约定:** 所有公共 API 在 `__init__.py` 中 re-export。`_` 前缀为私有，外部禁止使用。

---

## 1. Package: `zmai` (顶层)

```python
# src/zmai/__init__.py — 顶层 re-export

# 版本
__version__ = "0.1.0"

# 核心类
from zmai.runtime import Runtime
from zmai.config import Config
from zmai.errors import ZMAIError

# 常用类型
from zmai.agent import Agent, AgentState, AgentResult, AgentAction
from zmai.gateway import Backend, BackendRequest, BackendResponse
from zmai.memory import Memory, MemoryManager
from zmai.tool import Tool, ToolRegistry, ToolContext, ToolResult
```

---

## 2. Module: `zmai.errors`

```python
# 导出:
#   ZMAIError
#   RuntimeError, BackendError, MemoryError, PluginError
#   ConfigError, WorkspaceError, ToolError, AgentError


class ZMAIError(Exception):
    """所有 ZMAI 异常的基类。"""

    code: str       # 机器可读错误码
    message: str    # 人类可读描述

    def __init__(self, code: str, message: str) -> None: ...
    def __str__(self) -> str: ...
    def to_dict(self) -> dict:
        """返回 JSON 可序列化的错误表示。"""
        ...


class RuntimeError(ZMAIError):
    """Runtime 内部错误。"""
    code = "RUNTIME_ERROR"
    def __init__(self, message: str) -> None: ...


class BackendError(ZMAIError):
    """Backend 调用错误。"""
    code = "BACKEND_ERROR"
    def __init__(self, message: str, status_code: int | None = None) -> None: ...


class MemoryError(ZMAIError):
    """Memory 操作错误。"""
    code = "MEMORY_ERROR"
    def __init__(self, message: str) -> None: ...


class PluginError(ZMAIError):
    """Plugin 加载/执行错误。"""
    code = "PLUGIN_ERROR"
    def __init__(self, message: str, plugin_name: str | None = None) -> None: ...


class ConfigError(ZMAIError):
    """配置错误。"""
    code = "CONFIG_ERROR"
    def __init__(self, message: str, key: str | None = None) -> None: ...


class WorkspaceError(ZMAIError):
    """Workspace 错误。"""
    code = "WORKSPACE_ERROR"
    def __init__(self, message: str, path: str | None = None) -> None: ...


class ToolError(ZMAIError):
    """工具执行错误。"""
    code = "TOOL_ERROR"
    def __init__(self, message: str, tool_name: str | None = None) -> None: ...


class AgentError(ZMAIError):
    """Agent 生命周期错误。"""
    code = "AGENT_ERROR"
    def __init__(self, message: str, agent_id: str | None = None) -> None: ...
```

---

## 3. Module: `zmai.config`

```python
from zmai.config import Config, ConfigSource, FileSource, EnvSource, CLISource


class ConfigSource(ABC):
    def load(self) -> dict: ...
    def name(self) -> str: ...


class FileSource(ConfigSource):
    def __init__(self, path: Path | str) -> None: ...
    def load(self) -> dict: ...
    def name(self) -> str: ...


class EnvSource(ConfigSource):
    def __init__(self, prefix: str = "ZMAI_", separator: str = "__") -> None: ...
    def load(self) -> dict: ...
    def name(self) -> str: ...


class CLISource(ConfigSource):
    def __init__(self, args: list[str] | None = None, prefix: str = "--") -> None: ...
    def load(self) -> dict: ...
    def name(self) -> str: ...


class Config:
    """统一配置管理器。"""

    def __init__(self, sources: list[ConfigSource] | None = None) -> None:
        """sources 顺序决定优先级，后者覆盖前者。
        默认: [FileSource("zmai.json"), EnvSource(), CLISource()]
        """
        ...

    def get(self, key: str, default: Any = None) -> Any:
        """读取配置值。key 使用点号分隔路径。
        例: config.get("runtime.timeout", 30)
        """
        ...

    def set(self, key: str, value: Any) -> None:
        """设置配置值。运行时动态覆盖。"""
        ...

    def has(self, key: str) -> bool:
        """检查配置键是否存在。"""
        ...

    def export(self) -> dict:
        """导出所有配置（嵌套 dict 格式）。"""
        ...

    def reload(self) -> None:
        """重新加载所有配置源。"""
        ...
```

---

## 4. Module: `zmai.tool`

```python
from zmai.tool import Tool, ToolRegistry, ToolContext, ToolResult, ToolCall, ToolDefinition


class ToolContext:
    agent_id: str
    workspace_path: Path
    config: dict
    timeout: int = 30
    env: dict = {}
    logger: logging.Logger | None = None


class ToolResult:
    success: bool
    output: str
    error: str | None = None
    metadata: dict = {}
    duration_ms: int = 0

    def to_dict(self) -> dict: ...


class ToolCall:
    id: str
    name: str
    params: dict


class ToolDefinition:
    name: str
    description: str
    input_schema: dict


class Tool(ABC):
    """实现此接口以创建自定义 Tool。"""

    name: str
    description: str
    parameters: dict  # JSON Schema

    @abstractmethod
    def execute(self, context: ToolContext, params: dict) -> ToolResult: ...

    def validate(self, params: dict) -> bool:
        """校验参数是否符合 JSON Schema。"""
        ...

    def to_definition(self) -> ToolDefinition:
        """生成 LLM 可消费的工具定义。"""
        ...


class ToolRegistry:
    def register(self, tool: Tool) -> None:
        """注册工具。同名工具会覆盖（log warning）。"""
        ...

    def unregister(self, name: str) -> None:
        """注销工具。"""
        ...

    def get(self, name: str) -> Tool:
        """获取工具。不存在时抛出 ToolError。"""
        ...

    def list(self) -> list[Tool]:
        """列出所有已注册工具。"""
        ...

    def definitions(self) -> list[ToolDefinition]:
        """获取所有工具的 LLM 定义格式。"""
        ...

    def execute(self, name: str, params: dict, context: ToolContext) -> ToolResult:
        """快捷方法：get + execute 一步完成。"""
        ...
```

---

## 5. Module: `zmai.memory`

```python
from zmai.memory import Memory, WorkingMemory, LongTermMemory, MemoryManager, MemoryEntry


class MemoryEntry:
    key: str
    value: Any
    namespace: str = "default"
    created_at: datetime
    updated_at: datetime
    ttl: int | None = None

    @property
    def is_expired(self) -> bool: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> MemoryEntry: ...


class Memory(ABC):
    @abstractmethod
    def store(self, key: str, value: Any, namespace: str = "default") -> None:
        """存储值。值必须 JSON 可序列化。"""
        ...

    @abstractmethod
    def read(self, key: str, namespace: str = "default") -> Any:
        """读取值。不存在或已过期返回 None。"""
        ...

    @abstractmethod
    def update(self, key: str, value: Any, namespace: str = "default") -> None:
        """更新值。不存在时抛出 MemoryError。"""
        ...

    @abstractmethod
    def delete(self, key: str, namespace: str = "default") -> None:
        """删除值。"""
        ...

    @abstractmethod
    def search(self, query: str, namespace: str = "default") -> list[MemoryEntry]:
        """搜索键名包含 query 的条目。"""
        ...

    @abstractmethod
    def clear(self, namespace: str = "default") -> None:
        """清空指定 namespace 的全部条目。"""
        ...

    @abstractmethod
    def list_namespaces(self) -> list[str]:
        """列出所有 namespace。"""
        ...


class WorkingMemory(Memory):
    def __init__(self, max_size: int = 1000) -> None: ...
    # 继承 Memory 全部方法


class LongTermMemory(Memory):
    def __init__(self, root_dir: Path, max_file_size: int = 10485760) -> None: ...
    # 继承 Memory 全部方法


class MemoryManager:
    def __init__(self, config: MemoryConfig | None = None) -> None: ...

    def working(self, agent_id: str) -> WorkingMemory:
        """获取 Agent 的工作记忆。按需创建。"""
        ...

    def long_term(self, agent_id: str) -> LongTermMemory:
        """获取 Agent 的长期记忆。按需创建。"""
        ...

    def persist(self, agent_id: str) -> None:
        """将 Working Memory 同步到 Long-term Memory。"""
        ...

    def restore(self, agent_id: str) -> None:
        """从 Long-term Memory 恢复到 Working Memory。"""
        ...

    def cleanup(self, agent_id: str) -> None:
        """删除 Agent 的所有记忆数据。"""
        ...

    def exists(self, agent_id: str) -> bool:
        """检查 Agent 是否有已持久化的记忆。"""
        ...
```

---

## 6. Module: `zmai.plugin`

```python
from zmai.plugin import (
    Plugin, PluginManager,
    HookPoint, HookRegistry, HookResult,
    PluginInfo, PluginMetadata,
)


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


class Plugin(ABC):
    """实现此接口以创建自定义 Plugin。"""

    name: str
    version: str
    description: str

    @abstractmethod
    def on_load(self) -> None: ...
    @abstractmethod
    def on_unload(self) -> None: ...

    def on_enable(self) -> None: ...
    def on_disable(self) -> None: ...

    def register_hooks(self) -> None:
        """向 HookRegistry 注册所有 hook。子类覆盖此方法。"""
        ...


class HookRegistry:
    def register(self, hook_point: HookPoint, handler: Callable, priority: int = 0) -> None: ...
    def unregister_all(self, plugin_name: str) -> None: ...

    def trigger(self, hook_point: HookPoint, **context: Any) -> list[HookResult]:
        """触发 hook。异常被捕获为 HookResult，不传播。"""
        ...


class PluginManager:
    def __init__(self, config: dict | None = None) -> None: ...

    def discover(self) -> list[PluginMetadata]:
        """通过 entry_points 发现可用插件。"""
        ...

    def load(self, name: str) -> Plugin:
        """加载插件。已加载则跳过。"""
        ...

    def unload(self, name: str) -> None:
        """卸载插件。"""
        ...

    def enable(self, name: str) -> None:
        """启用插件。注册 hook 到 HookRegistry。"""
        ...

    def disable(self, name: str) -> None:
        """禁用插件。从 HookRegistry 移除 hook。"""
        ...

    def list(self) -> list[PluginInfo]:
        """列出所有插件的状态。"""
        ...

    def get(self, name: str) -> Plugin | None:
        """获取插件实例。"""
        ...

    def trigger(self, point: HookPoint, **context: Any) -> list[HookResult]:
        """触发 hook 点。委托给 HookRegistry。"""
        ...


class PluginInfo:
    name: str
    version: str
    description: str
    status: str  # "loaded" | "enabled" | "disabled" | "unloaded" | "failed"


class PluginMetadata:
    name: str
    version: str
    description: str
    entry_point: str


class HookResult:
    hook_point: HookPoint
    plugin_name: str
    success: bool
    data: Any = None
    error: str | None = None
```

---

## 7. Module: `zmai.gateway`

```python
from zmai.gateway import (
    Backend, BackendRegistry, ToolRouter,
    BackendRequest, BackendResponse, BackendEvent,
    BackendCapability, TokenUsage,
)
from zmai.gateway.backends import ClaudeBackend


class BackendCapability(Enum):
    STREAMING = "streaming"
    TOOL_USE = "tool_use"
    SYSTEM_PROMPT = "system_prompt"
    MULTI_TURN = "multi_turn"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"


class BackendRequest:
    messages: list[dict]
    tools: list[ToolDefinition] | None = None
    system_prompt: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7
    stop_sequences: list[str] | None = None
    metadata: dict = {}


class BackendResponse:
    content: str
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage | None = None
    stop_reason: str = "end_turn"
    metadata: dict = {}


class BackendEvent:
    type: str  # "text" | "tool_call" | "error" | "done"
    data: Any
    index: int = 0


class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    @property
    def total(self) -> int: ...


class Backend(ABC):
    """实现此接口以创建自定义 Backend。"""

    name: str

    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse: ...
    @abstractmethod
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]: ...
    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]: ...
    def supports(self, capability: BackendCapability) -> bool: ...


class BackendRegistry:
    def __init__(self) -> None: ...

    def register(self, name: str, backend_cls: type[Backend], *, default: bool = False) -> None:
        """注册 Backend 类。default=True 设为默认。"""
        ...

    def get(self, name: str | None = None) -> Backend:
        """获取 Backend 实例。name=None 返回默认 Backend。"""
        ...

    def list(self) -> list[str]:
        """列出所有已注册 Backend 名称。"""
        ...

    def set_default(self, name: str) -> None:
        """设置默认 Backend。"""
        ...


class ToolRouter:
    def __init__(self, registry: ToolRegistry, config: dict | None = None) -> None: ...
    def execute(self, tool_call: ToolCall, context: ToolContext) -> ToolResult: ...
    def definitions(self) -> list[ToolDefinition]: ...
    def execute_with_timeout(self, tool_call: ToolCall, context: ToolContext, timeout: int) -> ToolResult: ...


class ClaudeBackend(Backend):
    name = "claude"

    def __init__(self, config: dict) -> None: ...
    def invoke(self, request: BackendRequest) -> BackendResponse: ...
    def stream(self, request: BackendRequest) -> Iterator[BackendEvent]: ...

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {
            BackendCapability.STREAMING,
            BackendCapability.TOOL_USE,
            BackendCapability.SYSTEM_PROMPT,
            BackendCapability.MULTI_TURN,
        }
```

---

## 8. Module: `zmai.workspace`

```python
from zmai.workspace import Workspace


class Workspace:
    def __init__(self, root: Path | str, config: dict | None = None) -> None: ...

    def prepare(self, agent_id: str) -> Path:
        """创建 Agent 工作目录。返回绝对路径。"""
        ...

    def cleanup(self, agent_id: str, *, keep_output: bool = True) -> None:
        """清理 Agent 工作目录。"""
        ...

    def read(self, agent_id: str, path: str) -> bytes:
        """读取文件。path 相对于 agent 工作目录。"""
        ...

    def write(self, agent_id: str, path: str, data: bytes) -> Path:
        """写入文件。自动创建父目录。返回绝对路径。"""
        ...

    def list(self, agent_id: str, pattern: str = "**/*") -> list[Path]:
        """Glob 列出文件。"""
        ...

    def exists(self, agent_id: str, path: str) -> bool: ...
    def delete(self, agent_id: str, path: str) -> None: ...

    def temp(self, agent_id: str) -> Path:
        """返回 temp/ 目录路径。"""
        ...

    def output(self, agent_id: str) -> Path:
        """返回 output/ 目录路径。"""
        ...

    def input_dir(self, agent_id: str) -> Path:
        """返回 input/ 目录路径。"""
        ...
```

---

## 9. Module: `zmai.agent`

```python
from zmai.agent import Agent, AgentState, AgentAction, AgentContext, AgentResult, AgentInfo


class AgentState(Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool: ...
    @property
    def is_active(self) -> bool: ...


class AgentAction:
    """Agent 动作指令。通知 Runtime 下一步行为。"""

    type: Literal["continue", "pause", "complete", "fail"]
    output: str | None = None
    metadata: dict = {}
    pause_reason: str | None = None
    error: str | None = None

    @classmethod
    def cont(cls, output: str = "", metadata: dict | None = None) -> AgentAction:
        """继续执行。"""
        ...

    @classmethod
    def pause(cls, reason: str, metadata: dict | None = None) -> AgentAction:
        """暂停（等待外部输入）。"""
        ...

    @classmethod
    def complete(cls, output: str = "", metadata: dict | None = None) -> AgentAction:
        """成功完成。"""
        ...

    @classmethod
    def fail(cls, error: str, metadata: dict | None = None) -> AgentAction:
        """失败终止。"""
        ...


class AgentContext:
    """Agent 执行上下文。由 Runtime 构造并传入。"""

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


class AgentResult:
    agent_id: str
    status: AgentState
    output: str = ""
    steps: int = 0
    usage: TokenUsage | None = None
    error: str | None = None
    metadata: dict = {}

    def to_dict(self) -> dict: ...


class AgentInfo:
    agent_id: str
    name: str
    status: AgentState
    task: str
    created_at: datetime
    updated_at: datetime
    step_count: int


class Agent(ABC):
    """实现此接口以创建自定义 Agent。"""

    agent_id: str
    name: str
    description: str
    state: AgentState

    def __init__(self, agent_id: str) -> None: ...

    @abstractmethod
    async def initialize(self, context: AgentContext) -> None:
        """初始化。加载记忆、注册工具。"""
        ...

    @abstractmethod
    async def step(self, context: AgentContext) -> AgentAction:
        """执行一步。返回下一步动作。"""
        ...

    @abstractmethod
    async def finalize(self, context: AgentContext) -> AgentResult:
        """结束。保存结果、清理资源。"""
        ...

    def on_pause(self, reason: str | None = None) -> None:
        """暂停回调。"""
        ...

    def on_resume(self, input: str | None = None) -> None:
        """恢复回调。"""
        ...
```

---

## 10. Module: `zmai.workflow`

```python
from zmai.workflow import (
    Workflow, WorkflowEngine,
    WorkflowStep, WorkflowResult, StepResult,
    WorkflowStatus, StepStatus,
)


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStep:
    id: str
    name: str
    agent_type: type[Agent]
    input_mapping: dict = {}
    output_mapping: dict = {}
    next_on_success: str | None = None
    next_on_failure: str | None = None
    max_retries: int = 0
    config: dict = {}


class WorkflowResult:
    workflow_id: str
    name: str
    status: WorkflowStatus
    steps: list[StepResult] = []
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class StepResult:
    step_id: str
    name: str
    status: StepStatus
    agent_result: AgentResult | None = None
    error: str | None = None
    retries: int = 0
    started_at: datetime
    completed_at: datetime | None = None


class Workflow(ABC):
    """实现此接口以创建自定义工作流。"""

    name: str
    description: str

    @abstractmethod
    def build(self) -> list[WorkflowStep]: ...
    def validate(self) -> None: ...


class WorkflowEngine:
    def __init__(self, runtime: Runtime) -> None: ...

    async def execute(self, workflow: Workflow, global_context: dict) -> WorkflowResult:
        """执行工作流。"""
        ...

    async def cancel(self, workflow_id: str) -> None:
        """取消工作流。"""
        ...

    def get_progress(self, workflow_id: str) -> WorkflowProgress:
        """获取执行进度。"""
        ...
```

---

## 11. Module: `zmai.runtime`

```python
from zmai.runtime import Runtime, LifecycleManager, StateManager, Scheduler, RuntimeInfo


class Runtime:
    """ZMAI 运行时主类。"""

    def __init__(self, config: Config | None = None) -> None: ...

    async def run(
        self,
        agent_id: str,
        agent_type: type[Agent],
        task: str,
        backend: str = "claude",
        config: dict | None = None,
    ) -> AgentResult:
        """运行 Agent。等待完成或失败。"""
        ...

    async def pause(self, agent_id: str, reason: str | None = None) -> None:
        """暂停正在运行的 Agent。"""
        ...

    async def resume(self, agent_id: str, input: str | None = None) -> None:
        """恢复暂停的 Agent。"""
        ...

    async def cancel(self, agent_id: str, reason: str | None = None) -> None:
        """取消 Agent。"""
        ...

    def get_status(self, agent_id: str) -> AgentInfo:
        """获取 Agent 状态摘要。"""
        ...

    def list_agents(self) -> list[AgentInfo]:
        """列出所有 Agent。"""
        ...

    def get_info(self) -> RuntimeInfo:
        """获取 Runtime 统计信息。"""
        ...

    async def shutdown(self) -> None:
        """关闭 Runtime。取消所有 Agent。"""
        ...


class LifecycleManager:
    def __init__(self) -> None: ...
    def initialize(self, agent_id: str) -> None: ...
    def mark_ready(self, agent_id: str) -> None: ...
    def pause(self, agent_id: str) -> None: ...
    def resume(self, agent_id: str) -> None: ...
    def complete(self, agent_id: str) -> None: ...
    def fail(self, agent_id: str) -> None: ...
    def cancel(self, agent_id: str) -> None: ...
    def get_state(self, agent_id: str) -> AgentState: ...
    def is_terminal(self, agent_id: str) -> bool: ...
    def list(self) -> dict[str, AgentState]: ...
    def remove(self, agent_id: str) -> None: ...


class StateManager:
    def __init__(self, persist_path: Path | str | None = None) -> None: ...
    def get(self, agent_id: str) -> AgentStateData: ...
    def update(self, agent_id: str, **fields: Any) -> None: ...
    def delete(self, agent_id: str) -> None: ...
    def list(self) -> list[AgentStateData]: ...
    def persist(self) -> None: ...
    def restore(self) -> None: ...
    def cleanup(self, max_age_days: int = 7) -> None: ...


class Scheduler:
    def __init__(self, max_concurrent: int = 10) -> None: ...
    async def schedule(self, agent_id: str, coro: Coroutine) -> asyncio.Task: ...
    async def cancel(self, agent_id: str) -> None: ...
    async def wait(self, agent_id: str) -> AgentResult: ...
    def is_running(self, agent_id: str) -> bool: ...
    def running_count(self) -> int: ...
    def list_active(self) -> list[str]: ...
    async def shutdown(self) -> None: ...


class RuntimeInfo:
    running_agents: int
    completed_agents: int
    total_agents: int
    uptime_seconds: float
    memory_usage: dict
    config: dict
```

---

## 12. Module: `zmai.cli`

```python
# CLI 入口 — 通过 pyproject.toml 的 console_scripts 注册

# 命令行:
#   zmai                       交互式模式（待定）
#   zmai run <agent> [options]
#   zmai init [options]
#   zmai config <subcommand> [options]
#   zmai plugin <subcommand> [options]
#   zmai --version
#   zmai --help

# 参数:
def main(argv: list[str] | None = None) -> None:
    """CLI 入口。解析参数，调用对应命令。"""


# 退出码:
#   0  — 成功
#   1  — 通用错误
#   2  — 配置错误
#   3  — Agent 执行失败
#   4  — Plugin 错误
#   5  — 参数错误


# pyproject.toml 注册:
# [project.scripts]
# zmai = "zmai.cli.main:main"
```

---

## 13. 模块导出总表

| 模块 | 导出符号 | 用途 |
|---|---|---|
| `zmai` | `Runtime`, `Config`, `ZMAIError` | 顶层快速入口 |
| `zmai` | `Agent`, `AgentState`, `AgentResult`, `AgentAction` | 顶层快速入口 |
| `zmai` | `Backend`, `BackendRequest`, `BackendResponse` | 顶层快速入口 |
| `zmai` | `Tool`, `ToolRegistry`, `ToolContext`, `ToolResult` | 顶层快速入口 |
| `zmai` | `Memory`, `MemoryManager` | 顶层快速入口 |
| `zmai.errors` | 8 个异常类 | 异常处理 |
| `zmai.config` | `Config`, `ConfigSource`, `FileSource`, `EnvSource`, `CLISource` | 配置管理 |
| `zmai.tool` | `Tool(ABC)`, `ToolRegistry`, `ToolContext`, `ToolResult`, `ToolCall`, `ToolDefinition` | 工具系统 |
| `zmai.memory` | `Memory(ABC)`, `WorkingMemory`, `LongTermMemory`, `MemoryManager`, `MemoryEntry` | 记忆系统 |
| `zmai.plugin` | `Plugin(ABC)`, `PluginManager`, `HookPoint`, `HookRegistry`, `HookResult`, `PluginInfo`, `PluginMetadata` | 插件系统 |
| `zmai.gateway` | `Backend(ABC)`, `BackendRegistry`, `ToolRouter`, `BackendRequest`, `BackendResponse`, `BackendEvent`, `BackendCapability`, `TokenUsage` | Gateway 层 |
| `zmai.gateway.backends` | `ClaudeBackend` | 默认 Backend |
| `zmai.workspace` | `Workspace` | 沙箱管理 |
| `zmai.agent` | `Agent(ABC)`, `AgentState`, `AgentAction`, `AgentContext`, `AgentResult`, `AgentInfo` | Agent 系统 |
| `zmai.workflow` | `Workflow(ABC)`, `WorkflowEngine`, `WorkflowStep`, `WorkflowResult`, `StepResult`, `WorkflowStatus`, `StepStatus` | 工作流 |
| `zmai.runtime` | `Runtime`, `LifecycleManager`, `StateManager`, `Scheduler`, `RuntimeInfo` | 运行时 |
| `zmai.cli` | `main()` | CLI 入口 |

---

> **下一份阅读:** [DESIGN.md](DESIGN.md) — 设计模式、数据流、并发模型、配置格式
