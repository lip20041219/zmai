# ZMAI 测试覆盖缺口分析

> 基于 ../TESTING_STRATEGY.md v1.0 的逐项审计
> 目标: 验证 18 个关键场景是否真正覆盖，Mock Backend 是否完备

---

## 目录

- [A: Mock Backend 缺口](#a-mock-backend-缺口)
- [B: 8 个关键行为覆盖验证](#b-8-个关键行为覆盖验证)
- [C: 10 个额外检查项](#c-10-个额外检查项)
- [D: 测试质量检查](#d-测试质量检查)
- [E: 修复方案](#e-修复方案)

---

## A: Mock Backend 缺口

### A.1 需求 vs 现状

```
需求 Mock Backend          当前设计中的 Mock            状态
─────────────────────     ─────────────────────        ─────
SuccessBackend            MockBackend                  ✅ 语义等价
AuthErrorBackend          AuthFailMockBackend           ⚠️ 名称不对，返回 401 ✅
ConnectionErrorBackend    FailMockBackend               ❌ 抛出通用 BackendError，非连接错误
TimeoutBackend            SlowMockBackend               ❌ 延迟返回，不触发超时
InvalidResponseBackend    ❌ 不存在                     ❌
FlakyBackend              ❌ 不存在                     ❌
```

### A.2 具体缺口

#### 缺口 A1: ConnectionErrorBackend 缺失

`FailMockBackend` 抛出 `BackendError`，但语义是"后端通用错误"。
连接错误（DNS 解析失败、连接被拒绝、SSL 握手失败）在真实场景中表现为 `urllib.error.URLError`。

**影响**：如果 Runtime 对连接错误和 API 错误的处理路径不同，当前测试无法覆盖连接错误路径。

```python
# 需要的 Mock
class ConnectionErrorBackend(Backend):
    """模拟网络连接失败。"""
    name = "connection_error"

    def invoke(self, request):
        raise BackendError(
            "connection failed: Failed to resolve DNS for api.example.com",
            status_code=None,  # 网络错误无 HTTP 状态码
        )
```

#### 缺口 A2: TimeoutBackend 语义错误

`SlowMockBackend` 延迟 5 秒后**正常返回**，不是超时。测试超时需要 Backend 在 `invoke()` 中抛出超时异常。

```python
# 需要的 Mock
class TimeoutBackend(Backend):
    """模拟 Backend 调用超时。"""
    name = "timeout"

    def invoke(self, request):
        raise BackendError("timeout: Request timed out after 30s")
```

#### 缺口 A3: InvalidResponseBackend 缺失

Backend 返回不可解析的响应（空 body、JSON 解析失败、缺少必要字段）。

```python
class InvalidResponseBackend(Backend):
    """模拟 Backend 返回非法响应。"""
    name = "invalid_response"

    def invoke(self, request):
        # 返回空响应
        return BackendResponse(content="")
```

#### 缺口 A4: FlakyBackend 缺失

Backend 时好时坏：前 N 次调用失败，第 N+1 次成功。用于测试重试逻辑。

```python
class FlakyBackend(Backend):
    """前 fail_count 次调用失败，之后成功。"""
    name = "flaky"

    def __init__(self, fail_count=2):
        self._fail_count = fail_count
        self._call_count = 0

    def invoke(self, request):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise BackendError("temporary server error (503)")
        return BackendResponse(content="ok after retry")
```

#### 缺口 A5: Mock 命名不一致

当前设计使用的命名与用户要求的命名不匹配：

| 用户要求 | 当前设计 | 建议 |
|---------|---------|------|
| `SuccessBackend` | `MockBackend` | 保留 `MockBackend` 作为别名，或重命名 |
| `AuthErrorBackend` | `AuthFailMockBackend` | 统一为 `AuthErrorBackend` |
| `ConnectionErrorBackend` | `FailMockBackend` | 拆分为独立的 `ConnectionErrorBackend` |
| `TimeoutBackend` | `SlowMockBackend` | 替换为真正的 `TimeoutBackend` |
| `InvalidResponseBackend` | ❌ | 新增 |
| `FlakyBackend` | ❌ | 新增 |

---

## B: 8 个关键行为覆盖验证

### B.1 API Key 缺失

| 检查项 | 状态 | 证据 |
|--------|------|------|
| 有测试验证 Preflight 在 Key 缺失时失败？ | ❌ 未覆盖 | 当前 `test_missing_backend_fails` 测试的是"Backend 未注册"，不是"Backend 已注册但 API Key 为空" |
| 有测试验证 Preflight 输出友好错误？ | ❌ 未覆盖 | PreflightResult.print() 无测试 |
| 有测试验证 CLI 提示用户配置？ | ❌ 未覆盖 | `_offer_auth_fix()` 未测试 |

**缺口**: Preflight check 的 API Key 缺失路径需要测试。当前设计中的 `test_missing_backend_fails` 测试的是 `BackendRegistry.get("nonexistent")`，不是"有 Backend 配置但 Key 为空字符串"。

```python
# 需要新增的测试场景
def test_preflight_rejects_empty_api_key(self, runtime):
    """Backend 存在但 API Key 为空时应失败。"""
    runtime._gateway.register("test", MockBackend, default=True, config={"api_key": ""})
    result = asyncio.run(runtime.run(agent_id="empty_key", task="test", backend="test"))
    assert result["status"] == "failed"
```

### B.2 API Key 无效（401）

| 检查项 | 状态 | 证据 |
|--------|------|------|
| AuthErrorBackend 存在？ | ❌ | `AuthFailMockBackend` 存在但命名不匹配 |
| AuthErrorBackend 返回 401？ | ✅ | 设计中有此 intent |
| Runtime 正确传播 401 错误？ | ⚠️ 部分 | `test_unauthorized_key` 存在但 body 为 `...` |
| Agent 在 401 后返回 fail？ | ⚠️ 部分 | 同上 |

**缺口**: `test_unauthorized_key` 的 body 填充为 `...`，不是完整实现。

### B.3 Backend 不可用

| 检查项 | 状态 | 证据 |
|--------|------|------|
| ConnectionErrorBackend 存在？ | ❌ | `FailMockBackend` 抛出通用错误 |
| Runtime 在网络错误时返回 fail？ | ⚠️ 部分 | `test_backend_unavailable` body 为 `...` |
| Agent 在网络错误时不清真成功？ | ❌ 未覆盖 | 无 Agent 级别的网络错误处理测试 |

**缺口**: 需要 `ConnectionErrorBackend` + Agent 级别断言 Agent 不宣称成功。

### B.4 Tool 执行失败

| 检查项 | 状态 | 证据 |
|--------|------|------|
| 工具级失败测试？ | ✅ 充分 | `test_swe.py` 中所有工具有失败路径测试 |
| Agent 级工具失败处理？ | ⚠️ 部分 | `test_agent_workflow.py::TestAgentToolFailure` body 为 `...` |
| ToolResult.err() 包含具体原因？ | ✅ | 所有工具返回具体错误 |

**缺口**: Agent 级别的工具失败处理测试是空的。

### B.5 Tool 失败时 Agent 不得宣称成功

| 检查项 | 状态 | 证据 |
|--------|------|------|
| 三条铁律有测试？ | ✅ | `test_execution_state.py::TestAgentResultHandling` 4 个测试 |
| Agent 读取 success 字段？ | ⚠️ 部分 | 当前 Agent 代码读取但不强制分支 |
| 最终报告基于 ExecutionLog？ | ❌ 未覆盖 | 无 Runtime 级测试验证报告来源 |

**缺口**: 铁律 3（最终报告基于 ExecutionLog 而非 LLM 输出）无 Runtime 级测试。

### B.6 多步 Agent Workflow

| 检查项 | 状态 | 证据 |
|--------|------|------|
| 两步对话测试（工具调用 → 完成）？ | ✅ | `test_e2e_mock.py` 有 |
| 三步以上对话测试？ | ❌ 未覆盖 | 最多 2 步 |
| 混合工具序列测试？ | ❌ 未覆盖 | `test_agent_workflow.py::TestAgentMultiToolSequence` body 为 `...` |
| 工具结果作为下一步输入？ | ❌ 未覆盖 | 无依赖链测试 |

**缺口**: 无超过 2 步的 Agent 对话测试。

### B.7 max_steps 超时

| 检查项 | 状态 | 证据 |
|--------|------|------|
| Runtime max_steps 限制？ | ❌ 未覆盖 | `test_agent_max_steps` body 为 `...` |
| 超时后 Agent 状态正确？ | ❌ 未覆盖 | 无 |
| 超时后不会假装成功？ | ❌ 未覆盖 | 无 |

**缺口**: `test_agent_max_steps` 逻辑上由 Runtime 控制循环，但测试 body 是 `...`，且断言不完整。

### B.8 Plan Mode 只读保护

| 检查项 | 状态 | 证据 |
|--------|------|------|
| PlanModeGuard 拒绝 write_file？ | ✅ | 16 个参数化测试 |
| PlanModeGuard 拒绝 shell 写命令？ | ✅ | rm/mkdir/npm install 被拦截 |
| PlanModeGuard 允许 shell 读命令？ | ✅ | dir/ls/pwd 被允许 |
| PlanAgent 注册工具白名单？ | ⚠️ 部分 | PlanAgent 测试只有 2 个，不完整 |
| Plan → Execute 状态转换？ | ❌ 未覆盖 | 无 Lifecycle 测试验证 planning → running 转换 |

**缺口**: PlanAgent 初始化测试少，Plan → Execute 状态转换无测试。

---

## C: 10 个额外检查项

### C.1 Agent 无限循环

| 子项 | 状态 |
|------|------|
| 有测试验证 Agent 不陷入无限工具调用循环？ | ❌ |
| Backend 每次返回新工具调用时 Runtime 终止？ | ❌ |
| Runtime 的 max_steps 循环能阻止无限循环？ | ❌ max_steps 逻辑在 Runtime 中，但无测试 |

**缺口**: 需要测试：Backend 永远返回 tool_call（不回 text），Runtime 在 max_steps 后终止。

```python
def test_agent_does_not_loop_infinitely(self, runtime):
    """Backend 持续返回工具调用时，Runtime 在 max_steps 后终止循环。"""
    backend = InfiniteLoopBackend()  # 始终返回 tool_call
    runtime._gateway.register("loop", InfiniteLoopBackend, default=True)
    runtime._config.set("runtime.max_iterations", 5)
    result = asyncio.run(runtime.run(agent_id="loop_test", task="do forever", backend="loop"))
    assert result["status"] in ("completed", "failed")  # 不卡死
    assert result["steps"] <= 5  # 不超过 max_steps
```

### C.2 重复 Tool 失败

| 子项 | 状态 |
|------|------|
| FailureTracker 测试 max_retries？ | ✅ |
| Agent 级测试：同一工具连续失败 3 次后终止？ | ❌ |
| Agent 级测试：失败后换参数重试？ | ❌ |

**缺口**: FailureTracker 在单元级测试了，但 Agent 级别的"同一工具连续失败"行为未测试。

### C.3 Backend 超时

| 子项 | 状态 |
|------|------|
| Backend 调用超时？ | ❌ TimeoutBackend 设计为延迟返回，不是超时抛出 |
| Runtime 处理 Backend 超时？ | ❌ |
| Agent 在 Backend 超时后不清真成功？ | ❌ |

**缺口**: 需要真正的 TimeoutBackend（抛出异常），以及 Agent 行为验证。

### C.4 Backend 重试

| 子项 | 状态 |
|------|------|
| ClaudeBackend 的 max_retries 有测试？ | ❌ |
| DeepSeekBackend 的 max_retries 有测试？ | ❌ |
| FlakyBackend 驱动重试逻辑？ | ❌ FlakyBackend 不存在 |

**缺口**: `ClaudeBackend._max_retries` 在 init 测试中验证了设置，但实际重试行为未测试。需要 `FlakyBackend` + 测试验证 `invoke` 被调用了 `max_retries + 1` 次。

### C.5 Tool 重试

| 子项 | 状态 |
|------|------|
| ToolRegistry 重试逻辑？ | ❌ ToolRegistry 没有内置重试 |
| Agent 级别工具重试？ | ⚠️ 测试 body 为 `...` |
| 重试次数限制？ | ✅ FailureTracker 单元测试 |

**缺口**: ToolRegistry 层面没有重试。当前的 TOOL_RESULT_CONTRACT 设计将重试放在 Agent 层（FailureTracker），但 Agent 级的重试测试不完整。

### C.6 验证失败后的恢复

| 子项 | 状态 |
|------|------|
| 验证失败 → 修复 → 再验证流程？ | ❌ 无完整测试 |
| 修复循环次数限制？ | ❌ 无 |
| 修复次数超限后报告失败？ | ❌ 无 |

**缺口**: 验证失败后的恢复机制在当前设计中存在概念，但无测试。

### C.7 最终失败状态

| 子项 | 状态 |
|------|------|
| Runtime 返回 status=failed？ | ✅ `test_backend_unavailable` 有断言 |
| LifecycleManager 状态为 failed？ | ❌ 无 |
| StateManager 状态为 failed？ | ❌ 无 |
| Workspace 状态为 failed？ | ❌ 无 |
| 失败原因在 metadata 中？ | ❌ 无 |

**缺口**: 最终失败状态只验证了返回值的 `status` 字段，没有验证：
- LifecycleManager: `get_state("agent_id") == "failed"`
- StateManager: `get("agent_id").status == "failed"`  
- Workspace: `get_state("agent_id").status == "failed"`

这些是"删除状态一致性"问题 — 如果 Runtime 返回 success 但 Lifecycle 状态是 failed，就有 bug。

### C.8 中途异常后的状态持久化

| 子项 | 状态 |
|------|------|
| Memory 在 Agent 异常时持久化？ | ❌ |
| Workspace 在 Agent 异常时保留？ | ❌ |
| State 在 Agent 异常时可查？ | ❌ |

**缺口**: Runtime 的 `except Exception` 块中有 `self._memory.persist()`，但无测试验证。

### C.9 取消任务

| 子项 | 状态 |
|------|------|
| 取消正在运行的 Agent？ | ❌ `test_cancel_running_agent` 取消的是 nonexistent agent |
| 取消后 Agent 状态为 cancelled？ | ❌ |
| 取消后 Memory 持久化？ | ❌ |
| 取消后 Workspace 保留？ | ❌ |

**缺口**: `test_cancel_running_agent` 是空的（取消一个不存在的 Agent 是 no-op）。需要真实取消测试。

```python
def test_cancel_running_agent(self, runtime):
    """取消正在运行的 Agent，状态应为 cancelled。"""
    runtime._gateway.register("slow", SlowMockBackend, default=True)
    task = asyncio.ensure_future(runtime.run(
        agent_id="cancel_me", task="slow task", backend="slow",
    ))
    await asyncio.sleep(0.1)  # 确保任务开始
    await runtime.cancel("cancel_me")
    result = await task
    assert result["status"] == "cancelled"
```

### C.10 Mock Backend 与真实 Backend 接口一致性

| 子项 | 状态 |
|------|------|
| MockBackend 实现 Backend 所有抽象方法？ | ✅ invoke/stream/capabilities 全部实现 |
| MockBackend 响应格式与真实 Backend 一致？ | ❌ 无契约测试 |
| 真实 Backend 的响应解析与 Mock 兼容？ | ❌ 无互操作性验证 |

**缺口**: 需要契约测试验证 MockBackend 的输出格式与 ClaudeBackend.parse_response() 兼容。

```python
def test_mock_backend_response_compatible_with_claude_parser(self):
    """MockBackend 返回的数据格式应能被 ClaudeBackend 的解析器消费。"""
    mock = MockBackend()
    req = BackendRequest(messages=[{"role": "user", "content": "hi"}])
    resp = mock.invoke(req)
    # ClaudeBackend 的 _parse_response 应能处理此格式
    cb = ClaudeBackend(config={"api_key": "test"})
    parsed = cb._parse_response({
        "id": "mock_msg",
        "model": "mock",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": resp.content}],
        "usage": {"input_tokens": resp.usage.input_tokens if resp.usage else 0,
                  "output_tokens": resp.usage.output_tokens if resp.usage else 0},
    })
    assert parsed.content == resp.content
```

---

## D: 测试质量检查

### D.1 重复测试

当前设计中存在以下重复：

| 重复 | 出现位置 | 建议 |
|------|---------|------|
| `MockBackend` 在 3 个文件中的重复定义 | test_runtime.py / test_gateway.py / test_e2e_mock.py | 统一到 mocks.py（已规划） |
| Tool 正常执行测试 × 多个文件 | test_tool.py / test_swe.py / test_swe_regression.py | 可以接受（不同粒度），检查是否过度 |
| 参数校验测试 × 多个工具 | test_tool.py（通用） / test_swe_regression.py | 可以接受，回归测试需要 |

**结论**: 无严重重复。统一 Mock 后重复自然消除。

### D.2 每个测试是否验证唯一行为

**问题测试**:

| 测试 | 问题 |
|------|------|
| `test_lifecycle_idle_to_completed` | 与 `test_run_has_status` 功能重叠 |
| `test_list_agents_after_run` | 与 `test_run_has_status` 路径重复 |
| `test_two_agents_sequential` | 与多个单 Agent 测试路径重复 |
| `test_run_with_json_tool_defs` | 几乎不验证任何行为 |

**建议**: 合并或删除上述 4 个测试，释放约 4 个测试配额给缺失场景。

### D.3 占位符测试

以下测试 body 为 `...`（空实现），在策略阶段尚可接受，进入实现时必须填充：

```python
# test_agent_workflow.py — 所有测试 body 都是 ...
TestAgentToolFailure.test_agent_reports_tool_failure      # ...
TestAgentToolFailure.test_agent_retries_after_failure     # ...
TestAgentToolFailure.test_agent_aborts_after_max_retries  # ...
TestAgentMultiToolSequence.test_write_then_read_then_report # ...
TestAgentMultiToolSequence.test_write_edit_read_cycle     # ...
TestAgentMultiToolSequence.test_grep_then_read_then_report # ...
TestAgentTaskVerification.test_verify_file_exists          # ...
TestAgentTaskVerification.test_verify_file_content          # ...
TestAgentTaskVerification.test_verify_after_fix_cycle      # ...
TestAgentWithMemory.test_agent_saves_tool_results_to_memory # ...
TestAgentWithMemory.test_agent_injects_memory_into_prompt  # ...
TestAgentWithMemory.test_memory_persisted_after_run        # ...
TestAgentMaxSteps.test_agent_stops_at_max_steps            # ...
TestAgentMaxSteps.test_agent_with_zero_steps               # ...
TestAgentEdgeCases.test_empty_task                         # ...
TestAgentEdgeCases.test_very_long_task                     # ...
TestAgentEdgeCases.test_backend_returns_only_tool_calls    # ...
TestAgentEdgeCases.test_backend_returns_only_text          # ...
TestAgentEdgeCases.test_agent_cancelled_mid_execution      # ...

# test_runtime.py — 部分测试 body 为 ...
TestRuntimePreflight.test_missing_backend_fails             # 有断言但依赖 fixture
TestRuntimePreflight.test_backend_unavailable               # ...
TestRuntimePreflight.test_unauthorized_key                  # ...
TestRuntimeCancel.test_cancel_running_agent                 # ...
```

**结论**: 约 23 个测试在策略阶段是 `...`，实现时必须填充。

---

## E: 修复方案

### E.1 Mock Backend 重设计

```python
# tests/mocks.py — 最终 7 个 Mock Backend

class SuccessBackend(Backend):          # 原名 MockBackend
    """正常返回预设回复。"""
    name = "success"

class AuthErrorBackend(Backend):        # 原名 AuthFailMockBackend
    """返回 401 认证错误。"""
    name = "auth_error"

class ConnectionErrorBackend(Backend):  # 新增
    """模拟网络连接失败（DNS/连接被拒绝）。"""
    name = "connection_error"

class TimeoutBackend(Backend):          # 替代 SlowMockBackend
    """模拟请求超时。抛出超时异常而非延迟返回。"""
    name = "timeout"

class InvalidResponseBackend(Backend):  # 新增
    """返回空响应或无法解析的响应。"""
    name = "invalid_response"

class FlakyBackend(Backend):            # 新增
    """前 N 次失败，之后成功。测试重试机制。"""
    name = "flaky"

class InfiniteLoopBackend(Backend):     # 新增
    """始终返回工具调用，永不返回 text。用于测试无限循环防护。"""
    name = "infinite_loop"
```

### E.2 覆盖缺口修复优先级

```
P0 🔴 立即修复（Mock 不可用则测试不可写）:
  ├─ 新增 ConnectionErrorBackend
  ├─ 新增 TimeoutBackend（真正抛出超时）
  ├─ 新增 InvalidResponseBackend
  ├─ 新增 FlakyBackend
  └─ 新增 InfiniteLoopBackend

P1 🔴 核心行为缺失:
  ├─ Agent 无限循环防护测试
  ├─ Agent 取消真实场景测试
  ├─ Tool 失败时 Agent 不宣称成功（Runtime 级）
  ├─ Backend 超时后 Agent 行为
  └─ Mock Backend 接口一致性契约测试

P2 🟡 现有占位符填充:
  ├─ TestAgentToolFailure (3 tests)
  ├─ TestAgentMultiToolSequence (3 tests)
  ├─ TestAgentTaskVerification (3 tests)
  ├─ TestAgentWithMemory (3 tests)
  ├─ TestAgentMaxSteps (2 tests)
  ├─ TestAgentEdgeCases (5 tests)
  └─ TestRuntimePreflight (3 tests)

P3 🟢 深度场景:
  ├─ Preflight API Key 空字符串测试
  ├─ 最终失败状态全组件一致性验证
  ├─ 中途异常后 Memory 持久化
  ├─ Backend 重试次数验证
  ├─ 状态管理器一致性测试
  └─ 验证失败恢复循环测试
```

### E.3 重复测试处理

| 操作 | 测试 |
|------|------|
| 合并 | `test_lifecycle_idle_to_completed` → 并入 `test_run_has_status` |
| 合并 | `test_list_agents_after_run` → 并入 `test_run_has_status` |
| 保留 | `test_two_agents_sequential` — 验证 Scheduler 行为，有独立价值 |
| 删或加强断言 | `test_run_with_json_tool_defs` — 当前几乎无断言 |

### E.4 测试计数修正

```
原设计: 479 测试
删除重复:   -4
新增 Mock: +0 (共享代码，无测试)
新增缺口:  +18
─────────────────
修正目标:  493 测试
```

新增的 18 个测试：

| 新增测试 | 覆盖缺口 |
|---------|---------|
| `test_preflight_rejects_empty_api_key` | B1 API Key 缺失 |
| `test_agent_does_not_loop_infinitely` | C1 无限循环 |
| `test_same_tool_fails_three_times_agent_aborts` | C2 重复 Tool 失败 |
| `test_backend_timeout_agent_does_not_claim_success` | C3 Backend 超时 |
| `test_backend_retry_on_flaky_connection` | C4 Backend 重试 |
| `test_verify_then_fix_then_reverify` | C6 验证恢复 |
| `test_failure_state_consistent_across_components` | C7 最终失败状态 |
| `test_memory_persisted_on_crash` | C8 异常持久化 |
| `test_cancel_running_agent` | C9 取消任务 |
| `test_mock_backend_interface_contract` | C10 接口一致性 |
| `test_agent_final_report_based_on_log_not_llm` | B5 铁律 3 |
| `test_preflight_outputs_friendly_message` | B1 Preflight 友好错误 |
| `test_lifecycle_state_after_max_steps` | B7 max_steps 状态 |
| `test_planning_to_running_transition` | B8 模式转换 |
| `test_agent_steps_never_exceed_max` | B7 max_steps 边界 |
| `test_backend_connection_error_agent_fails` | B3 连接错误 |
| `test_agent_seven_step_conversation` | B6 多步对话 |
| `test_agent_fails_on_all_tool_errors` | B4 Agent 工具失败 |

---

## 附录: 覆盖矩阵（修正版）

```
┌──────────────────────────────────┬──────┬──────┬──────┬──────┬──────┐
│ 场景                             │ Unit │ Int  │ Runt │ Work │ CLI  │
├──────────────────────────────────┼──────┼──────┼──────┼──────┼──────┤
│ 8 个关键行为                      │      │      │      │      │      │
│ API Key 缺失                    │  —   │  ✅  │  ✅  │  —   │  ✅  │
│ API Key 无效 (401)              │  —   │  ✅  │  ✅  │  —   │  —   │
│ Backend 不可用 (连接错误)        │  —   │  ✅  │  ✅  │  ✅  │  —   │
│ Tool 执行失败                   │  ✅  │  ✅  │  ✅  │  ✅  │  —   │
│ Tool 失败不清真成功             │  ✅  │  —   │  ✅  │  ✅  │  —   │
│ 多步 Agent Workflow (≥3步)      │  —   │  —   │  —   │  ✅  │  —   │
│ max_steps 超时                  │  —   │  —   │  ✅  │  ✅  │  —   │
│ Plan Mode 只读保护              │  ✅  │  ✅  │  ✅  │  —   │  ✅  │
├──────────────────────────────────┼──────┼──────┼──────┼──────┼──────┤
│ 10 个额外检查项                   │      │      │      │      │      │
│ Agent 无限循环                  │  —   │  —   │  ✅  │  ✅  │  —   │
│ 重复 Tool 失败                  │  ✅  │  —   │  —   │  ✅  │  —   │
│ Backend 超时                    │  —   │  —   │  ✅  │  ✅  │  —   │
│ Backend 重试                    │  ✅  │  —   │  —   │  —   │  —   │
│ Tool 重试                       │  ✅  │  —   │  —   │  ✅  │  —   │
│ 验证失败后恢复                   │  —   │  —   │  —   │  ✅  │  —   │
│ 最终失败状态一致性               │  —   │  —   │  ✅  │  ✅  │  —   │
│ 中途异常后状态持久化             │  —   │  —   │  ✅  │  —   │  —   │
│ 取消任务                         │  —   │  —   │  ✅  │  ✅  │  —   │
│ Mock/真实 Backend 接口一致       │  ✅  │  —   │  —   │  —   │  —   │
└──────────────────────────────────┴──────┴──────┴──────┴──────┴──────┘
```

修正后总计: **~493 测试**。

---

> **核心结论**
>
> 1. **Mock Backend**: 7 个类型中缺 3 个（ConnectionError/InvalidResponse/Flaky），需补 2 个（Timeout 改为真正超时）
> 2. **18 个场景**: 7 个完全覆盖 ✅，5 个部分覆盖 ⚠️，6 个完全未覆盖 ❌（无限循环、Backend 超时、Backend 重试、恢复循环、取消、接口一致性）
> 3. **测试质量**: 23 个 `...` 占位符 + 4 个重复测试 + 1 个弱断言测试
> 4. **最终目标**: 从原设计的 479 调整为 **~493 测试**，新增 18 个
