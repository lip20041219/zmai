# ZMAI Design

Version: 1.0

> 本文档定义 ZMAI 的设计模式、数据流、配置格式、并发模型、错误处理策略。
>
> **前置阅读:** ../ARCHITECTURE.md, ../SPECIFICATION.md, ../CLASS.md, API.md
> **读者:** 实现者、Reviewer、架构师

---

## 1. Design Patterns

### 1.1 Abstract Base Class (ABC) — 核心模式

ZMAI 的核心扩展点全部使用 ABC 模式：

| ABC | 所在模块 | 用途 | 实现者 |
|---|---|---|---|
| `Memory` | memory | 记忆存储 | WorkingMemory, LongTermMemory |
| `Tool` | tool | 外部工具 | SWE Agent 工具集、社区工具 |
| `Plugin` | plugin | 插件 | MCP 插件、自定义 Hook |
| `Backend` | gateway | LLM 模型 | ClaudeBackend, OpenAIBackend |
| `Agent` | agent | 任务 Agent | SWE Agent、自定义 Agent |
| `Workflow` | workflow | 工作流 | SWE Workflow |
| `ConfigSource` | config | 配置源 | FileSource, EnvSource, CLISource |

**用法规则:**
- 所有 ABC 使用 `abc.ABC` 和 `@abstractmethod`
- ABC 只定义接口契约，不包含实现逻辑
- 每个 ABC 必须有且仅有一个默认实现（在 `impl/` 或同级模块中）
- 第三方实现通过 Plugin 系统注册

### 1.2 Registry Pattern

```
BackendRegistry:  name → Backend 类     (gateway)
ToolRegistry:     name → Tool 实例       (tool)
PluginManager:    name → Plugin          (plugin)
HookRegistry:     HookPoint → [Handler]  (plugin)
```

Registry 特点：
- 所有 Registry 是 **线程安全的**（`threading.Lock` 保护）
- 提供 `register()` / `unregister()` / `get()` / `list()` 标准接口
- 注册重复名称时：log warning + 覆盖（Tool/Backend）/ 抛出 PluginError（Plugin）

### 1.3 Strategy Pattern

Config 配置源使用 Strategy 模式：

```
Config.__init__([FileSource, EnvSource, CLISource])
                              ↓
                 每个 Source 独立实现加载策略
                              ↓
                 按优先级合并为统一配置
```

### 1.4 State Machine Pattern

Agent 生命周期使用显式状态机：

```
LifecycleManager
    ↓
validate_transition(from, to) → bool
    ↓
transition(agent_id, to) → None | raises RuntimeError
```

不允许 goto 式跳转，所有合法转换在 `_transitions` 矩阵中声明。

### 1.5 Observer Pattern (Hook)

Plugin 系统使用 Observer 模式：

```
HookRegistry
    ↓
.register(HookPoint, handler, priority)
    ↓
.trigger(HookPoint, **context) → [HookResult]
    ↓
Runtime 在生命周期点调用 trigger()
    ↓
Plugin 通过 Hook 返回值影响行为
```

### 1.6 Factory Methods

```python
# 取代复杂的 Builder/Factory 类层级
AgentAction.cont(output="...")        # 返回 AgentAction(type="continue")
AgentAction.complete(output="...")    # 返回 AgentAction(type="complete")
AgentAction.fail(error="...")         # 返回 AgentAction(type="fail")
AgentAction.pause(reason="...")       # 返回 AgentAction(type="pause")
```

### 1.7 为什么不用其他模式

| 模式 | 不使用的原因 |
|---|---|
| Singleton | 显式构造，通过依赖注入传递实例 |
| Event Bus | Plugin Hook 已覆盖事件需求，不引入额外复杂度 |
| Dependency Injection Container | Python 的灵活性使 DI Container 收益极低 |
| ORM / Data Mapper | Memory 仅需 JSON 序列化，不需要 ORM |
| MVC | 不是 Web 框架，无 View 层 |

---

## 2. Module Interaction Design

### 2.1 模块间调用协议

所有模块间调用必须遵循以下协议：

```
调用方 → 被调用方.公共方法()
            ↓
          成功 → 返回正常结果
            ↓
          失败 → 抛出 ZMAIError 子类
                    ↓
           调用方捕获 → 包装为本层异常 → 向上传播
```

