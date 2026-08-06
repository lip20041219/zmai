# Backend 同步阻塞审计

> 审计日期: 2026-07-26
> 审计方式: 只读，不修改代码

---

## 1. 接口定义

### Backend ABC（`gateway/base.py:120-198`）

```python
class Backend(ABC):
    @abstractmethod
    def invoke(self, request: BackendRequest) -> BackendResponse:
        """同步调用模型，返回完整响应。"""
        ...
```

**签名明确为同步函数** — 返回 `BackendResponse`，不是 `async def`，不是 `Coroutine`。

### 调用链

```
SWEAgent.step()                          ← async def (asyncio 上下文)
  └── context.backend.invoke(request)    ← 同步调用 (SYNC)
      ├── ClaudeBackend.invoke()          ← urllib (SYNC) + time.sleep (SYNC)
      ├── DeepSeekBackend.invoke()        ← urllib (SYNC)
      └── GeminiBackend.invoke()          ← urllib (SYNC)
```

**结论**: 接口是 sync，在 async 函数中直接调用。没有 `run_in_executor` 包装。

---

## 2. 每个 Backend 的同步阻塞点

### 2.1 ClaudeBackend

| 行 | 代码 | 阻塞类型 | 最坏等待 |
|----|------|----------|----------|
| 94 | `self._post(url, body)` | urllib 同步 HTTP | `timeout=300s` |
| 106-111 | `time.sleep(wait)` with `wait=2**attempt` | 线程级 sleep | 1+2+4=7s (重试间隙) |
| 120-200 | `stream()` 中 `urllib.request.urlopen(req)` | urllib 同步 HTTP SSE | `timeout=300s` |

`_post()` 内部（`claude.py:250-278`）:
```python
urllib.request.urlopen(req, timeout=self._timeout)  # 同步, 最长 300s
```

### 2.2 DeepSeekBackend

| 行 | 代码 | 阻塞类型 | 最坏等待 |
|----|------|----------|----------|
| 100 | `urllib.request.urlopen(req, timeout=120)` | urllib 同步 HTTP | `timeout=120s` |
| 169 | `stream()` → `raise NotImplementedError` | — | N/A |

### 2.3 GeminiBackend

| 行 | 代码 | 阻塞类型 | 最坏等待 |
|----|------|----------|----------|
| 85 | `urllib.request.urlopen(req, timeout=self._timeout)` | urllib 同步 HTTP | `timeout=120s` |
| 130 | `urllib.request.urlopen(req, timeout=self._timeout)` | urllib 同步 SSE | `timeout=120s` |

### 总结

| Backend | 同步 HTTP | 同步 sleep | 标准库 urllib | 内部重试 |
|---------|-----------|------------|---------------|----------|
| Claude  | ✅ | ✅ `time.sleep` | ✅ | 3次 (1s, 2s, 4s) |
| DeepSeek | ✅ | ❌ | ✅ | 无 |
| Gemini  | ✅ | ❌ | ✅ | 无 |

---

## 3. Runtime 的 asyncio 调用路径

### 调用栈

```
Runtime.run()                           ← async def
  └── await _scheduler.schedule()
    └── _run_agent()                    ← async def
      └── while step_count < max_steps:
          └── action = await agent.step(ctx)  ← async def 内部:
              ├── generate_plan()       ← BACKEND.invoke() (SYNC) — 阻塞!
              └── Backend.invoke()      ← SYNC: urllib.urlopen — 阻塞事件循环!
```

### 阻塞持续时间

最坏情况单步阻塞:
```
Claude:   300s (HTTP timeout) + 7s (3次重试 sleep) = 307s
DeepSeek: 120s (HTTP timeout)
Gemini:   120s (HTTP timeout)
```

**在 `await agent.step(ctx)` 的整个期间，事件循环被冻结。同时运行的其它协程无法取得进展。**

### Runtime 的并发设计

