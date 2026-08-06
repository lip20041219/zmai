# ZMAI Runtime Review

> 审查日期: 2026-07-17
> 范围: Runtime 生命周期、Task 生命周期、Agent 生命周期、Session 生命周期、
>       Backend Routing、Workspace、Tool 调度
> 原则: 不新增功能，不重构，只检查问题并按 P0/P1/P2 排序

---

## P0 — 严重问题（可能导致崩溃、数据丢失、安全风险）

### [Runtime] `shutdown()` 不完整 — 不做任何清理

**文件:** `src/zmai/runtime/runtime.py:300`

```python
async def shutdown(self) -> None:
    await self._scheduler.shutdown()
```

仅关闭 Scheduler，但：

- **不取消** `self._tasks` 中的活跃 Agent（`_execute_task` 创建的异步任务）
- **不清理** Workspace 临时文件
- **不持久化** 当前状态
- **不等待** 正在运行的 Agent 优雅退出

后果：进程退出时残留工作区文件、僵尸任务、未保存的状态。Runtime 作为主生命周期管理器，
`shutdown` 应当终结所有子资源。

---

### [Runtime] `run()` 绕过 Scheduler — 并发控制失效

**文件:** `src/zmai/runtime/runtime.py:103`

`run()` 直接在 `asyncio.create_task(coro)` 执行 `_execute_task`，然后存入 `self._tasks`。
完全不经过 `self._scheduler.schedule()`。

```python
# runtime.py run() 内部 — 未使用 Scheduler
# 并没有:
#   task = await self._scheduler.schedule(agent_id, ...)
```

- `Runtime.__init__` 创建 Scheduler 时传入了 `max_concurrent=10`，但这个限制**从未生效**
- `RuntimeInfo.running_agents` 使用 `self._scheduler.running_count()`，和实际运行数量不一致
- Scheduler 实质上成了死代码

---

### [Runtime] 取消操作无法传播到正在执行的 Agent

**文件:** `src/zmai/runtime/runtime.py:269`

```python
async def cancel(self, agent_id: str, reason: str | None = None) -> None:
    await self._scheduler.cancel(agent_id)  # 取消 Scheduler 中的任务
    self._lifecycle.cancel(agent_id)
    self._state.update(agent_id, status="cancelled")
```

由于 `run()` 创建的任务不在 Scheduler 中，`scheduler.cancel()` 不会实际取消任务。
Agent 继续执行直到自然完成，但状态已被标记为 cancelled — 状态不同步。

---

### [Runtime] `_tasks` 字典内存泄漏

**文件:** `src/zmai/runtime/runtime.py:99`

```python
self._tasks: dict[str, asyncio.Task[Any]] = {}
```

任务完成或异常后，没有从 `_tasks` 中移除条目。每个 `run()` 调用都在 `_tasks` 中留下永久引用。
长期运行的 Runtime 会持续累积已完成的 Task 对象，阻止 GC 回收。

---

### [Workspace] `read()` 未加锁 — 并发读写竞争

**文件:** `src/zmai/workspace/workspace.py:527`

`write()` 获取 Agent 级线程锁，但 `read()`、`read_text()`、`exists()`、`list()` 等读取操作
完全不获取任何锁。在 asyncio 并发环境中，读写可能交错：

```
Agent A write(path, data)  → 获取锁 → 写入 → 释放锁
Agent B read(path)          → (未锁) 读到不完整内容
```

`_validate_path()` 操作是幂等的且无副作用，不会触发竞争 — 但不是因为设计如此，而是恰好如此。

---

### [Workspace] 全局 manifest 更新 O(n) 每次文件写入

**文件:** `src/zmai/workspace/workspace.py:946`

每次 `write()` 成功后，`_update_manifest()` → `_update_global_manifest()` 会遍历**所有 Agent**
来重建全局 manifest：

```python
def _update_global_manifest(self):
    for agent_id in self.list_agents():     # O(n) 遍历所有 agent
        manifest = self.get_manifest(agent_id)
        ...
```

