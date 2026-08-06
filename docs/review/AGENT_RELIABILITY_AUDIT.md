# Agent Reliability Audit

> 日期：2026-07-22
> 审计方式：静态代码审查，不修改代码

---

## 审计范围

Agent 生命周期、Runtime 生命周期、Backend 调用、Tool 调用、错误传播、重试、超时、取消、循环检测、成功判定。

---

## 1. 核心问题：Tool 执行失败 → Agent 仍可返回 success

**位置：** `swe/agent.py:256-277`

**流程：**

```
Tool 执行失败
  → ToolRegistry.execute() 返回 ToolResult(success=False, error="...")
  → SWEAgent.step() 追加 "FAIL: ..." 到消息
  → 返回 AgentAction.cont()   ← 继续，让 LLM 决策
  → LLM 可能选择放弃（返回无 tool_call 的文本）
  → AgentAction.complete()    ← 标记为 "complete"！
```

**风险：** 🔴 **严重。** Agent 可以在什么都没做成的情况下返回 `success`。例如：
- Agent 尝试 `shell_exec("deploy")` → 失败
- Agent 尝试 `write_file(...)` → 失败（权限不足）
- Agent 说"我无法完成此任务"→ **返回 status = completed**

**根源：** `AgentAction.complete()` 只表示"LLM 决定结束"，不表示"任务成功完成"。但 `Runtime._run_agent()` 将其视为 success（第 159-160 行）。

**当前缓解：** 无。

---

## 2. BackendError（401/403/404）错误传播

**位置：** `swe/agent.py:221-222`

```
Backend.invoke() 抛出 BackendError
  → SWEAgent.step() 捕获并直接 raise（不重试）
  → Runtime._run_agent() 捕获 Exception
  → 返回 {"status": "failed", "error": "[KEY_INVALID] ..."}
```

**状态：** ✅ 正确。BackendError 不进入重试路径，直接报告失败。

**friendly_http_error() 消息映射：**

| 状态码 | 错误码 | 消息 |
|--------|--------|------|
| 401 | `KEY_INVALID` | API Key 无效 |
| 403 | `KEY_EXPIRED` 或 `KEY_INVALID` | 过期或权限不足 |
| 404 | `MODEL_NOT_FOUND` | 模型不存在 |
| 429 | `RATE_LIMITED` | 频率限制 |
| 5xx | `SERVER_ERROR` | 服务不可用 |

---

## 3. Backend 永不返回 → 无法终止

**位置：** `swe/agent.py:218`

```python
response = context.backend.invoke(request)  # 同步阻塞！
```

**风险：** 🔴 **严重。**

- `invoke()` 使用 `urllib.request.urlopen()` 发送 HTTP 请求
- `urlopen` 有 `timeout` 参数（默认 120-300 秒），但如果底层 TCP 连接无响应且不触发 timeout... **实际上 `urlopen` 的 `timeout` 参数确保最坏情况下会超时**
- 但 `timeout` 超时后抛出 `URLError`，被 `BackendError` 包装
- 然而 `BackendError` 被 `swe/agent.py:221` 捕获并 `raise`，propagates to Runtime

**实际分析：**
- `timeout` 参数在 Backend `__init__` 中设置（Claude: 300s, DeepSeek: 120s, Gemini: 120s）
- `urlopen` 的 `timeout` 是 socket 级别超时，不是 wall-clock 超时
- 如果 socket 连接建立后服务器不返回数据（慢响应），`timeout` 会触发
- `asyncio` 侧没有额外的 `wait_for()` 包装

**结论：** Backend HTTP timeout 已配置，但 `asyncio` 级别的超时保护不存在。当前依赖 socket timeout。

---

## 4. Agent 循环检测 — 不存在

**风险：** 🟠 **中。** 当前没有任何循环检测机制。

| 检测类型 | 状态 |
|---------|------|
| 相同 tool 重复调用 | ❌ 无 |
| 相同消息重复 | ❌ 无 |
| 完全相同结果 | ❌ 无 |
| **唯一保护** | `max_steps`（默认 100） |

**示例绕过：**
```
Agent 反复执行：
  shell_exec("ls")
  → 结果相同
  → 继续 shell_exec("ls")
  → ...直到 max_steps=100
```

**唯一保护：** `Runtime._run_agent()` 的 `while step_count < ctx.max_steps` 循环。默认 `max_steps=100`，可在配置中设置 `runtime.max_iterations`。

---

## 5. 任务取消 — 部分工作

**位置：** `runtime.py:202-208`