```
Scheduler(max_concurrent=10)  ← asyncio 调度
  └── asyncio.create_task(_run_agent(), name=agent_id)
```

设计上支持 10 个 Agent 并发。但实际上:

```
Agent1: -----------[同步 HTTP 300s阻塞]----------->
Agent2: --------[被 Agent1 阻塞，无法执行]-------->
          ← 单线程事件循环被 Blocking I/O 占满
```

---

## 4. 当前双层重试导致的累积阻塞

```
SWEAgent.step():                     ClaudeBackend.invoke():
  ┌─────────────────┐                ┌─────────────────────┐
  │ attempt 1        │────invoke()───>│ _post() [urllib]    │
  │   → Exception     │<──raise──────│   → Exception        │
  │ await asyncio.sleep(1s)          │ time.sleep(1s)      │
  │ attempt 2        │────invoke()───>│ _post() [urllib]    │
  │   → Exception     │<──raise──────│   → Exception        │
  │ await asyncio.sleep(2s)          │ time.sleep(2s)      │
  │ attempt 3        │────invoke()───>│ _post() [urllib]    │
  │   → Exception     │<──raise──────│   → Exception        │
  │ return fail       │              │                     │
  └─────────────────┘                └─────────────────────┘
```

- ClaudeBackend 内部重试: `time.sleep(1+2+4) = 7s` 线程阻塞
- SWEAgent 外部重试: `asyncio.sleep(1+2+4) = 7s` 协程让出
- 最坏: 9 次 HTTP 请求 × 各自超时

---

## 5. 现有测试对同步行为的依赖

所有 Mock Backend 都是同步的:

```python
# tests/mocks.py:91
class SuccessBackend(Backend):
    def invoke(self, request: BackendRequest) -> BackendResponse:
        self.invoke_count += 1
        if self._delay > 0:
            time.sleep(self._delay)  # ← 同步 sleep, 测试也用 time.sleep
        return _text_response(...)
```

```python
# tests/mocks.py:313
class FlakyBackend(Backend):
    def invoke(self, request):
        self.invoke_count += 1
        if self.invoke_count <= self._fail_count:
            raise Exception(...)       # ← 同步 raise
        return _text_response(...)
```

```python
# tests/mocks.py:352
class InfiniteLoopBackend(Backend):
    def invoke(self, request):
        self.invoke_count += 1
        return BackendResponse(         # ← 即时同步返回
            tool_calls=[ToolCall(...)]
        )
```

| Mock Backend | 行为 | 同步/异步 |
|-------------|------|-----------|
| SuccessBackend | 即时或延时返回 | 同步, `time.sleep` |
| AuthErrorBackend | 立即 raise | 同步 |
| ConnectionErrorBackend | 立即 raise | 同步 |
| TimeoutBackend | 立即 raise | 同步 |
| InvalidResponseBackend | 即时返回/raise | 同步 |
| FlakyBackend | 前 N 次 raise, 后成功 | 同步 |
| InfiniteLoopBackend | 即时返回 tool_call | 同步 |

**测试依赖同步行为**: 所有 Mock Backend 的 `invoke()` 都在调用线程中即时返回或报错。如果将 Backend.invoke() 改为 async，需要同时修改所有 Mock 和 Backend 接口。

---

## 6. 核心发现

| 编号 | 问题 | 严重性 | 位置 |
|------|------|--------|------|
| B1 | `Backend.invoke()` 是 sync，在 async 函数中直接调用 | P0 | `gateway/base.py:156` |
| B2 | `urllib.request.urlopen()` 阻塞事件循环 | P0 | 3 个 Backend 实现 |
| B3 | 并发 10 个 Agent 但实际只能串行执行 HTTP | P1 | `runtime/scheduler.py:21` |
| B4 | `time.sleep()` 在 async 上下文中线程阻塞 | P1 | `claude.py:111` |
| B5 | 双层重试 (Backend + Agent) 累积阻塞 | P2 | `claude.py:92` + `agent.py:325` |
| B6 | `Backend.stream()` 也是同步 Iterator | P2 | `gateway/base.py:171` |
| B7 | 同步接口使 `Runtime.cancel()` 在 invoke 期间失效 | P0 | `runtime.py:233` |

