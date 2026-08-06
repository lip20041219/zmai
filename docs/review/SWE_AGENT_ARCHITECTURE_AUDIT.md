# SWE Agent Architecture Audit

> **日期：** 2026-07-23
> **类型：** 只读代码审计，不修改任何代码
> **目标：** 评估当前 SWE Agent 架构与"可规划、可管理上下文、可验证、可恢复、可基准测试的 SWE Agent"的差距
> **范围：** 13 个维度全面审计

---

## 目录

- [1. 当前架构总览](#1-当前架构总览)
- [2. SWEAgent 生命周期](#2-sweagent-生命周期)
- [3. AgentContext](#3-agentcontext)
- [4. Runtime](#4-runtime)
- [5. step()](#5-step)
- [6. finalize()](#6-finalize)
- [7. Messages/Context 存储](#7-messagescontext-存储)
- [8. Tool 调用](#8-tool-调用)
- [9. Tool 失败处理](#9-tool-失败处理)
- [10. Success 状态判定](#10-success-状态判定)
- [11. max_steps](#11-max_steps)
- [12. Plan Mode 当前实现](#12-plan-mode-当前实现)
- [13. Memory 当前实现](#13-memory-当前实现)
- [14. Gateway / Backend 接口](#14-gateway--backend-接口)
- [15. 差距分析汇总](#15-差距分析汇总)
- [16. 建议的实施路径](#16-建议的实施路径)

---

## 1. 当前架构总览

### 1.1 当前流程

```
CLI (main.py)
  │
  └─ Runtime.run()
       │
       ├─ Preflight Check (preflight.py)
       ├─ LifecycleManager.initialize()    → initializing
       ├─ Workspace.prepare()              → 创建 agent 工作目录
       ├─ PluginRegistry.get(backend)       → 获取 Backend 实例
       ├─ LifecycleManager.mark_ready()     → running
       │
       ├─ _run_agent() — 内联协程
       │    │
       │    ├─ SWEAgent.__init__()
       │    ├─ SWEAgent.initialize()       → 注册 8 个工具
       │    │
       │    ├─ while step_count < max_steps:   ← 主循环
       │    │    │
       │    │    └─ SWEAgent.step()
       │    │         │
       │    │         ├─ 1. 读取 messages (从 metadata)
       │    │         ├─ 2. 构建 BackendRequest
       │    │         │     - messages + tool_defs + system_prompt + memory_context
       │    │         ├─ 3. backend.invoke()    ← 同步 HTTP 调用（带重试）
       │    │         │
       │    │         ├─ 4. 有 tool_calls?
       │    │         │    ├─ 是 → 循环执行每个 Tool
       │    │         │    │        ├─ ToolRegistry.execute()
       │    │         │    │        ├─ 追加结果到 messages
       │    │         │    │        ├─ 统计成功/失败计数
       │    │         │    │        └─ return AgentAction.cont()
       │    │         │    │
       │    │         │    └─ 否 → return AgentAction.complete()
       │    │         │
       │    │         └─ 异常 → return AgentAction.fail()
       │    │
       │    ├─ SWEAgent.finalize()        → 用 tool_calls_ok/fail 判断实际状态
       │    ├─ MemoryManager.persist()
       │    ├─ LifecycleManager.complete()
       │    ├─ Workspace.cleanup()
       │    └─ return {"status": "completed", ...}
       │
       ├─ except CancelledError → cancel 路径
       └─ except Exception → fail 路径
```

### 1.2 当前模块依赖关系

```
zmai/
├── cli/main.py           → Runtime.run()
├── runtime/
│   ├── runtime.py        → Runtime 主类（编排）
│   ├── lifecycle.py      → 状态机（7 状态）
│   ├── state.py          → JSON 持久化（StateManager）
│   ├── scheduler.py      → 异步任务调度
│   └── preflight.py      → Backend API Key 预检查
├── swe/
│   ├── agent.py          → SWEAgent（核心：step + finalize）
│   └── tools.py          → 8 个工具实现
├── agent/
│   └── base.py           → Agent ABC, AgentContext, AgentAction, AgentResult, AgentState
├── gateway/
│   ├── base.py           → Backend ABC, BackendRequest/Response
│   ├── registry.py       → BackendRegistry
│   ├── plugin.py         → PluginRegistry（自动发现 + 配置装配）
│   ├── tool_router.py    → ToolRouter（分发 tool call）
│   ├── mcp.py            → MCPClient
│   ├── errors.py         → HTTP 错误映射 + 响应验证
│   └── backends/
│       ├── claude.py     → ClaudeBackend
│       ├── deepseek.py   → DeepSeekBackend
│       └── gemini.py     → GeminiBackend
├── tool/
│   ├── base.py           → Tool ABC, ToolContext, ToolResult
│   └── registry.py       → ToolRegistry（注册 + 执行 + 超时）
├── memory/
│   ├── base.py           → Memory ABC, MemoryEntry
│   ├── working.py        → WorkingMemory（内存 dict）
│   ├── long_term.py      → LongTermMemory（JSONL 文件）
│   └── manager.py        → MemoryManager（Working + LongTerm 配对）
├── workspace/
│   └── workspace.py      → Workspace（沙箱文件系统）
├── workflow/
│   ├── base.py           → Workflow ABC
│   └── engine.py         → WorkflowEngine（线性/条件分支）
├── config/
│   ├── config.py         → Config（多源合并）
│   └── sources.py        → FileSource, EnvSource, CLISource
├── auth/
│   ├── store.py          → AuthStore（加密凭据存储）
│   ├── resolver.py       → CredentialResolver（统一凭据解析）
│   └── status.py         → CredentialStatus
└── errors/
    └── __init__.py       → 13 种异常类型
```

---

## 2. SWEAgent 生命周期

### 2.1 代码位置

`swe/agent.py` — `class SWEAgent(Agent)`

### 2.2 当前状态

SWEAgent 继承自 `Agent`（`agent/base.py`），后者是抽象基类，定义了三个抽象方法：

```python
class Agent(ABC):
    async def initialize(self, context: AgentContext) -> None: ...
    async def step(self, context: AgentContext) -> AgentAction: ...
    async def finalize(self, context: AgentContext) -> AgentResult: ...
```

SWEAgent 实现了全部三个方法。Agent 实例的**状态由 Runtime 的 LifecycleManager 统一管理**，Agent 自身不维护独立状态。

### 2.3 详细分析

| 方法 | 行为 | 问题 |
|------|------|------|
| `__init__()` | 设置 `agent_id`，状态 `IDLE` | 只做赋值，无副作用 |
| `initialize()` | 注册 8 个工具到 `context.tools` | ✅ 正确，但工具列表硬编码 |
| `step()` | 单轮 LLM 调用 → 工具执行 → 返回 Action | 见第 5 节 |
| `finalize()` | 根据工具成功/失败统计判状态 | ✅ 已修复（对比早期版本总是 COMPLETED） |
| `on_pause()` | 设置 `state = PAUSED` | ⚠️ 接口定义但未被 Runtime 调用 |
| `on_resume()` | 设置 `state = RUNNING` | ⚠️ 接口定义但未被 Runtime 调用 |

### 2.4 差距

| 要求 | 当前 | 目标 |
|------|------|------|
| 阶段划分 | 单循环 | Understand → Plan → Execute → Verify → Report |
| 简单/复杂分级 | ❌ 无 | 简单任务直接执行，复杂任务五阶段 |
| 暂停/恢复 | ⚠️ 接口存在，未使用 | 支持暂停和恢复 |
| 生命周期事件 | ❌ 无 | step begin/end, phase change 等事件 |
| 执行日志 | ❌ 无结构化记录 | 每步记录 StepRecord |

---

## 3. AgentContext

### 3.1 代码位置

`agent/base.py:60-72` — `@dataclass AgentContext`

### 3.2 当前字段

```python
@dataclass
class AgentContext:
    agent_id: str
    task: str
    config: dict[str, Any] = field(default_factory=dict)
    backend: Backend | None = None
    workspace: Path | None = None
    tools: ToolRegistry | None = None
    logger: logging.Logger | None = None
    max_steps: int = 100
    step_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    memory: Any = None  # MemoryManager 实例
```

### 3.3 分析

- `metadata` 是 `dict[str, Any]`，用作**所有非结构化数据的通配容器**：
  - `messages` — 对话消息历史
  - `on_progress` — 进度回调函数
  - `tool_calls_ok` / `tool_calls_fail` — 工具执行统计
  - `output` — 最终输出文本

- `memory` 字段类型为 `Any`，实际传入 `MemoryManager` 实例

### 3.4 差距

| 缺失字段 | 用途 | 为什么需要 |
|----------|------|-----------|
| `execution_phase` | 当前阶段标识 | 区分 understand/plan/execute/verify/report |
| `complexity` | 简单/复杂标记 | 决定跳过还是走全流程 |
| `execution_plan` | 执行计划 | Plan 阶段产出的结构化步骤 |
| `execution_log` | 执行日志 | `list[StepRecord]` 结构化记录 |
| `state` | 当前状态 | Agent 自身状态（当前由 lifecycle 管理） |
| `max_retries` | 阶段级别重试上限 | 区分 tool 重试和 phase 重试 |

---

## 4. Runtime

### 4.1 代码位置

`runtime/runtime.py:81-262` — `class Runtime`

### 4.2 当前职责

Runtime 是 ZMAI 的**顶层编排者**：

```
Runtime.__init__()
  ├─ LifecycleManager       → 状态机
  ├─ StateManager           → JSON 持久化
  ├─ Scheduler              → asyncio 任务调度
  ├─ Workspace              → 文件沙箱
  ├─ ToolRegistry           → 工具注册表（全局共享）
  ├─ PluginRegistry         → Backend 插件注册表
  ├─ ToolRouter             → 工具路由
  └─ MemoryManager          → 记忆管理
```

### 4.3 `Runtime.run()` 方法

```python
async def run(self, agent_id, task, backend, config, on_progress, tool_defs) -> dict:
    1. Preflight Check
    2. LifecycleManager.initialize(agent_id)
    3. StateManager.update(agent_id, "initializing")
    4. Workspace.prepare(agent_id)
    5. PluginRegistry.get(backend)
    6. LifecycleManager.mark_ready(agent_id)
    7. _run_agent()  ← 内联协程
    8. 返回结果
```

### 4.4 `_run_agent()` 内联协程

```python
async def _run_agent() -> dict:
    agent = SWEAgent(agent_id)
    ctx = AgentContext(...)
    await agent.initialize(ctx)
    self._memory.restore(agent_id)          # 长期记忆恢复
    
    while step_count < ctx.max_steps:
        action = await agent.step(ctx)      # ← 主循环
        if action.type == "fail": raise RuntimeError
        if action.type in ("complete", "fail"): break
    
    if action.type not in ("complete", "fail"):
        raise RuntimeError("达到最大执行步数，任务未完成")
    
    result_obj = await agent.finalize(ctx)
    self._memory.persist(agent_id)
    # ... cleanup, return
```

### 4.5 关键设计决策

- **全局共享 ToolRegistry**：Runtime 持有唯一的 `self._tools`，所有 Agent 共用。这意味着：
  - Agent 在 `initialize()` 中注册的工具是全局的
  - 不同 Agent 实例之间工具会互相覆盖（同名警告）
  - 没有 Agent 级别的工具隔离

- **`on_progress` 回调**：通过 `context.metadata["on_progress"]` 注入，用于 CLI 实时输出。

- **`_run_agent()` 是内联协程**：在 `run()` 方法内部定义，通过 `Scheduler.schedule()` 调度。这意味着 `run()` 本身是异步的。

### 4.6 差距

| 缺失能力 | 说明 |
|----------|------|
| ❌ Plan Mode 入口 | 无 `run_plan()` 方法 |
| ❌ Agent 级别暂停/恢复 | `pause()`/`resume()` 调用了 lifecycle 但 agent 的 on_pause 未被调用 |
| ❌ 执行日志持久化 | 无 `execution_log.json` 写入 |
| ❌ 阶段级别控制 | 循环内部无阶段状态检测 |
| ❌ Agent 级别 Tool 隔离 | 全局 ToolRegistry 导致 agent 间工具互相污染 |
| ⚠️ Cancel 时 lifecycle 双重调用 Bug | 见 AGENT_RELIABILITY_AUDIT 第 5 节 |

---

## 5. step()

### 5.1 代码位置

`swe/agent.py:173-292` — `SWEAgent.step()`

### 5.2 当前流程

```
step(context)
  │
  ├─ 1. 检查 context.backend 是否存在
  │     → 不存在 → return AgentAction.fail("无可用 Backend")
  │
  ├─ 2. step_count += 1
  │
  ├─ 3. 读取 messages (从 context.metadata["messages"])
  │     → 空则初始化为 [{"role": "user", "content": task}]
  │
  ├─ 4. 读取工具定义: context.tools.definitions()
  │
  ├─ 5. 注入记忆上下文（最多 10 条）
  │
  ├─ 6. 构建 BackendRequest
  │     - messages + tools + system_prompt + memory_context
  │     - system_prompt = _build_system_prompt() + memory_context
  │
  ├─ 7. backend.invoke(request)  ← 同步 HTTP 调用
  │     ├─ for attempt in range(max_retries):
  │     │   try: response = invoke(request)
  │     │   except BackendError: raise        # 不重试
  │     │   except Exception: sleep(2^attempt)  # 指数退避
  │     └─ 全部失败 → return AgentAction.fail()
  │
  ├─ 8. 有 text content? → messages.append(assistant content)
  │
  ├─ 9. 有 tool_calls?
  │    │
  │    ├─ YES → 循环执行每个 tool
  │    │   ├─ ToolContext(agent_id, workspace_path, config, timeout)
  │    │   ├─ ToolRegistry.execute(name, params, tctx)
  │    │   ├─ 统计 ok/fail
  │    │   ├─ on_progress 回调
  │    │   ├─ 自动保存到 Memory（namespace="tools"）
  │    │   └─ messages.append(tool result)
  │    │
  │    └─ NO → return AgentAction.complete(output=content)
  │
  └─ 10. return AgentAction.cont(output=...)
```

### 5.3 关键发现

#### 5.3.1 同步阻塞的 Backend 调用

```python
response = context.backend.invoke(request)  # 同步！
```

`invoke()` 使用 `urllib.request.urlopen()`，是同步阻塞调用。在当前 asyncio 事件循环中，这会阻塞整个事件循环直到 HTTP 响应返回。虽然不是致命问题（因为一个 Runtime 通常只跑一个 Agent），但在并发场景下会影响其他 Agent。

#### 5.3.2 重试逻辑细节

- 非 `BackendError` 异常（网络波动、503 等）：指数退避重试（1s, 2s, 4s），默认最多 3 次
- `BackendError`（401/400/模型不存在）：直接透传，不重试
- ClaudeBackend 内部还有自己的重试（默认 3 次），总计最多 9 次

#### 5.3.3 System Prompt 构建

```python
_system_prompt = (
    _build_system_prompt(backend)  # 包含 Backend 身份 + 平台指令 + 基础 prompt
    + memory_context               # 追加记忆上下文
)
```

`swe/agent.py` 的 `_build_system_prompt()` 和 `runtime/runtime.py` 的 `_build_system_prompt()` 是**两套独立的 prompt 构建逻辑**。前者用于 SWEAgent，后者未使用。

### 5.4 差距

| 缺失/问题 | 影响 |
|-----------|------|
| ❌ 无阶段感知 | step() 不理解当前处于哪个阶段，所有步骤同等处理 |
| ❌ 无 Plan 注入 | 不读取或遵循 execution_plan |
| ❌ 无 Verify 阶段 | 无客观验证环节 |
| ❌ 无执行日志 | 每一步不记录结构化日志 |
| ⚠️ 同步 HTTP 调用 | 阻塞 asyncio 事件循环 |
| ⚠️ 记忆上下文上限 10 条 | 硬编码，无分类注入策略 |

---

## 6. finalize()

### 6.1 代码位置

`swe/agent.py:294-324` — `SWEAgent.finalize()`

### 6.2 当前逻辑

```python
async def finalize(self, context: AgentContext) -> AgentResult:
    tool_ok = context.metadata.get("tool_calls_ok", 0)
    tool_fail = context.metadata.get("tool_calls_fail", 0)

    if tool_fail > 0 and tool_ok == 0:
        status = AgentState.FAILED     # ← 所有工具都失败 → FAILED
    else:
        status = AgentState.COMPLETED  # ← 其他情况 → COMPLETED

    result = AgentResult(
        agent_id=self.agent_id,
        status=status,
        output=context.metadata.get("output", ""),
        steps=context.step_count,
    )
    return result
```

### 6.3 状态判定矩阵

实际状态由 `context.metadata` 中的 `tool_calls_ok` / `tool_calls_fail` 统计决定：

| tool_calls_ok | tool_calls_fail | finalize 状态 |
|:---:|:---:|:---:|
| 0 | 0 | ✅ COMPLETED（纯对话任务） |
| >0 | 0 | ✅ COMPLETED |
| >0 | >0 | ✅ COMPLETED（部分工具成功） |
| 0 | >0 | ✅ FAILED（全部工具失败） |

### 6.4 剩余问题

即使 `finalize()` 返回 `FAILED`，`Runtime._run_agent()` 中的异常路径处理如下：

```python
# runtime.py:169
result_obj = await agent.finalize(ctx)
# 不管 result_obj.status 是什么，只要没抛异常：
self._lifecycle.complete(agent_id)
self._state.update(agent_id, status="completed")  # ← 强制设置为 completed！
self._workspace.cleanup(agent_id, keep_output=True)
return {"status": "completed", ...}  # ← 始终返回 completed
```

⚠️ **Runtime 忽略 `finalize()` 返回的状态**，始终返回 `"status": "completed"`。`finalize()` 的 FAILED 状态被 Runtime 覆盖。

---

## 7. Messages/Context 存储

### 7.1 存储位置

所有消息存储在 `context.metadata["messages"]`：

```python
# 初始化
context.metadata["messages"] = []

# step() 中
messages = context.metadata.get("messages", [])
# ... 追加 assistant content ...
messages.append({"role": "assistant", "content": response.content})
# ... 追加 tool result ...
messages.append({"role": "user", "content": f"[工具 {name} 结果]\nOK/FAIL: ..."})
context.metadata["messages"] = messages
```

### 7.2 存储策略

- **持久化**：仅在内存中，不写入磁盘
- **结构**：`list[dict]`，每项含 `role` 和 `content`
- **Tool Result 格式**：
  ```python
  {"role": "user", "content": "[工具 <name> 结果]\nOK/FAIL: <output or error>"}
  ```
- **生命周期**：Agent 完成后丢失（除非外部持久化）

### 7.3 问题

| 问题 | 说明 |
|------|------|
| ❌ 无磁盘持久化 | 任务中断或恢复后消息丢失 |
| ❌ 无 Token 计数追踪 | 不知道 context window 使用情况 |
| ❌ 无消息修剪策略 | 长对话时 context 膨胀 |
| ❌ Tool result 格式非结构化 | 用纯文本换行分隔，不便于 LLM 解析 |
| ❌ 无摘要/压缩机制 | 达到 token 限制时只能截断 |
| ⚠️ metadata 作为万能袋 | messages, on_progress, 统计, output 全塞一起 |

---

## 8. Tool 调用

### 8.1 调用链

```
LLM 返回 tool_calls
  → SWEAgent.step() 循环执行
    → ToolRegistry.execute(name, params, context)
      → Tool.validate(params)           ← 参数校验
      → Tool.execute(context, params)   ← 实际执行
        → 返回 ToolResult(success, output, error)
```

### 8.2 工具注册

在 `SWEAgent.initialize()` 中注册 8 个工具：

| 工具 | 文件 | 用途 |
|------|------|------|
| `ReadFileTool` | `swe/tools.py:166` | 读取文件（支持行范围） |
| `WriteFileTool` | `swe/tools.py:241` | 写入/覆盖文件 |
| `EditTool` | `swe/tools.py:300` | 行级编辑（4 种 mode） |
| `GrepTool` | `swe/tools.py:406` | 文本搜索 |
| `ShellTool` | `swe/tools.py:514` | 执行 shell 命令 |
| `GitTool` | `swe/tools.py:564` | 执行 git 命令 |
| `ShowToUserTool` | `swe/tools.py:92` | 打印到终端 |
| `OpenInBrowserTool` | `swe/tools.py:116` | 浏览器打开 HTML |

### 8.3 ToolRouter（Gateway 层）

`ToolRouter`（`gateway/tool_router.py`）提供了另一条工具调用路径，但 **SWEAgent 当前未使用 ToolRouter**，而是直接调用 `context.tools.execute()`。

### 8.4 工具执行安全

| 安全机制 | 实现 | 状态 |
|----------|------|------|
| 路径穿越防护 | `_resolve_tool_path()` 使用 `Path.relative_to()` | ✅ 严格 |
| 文件大小限制 | read_file 10MB, write_file `max_file_size` | ✅ 有 |
| 二进制检测 | 前 8KB 检测 null 字节 | ✅ 有 |
| Shell 命令翻译 | `_translate_cmd()` Linux → Windows | ✅ 有 |
| 用户确认回调 | `on_confirm` 配置项（shell/git 工具） | ✅ 有 |
| Write 失败回退 | Path.write_text → open() 两阶段 | ✅ 有 |
| Edit 恢复 | 编辑失败时恢复原始内容 | ✅ 有 |

### 8.5 差距

| 缺失 | 说明 |
|------|------|
| ❌ 参数 JSON Schema 严格校验 | `Tool.validate()` 只做基础类型检查 |
| ❌ Tool 执行历史 | 无 `list[ToolCallRecord]` 持久化 |
| ❌ Tool 级别重试策略 | 当前重试在 step 级别，无 tool 级别重试 |
| ❌ 并发工具执行 | 顺序执行，无并行优化 |
| ⚠️ Tool 超时机制 | `_execute_with_timeout()` 只有 ToolRegistry 层有 |

---

## 9. Tool 失败处理

### 9.1 当前流程

```
Tool 执行失败
  → ToolRegistry.execute() 返回 ToolResult(success=False, error="...")
  → SWEAgent.step() 循环中：
      step_tool_fail += 1
      context.metadata["tool_calls_fail"] 累计
      messages.append({"role": "user", "content": "FAIL: <error>"})
  → return AgentAction.cont()   ← 关键：继续！
  → LLM 决定下一步
```

### 9.2 Tool 失败 → Agent 如何决定完成

| 场景 | 生成了什么 | AgentAction | 最终状态 |
|------|-----------|-------------|---------|
| Tool 全部成功 | LLM 可选结束 | complete / cont | COMPLETED |
| Tool 部分失败 | LLM 可重试或放弃 | 取决于 LLM | 可能 COMPLETED |
| Tool 全部失败 | LLM 说"无法完成" | complete | **COMPLETED** ⚠️ |
| Backend 异常 | N/A | fail | FAILED（Runtime 捕获） |

### 9.3 核心缺陷

> **Tool 执行失败 → Agent 仍可返回 COMPLETED**

这是当前架构最严重的问题。`AgentAction.complete()` 只表示"LLM 决定结束"，不表示"任务成功完成"。但由于 `Runtime` 和 `finalize()` 的状态判定逻辑：

1. LLM 尝试了 3 个 tool，全部失败
2. LLM 说"我无法完成此任务"
3. LLM 返回无 tool_calls 的文本 → `AgentAction.complete()`
4. `finalize()` 检查 `tool_fail > 0 and tool_ok == 0` → 正确返回 FAILED
5. **但 Runtime 忽略 finalize() 的状态** → 返回 `{"status": "completed"}`

---

## 10. Success 状态判定

### 10.1 状态判定路径

```
            ┌──────────────────────────────────────────┐
            │              SWEAgent.step()               │
            │                                            │
            │  response.tool_calls?                      │
            │    ├─ YES → execute tools → cont()         │
            │    └─ NO  → content? → complete(content)   │
            │              else → complete("")            │
            └──────────────────┬─────────────────────────┘
                               │
          Action.type ─────────┤
                               │
    ┌──────────────────────────┼──────────────────────────┐
    │                          │                          │
    ▼                          ▼                          ▼
"complete"                 "fail"               "continue" / max_steps
    │                          │                          │
    ▼                          ▼                          ▼
finalize()              Runtime raises            Runtime raises
    │                    RuntimeError              RuntimeError
    ▼                          │                          │
Runtime 始终返回               ▼                          ▼
{"status": "completed"}   {"status": "failed"}   {"status": "failed"}
```

### 10.2 成功判定总结

| 实际结果 | AgentAction | finalize() | Runtime 返回 |
|---------|------------|------------|-------------|
| 所有工具成功 | complete | COMPLETED | completed ✅ |
| 部分工具成功 | complete | COMPLETED | completed ✅ |
| 全部工具失败但 LLM 放弃 | complete | **FAILED** ✅ | **completed** ❌ |
| max_steps 耗尽 | - | COMPLETED | completed ❌ |
| Backend 401 | fail (before invoke) | - | failed ✅ |
| Backend Exception | fail (after retry) | - | failed ✅ |
| Tool Exception 传播 | - | - | failed ✅ |
| CancelledError | - | - | cancelled ✅ |

### 10.3 假成功场景

| # | 场景 | Root Cause |
|---|------|-----------|
| 1 | Tool 全部失败，LLM 放弃 | Runtime 忽略 finalize() 的 FAILED |
| 2 | max_steps 耗尽 | Runtime 将 max_steps 退出视为 completed |
| 3 | 纯对话任务无输出 | `complete("")` 被 Runtime 视为成功 |
| 4 | LLM 陷入循环直到 max_steps | 同 2 |

---

## 11. max_steps

### 11.1 配置和默认值

```
配置键: runtime.max_iterations
默认值: 100
来源: Config.get("runtime.max_iterations", 100)
设置位置: runtime.py:138 — ctx.max_steps = int(...)
```

### 11.2 当前行为

```python
# runtime.py_152-166
while step_count < ctx.max_steps:
    action = await agent.step(ctx)
    output = action.output or output
    step_count += 1
    self._state.update(agent_id, step_count=step_count)
    if action.type == "fail" and action.error:
        raise RuntimeError(action.error)
    if action.type in ("complete", "fail"):
        break

# max_steps 耗尽检测
if action is not None and action.type not in ("complete", "fail"):
    raise RuntimeError(f"达到最大执行步数 ({ctx.max_steps})，任务未完成")
```

### 11.3 问题

| 问题 | 严重程度 |
|------|---------|
| max_steps 耗尽导致假 success | 🔴 严重 |
| 无每阶段步数上限 | 🟡 中 |
| 无每工具调用次数上限 | 🟡 中 |
| 无循环检测 | 🟠 中 |

当前唯一防止无限循环的保护是 `max_steps` 硬上限。没有任何基于内容（重复工具调用、重复消息模式）的循环检测。

---

## 12. Plan Mode 当前实现

### 12.1 实现状态

**Plan Mode 尚未实现。**

存在两份详尽的设计文档：
- `docs/design/PLAN_MODE_DESIGN.md` — 状态机扩展、PlanAgent、PlanModeGuard 白名单
- `docs/design/PLAN_RUN_DESIGN.md` — CLI 交互流程（`zmai plan` → `zmai run`）

### 12.2 设计文档中规划的内容

| 组件 | 设计文档 | 代码实现 |
|------|---------|---------|
| `AgentState.PLANNING` | PLAN_MODE_DESIGN §2.1 | ❌ 未实现 |
| 生命周期 `planning` 转换 | PLAN_MODE_DESIGN §2.2 | ❌ 未实现 |
| `PlanAgent` 类 | PLAN_MODE_DESIGN §3 | ❌ 未实现 |
| `PlanModeGuard` 白名单 | PLAN_MODE_DESIGN §4 | ❌ 未实现 |
| `Plan` `PlanStep` 数据类 | PLAN_MODE_DESIGN §5 | ❌ 未实现 |
| `Runtime.run_plan()` | PLAN_MODE_DESIGN §6 | ❌ 未实现 |
| CLI `zmai plan`/`zmai run` | PLAN_RUN_DESIGN §2 | ❌ 未实现 |
| `cli/planner.py` | PLAN_RUN_DESIGN §6.2 | ❌ 未实现 |
| 执行日志 `StepRecord` | EXECUTION_LIFECYCLE_DESIGN §5 | ❌ 未实现 |
| 五阶段状态机 | EXECUTION_LIFECYCLE_DESIGN §2 | ❌ 未实现 |
| 简单/复杂任务分级 | EXECUTION_LIFECYCLE_DESIGN §3 | ❌ 未实现 |
| Verify 阶段 | EXECUTION_LIFECYCLE_DESIGN §4.4 | ❌ 未实现 |

### 12.3 差距分析

| 差距 | 影响 |
|------|------|
| 无 Plan 阶段 → 无结构化执行计划 | Agent 靠 LLM "即兴发挥" |
| 无 Verify 阶段 → 无客观验证 | 无法检测"假成功" |
| 无执行日志 → 无可审计性 | 无法回溯和调试 |
| 无简单/复杂分级 | 简单任务也被过度复杂化 |

---

## 13. Memory 当前实现

### 13.1 代码状态

Memory 系统已实现且功能完整：

| 组件 | 文件 | 状态 | 覆盖率 |
|------|------|------|--------|
| `MemoryEntry` | `memory/base.py` | ✅ | 核心数据类 |
| `WorkingMemory` | `memory/working.py` | ✅ | LRU 淘汰 + TTL 过期 |
| `LongTermMemory` | `memory/long_term.py` | ✅ | JSONL 持久化 |
| `MemoryManager` | `memory/manager.py` | ✅ | Working + LongTerm 配对 |

### 13.2 当前使用方式

**Runtime 级别**：
```python
# Runtime.__init__()
self._memory = MemoryManager()

# _run_agent() — 每次 agent 启动时
restored = self._memory.restore(agent_id)  # LongTerm → Working

# _run_agent() — agent 完成后
self._memory.persist(agent_id)  # Working → LongTerm
```

**SWEAgent 级别**：
```python
# step() — 注入记忆上下文到 system_prompt
wm = context.memory.working(context.agent_id)
mem_items = wm.search("")  # 全部条目，最多 10 条
memory_context = "\n## 记忆上下文\n" + "\n".join(...)

# step() — 每次 tool call 后自动保存
context.memory.working(context.agent_id).store(
    f"tool:{tc.name}", {"success": ..., "output": ..., "error": ...},
    namespace="tools",
)
```

### 13.3 差距

v1 Memory 设计文档（`docs/design/MEMORY_SYSTEM_DESIGN.md`）中规划但未实现的内容：

| 功能 | 设计文档 | 当前状态 |
|------|---------|---------|
| `MemoryStore` 门面类 | §2.4 | ❌ 未实现 |
| `SensitiveDataFilter` | §6 | ❌ 未实现 |
| CLI `zmai memory` 子命令 | §5 | ❌ 未实现 |
| 用户事实命名空间 | §2.1 | ❌ 未实现 |
| 历史任务摘要自动保存 | §3.4 | ❌ 未实现 |
| enable/disable 控制 | §7.4 | ❌ 未实现 |
| 分类记忆注入 | §7.3 | ⚠️ 当前全部搜索，无分类 |
| 记忆条目 source/ tags/ summary | §2.3 | ❌ MemoryEntry 无这些字段 |

---

## 14. Gateway / Backend 接口

### 14.1 架构

```
BackendRegistry (registry.py)
  └── PluginRegistry (plugin.py) ← 实际使用的是这个
        ├── 自动发现插件
        ├── 配置装配 CredentialResolver
        ├── Fallback 机制
        └── 返回 Backend 实例

Backend (base.py) — ABC
  ├── ClaudeBackend (backends/claude.py)
  ├── DeepSeekBackend (backends/deepseek.py)
  └── GeminiBackend (backends/gemini.py)
```

### 14.2 Backend 接口

```python
class Backend(ABC):
    name: str
    model: str        # property
    provider: str     # property
    config: dict      # property
    
    def invoke(request: BackendRequest) -> BackendResponse  # abstract, synchronous
    def stream(request: BackendRequest) -> Iterator[BackendEvent]  # abstract
    
    capabilities: set[BackendCapability]
    def supports(capability) -> bool
```

### 14.3 BackendRequest / BackendResponse

```python
@dataclass
class BackendRequest:
    messages: list[dict]
    tools: list[ToolDefinition] | None
    system_prompt: str | None
    max_tokens: int = 4096
    temperature: float = 0.7
    stop_sequences: list[str] | None
    metadata: dict

@dataclass
class BackendResponse:
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage | None = None
    stop_reason: str = "end_turn"
    metadata: dict
```

### 14.4 当前实现

| Backend | invoke | stream | tool_use | system_prompt | 重试 |
|---------|--------|--------|----------|---------------|------|
| Claude | ✅ | ✅ | ✅ | ✅ | 内置 3 次 |
| DeepSeek | ✅ | ❌ NotImplemented | ✅ | ✅ | 无内置重试 |
| Gemini | ✅ | ❌ | ✅ | ✅ | 无内置重试 |

### 14.5 错误处理

- **HTTP 错误映射**：`gateway/errors.py:friendly_http_error()` 将原始 HTTP 错误映射为用户可读消息
  - 401 → KEY_INVALID
  - 403 → KEY_EXPIRED 或 KEY_INVALID
  - 404 → MODEL_NOT_FOUND
  - 429 → RATE_LIMITED
  - 5xx → SERVER_ERROR

- **响应验证**：`validate_backend_response()` 在 `invoke()` 中调用，拦截：
  - 空响应 `{}`
  - API 层错误 `{"error": ...}`
  - 缺少必要字段

### 14.6 差距

| 缺失 | 说明 |
|------|------|
| ❌ Async invoke | 所有 Backend 的 `invoke()` 都是同步的，阻塞 asyncio 事件循环 |
| ❌ Token 计数/成本追踪 | `TokenUsage` 已定义但未在 Runtime 层汇总 |
| ❌ Streaming 未集成 | 三个 Backend 中只有 Claude 实现了 `stream()`，但 SWEAgent 未使用 |
| ❌ Backend 级别超时 | 依赖 socket timeout，无 asyncio `wait_for()` 包装 |
| ❌ 统一的 Backend 健康检查 | 仅 Auth CLI 层有 `auth test` |

---

## 15. 差距分析汇总

### 15.1 当前 vs 目标架构

**当前架构：**
```
Task → Preflight → LLM → Tool → Final Result
                              ↑
                         单循环无阶段
```

**目标架构：**
```
Task → Preflight → Plan → Execute → Observe → Verify → Recover/Retry → Complete
                    │        │          │           │          │
                    │    Each step    Log tool     Check      If verify
                    │    is recorded  results      results    fails
                    │                              against
               Read-only                          plan
               analysis
```

### 15.2 13 维度差距总表

| # | 维度 | 当前状态 | 目标状态 | 优先级 |
|---|------|---------|---------|--------|
| 1 | **SWEAgent 生命周期** | 单循环无阶段 (initialize → step loop → finalize) | Understand → Plan → Execute → Verify → Report 五阶段 | P0 |
| 2 | **AgentContext** | 10 个字段，metadata 作为万能袋 | +execution_phase, +execution_plan, +execution_log, +complexity | P1 |
| 3 | **Runtime** | 全局 ToolRegistry，无 Plan Mode 入口，cancel 有 double-call bug | +run_plan(), +Agent 级别 Tool 隔离, +执行日志持久化 | P0 |
| 4 | **step()** | 同步 invoke，无阶段感知，无 Plan 注入 | 异步 invoke，阶段感知，Plan-driven execution | P0 |
| 5 | **finalize()** | 已根据 tool_calls_ok/fail 判状态，但 Runtime 忽略它 | Runtime 尊重 finalize() 返回的状态 | P0 |
| 6 | **Messages/Context 存储** | 纯内存 `metadata["messages"]`，无持久化/修剪/摘要 | 结构化的 StepRecord 存储 + Token 计数 + 智能修剪 | P1 |
| 7 | **Tool 调用** | 8 个工具，直接调用 ToolRegistry，无 ToolRouter | Plan Mode 白名单守卫，Tool 执行历史 | P1 |
| 8 | **Tool 失败处理** | 失败后 LLM 自行决策，可导致假 success | Tool 失败 → 重试 → Verify → 真实状态报告 | P0 |
| 9 | **Success 状态判定** | finalize() 正确但 Runtime 覆盖，max_steps 耗尽假 success | 多维度客观判定：工具成功率 + Verify 结果 + Plan 完成度 | P0 |
| 10 | **max_steps** | 默认 100，耗尽假 success，无阶段级别/工具级别上限 | 每阶段上限 + 循环检测 + 耗尽时正确报告 failed | P1 |
| 11 | **Plan Mode** | ❌ 未实现（有完整设计文档） | PlanAgent + PlanModeGuard + 结构化 Plan 产出 | P0 |
| 12 | **Memory** | Working + LongTerm 已完整实现，v1 功能待实现 | MemoryStore + SensitiveDataFilter + CLI 命令 | P2 |
| 13 | **Gateway/Backend** | 三个 Backend，同步 invoke，错误映射完整 | 异步 invoke + Streaming 集成 + Token 成本追踪 | P2 |

### 15.3 优先级定义

| 优先级 | 定义 | 数量 |
|--------|------|------|
| **P0** | 当前架构缺陷，导致假成功或数据丢失 | 6 |
| **P1** | 架构缺失，阻碍可规划/可验证能力的实现 | 4 |
| **P2** | 能力增强，提升可审计/可基准测试水平 | 3 |

---

## 16. 建议的实施路径

> ⚠️ 注意：以下为只读分析结果，不涉及代码修改。仅供后续实施参考。

### Phase 1: 修复假成功（P0）

```
1. Runtime 尊重 finalize() 返回的状态
   - 当前: Runtime 始终返回 {"status": "completed"}
   - 目标: 根据 finalize().status 决定返回状态

2. max_steps 耗尽正确报告 failed
   - 当前: 抛出 RuntimeError → 被 catch → failed
   - 但 finalize() 仍被调用前就抛异常了
   - 确保耗尽时不返回 completed

3. Cancel 时 lifecycle 双重调用 Bug 修复
   - Lifecycle.cancel() 在 terminal 状态不抛异常
```

### Phase 2: Plan Mode + Verify（P0 + P1）

```
4. AgentState.PLANNING + Lifecycle 转换扩展
   - 新增 planning 状态和处理转换

5. PlanModeGuard 工具白名单
   - 在 ToolRouter 或 ToolRegistry 层拦截写操作

6. PlanAgent（只读规划）
   - 独立 Agent 类，只注册只读工具

7. Runtime.run_plan() 方法

8. Verify 阶段（简单实现）
   - step() 中判断是否进入 verify 模式
   - 基本的文件存在/内容验证
```

### Phase 3: 结构化执行日志 + Context 管理（P1）

```
9. StepRecord 数据类 + 执行日志
   - 每步记录结构化日志
   - 任务完成后持久化到 .state/execution_log.json

10. 简单/复杂任务分级
    - 基于关键词/步骤数量的分级函数

11. Messages 存储增强
    - Token 计数追踪
    - 按 token 用量的自动修剪策略
```

### Phase 4: Memory v1 + Async Backend（P2）

```
12. MemoryStore + SensitiveDataFilter + CLI 命令
13. Backend async invoke + Streaming 集成
14. Token 成本追踪
```

---

> **文档结束**
>
> 本次审计覆盖 45 个源文件、13 个架构维度，输出差距项共 13 条（P0: 6, P1: 4, P2: 3）。
>
> 最严重的问题：**Tool 全部失败后 LLM 放弃仍返回 COMPLETED** 和 **Runtime 忽略 finalize() 的状态**。
> 这两个问题的修复可以实现"不谎报成功"。