如果系统有数百个 Agent 工作区，每次写入单个文件都要全量扫描磁盘。应增量更新。

---

## P1 — 重要问题（设计缺陷、边界错误、潜在故障）

### [Agent Lifecycle] 双重状态跟踪 — LifecycleManager 与 Agent 状态可能发散

**文件:** `src/zmai/runtime/lifecycle.py` 和 `src/zmai/agent/base.py`

Runtime 使用 `LifecycleManager`（`runtime/lifecycle.py`）跟踪 Agent 生命周期状态。
而 `SWEAgent` 自身维护一个独立的 `AgentState` 枚举（`agent/base.py:17`）。

```
Runtime.run():
    self._lifecycle.initialize(agent_id)    # → LifecycleManager 状态机
    agent = SWEAgent(agent_id)
    await agent.initialize(ctx)              # → Agent.state = INITIALIZING
```

两个状态没有同步机制：

- `LifecycleManager` 的转换表是自定义 dict 字典
- `Agent.state` 使用独立的 `AgentState` 枚举（定义了 `is_terminal` 和 `is_active`）
- `LifecycleManager` 是线程安全的（`threading.Lock`），但 `Agent.state` 没有保护
- `LifecycleManager.remove()` 删除状态条目后，`get_state()` 返回 `"idle"`，但 Agent 实例可能还在

---

### [Agent Lifecycle] `LifecycleManager` 操作前未验证 Agent 存在

**文件:** `src/zmai/runtime/lifecycle.py:35`

```python
def _transition(self, agent_id: str, to: str) -> None:
    with self._lock:
        cur = self._states.get(agent_id, "idle")
        if cur in _TERMINAL:
            raise RuntimeError(...)
        if not _TRANSITIONS.get((cur, to)):
            raise RuntimeError(...)
```

未注册的 agent_id 返回 `"idle"` 而不是报错，任何状态转换对不存在 agent 都"可以"从 idle 开始。
这掩盖了调用方的逻辑错误。

---

### [Backend] `DeepSeekBackend.stream()` 是桩方法

**文件:** `src/zmai/gateway/backends/deepseek.py:146`

```python
def stream(self, request: BackendRequest) -> Iterator[BackendEvent]:
    yield BackendEvent(type="text", data="stream not implemented")
    yield BackendEvent(type="done", data="", index=1)
```

虽然 `capabilities` 未声明 `STREAMING`，所以 `Runtime.run()` 不会调用它（走 `invoke` 分支），
但：

- 如果子类或手动强制使用流模式，会收到"stream not implemented"文本而非错误
- 没有显式抛出 `NotImplementedError` 来提醒开发者
- 违反"非受检路径应显式失败"的原则

---

### [Backend] 实例缓存不支持刷新

**文件:** `src/zmai/gateway/registry.py:66`

```python
def get(self, name: str | None = None) -> Backend:
    ...
    if resolved not in self._instances:
        cls = self._backends[resolved]
        config = self._configs.get(resolved, {})
        self._instances[resolved] = cls(config=config)
    return self._instances[resolved]
```

Backend 实例在首次 `get()` 时创建后永久缓存。重新注册同名 Backend 时虽然清除了旧实例
（`register()` 第 59 行会 `del self._instances[name]`），但调用 `register()` 才能刷新。
没有公开的刷新机制，运行时修改配置后无法热更新 Backend。

---

### [ToolRouter] `execute()` 复制 Context 时丢失 `project_path`

**文件:** `src/zmai/gateway/tool_router.py:57`

```python
exec_context = ToolContext(
    agent_id=context.agent_id,
    workspace_path=context.workspace_path,
    project_path=context.project_path,  # ← 已传入！
    config={...},
    timeout=effective_timeout,
    env=context.env,
    logger=context.logger,
)
```