---

## 7. 最小修改方案

### 方案 A: `run_in_executor` 包装（推荐，最小侵入）

**思路**: 保持 `Backend.invoke()` 同步接口不变，在调用点用 `asyncio.loop.run_in_executor()` 将同步调用委托到线程池，不阻塞事件循环。

**修改点**:

| 文件 | 修改 | 行数 |
|------|------|------|
| `swe/agent.py:327` | 将 `context.backend.invoke(request)` 改为 `await _sync_invoke(context.backend, request)` | 2 |
| `swe/planner.py:87` | 将 `backend.invoke(request)` 改为 `await _sync_invoke(backend, request)` | 2 |

新增辅助函数:
```python
# swe/agent.py (或其他共享模块)
async def _sync_invoke(backend: Backend, request: BackendRequest) -> BackendResponse:
    """将同步 backend.invoke() 放到线程池执行，不阻塞事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, backend.invoke, request)
```

**优点**:
- 不改 `Backend ABC` 接口 → 100% 向后兼容
- 不改 3 个 Backend 实现 → 0 行为变化
- 不改 Mock Backend → 全部测试不变
- 不改 `time.sleep()` → ClaudeBackend 重试继续在独立线程工作
- 不改 `stream()` → SSE 流暂不受影响
- 2 个文件，每文件 2 行，总计约 10 行

**缺点**:
- 线程池额外开销 (~100μs 每次调度)
- `time.sleep` 在线程中仍是线程阻塞（但线程池有线程可用，不影响事件循环）
- `stream()` 仍同步 — 但 stream 不是当前主路径

**对 Cancel 的影响**: `Runtime.cancel()` 调用 `asyncio.Task.cancel()`。当 task 在 `run_in_executor` 中等待时，`cancel()` 会设置 `CancelledError` 但 **不能中断已在执行的线程**。HTTP 请求会继续直到完成或超时。改进需要改用 `asyncio.timeout()` + `cancel()` 配合，或使用 `aiohttp`。

### 方案 B: 仅添加 `asyncio.timeout` 包装

**思路**: 保持同步，但添加超时保护。

```python
try:
    async with asyncio.timeout(context.config.get("backend_timeout", 120)):
        response = context.backend.invoke(request)
except asyncio.TimeoutError:
    return AgentAction.fail("Backend 调用超时")
```

**优点**: 极简，防止永久阻塞
**缺点**: 超时后无法真正中断 urllib（Python 线程内无法从外部杀死），只能通过 `cancel()` 配合

### 方案 C: 用户态超时 + 取消协作

**思路**: 在 `backend.invoke()` 内部检查 `asyncio.current_task().cancelled()`。但这需要 Backend 接口变成 async 或传入取消令牌。

**缺点**: 改动量大，涉及接口变更。

### 推荐: **方案 A（`run_in_executor`）**

```
SWEAgent.step()                          ← async
  └── await _sync_invoke(backend, req)   ← 不阻塞事件循环
      └── loop.run_in_executor(None, backend.invoke, req)
          └── [线程池线程] urllib.urlopen  ← 在线程中阻塞
```

不侵入 Backend ABC、不修改 3 个 Backend 实现、不修改任何测试。

### 涉及修改文件清单

```
M  src/zmai/swe/agent.py       # 2 行: 替换 invoke 调用为 _sync_invoke
M  src/zmai/swe/planner.py     # 2 行: 替换 generate_plan 中的 invoke 调用
A  src/zmai/swe/_async_utils.py # ~10 行: _sync_invoke 辅助函数
```