### 2.2 关键交互序列

#### Agent 运行（简化时序）

```
CLI         Runtime      Lifecycle   Memory    Gateway     Agent     Workspace
 │             │            │          │         │          │          │
 │  run()      │            │          │         │          │          │
 │────────────→│            │          │         │          │          │
 │             │ initialize │          │         │          │          │
 │             │───────────→│          │         │          │          │
 │             │  prepare() │          │         │          │          │
 │             │──────────────────────────────────────────────────────→│
 │             │            │          │         │          │          │
 │             │ init()     │          │         │          │          │
 │             │─────────────────────────────────→         │          │
 │             │            │          │         │  init() │          │
 │             │            │          │         │────────→│          │
 │             │            │          │         │          │          │
 │             │ 开始主循环 │          │         │          │          │
 │             │ loop:      │          │         │          │          │
 │             │            │          │  step() │          │          │
 │             │            │          │────────→│          │          │
 │             │            │          │         │ invoke()│          │
 │             │            │          │         │────────→│          │
 │             │            │          │         │ ←───────│          │
 │             │            │          │  ←──────│          │          │
 │             │            │          │         │          │          │
 │             │  until done│          │         │          │          │
 │             │            │          │         │          │          │
 │             │ finalize()│          │         │          │          │
 │             │─────────────────────────────────→         │          │
 │             │            │          │         │          │          │
 │             │ cleanup()  │          │         │          │          │
 │             │──────────────────────────────────────────────────────→│
 │             │            │          │         │          │          │
 │  AgentResult│            │          │         │          │          │
 │←────────────│            │          │         │          │          │
```

#### Plugin Hook 触发（详细）

```
Runtime 在生命周期点:
    │
    ├── PluginManager.trigger(ON_AGENT_INIT, agent_id=..., config=...)
    │       │
    │       ├── HookRegistry.trigger(ON_AGENT_INIT, ...)
    │       │       │
    │       │       ├── plugin_a.handler(context) → return data_a
    │       │       ├── plugin_b.handler(context) → return data_b
    │       │       └── ...
    │       │
    │       └── return [HookResult, HookResult, ...]
    │
    └── Runtime 合并 Hook 返回值（如配置覆盖）
```

---

## 3. Concurrency Model

### 3.1 asyncio 事件循环

```
Runtime 主线程
    ↓
asyncio.run()  ← 单事件循环
    ↓
┌─────────────────────────────────────┐
│  Event Loop                         │
│                                     │
│  Task[agent_a] ───→ AgentA 主循环   │
│  Task[agent_b] ───→ AgentB 主循环   │
│  Task[agent_c] ───→ AgentC 主循环   │
│                                     │
│  Scheduler → 管理 Task 生命周期     │
└─────────────────────────────────────┘
```

### 3.2 同步 vs 异步

| 操作 | 类型 | 原因 |
|---|---|---|
| Backend.invoke() | 同步（阻塞） | HTTP 请求在 httpx 内部已异步，外部同步接口更简单 |
| Backend.stream() | 同步（Iterator） | 流式处理需要逐个消费事件 |
| Agent.initialize() | async | 涉及 I/O（记忆加载、工具注册） |
| Agent.step() | async | Runtime 需要 await 让步 |
| Agent.finalize() | async | 涉及 I/O（记忆持久化） |
| Tool.execute() | 同步 | 工具执行由 Agent 控制超时 |
| Memory store/read/update | 同步 | 内存操作 + `threading.Lock` 保护 |

### 3.3 线程安全策略

| 模块 | 策略 |
|---|---|
| `ToolRegistry` | `threading.Lock` |
| `WorkingMemory` | `threading.Lock` |
| `LongTermMemory` | `threading.Lock` |
| `LifecycleManager` | `threading.Lock` |
| `StateManager` | `threading.Lock` |
| `PluginManager` | `threading.Lock` |
| `BackendRegistry` | `threading.Lock` |
| `Config` | `threading.Lock` |

所有使用 `threading.Lock` 的位置遵守：
```python
self._lock.acquire()
try:
    # 临界区
finally:
    self._lock.release()
```
或使用 `with self._lock:`。

### 3.4 Runtime 并发