`execute_with_timeout()` (第 108 行) 同样复制 Context 但**完全没传** `project_path`：

```python
ctx = ToolContext(
    agent_id=context.agent_id,
    workspace_path=context.workspace_path,
    config=context.config,
    timeout=timeout,
    env=context.env,
    logger=context.logger,
)
```

`project_path` 在工具路径解析中用于确定"启动终端目录"，丢失它后工具可能错误地回退到
`workspace_path`。

---

### [Workspace] `cleanup()` 重建目录掩盖失败

**文件:** `src/zmai/workspace/workspace.py:435`

```python
def cleanup(self, agent_id, *, keep_output=True, keep_input=False):
    ...
    shutil.rmtree(temp_dir)
    temp_dir.mkdir(exist_ok=True)   # 重建空的 temp/
    ...
    shutil.rmtree(input_dir)
    input_dir.mkdir(exist_ok=True)   # 重建空的 input/
```

先删除、再重建同一目录。如果重建失败（权限变更、磁盘满），`shutil.rmtree` 已经成功删除了
原始目录，数据丢失但调用方无法感知。较好的做法是先重命名再删除，或只清空内容不重建。

---

### [Tool] 执行超时未在 `ToolRegistry` 中强制执行

**文件:** `src/zmai/tool/registry.py:88`

```python
def execute(self, name, params, context):
    tool = self.get(name)
    if not tool.validate(params):
        ...
    return tool.execute(context, params)  # 未包装 timeout
```

`ToolContext` 中有 `timeout` 字段，`ToolRouter` 也将其传入执行上下文，但在
`ToolRegistry.execute()` 中没有实际的时间限制。工具实现自行决定是否使用
`context.timeout`。部分工具（如 `ShellTool`）使用了 `subprocess.run(timeout=...)`，
但这是工具内部行为，非注册表保证。恶意或出错的工具可以永远阻塞。

---

### [Tool] `WriteFileTool` 回退标记命名错误

**文件:** `src/zmai/swe/tools.py:246`

```python
except Exception as e:
    errors.append(f"Attempt 1 (Path.write_text): ...")

# ── Attempt 2: Python open() with explicit encoding ──
try:
    ...
except Exception as e3:
    errors.append(f"Attempt 3 (open): ...")  # 实际是 Attempt 2，标记为 Attempt 3
```

仅注释/错误消息问题，不影响执行。但说明代码经过多次修改后一致性退化。

---

### [Runtime] `_execute_task()` 是完全的死代码路径

**文件:** `src/zmai/runtime/runtime.py:170`

`_execute_task()` 是一个独立的 Agent 执行循环，支持流式输出、工具路由、滑动窗口。
但 `run()` 方法**根本不调用 `_execute_task()`** — 它直接使用 `SWEAgent` 的
`initialize → step → finalize` 模式。

`_execute_task()` 维护了另一套工具调度逻辑（`ToolRouter.execute()` 对应
`tool_router.definitions()` / `_make_tool_context`），与 `SWEAgent.step()`
中的工具调度逻辑重复。这是两条并行演化的 Agent 执行路径，其中之一是死代码。

---

### [Prompt] `PromptEngine` 完全未被 Runtime 使用

**文件:** `src/zmai/prompt/engine.py`

`PromptEngine` 提供了 system/planner/executor/verifier/report 五种模板和渲染机制。
但：

- `Runtime` 从未引用 `PromptEngine`
- `SWEAgent` 使用硬编码的 `_build_system_prompt()` 生成提示词
- `PromptEngine.render_system()` 等便捷方法从未被调用
- `PromptEngine` 中引用的 `PromptType.*` 和 `DEFAULT_TEMPLATES` 在 Agent 实现中不存在对应执行路径

`PromptEngine` 是设计中预期的功能，但未集成到实际运行时。

---

### [Session] 没有正式的 Session 概念 — 无法断点续跑

**文件:** `src/zmai/cli/main.py:21`