```
Runtime.cancel()
  → Scheduler.cancel()         → asyncio.Task.cancel()
  → Runtime._tasks[id].cancel() → 重复取消
  → Lifecycle.cancel()         → running → cancelled
  → State.update(cancelled)
```

**可取消的时间点（await 点）：**

| 代码位置 | `await` | 可取消？ |
|---------|---------|---------|
| `action = await agent.step(ctx)` | ✅ | ✅ 是 |
| `result_obj = await agent.finalize(ctx)` | ✅ | ✅ 是 |
| `response = context.backend.invoke(request)` | ❌ 同步阻塞 | ❌ **否** |
| `await asyncio.sleep(wait)` | ✅ | ✅ 是（重试间） |

**已知 Bug（P0-2 遗留）：** `Runtime.cancel()` 调用 `Lifecycle.cancel()` 后，`_run_agent()` 的 `CancelledError` handler 再次调用 `Lifecycle.cancel()` 导致 `RuntimeError`：

```python
# runtime.py:202-208
self._lifecycle.cancel(agent_id)  # 第一次: running → cancelled

# runtime.py:172-176 (CancelledError handler)
self._lifecycle.cancel(agent_id)  # 第二次: cancelled → cancelled → CRASH
```

---

## 6. max_steps 限制

**位置：** `runtime.py:152`

```python
while step_count < ctx.max_steps:
```

**配置：** `runtime.max_iterations`（默认 100）

**行为：**
- 到达 `max_steps` 后循环退出，不报错
- 直接进入 `finalize` → 返回 `status = completed`
- **不会提示"达到最大步数"或返回 `failed`**

**风险：** 🟡 **中。** Agent 达到 `max_steps` 时假 success。

```
达到 max_steps
  → 循环结束
  → finalize()
  → return {"status": "completed", ...}  ← 假 success！
```

---

## 7. Tool 超时

**位置：** `tool/registry.py:108-111`

```python
timeout = context.timeout or 0
if timeout > 0:
    return self._execute_with_timeout(tool, context, params, timeout)
return tool.execute(context, params)
```

- `ToolContext.timeout` 默认 120 秒
- `_execute_with_timeout()` 使用 `ThreadPoolExecutor` 执行工具
- 超时后返回 `ToolResult(success=False, error="...: 执行超时 (Ns)")`

**工具特定超时：**

| Tool | 超时实现 | 覆盖 |
|------|---------|------|
| `ShellTool` | `subprocess.run(timeout=...)` | params 中可指定 |
| `GitTool` | `subprocess.run(timeout=30)` | 固定 |
| 其他工具 | `ToolContext.timeout` 默认 120s | 配置 |

---

## 8. finalize() 总是 COMPLETED

**位置：** `swe/agent.py:282-290`

```python
async def finalize(self, context: AgentContext) -> AgentResult:
    result = AgentResult(
        agent_id=self.agent_id,
        status=AgentState.COMPLETED,  # ← 硬编码！
        ...
    )
```

**风险：** 🟡 **中。** `finalize()` 无视实际执行结果，总是返回 `COMPLETED`。Runtime 虽然有自己的状态机，但 `finalize()` 作为 Agent 的"最终报告"应该反映真实状态。

---

## 9. 成功判定逻辑

**`Runtime._run_agent()` 的三种返回路径：**

| 路径 | 触发条件 | 状态 |
|------|---------|------|
| 正常结束 | `action.type in ("complete", "fail")` | `completed` |
| `action.type == "fail"` | RuntimeError 抛出 | `failed` |
| `max_steps` 耗尽 | 循环结束 | `completed` (假) |
| `CancelledError` | 任务取消 | `cancelled` |
| `Exception` | 任何其他异常 | `failed` |

**假成功风险汇总：**

| 场景 | 实际状态 | 返回状态 |
|------|---------|---------|
| Tool 都失败了但 LLM 说"完成了" | ❌ 失败 | ✅ `completed` |
| 达到 max_steps | ❌ 未完成 | ✅ `completed` |
| LLM 生成空回复 | ❌ 无输出 | ✅ `completed` |
| 所有工具都超时 | ❌ 超时 | ✅ `completed`（若 LLM 选择结束） |

---

## 10. Backend 重试机制

**位置：** `swe/agent.py:216-236`

```
for attempt in range(max_retries):         # 默认 3
    try:
        response = context.backend.invoke(request)
        break
    except BackendError:
        raise                               # 不重试
    except Exception as e:
        if attempt < max_retries - 1:
            await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
        else:
            return AgentAction.fail(...)
```

**ClaudeBackend 内部还有自己的重试：** 默认 3 次（指数退避 1s, 2s, 4s）。