```python
# Runtime 内部使用 asyncio.Task 管理 Agent 并发
class Runtime:
    _tasks: dict[str, asyncio.Task]

    async def run(self, agent_id, agent_type, task, ...):
        # 1. 检查 max_concurrent_agents
        # 2. 创建 Agent 实例
        # 3. task = asyncio.create_task(_run_agent_loop(agent))
        # 4. 注册到 _tasks
        # 5. await task
        # 6. 返回 AgentResult

    async def cancel(self, agent_id):
        # 1. 查找 task
        # 2. task.cancel()
        # 3. 状态转换 → CANCELLED
```

---

## 4. Data Flow Design

### 4.1 配置数据流

```
FileSource(.json) ──┐
EnvSource(ZMAI_*)  ──┤──→ Config.merge() ──→ Config.get("key")
CLISource(--key=v) ──┘
```

配置合并算法：
1. 按 `sources` 列表顺序逐个 `load()`
2. 每个 `load()` 返回扁平 dict（点号分隔键）
3. `dict_1.update(dict_2).update(dict_3)` — 后者覆盖前者
4. 对外提供 `get(key, default)`，自动处理嵌套路径

### 4.2 Backend 数据流

```
Runtime
  │
  ├── Agent.step()
  │     ↓
  ├── Memory.build_context()
  │     ↓
  ├── Gateway:
  │     ├── BackendRequest(messages, tools, ...)
  │     ├── Backend.invoke(request)
  │     │     ↓
  │     │   Claude API → HTTP POST /v1/messages
  │     │     ↓
  │     ├── BackendResponse(content, tool_calls, usage)
  │     │
  │     └── if tool_calls:
  │           ├── ToolRouter.execute(tool_call)
  │           │     ↓
  │           │   Tool.execute(context, params) → ToolResult
  │           │     ↓
  │           └── 将 ToolResult 注入下一轮消息
  │
  ├── Memory.update(agent_id, step_result)
  │
  └── continue loop
```

### 4.3 Memory 数据流

```
write path:
  Runtime → MemoryManager.working(id).store(key, value, ns)
                                            ↓
                                     WorkingMemory._data[ns][key] = entry
                                            ↓
                                   (可选) MemoryManager.persist(id)
                                            ↓
                                   LongTermMemory → JSONL 文件追加写

read path:
  Runtime → MemoryManager.working(id).read(key, ns)
                                            ↓
                                     WorkingMemory._data[ns].get(key)
                                            ↓
                                   缓存命中 → 返回 value
                                            ↓
                                   缓存未命中 → LongTermMemory → 重建缓存
```

### 4.4 状态数据流

```
状态更新:
  LifecycleManager.transition(id, to)
       ↓
  StateManager.update(id, status=to)
       ↓
  (auto_persist=true) → 状态写入文件
       ↓
  <workspace>/<id>/.state/state.json

状态恢复:
  Runtime.__init__ → StateManager.restore()
       ↓
  读取 state.json → 重建 Agent 状态 → 可恢复已暂停的 Agent
```

---

## 5. Configuration Format

### 5.1 默认配置文件: `zmai.json`

```json
{
    "runtime": {
        "max_iterations": 100,
        "timeout": 300,
        "log_level": "INFO",
        "max_concurrent_agents": 10,
        "state_auto_persist": true,
        "state_persist_interval": 30
    },
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
    },
    "workspace": {
        "root": "./workspace",
        "max_file_size": 10485760,
        "max_files": 1000,
        "min_disk_space": 104857600,
        "cleanup_temp": true,
        "cleanup_output": false,
        "cleanup_input": false
    },
    "memory": {
        "working": {
            "max_size": 1000,
            "cleanup_interval": 300
        },
        "long_term": {
            "root_dir": "./zmai_memory",
            "max_file_size": 10485760
        }
    },
    "agent": {
        "default_max_steps": 100,
        "default_timeout": 600
    },
    "plugin": {
        "enabled": [],
        "disabled": [],
        "paths": []
    },
    "workflow": {
        "max_steps": 50,
        "default_max_retries": 2
    }
}
```

### 5.2 环境变量覆盖