```
SESSION_DIR = Path.home() / ".zmai" / "sessions"
HISTORY_FILE = Path.home() / ".zmai" / "history"
```

"Session" 仅是保存"上一次任务文字"到 `latest.json`，用于在 REPL 启动时展示。
不存在真正的 Session 管理：

- 没有 Session ID / Session 恢复
- Agent 中断后无法恢复上下文
- 没有任何持久化机制保存消息历史、Agent 状态、执行进度
- `_save_session` 只记录任务描述文本

---

### [CLI] 项目检测后 `os.chdir` 影响全局状态

**文件:** `src/zmai/cli/main.py:699`

```python
root = find_project_root()
if root:
    os.chdir(str(root))           # 切换进程 CWD
    project_ctx = build_context(root)
```

`os.chdir()` 是进程级操作，同时修改了 Runtime、Config、Workspace 等多个组件的相对路径
解析基准。如果同时有多个 Runtime 实例运行在不同项目上，此操作会造成全局竞态。
Python 3.11+ 的 `contextlib.chdir` 可提供更好的隔离。

---

## P2 — 次要问题（代码质量、健壮性、可维护性）

### [Agent Lifecycle] 生命周期转换表缺少边界转换

**文件:** `src/zmai/runtime/lifecycle.py:11`

```python
_TRANSITIONS: dict[tuple[str, str], bool] = {
    ("idle", "initializing"): True,
    ("initializing", "running"): True,
    ("initializing", "failed"): True,
    ("running", "paused"): True,
    ("running", "completed"): True,
    ("running", "failed"): True,
    ("paused", "running"): True,
    ("paused", "cancelled"): True,
    ("running", "cancelled"): True,
    ("initializing", "cancelled"): True,
    ("idle", "cancelled"): True,
}
```

缺少的可允许转换：
- `completed → completed`（如果要复用 agent_id）
- `failed → retrying`（如果要实现自动重试）
- `cancelled → initializing`（如果重调度相同 agent_id）
- `paused → cancelled`（覆盖 paused→cancelled 已存在，但 paused 时取消无中断逻辑）

当前表中 `("paused", "cancelled"): True` 存在但 `("paused", "failed"): True` 不存在。
暂停过程中出错无法转换到 failed。

---

### [Backend] API 调用无熔断/无重试上限保护

**文件:** `src/zmai/gateway/backends/claude.py:89`

`ClaudeBackend.invoke()` 实现了指数退避重试（最多 3 次），但：
- 没有熔断器（circuit breaker）：连续失败不会暂时禁用后端
- 没有对 HTTP 状态码的智能处理：401 不应重试，429 应等待更久再重试
- `DeepSeekBackend.invoke()` 完全没有重试逻辑
- 所有异常（包括 `KeyboardInterrupt`、`SystemExit`）被 `except Exception` 捕获

---

### [Backend] 自动注册使用字符串导入，无法静态分析

**文件:** `src/zmai/runtime/runtime.py:305`

```python
backends_to_check = [
    ("claude",   "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
     "zmai.gateway.backends.claude", "ClaudeBackend", "claude-sonnet-4-6"),
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
     "zmai.gateway.backends.deepseek", "DeepSeekBackend", "deepseek-chat"),
]
...
mod = importlib.import_module(mod_path)
cls = getattr(mod, cls_name)
self._gateway.register(name, cls, config={"model": model})
```

使用字符串路径动态导入 Backend 类，导致：
- IDE 无法追踪引用
- 静态类型检查器无法验证 Backend 类型
- 添加新 Backend 时必须同时修改此数组
- import 错误在运行时才暴露（被 `except Exception: pass` 吞没）

---

### [Workspace] `FILE_CATEGORIES` 有重复键

**文件:** `src/zmai/workspace/workspace.py:70`

```python
".rs": "code",
...
".rs": "code",  # 第 71 行重复
```

`.rs` 被声明了两次。虽然值相同，但 Python 字典按下标覆盖，不会报错。
可能是从其他列表合并时产生的重复。