**总重试次数（ClaudeBackend）：** Agent 3 次 × Backend 3 次 = **最多 9 次调用**，最长等待约 14 秒。

**即使所有 Backend 重试失败，Runtime 的 `except Exception` 会捕获 BackendError 并返回 `failed`。**

---

## 11. Scheduler 并发限制

**位置：** `runtime/scheduler.py:21-22`

```python
if len(self._tasks) >= self._max:  # 默认 10
    raise RuntimeError(f"并发 Agent 数超出限制: {self._max}")
```

超过并发限制抛出 `RuntimeError`（注意是 ZMAI 的 RuntimeError，不是 Python 的）。

---

## 可靠性问题汇总

| # | 问题 | 严重程度 | 影响 |
|---|------|---------|------|
| 1 | **Tool 失败后 Agent 可返回 success** | 🔴 严重 | 静默吞错误 |
| 2 | **max_steps 耗尽返回 success** | 🔴 严重 | 静默截断 |
| 3 | **finalize() 总是 COMPLETED** | 🟠 中 | 信息丢失 |
| 4 | **Cancel 时 lifecycle 双重调用 Bug** | 🟠 中 | 取消时报错 |
| 5 | **无循环检测** | 🟠 中 | 浪费 token |
| 6 | **invoke() 无 asyncio 超时** | 🟡 低 | 依赖 socket timeout |
| 7 | **401/403 错误消息** | ✅ 正确 | 用户可读 |
| 8 | **Backend 重试机制** | ✅ 正确 | 指数退避 |
| 9 | **Tool 超时机制** | ✅ 正确 | ThreadPool |
| 10 | **CancelledError 处理** | ⚠️ 部分 | 仅在 await 点工作 |

---

## 关键路径 trace

```
用户输入 task
  │
  ├─ Preflight Check ──→ failed → return {"status": "failed"}
  │
  ├─ Runtime.run()
  │    ├─ lifecycle.initialize()
  │    ├─ workspace.prepare()
  │    ├─ gateway.get(backend)
  │    ├─ lifecycle.mark_ready()
  │    │
  │    ├─ _run_agent()
  │    │    ├─ SWEAgent.initialize()
  │    │    │
  │    │    ├─ while step_count < max_steps:
  │    │    │    ├─ SWEAgent.step()
  │    │    │    │    ├─ Backend.invoke()      ← 同步阻塞，指数退避重试
  │    │    │    │    │    ├─ BackendError → raise → Runtime.fail
  │    │    │    │    │    └─ Exception → retry (3x)
  │    │    │    │    │
  │    │    │    │    ├─ response.tool_calls?
  │    │    │    │    │    ├─ 是 → ToolRegistry.execute()
  │    │    │    │    │    │    ├─ 成功 → AgentAction.cont()
  │    │    │    │    │    │    └─ 失败 → AgentAction.cont()  ← 继续，不终止！
  │    │    │    │    │    └─ 否 → AgentAction.complete()
  │    │    │    │    │
  │    │    │    │    └─ return AgentAction
  │    │    │    │
  │    │    │    ├─ action.type == "fail" → raise RuntimeError
  │    │    │    └─ action.type in ("complete", "fail") → break
  │    │    │
  │    │    ├─ SWEAgent.finalize()  ← 总是 COMPLETED
  │    │    │
  │    │    ├─ lifecycle.complete()
  │    │    └─ return {"status": "completed"}
  │    │
  │    ├─ except CancelledError → {"status": "cancelled"}
  │    └─ except Exception → {"status": "failed", "error": str(e)}
  │
  └─ return result
```

---

## 结论

| 可靠性维度 | 当前状态 |
|-----------|---------|
| 不假装成功 | ❌ Tool 失败 + max_steps 耗尽仍可返回 completed |
| 不无限循环 | ⚠️ max_steps 保护，但无循环检测 |
| 不无限重试 | ✅ 指数退避 + 3 次上限 |
| 正确处理 Backend 失败 | ✅ BackendError 不重试，消息友好 |
| 正确处理 Tool 失败 | ⚠️ 不终止，但 Agent 仍可假 success |
| 超时后结束 | ✅ Tool 超时，⚠️ Backend 依赖 socket timeout |
| 取消任务 | ⚠️ 仅在 await 点工作，cancel 时有 lifecycle double-call bug |
| 报告准确状态 | ❌ finalize() 总是 COMPLETED，max_steps 耗尽假 success |
| 恢复后继续执行 | ✅ Tool 失败后 Agent 可再尝试 |