```bash
# 使用 ZMAI_ 前缀，__ 作为层级分隔符
export ZMAI_RUNTIME__MAX_ITERATIONS=200
export ZMAI_GATEWAY__BACKENDS__CLAUDE__MODEL="claude-sonnet-4-8"
export ZMAI_WORKSPACE__ROOT="/data/zmai_workspace"
export ZMAI_LOG_LEVEL="DEBUG"           # 特殊映射：顶层 log_level

# 数组类型通过 JSON 字符串
export ZMAI_PLUGIN__ENABLED='["plugin_a", "plugin_b"]'
```

### 5.3 CLI 参数覆盖

```bash
# 使用 --key=value 语法
zmai run swe --runtime.max_iterations=200 --gateway.backends.claude.model=claude-sonnet-4-8
```

### 5.4 配置变量引用

```json
{
    "gateway": {
        "backends": {
            "claude": {
                "api_key": "${ANTHROPIC_API_KEY}",
                "api_base": "${CLAUDE_API_BASE:-https://api.anthropic.com}"
            }
        }
    }
}
```

- `${VAR_NAME}` — 引用环境变量。运行时替换
- `${VAR_NAME:-default}` — 带默认值的引用
- 不支持嵌套引用（`${OTHER_CONFIG_KEY}`）

---

## 6. Data Structures — JSON Schema

### 6.1 Long-term Memory 文件格式 (JSONL)

```
# <memory_root>/<namespace>/<agent_id>.jsonl
# 每行一个 JSON 对象
{"key": "task_history", "value": {"task": "fix bug", "result": "done"}, "namespace": "default", "created_at": "2026-07-14T10:00:00", "updated_at": "2026-07-14T10:30:00", "ttl": null}
{"key": "user_pref", "value": {"theme": "dark"}, "namespace": "preferences", "created_at": "2026-07-14T09:00:00", "updated_at": "2026-07-14T09:00:00", "ttl": null}
```

### 6.2 Agent 状态文件 (JSON)

```json
{
    "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "paused",
    "task": "Implement login feature",
    "created_at": "2026-07-14T10:00:00Z",
    "updated_at": "2026-07-14T10:35:00Z",
    "step_count": 42,
    "error": null,
    "metadata": {
        "agent_type": "swe_agent",
        "backend": "claude",
        "total_tokens": 125000
    }
}
```

### 6.3 BackendRequest JSON

```json
{
    "messages": [
        {"role": "user", "content": "Fix the bug in parser.py"}
    ],
    "system_prompt": "You are an expert software engineer.",
    "tools": [
        {
            "name": "read_file",
            "description": "Read a file from workspace",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        }
    ],
    "max_tokens": 4096,
    "temperature": 0.7
}
```

### 6.4 BackendResponse JSON

```json
{
    "content": "I'll read the parser.py file first.",
    "tool_calls": [
        {
            "id": "toolu_abc123",
            "name": "read_file",
            "params": {"path": "parser.py"}
        }
    ],
    "usage": {
        "input_tokens": 450,
        "output_tokens": 120
    },
    "stop_reason": "tool_use"
}
```

---

## 7. Error Handling Strategy

### 7.1 错误传播规范

```
下层异常 → 上层 catch → 包装 + 附加上下文 → 继续传播

示例:
  BackendError("API rate limited", status_code=429)
      ↓ Runtime catch
  RuntimeError("Backend 'claude' failed: API rate limited")
      ↓ CLI catch
  print("[RUNTIME_ERROR] Backend 'claude' failed: API rate limited")
  sys.exit(1)
```

### 7.2 错误恢复策略

| 异常 | 恢复策略 |
|---|---|
| `BackendError` (网络临时故障) | 自动重试（`max_retries=3`, exponential backoff） |
| `BackendError` (HTTP 4xx) | 不重试，直接传播 |
| `ToolError` (执行超时) | 错误返回给 Backend，由 LLM 决定如何处理 |
| `MemoryError` (磁盘满) | 不可恢复，立即停止 Agent |
| `PluginError` (插件加载失败) | 禁用该插件，继续运行 |
| `PluginError` (Hook 超时) | 跳过该 Plugin，记录 warning，继续 |
| `ConfigError` (配置缺失) | 使用默认值，log warning |
| `AgentError` (step 抛出) | Runtime 捕获 → Agent.finalize() → 返回 failed result |

### 7.3 日志与错误关联

每条错误日志应包含：
```
timestamp  level  module  agent_id  code  message  [extra_fields]
```

