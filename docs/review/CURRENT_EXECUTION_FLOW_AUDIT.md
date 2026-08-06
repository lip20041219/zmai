# 当前执行流程审计报告

> 审计日期: 2026-07-26
> 审计范围: 只读，不修改代码
> 审计人员: Claude Code (省 token 模式)

---

## 目录

1. [用户任务 → Runtime.run()](#1-用户任务--runtimerun)
2. [Runtime.run() → Agent](#2-runtimerun--agent)
3. [SWEAgent.step()](#3-sweagentstep)
4. [SWEAgent.finalize()](#4-sweagentfinalize)
5. [AgentContext 数据流](#5-agentcontext-数据流)
6. [Agent 状态管理](#6-agent-状态管理)
7. [Tool 失败传播](#7-tool-失败传播)
8. [max_steps 处理](#8-max_steps-处理)
9. [当前 Plan 实现](#9-当前-plan-实现)
10. [messages/context 管理](#10-messagescontext-管理)
11. [Backend invoke](#11-backend-invoke)
12. [Workspace 路径限制](#12-workspace-路径限制)
13. [CLI 入口](#13-cli-入口)
14. [测试覆盖分析](#14-测试覆盖分析)
15. [未使用代码清单](#15-未使用代码清单)
16. [状态丢失点清单](#16-状态丢失点清单)
17. [错误吞掉位置清单](#17-错误吞掉位置清单)

---

## 1. 用户任务 → Runtime.run()

### 实际执行路径

```
CLI (oneshot/REPL)
  → Runtime.run(agent_id, task, backend, config, on_progress, tool_defs)
    → PreflightCheck.check()                    ← 检查 API Key 等系统状态
    → LifecycleManager.create(agent_id)         ← 状态: created
    → StateManager.update("created")            ← 写入 state.json
    → Workspace.prepare(agent_id)               ← 创建沙箱目录
    → PluginRegistry.get(backend)               ← 装配配置 + 实例化 Backend
    → LifecycleManager.execute(agent_id)        ← 状态: executing
    → _run_agent()                              ← 内部 async 函数
      → SWEAgent(agent_id)
      → AgentContext(...)                        ← 构建上下文
      → SWEAgent.initialize(ctx)                ← 注册 8 个工具
      → MemoryManager.restore(agent_id)         ← Long-term → Working
      → while step_count < max_steps:
          action = SWEAgent.step(ctx)           ← 核心循环
          if action.type == "fail" + error → raise RuntimeError
          if action.type in ("complete", "fail") → break
      → if not terminated → ctx.metadata["timed_out"] = True
      → result_obj = SWEAgent.finalize(ctx)     ← 终态判定
      → MemoryManager.persist(agent_id)         ← Working → Long-term
      → LifecycleManager.complete/fail/timeout
      → StateManager.update(status)
      → return dict
```

### 关键文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `runtime/runtime.py` | 286 | Runtime 主类，run() 编排 |
| `runtime/preflight.py` | 279 | Preflight 检查 |
| `runtime/lifecycle.py` | 158 | 生命周期状态机 |
| `runtime/state.py` | 117 | JSON 状态持久化 |
| `runtime/scheduler.py` | 66 | 并发调度 |

---

## 2. Runtime.run() → Agent

### 实际执行路径

`Runtime.run()` 在 `_run_agent()` 闭包中：

1. 创建 `SWEAgent(agent_id)` — 仅设置 `self.agent_id`、`self.state = CREATED`
2. 创建 `AgentContext(...)` — 填充所有字段
3. `SWEAgent.initialize(ctx)` — 注册 ReadFile/WriteFile/Edit/Grep/Shell/Git/ShowToUser/OpenInBrowser 工具
4. `MemoryManager.restore(agent_id)` — 恢复记忆
5. 循环调用 `SWEAgent.step(ctx)` 直到 max_steps 或 complete/fail

### 问题

- **Runtime.step_count 和 AgentContext.step_count 双份计数**
  - `Runtime.run()` 局部变量 `step_count += 1` (line 160)
  - `SWEAgent.step()` 中 `context.step_count += 1` (agent.py line 200)
  - 两者独立递增，前者用于 loop 边界，后者用于 Agent 状态
  - 行为正确但追踪混乱，finalize 用的是 `context.step_count`

- **_build_system_prompt() 死代码**
  - `runtime.py:37-78` 定义了 `_build_system_prompt()` 但从未调用
  - SWEAgent 有自己的 `_build_system_prompt()` 在 `agent.py:153`
  - Runtime 版多 40 行死代码

- **output 收集脆弱**
  - `output = action.output or output` (runtime.py:159)
  - `action.output` 为 `""`（空字符串，falsy）时保留旧值
  - 可能丢失信息，取决于 AgentAction 的 output 是否为空

---

## 3. SWEAgent.step()

### 实际执行路径

```
SWEAgent.step(context): AgentAction
  ├── 检查 backend 是否存在 → 否则返回 fail
  ├── context.step_count += 1
  ├── 初始化 ContextManager（cm）
  ├── cm.set_task(context.task)
  │
  ├── [可选] 自动规划
  │   ├── 如果 auto_plan=True 且 无已有 plan
  │   ├── generate_plan(task, backend, config)
  │   ├── 存入 context.metadata["execution_plan"]
  │   └── 更新 cm（清除 recent，注入 plan 消息）
  │
  ├── 构建 system_prompt
  │   ├── identity（Backend 信息）
  │   ├── _BASE_SYSTEM_PROMPT（8 工具定义 + 4 阶段流程）
  │   ├── _build_platform_prompt()（Windows/Linux 命令对照）
  │   ├── memory_context（最多 10 条）
  │   └── _PLAN_EXECUTION_PROMPT + plan_summary（如果有 plan）
  │
  ├── 构建 BackendRequest
  │   ├── messages = cm.get_context()
  │   ├── tools = context.tools.definitions()
  │   └── system_prompt, max_tokens, temperature
  │
  ├── Backend 调用（带重试）
  │   ├── for attempt in range(max_retries):
  │   │   try: response = backend.invoke(request) → break
  │   │   except BackendError: raise（不重试）
  │   │   except Exception: 指数退避重试（1s, 2s, 4s…）
  │   └── 全部耗尽 → 返回 AgentAction.fail
  │
  ├── [如有 tool_calls]
  │   ├── for each tool_call:
  │   │   ├── on_progress("tool", tc.name)
  │   │   ├── ToolContext(...)
  │   │   ├── result = context.tools.execute(...)
  │   │   ├── on_progress("result", ...)
  │   │   ├── 自动记忆工具结果
  │   │   └── cm.add_tool_result(...)
  │   ├── 累计 tool_calls_ok/fail
  │   ├── cm.compact()
  │   └── 返回 AgentAction.cont
  │
  ├── [无 tool_calls] Plan 未完成检查
  │   ├── 如果 plan 存在且未完成:
  │   │   ├── replan_count < MAX_REPLANS → 清除旧 plan + 注入 replan 消息
  │   │   └── replan_count >= MAX_REPLANS → 记录警告，继续
  │
  ├── cm.compact()
  │
  ├── _auto_verify(context)
  │   └── 校验失败 → 注入失败消息 + AgentAction.cont
  │
  └── 返回 AgentAction.complete(output=response.content)
```

### 问题

- **同步 HTTP 阻塞事件循环**
  - `backend.invoke(request)` 是同步调用（urllib）
  - SWEAgent 在 `asyncio` 上下文中调用同步代码
  - 长时间请求（如 Claude 复杂工具链）阻塞整个 asyncio 事件循环
  - 导致 `Runtime.cancel()` 无法及时生效

- **Plan step 状态永不更新**
  - `Plan.mark_step()` 在代码中定义但从未被调用
  - Plan 中所有步骤始终为 `"pending"` 状态
  - `plan.is_finished` 总是返回 `False`
  - **后果**: 每次非 tool_call 返回都会触发 replan 逻辑（直到 MAX_REPLANS 耗尽）
  - Plan 功能实际不可用

- **ContextManager 初始化路径混乱**
  - `context.metadata["cm"]` 既在 `initialize()` 中创建，又在 `step()` 中 fallback 创建
  - `initialize()` line 181: `context.metadata["cm"] = ContextManager(...)`
  - `step()` line 201-204: 如果 `cm is None` 又创建一次
  - 两个路径创建方式一致，但说明职责边界模糊

- **Tool 参数 `on_confirm` 回调**
  - `ShellTool.execute()` 和 `GitTool.execute()` 中读取 `context.config.get("on_confirm")`
  - 但 SWEAgent.step() 构造的 `ToolContext` 从未传递 `on_confirm`
  - `_emit_tool_result()` 中读取 `context.config.get("_quiet")` 也从未传递
  - `on_confirm` 和 `_quiet` 永远不会被触发

---

## 4. SWEAgent.finalize()

### 实际执行路径

```python
SWEAgent.finalize(context): AgentResult
  ├── timed_out → AgentState.TIMEOUT
  ├── replan_count >= MAX_REPLANS 且 plan 未完成 → FAILED
  ├── tool_fail > 0 且 tool_ok == 0 → FAILED
  ├── 有验证结果且未通过 → FAILED
  └── 其他 → COMPLETED
```

### 问题

- **`result_obj.output` 永远为空**
  - `<AgentResult>.output` 来自 `context.metadata.get("output", "")` (agent.py:493)
  - `metadata["output"]` 在代码中从未被写入
  - `Runtime.run()` 最终依赖局部变量 `output`（来自 action.output 链）
  - 最终返回 `output or (result_obj.output if result_obj else "")` — 靠 `or` fallback 才拿到正确值

- **优先级逻辑本身正确**但：
  - timed_out 判定在 finalize 前已经在 Runtime 中设置了 `metadata["timed_out"]` — ✓
  - 全工具失败判定仅检查 `tool_ok == 0`，不验证工具是否真正必要 — 无问题
  - 验证结果仅在 `vresult.passed == False` 时降级 FAILED — 但验证结果可能为空（_auto_verify 返回 None）

---

## 5. AgentContext 数据流

### 结构

```python
@dataclass
class AgentContext:
    agent_id: str
    task: str
    config: dict          # 运行配置
    backend: Backend | None
    workspace: Path | None
    tools: ToolRegistry | None
    logger: Logger | None
    max_steps: int = 100
    step_count: int = 0
    metadata: dict        # 通用数据交换区
    memory: MemoryManager
```

### 问题

- **metadata 作为通用数据交换区缺乏类型约束**
  - 承载: `messages`, `on_progress`, `cm`, `execution_plan`, `replan_count`,
    `tool_calls_ok`, `tool_calls_fail`, `verification`, `timed_out`, `output`
  - 任何 key 拼写错误静默失败（没有类型检查或 key 校验）
  - 没有文档列出所有可能的 metadata key

- **`metadata["messages"]` 是陈旧快照**
  - SWEAgent.step() 末尾同步 `context.metadata["messages"] = cm.get_context()`
  - 但下次 step 直接从 cm 读取，不检查 metadata["messages"]
  - metadata["messages"] 仅用于向后兼容，可能是死代码

---

## 6. Agent 状态管理

### 两个独立状态系统

| 系统 | 类 | 用途 |
|------|------|------|
| AgentState | `Agent` 基类枚举 | Agent 逻辑状态 (CREATED/PLANNING/EXECUTING/...) |
| LifecycleManager | `Runtime` 内部状态机 | 生命周期转换 (created/executing/completed/failed/...) |

两者语义一致但互不同步：
- `Agent.state`（在 SWEAgent 实例上）从未被更新
- `LifecycleManager._states` 是真实的运行时状态
- `StateManager` 写入磁盘供审计

### 问题

- **`Agent.state` 从未被更新**
  - SWEAgent 继承 `Agent` 基类，`self.state = AgentState.CREATED` 在 `__init__` 中设置
  - 整个执行过程中 `self.state` 始终是 `CREATED`
  - 代码注释承认："状态由 Runtime 的 LifecycleManager 统一管理，Agent 自身不维护独立状态"
  - 但基类定义了 `state` 属性并文档化了状态转换，容易误导

- **`LifecycleManager.create()` 重复创建检查**
  - `Runtime.run()` line 123: `self._lifecycle.create(agent_id)`
  - 但如果同一 agent_id 多次 run（如 REPL 不同 task），第二次会因 "已存在" 错误
  - REPL 模式用唯一 agent_id 规避了这个问题（`f"repl_{pid}_{counter}"`）
  - _oneshot_run 用 `f"agent_{os.getpid()}"` — 同进程内第二次运行会失败

---

## 7. Tool 失败传播

### 路径

```
SWEAgent.step()
  → context.tools.execute(name, params, tctx)    ← ToolRegistry.execute()
    → tool.validate(params)                       ← 参数校验
    → tool.execute(context, params)               ← 实际执行
    → 返回 ToolResult(success=True/False)
  → SWEAgent 处理 result:
      ├── success → step_tool_ok++
      │   ├── 写入 WorkingMemory
      │   └── cm.add_tool_result(name, success=True, ...)
      └── !success → step_tool_fail++
          ├── 同上
          └── cm.add_tool_result(name, success=False, ...)  ← LLM 可以看到错误
```

### 问题

- **Tool 失败永不终止 Agent**
  - 无论工具失败多少次，SWEAgent.step() 都返回 `AgentAction.cont()`
  - Agent 继续调用 LLM，让 LLM 决定是否重试
  - 仅在 finalize() 中综合判定 "全部失败 → FAILED"
  - **安全工具（路径穿越）失败也与普通工具失败同等对待**，没有即时终止机制

- **ToolRouter 完全未被使用**
  - `Runtime.__init__` 中创建了 `self._tool_router = ToolRouter(self._tools)` (runtime.py:97)
  - 但 SWEAgent.step() 直接调用 `context.tools.execute()`，绕过 ToolRouter
  - ToolRouter 中有超时处理、异常包装等逻辑，全部未生效
  - 实际超时由 `ToolRegistry._execute_with_timeout()` 处理

- **ShellTool/GitTool 的 `on_confirm` 回调永不被调用**
  - tools.py:537: `confirm_fn = context.config.get("on_confirm")`
  - SWEAgent.step() 构造 ToolContext 时只传入 `config=context.config`
  - `context.config` 是用户配置字典，不会包含 on_confirm
  - 此安全确认功能形同虚设

---

## 8. max_steps 处理

### 实际路径

```
Runtime.run() 循环:
  while step_count < ctx.max_steps:
      action = await agent.step(ctx)
      step_count += 1
      ...
  if action.type not in ("complete", "fail"):
      ctx.metadata["timed_out"] = True
  result_obj = await agent.finalize(ctx)
  # finalize 检查 timed_out → TIMEOUT
```

### 问题

- **双重计数**
  - Runtime: `step_count += 1` (局部变量)
  - SWEAgent: `context.step_count += 1` (上下文)
  - 边界条件: Runtime 的 local 变量达到 max_steps 时停止循环
  - SWEAgent 的 context.step_count 可能比 Runtime 的 local 少 1（最后一次 step 后 Runtime 先加 1 再检查）

- **超时后的 finalize 返回 TIMEOUT，但 Runtime 可能还没清理**
  - `ctx.metadata["timed_out"] = True` 设置在 finalize 之前
  - finalize 返回 TIMEOUT → Runtime 调用 `_lifecycle.timeout()`
  - 但 `_lifecycle.execute()` → `_lifecycle.timeout()` 需经 `executing → timeout`
  - 如果 LifecycleManager 状态已被意外改变，转换可能失败

- **无 Wall-clock 超时**
  - max_steps 仅限制迭代次数
  - 没有全局时间限制（wall-clock timeout）
  - 单个 step 可无限阻塞（同步 backend.invoke 无 asyncio timeout）

---

## 9. 当前 Plan 实现

### 架构

```
Planner
  ├── generate_plan(task, backend, config) → Plan
  │   ├── 用 Backend 调用 LLM（特殊 system prompt）
  │   └── 解析返回的 JSON → Plan 对象
  └── parse_plan_response(raw) → Plan
      ├── 支持 ```json``` 包裹
      └── validate_plan_dict() 校验

Plan
  ├── goal, steps[], estimated_complexity, risks
  ├── is_finished → 所有步骤状态在 (completed/failed/skipped)
  ├── mark_step(id, status) → 更新单步状态
  └── completed_steps / failed_steps / current_step

集成（SWEAgent.step() 中）
  1. auto_plan=True 且 无 plan → 调用 generate_plan()
  2. 注入到 system prompt（_PLAN_EXECUTION_PROMPT + format_plan_summary）
  3. 无 tool_calls 返回时检查 plan.is_finished
  4. 未完成 → replan（最多 MAX_REPLANS=3 次）
  5. finalize 中: replan 耗尽且 plan 未完成 → FAILED
```

### 严重问题

- **Plan step 状态永不更新**
  - `Plan.mark_step()` 从未被 SWEAgent 调用
  - 所有 steps 始终处于 `"pending"` 状态
  - `is_finished` 永远返回 `False`
  - **后果**: 只要 `auto_plan=True` 且 LLM 生成过 plan：
    - 每次 LLM 返回纯文本（无 tool_calls）都会触发 replan
    - 3 次 replan 后进入 finalize → FAILED（plan 未完成）
    - Agent **永远无法正常完成**带 plan 的任务

- **Plan 在 tool_calls 路径中被跳过**
  - `if plan and not plan.is_finished` 检查仅在 `response.tool_calls` 为 falsy 时执行 (agent.py:356)
  - 如果 LLM 持续返回 tool_calls，plan 检查永远不会触发
  - LLM 可能无限循环工具调用而不触发完成判定

- **replan_count 和 MAX_REPLANS 的边界**
  - `replan_count` 在 metadata 中，每次 replan +1
  - `MAX_REPLANS=3` 硬编码在 models.py
  - 达到上限后仅记录 warning，不阻止 replan 继续
  - finalize 中检查 `replan_count >= MAX_REPLANS and plan and not plan.is_finished`

- **重新规划会清除旧 plan**
  - agent.py:366: `context.metadata.pop("execution_plan", None)`
  - 下次 step 重新生成 plan（丢失原计划的上下文）

---

## 10. messages/context 管理

### 实际路径

```
ContextManager
  ├── set_task(task)           → _task + 首条 user message
  ├── set_plan(summary)        → _plan（注入 system prompt）
  ├── add_message(role, content) → _recent[]
  ├── add_tool_result(...)     → _tool_results[] + 自动 user 消息
  ├── get_context() → messages[]
  │   顺序: [summary] + [working_memory] + _recent[]
  └── compact() → 旧消息 → 摘要
```

### 问题

- **`metadata["messages"]` 是陈旧快照**
  - agent.py:351/409: `context.metadata["messages"] = cm.get_context()`
  - 但下次 step 从 cm 读取，不读 metadata
  - 仅在 replan 路径中可能读取（agent.py:371）

- **ContextManager 生命周期与 Agent 绑定**
  - 创建于 `SWEAgent.initialize()` 或 `step()` 的 fallback 中
  - 存储在 `context.metadata["cm"]`
  - 跨 step 持续存在 ✓

- **压缩逻辑可能丢失信息**
  - `_extract_key_info()` 中的关键词解析不可靠
  - 仅检查 `write_file`/`edit`/`FAIL:`/`test`/`无法` 等关键词
  - 如果工具输出不包含这些关键词，信息会丢失
  - 压缩后旧工具结果的失败信息可能丢失

---

## 11. Backend invoke

### 架构

```
SWEAgent.step()
  → BackendRequest(messages, tools, system_prompt, ...)
  → backend.invoke(request): BackendResponse        ← 同步阻塞!
      ├── ClaudeBackend: urllib → api.anthropic.com
      ├── DeepSeekBackend: urllib → api.deepseek.com
      └── GeminiBackend: urllib → generativelanguage.googleapis.com

重试机制（两层）:
  1. Backend 内部重试（如 ClaudeBackend: max_retries=3, time.sleep 指数退避）
  2. SWEAgent 外部重试（max_retries=3, asyncio.sleep 指数退避）
  → 最坏情况: 9 次重试
```

### 问题

- **同步 HTTP 阻塞 asyncio 事件循环**
  - 所有 Backend 使用 `urllib`（同步）
  - 在 `asyncio` 上下文（`_run_agent` 是 async）中运行同步 IO
  - 对于长耗时请求（Claude 复杂 multi-turn），事件循环被阻塞
  - `asyncio.CancelledError` 在 `backend.invoke()` 执行期间无法被接收
  - 取消操作（Runtime.cancel）可能无效，直到 Backend 返回

- **两层重试可能导致超长等待**
  - ClaudeBackend 内部: `time.sleep(1), sleep(2), sleep(4)` ≈ 7s
  - SWEAgent 外部: `asyncio.sleep(1), sleep(2), sleep(4)` ≈ 7s
  - 总等待时间: 约 14s（不含请求时间）
  - 如果两者都配置 max_retries=3 → 最多 9 次请求

- **Backend 实例缓存无过期**
  - `PluginRegistry.get()` 缓存实例（`self._instances`），永不失效
  - 配置变更（如 API Key 更新）不影响已缓存的 Backend
  - 用户更新 Key 后需重启进程

- **DeepSeekBackend.stream() 抛出 NotImplementedError**
  - 声明的 BackendCapability 不包含 STREAMING ✓
  - 但如果被误调用，抛出的是通用 `NotImplementedError` 而非 `BackendError`

---

## 12. Workspace 路径限制

### 架构

```
工具路径解析 (_resolve_tool_path in tools.py):
  绝对路径 → 检查是否在 project_path 或 workspace_path 内
  相对路径 → 优先 project_path → fallback workspace_path
  路径穿越 → relative_to() 检测 → 拒绝

Workspace._validate_path():
  target.relative_to(agent_path)     ← 防止逃逸出 agent 沙箱
  agent_path.relative_to(root)       ← 防止 agent_id 逃逸出 root
```

### 结论

- 路径安全性实现正确
- `relative_to()` 使用 pathlib 语义，防止了前缀相似目录名的绕过攻击
- Agent ID 校验拒绝 `/`、`\`、`..`
- 双层验证（工具层 + Workspace 层）

### 注意

- `_resolve_tool_path` 优先使用 `project_path`（启动终端目录），超出后再 fallback 到 `workspace_path`（沙箱）
- 这意味着在 `project_path` 下的文件即使不在 Agent 沙箱内也可被读写
- 这是有意设计（让 Agent 访问项目文件），但需要注意安全边界

---

## 13. CLI 入口

### 实际路径

```
main()
  ├── 子命令: config / auth / doctor / plugin / plan / benchmark
  ├── _build_parser() → argparse
  │   ├── --backend    指定 backend
  │   ├── --json       JSON 输出
  │   ├── --no-color   无颜色
  │   ├── --plan       自动规划
  │   └── --version    /
  │
  ├── _should_show_wizard() → 首次配置向导
  ├── find_project_root() + build_context() → 项目检测
  ├── Config(sources=[...])  → 分层配置
  ├── Runtime(config)
  │
  ├── [有 task] → _oneshot_run(task, runtime, config, args)
  │   └── asyncio.run(runtime.run(...))
  │       ├── on_progress → stderr 进度条
  │       └── 结果 → print_success / print_error → sys.exit(0/3)
  │
  ├── [无 task + tty] → _cmd_interactive(runtime, config, args)
  │   └── REPL 循环: input() → _repl_run() → Runtime.run()
  │
  └── [无 task + 管道] → sys.stdin.read() → _oneshot_run
```

### 问题

- **`--plan` 参数解析可能失效**
  - argparse 配置中 `--plan` 是 store_true
  - `_oneshot_run()` 中读取 `getattr(args, "plan", False)`
  - 但 parser 配置 `--plan` 和 `task`，`--backend` 同级别
  - `zmai --plan "fix bug"` → plan=True, task=["fix bug"] ✓
  - `zmai plan "fix bug"` → 子命令 plan，不会触发 auto_plan

- **`run_agent.py` 是完全独立的程序**
  - `run_agent.py` 不依赖 ZMAI Runtime
  - 直接调用 `claude` CLI 工具
  - 与 ZMAI 核心无关

- **`--json` 输出只有 `_oneshot_run` 生效**
  - REPL 模式 (`_cmd_interactive`) 中忽略 `--json`
  - REPL 中的 `_repl_run` 始终打印到 stderr

---

## 14. 测试覆盖分析

### 现有测试文件

| 测试文件 | 行数 | 覆盖内容 | 状态 |
|----------|------|----------|------|
| `test_runtime.py` | ~534 | Runtime init, run, cancel, retry, failure recovery | ✅ |
| `test_swe.py` | ~286 | 8 工具 + 路径安全 + 集成 | ✅ |
| `test_context.py` | - | ContextManager 压缩 | ✅ |
| `test_verifier.py` | - | 5 种验证策略 | ✅ |
| `test_planning.py` | - | Plan 生成/解析 | ✅ |
| `test_memory.py` | - | 记忆存储 | ✅ |
| `test_workspace.py` | - | 工作区操作 | ✅ |
| `test_gateway.py` | - | Backend 注册/获取 | ✅ |
| `test_cli.py` | - | CLI 命令 | ✅ |
| `test_auth.py` | - | 认证 | ✅ |
| `test_credential_store.py` | - | 凭据存储 | ✅ |
| `test_agent_lifecycle.py` | - | 生命周期状态机 | ✅ |
| `test_workflow.py` | - | Workflow 引擎 | ✅ |
| `test_benchmark.py` | - | Benchmark Runner | ✅ |
| `test_e2e_mock.py` | - | 端到端 Mock 测试 | ✅ |
| `test_integration.py` | - | 集成测试 | ✅ |
| `test_live_api.py` | - | 真实 API（手动运行） | ⚠️ |
| `test_detector.py` | - | 项目检测器 | ✅ |
| `test_detectors.py` | - | 检测器子模块 | ✅ |
| `test_doctor.py` | - | Doctor 诊断 | ✅ |
| `test_prompt.py` | - | Prompt Engine | ✅ |
| `test_tool.py` | - | Tool 基类/Registry | ✅ |
| `test_config.py` | - | 配置 | ✅ |
| `test_swe_regression.py` | - | SWE 回归测试 | ✅ |

### 覆盖缺口

| 未覆盖代码 | 风险 |
|-----------|------|
| `SWEAgent.initialize()` | 工具注册逻辑无独立测试 |
| `SWEAgent.step()` 中 Plan 集成路径 | Plan 功能测试缺失，无法发现 "状态永不更新" 的 bug |
| `SWEAgent._auto_verify()` | 自动验证无直接测试 |
| `SWEAgent.finalize()` 各分支 | timed_out/replan/全失败/验证失败四条路径未分别测试 |
| `Runtime.run()` with `on_progress` | 进度回调仅基础测试 |
| `MemoryManager.persist()` / `restore()` | 记忆持久化无独立测试 |
| `ToolRouter` | 从未使用，无测试 |
| Plan step status 更新 | 无测试（因为代码未调用） |
| `PluginRegistry._build_config()` 的 CredentialResolver 集成 | 配置装配路径 |
| `PluginRegistry._fallback()` | 自动 Fallback 路径 |
| Backend 重试耗尽 + Agent 重试耗尽的组合 | 9 次重试的边界行为 |
| MCPClient | 无测试 |
| `run_agent.py` | 独立工具，与 ZMAI 无关 |

---

## 15. 未使用代码清单

| 文件 | 行 | 代码 | 说明 |
|------|----|------|------|
| `runtime/runtime.py` | 37-78 | `_build_system_prompt()` | 定义未调用；SWEAgent 有自己的版本 |
| `runtime/runtime.py` | 224-226 | `pause()` | 显式 no-op |
| `runtime/runtime.py` | 228-230 | `resume()` | 显式 no-op |
| `runtime/runtime.py` | 97 | `self._tool_router` | 创建从未使用；SWEAgent 直接调用 ToolRegistry |
| `gateway/tool_router.py` | 全集 | `ToolRouter` 类 | 整个类未被任何代码引用 |
| `swe/models.py` | 89-94 | `Plan.mark_step()` | 定义未调用 |
| `agent/base.py` | 146-152 | `Agent.on_pause()` / `on_resume()` | 显式 no-op |
| `gateway/mcp.py` | 全集 | `MCPClient` 类 | 定义未在 Runtime 中集成 |

---

## 16. 状态丢失点清单

| 位置 | 文件:行 | 问题 |
|------|---------|------|
| Runtime 双 step_count | `runtime.py:160` vs `agent.py:200` | 两个独立计数器，混淆追踪 |
| output 收集 | `runtime.py:159` | `action.output or output` — 空字符串导致保留旧值 |
| Agent.state 未更新 | `agent/base.py:131` | SWEAgent.__init__ 设 CREATED，永不更新 |
| metadata["output"] 未写入 | `agent.py:493` | finalize 读取的 key 从未被写入 |
| Plan step status 未更新 | `swe/models.py:89` | mark_step() 从未被调用 |
| metadata["messages"] 陈旧 | `agent.py:351,409` | 快照在 step 末尾同步，下次不读取 |
| ContextManager 压缩 | `context.py:376-421` | 关键词解析不可靠，非结构化信息可能丢失 |
| Plan 清除旧信息 | `agent.py:366` | replan 时完全清除旧 plan，丢失上下文 |

---

## 17. 错误吞掉位置清单

| 位置 | 文件:行 | 异常 | 处理方式 |
|------|---------|------|----------|
| StateManager._flush | `state.py:98-99` | OSError | `pass` 静默忽略 |
| Workspace._read_json | `workspace.py:1036-1040` | JSONDecodeError, OSError | 返回 None |
| Runtime.shutdown | `runtime.py:281-282` | Exception | `pass` 静默忽略 |
| ContextManager.compact | `context.py:312-314` | Exception | 返回 False |
| GrepTool 文件读取 | `tools.py:455-456` | Exception | `pass` 静默忽略 |
| CLI _save_session | `cli/main.py:80-81` | Exception | `pass` 静默忽略 |
| CLI _offer_auth_fix | `cli/main.py:63` | EOFError, KeyboardInterrupt | 返回空字符串 |
| MemoryManager.persist | `memory/manager.py:38-47` | 所有异常 | 无 except，向上传播（可能终端崩溃） |
| Workspace._update_global_manifest | `workspace.py:975-1010` | 间接异常（_read_json 已吞） | 数据可能不一致 |

---

## 总结: 关键发现

### 🔴 严重级别 (必须修复)

1. **Plan step 状态永不更新** — `mark_step()` 未调用 → `is_finished` 始终 False → replan 循环 → 耗尽后 FAILED。`auto_plan=True` 时 Agent **永远无法成功完成任务**。

2. **同步 HTTP 阻塞事件循环** — 所有 Backend 用同步 urllib，在 asyncio 上下文中阻塞事件循环，导致取消操作失效、并发受限。

3. **finalize 输出为空** — `AgentResult.output` 读取 `metadata["output"]`，该 key 从未写入。依赖 Runtime 层的 `or` fallback 才拿到值，属于巧合工作。

### 🟡 中等级别

4. **ToolRouter 死代码** — 实例化在 Runtime 中但从未使用。
5. **Runtime._build_system_prompt() 死代码** — 40 行定义未调用。
6. **`on_confirm` 回调永不生效** — ShellTool/GitTool 的安全确认功能形同虚设。
7. **metadata 作为无类型数据交换区** — 缺乏约束和文档，拼写错误静默失败。
8. **Backend 实例缓存永不刷新** — API Key 变更需重启进程。
9. **Agent.state 从未更新** — 基类状态属性误导。

### 🟢 低级别

10. **StateManager OSError 静默忽略** — 磁盘写入失败不报错。
11. **Path 穿越工具失败不终止** — 安全工具失败与普通工具同等对待。
12. **ContextManager 压缩不可靠** — 关键词解析可能丢失信息。
13. **无 wall-clock 超时** — 仅 steps 次数限制。
14. **重复 Agent ID 错误** — 同进程二次运行同 agent_id 失败。