---

### [Tool] `ShellTool` 和 `GitTool` 使用 `shell=True`

**文件:** `src/zmai/swe/tools.py:458` 和 `src/zmai/swe/tools.py:499`

```python
r = subprocess.run(cmd, shell=True, cwd=cwd, ...)
r = subprocess.run(f"git {args}", shell=True, cwd=..., ...)
```

`shell=True` 在用户可控制输入（`cmd`, `args`）时存在 shell 注入风险。
当前 Agent 上下文中的用户是可信的，但随着系统扩展此风险需重新评估。

---

### [Config] 配置加载后不监听文件变更

**文件:** `src/zmai/config/config.py:52`

```python
def reload(self) -> None:
    with self._lock:
        self._load()
```

`reload()` 存在但没有任何自动触发机制。`zmai.json` 变更后需要显式调用 `reload()`。
没有文件系统 watcher，没有定时刷新。

---

### [CLI] `_cleanup_old_workspaces` 在启动时串行清理

**文件:** `src/zmai/cli/main.py:59`

```python
def _cleanup_old_workspaces(workspace_root, max_age_days=7):
    ws = Workspace(root=str(workspace_root))
    agents = ws.list_agents()
    for aid in agents:        # 串行遍历所有 Agent
        ...
```

在 Agent 数量大时显著增加启动时间。应在后台执行或使用惰性清理。
同时 `cleanup` 方法内部再次调用 `list_agents()`+`get_manifest()`，进一步放大开销。

---

### [StateManager] `persist()` 在每次 `update()` 时写盘

**文件:** `src/zmai/runtime/state.py:48`

```python
def update(self, agent_id, **fields):
    ...
    self.persist()  # 每次更新都写盘
```

缺少节流（throttle）/ 批量（batch）机制。在 `run()` 的 step 循环中每步至少调用一次
`update`，每次触发全量 JSON 序列化+磁盘写入。高并发下可能导致 IO 瓶颈。

---

### [Error Handling] `_read_json` 和 `_write_json` 静默吞异常

**文件:** `src/zmai/workspace/workspace.py:1018`

```python
@staticmethod
def _read_json(path):
    try:
        ...
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(...)  # 只记录 warning，不传播
    return None
```

多处 JSON 读取失败时返回 `None` 而非抛出异常，调用方难以区分"文件不存在"和"文件损坏"。
同理 `_write_json` 捕获异常后仅 `logger.error`。

---

### [Auth] XOR 加密不是真正的加密

**文件:** `src/zmai/auth/store.py:43`

```python
def _encrypt(plain, key):
    data = plain.encode()
    encrypted = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    return base64.b64encode(encrypted).decode()
```

XOR + base64 是编码（encoding）而非加密（encryption）。同一密钥 XOR 相同明文产生相同
密文，可被频率分析破解。文档中称之为"AES-like"（第 62 行）容易误导：
- 实际上源码中没有任何 AES 操作
- MachineGuid 泄露即可解密所有凭证

---

## 总结

| 层级 | P0 | P1 | P2 |
|------|----|----|----|
| Runtime 生命周期 | 3 | 1 | 1 |
| Task 生命周期 | 2 | 0 | 0 |
| Agent 生命周期 | 0 | 2 | 1 |
| Session 生命周期 | 1 | 0 | 0 |
| Backend Routing | 0 | 3 | 2 |
| Workspace | 2 | 1 | 1 |
| Tool 调度 | 0 | 2 | 2 |
| 跨层/其他 | 0 | 3 | 5 |
| **合计** | **8** | **12** | **12** |

### 最需要优先处理的 3 个问题

1. **Runtime.shutdown() 不做清理** (P0) — 影响数据完整性
2. **run() 绕过 Scheduler / _tasks 泄漏** (P0) — 并发控制完全失效
3. **Workspace 读写缺少同步** (P0) — 并发数据竞争