格式示例：
```
2026-07-14 10:00:00 ERROR zmai.runtime agent_a1b2 RUNTIME_ERROR Backend 'claude' failed after 3 retries
2026-07-14 10:00:00 WARN  zmai.plugin  -         PLUGIN_ERROR Plugin 'mcp' hook ON_AGENT_STEP timed out (plugin_name=mcp)
```

---

## 8. File Storage Layout

```
<project_root>/
├── zmai.json                          # 配置文件
├── src/zmai/                          # 源码
├── tests/                             # 测试
│
├── workspace/                         # Workspace 根目录 (config: workspace.root)
│   └── <agent_id>/
│       ├── input/                     # Agent 输入
│       ├── output/                    # Agent 输出产物
│       ├── temp/                      # 临时文件
│       └── .state/
│           ├── state.json             # Agent 状态
│           └── session.log            # Agent 执行日志
│
├── zmai_memory/                       # Long-term Memory (config: memory.long_term.root_dir)
│   └── <namespace>/
│       └── <agent_id>.jsonl          # JSON Lines 格式记忆文件
│
└── .zmai/                             # CLI 内部状态
    └── config_history.json            # 配置修改历史
```

---

## 9. Naming Conventions

### 9.1 配置键

```
<module>.<section>.<field>
  runtime.max_iterations
  gateway.backends.claude.model
  memory.working.max_size
```

### 9.2 环境变量

```
ZMAI_<MODULE>__<SECTION>__<FIELD>
  ZMAI_RUNTIME__MAX_ITERATIONS
  ZMAI_GATEWAY__BACKENDS__CLAUDE__MODEL
  ZMAI_MEMORY__WORKING__MAX_SIZE
```

### 9.3 JSON Schema `$id`

```
urn:zmai:schema:<module>:<version>
  urn:zmai:schema:config:1.0
  urn:zmai:schema:state:1.0
  urn:zmai:schema:memory-entry:1.0
```

### 9.4 Hook 点命名

```
on_<noun>_<verb>
  on_agent_init
  on_runtime_start
  on_memory_write
  on_tool_execute
```

---

## 10. Serialization

### 10.1 JSON 类型映射

| Python 类型 | JSON 类型 | 备注 |
|---|---|---|
| `str` | string | — |
| `int` | number | — |
| `float` | number | — |
| `bool` | boolean | — |
| `None` | null | — |
| `dict` | object | keys 必须为 str |
| `list` | array | — |
| `datetime` | string | ISO8601: `2026-07-14T10:00:00Z` |
| `Path` | string | `path.as_posix()` |
| `Enum` | string | `enum.value` |
| `AgentState` | string | `"running"`, `"paused"` |

### 10.2 自定义 JSON 编码器

```python
class ZMAIJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Path):
            return obj.as_posix()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, Exception):
            return str(obj)
        return super().default(obj)
```

---

## 11. Testing Strategy

### 11.1 单元测试

- 每个模块独立测试，mock 外部依赖
- 使用 `pytest` + `unittest.mock`
- 测试文件命名：`tests/test_<module>.py`
- 测试类命名：`Test<ModuleName>`
- 测试方法命名：`test_<behavior>_<scenario>`

### 11.2 Mock 策略

| 真实依赖 | Mock 方式 |
|---|---|
| Claude API (HTTP) | `responses` 或 `httpx.MockTransport` |
| File System (Memory/Workspace) | `tmp_path` fixture |
| Environment Variables | `monkeypatch.setenv` |
| CLI Arguments | `monkeypatch.setattr(sys, "argv", ...)` |
| Plugin Entry Points | `unittest.mock.patch("importlib.metadata.entry_points")` |
| Agent (在 Workflow 中) | `unittest.mock.AsyncMock` |

### 11.3 集成测试

测试核心通路：`Config → Runtime → Gateway(ClaudeMock) → Agent → Workspace → Result`

```
tests/
├── __init__.py
├── conftest.py                      # 共享 fixture
│
├── test_errors.py                   # errors 模块
├── test_config.py                   # config 模块
├── test_tool.py                     # tool 模块
├── test_memory.py                   # memory 模块
├── test_plugin.py                   # plugin 模块
├── test_gateway.py                  # gateway 模块
├── test_workspace.py                # workspace 模块
├── test_agent.py                    # agent 模块
├── test_workflow.py                 # workflow 模块
├── test_runtime.py                  # runtime 模块
├── test_cli.py                      # cli 模块
│
└── integration/
    ├── test_runtime_with_memory.py  # Runtime + Memory 集成
    ├── test_runtime_with_gateway.py # Runtime + Gateway 集成
    ├── test_agent_lifecycle.py      # 完整 Agent 生命周期
    └── test_end_to_end.py           # CLI → Runtime → Agent 端到端
```

---

## 12. Performance Design Considerations

### 12.1 已明确的性能设计

| 设计决策 | 理由 |
|---|---|
| Working Memory 用内存 dict | 读写 O(1)，适合高频访问 |
| Long-term Memory 用 JSONL 追加写 | 写 O(1)，不需要数据库 |
| Runtime 使用 asyncio | 非阻塞 I/O，单线程并发 |
| Backend API 调用使用 httpx | 连接池复用，减少 TCP 开销 |
| Plugin Hook 顺序执行 | 避免 Plugin 间竞态，简化错误处理 |
| 状态持久化异步化 | 不阻塞 Agent 执行主循环 |

### 12.2 明确不做的优化（YAGNI）

| 优化 | 不做原因 |
|---|---|
| Redis/数据库 Memory 后端 | 文件系统在 v1 足够。需要时通过 Plugin 扩展 |
| Memory LRU 淘汰策略 | `max_size` 到达时 log warning 即可。复杂淘汰策略后续版本 |
| Plugin Hook 并行执行 | 顺序执行足够。并行需要处理竞态，复杂度收益比不高 |
| Workflow DAG 并行步骤 | v1 仅支持线性。DAG 并行后续版本 |
| 分布式 Runtime | 远超 v1 范围 |

---

## 13. Package Entry Points

### 13.1 Plugin Discovery

```python
# pyproject.toml (Plugin 包)
[project.entry-points."zmai.plugins"]
my_plugin = "my_package:MyPlugin"
```

PluginManager 通过 `importlib.metadata.entry_points(group="zmai.plugins")` 发现插件。

### 13.2 CLI 入口

```python
# pyproject.toml (ZMAI 核心)
[project.scripts]
zmai = "zmai.cli.main:main"
```

### 13.3 Tool Discovery（未来）

```python
# 预留扩展点 (Phase 4+)
[project.entry-points."zmai.tools"]
my_tool = "my_package:MyTool"
```

---

## 14. Design Decision Records

### DDR-1: dataclass 优先于 TypedDict

**Decision:** 所有数据结构优先使用 `@dataclass`，而非 `TypedDict`。

**Rationale:** `@dataclass` 提供类型安全、默认值、`__init__`、`__repr__`、冻结选项（`frozen=True`）。TypedDict 仅在需要 dict 兼容性时使用（如 JSON Schema 定义）。

### DDR-2: 同步 Backend 接口 + asyncio 包装

**Decision:** `Backend.invoke()` 是同步方法，由 Runtime 在 `asyncio` Task 中通过 `loop.run_in_executor()` 调用。

**Rationale:** Backend 调用本质是 HTTP 请求（I/O 密集型），同步接口更易实现和测试。asyncio 的并发收益来自多 Agent 并发，而非 Backend 调用本身。

### DDR-3: ABC 而非 Protocol

**Decision:** 使用 `abc.ABC` 而非 `typing.Protocol`。

**Rationale:** ABC 提供继承关系检查（`isinstance`）、抽象方法强制实现、默认方法定义。Protocol 适用于 duck typing 场景，但在 ZMAI 中所有扩展点都是显式继承关系。

### DDR-4: 异常代码分层

**Decision:** ZMAI 异常包含 `code` 字段（字符串常量），而非使用异常类名做判断。

**Rationale:** `code` 可用于 JSON 输出、CLI 展示、跨语言通信。异常类名在反射场景下使用不便。

### DDR-5: 无全局单例

**Decision:** 不使用模块级全局变量或 Singleton 模式。所有实例显式构造，通过参数传递。

**Rationale:** 可测试性（mock 替换）、可扩展性（多实例）、避免隐式状态。

---

> **下一阶段:** Phase 2 — Implementation。根据本文档开始编码实现。
