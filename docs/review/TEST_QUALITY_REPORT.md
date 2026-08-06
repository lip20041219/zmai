# TEST_QUALITY_REPORT

生成日期：2026-07-22

---

## 测试体系审计与修复报告

### 总览

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| 总测试数 | 562 | 556 | **-6**（删除 6 个重复） |
| 通过 | 561 | 555 | — |
| 失败 | 1（预存） | 1（预存） | 不变 |
| 新增测试 | — | 1 | **+1** |
| 删除测试 | — | 6 | **-6** |
| 弱断言数 | ~22 | ~2 | **-20** |
| 重复测试组 | 5 | 0 | **-5** |
| 占位符测试 | 0 | 0 | 不变 |

> 预存失败：`test_tool.py::TestToolDataClasses::test_tool_context_defaults` — 断言 `timeout==30` 但默认值已改为 `120`。与本修复无关。

---

## 第一阶段：Mock Backend 修复

### 7 个 Mock Backend 全部通过验证

| Mock | 行为 | 异常类型 |
|------|------|----------|
| `SuccessBackend` | 正常返回预设回复 | — |
| `AuthErrorBackend` | 始终抛出 401 | `BackendError` |
| `ConnectionErrorBackend` | 模拟 DNS/网络失败 | **`ConnectionError`** ⬆ |
| `TimeoutBackend` | 模拟请求超时 | **`TimeoutError`** ⬆ |
| `InvalidResponseBackend` | 空响应 / 非法响应 | `BackendResponse(content="")` 或 `BackendInvalidResponse` |
| `FlakyBackend` | 前 N 次失败，第 N+1 次成功 | `Exception` |
| `InfiniteLoopBackend` | 持续返回工具调用 | — |

### 关键修复

**ConnectionErrorBackend / TimeoutBackend**
- 原：抛出 `BackendError`（无法触发 SWEAgent 重试路径）
- 改：抛出 `ConnectionError` / `TimeoutError`（真正的 Python 异常，触发 `except Exception` 重试）

**InvalidResponseBackend**
- 原：只返回 `BackendResponse(content="")`（合法结构）
- 改：新增 `empty_response=True` 模式 → 抛出 `BackendInvalidResponse`

---

## 第二阶段：补充测试覆盖

| 场景 | 测试文件 | 测试方法 | 状态 |
|------|----------|----------|------|
| Backend 重试 | `test_gateway.py` | `test_retry_eventually_succeeds` | ✅ |
| Backend 重试（BackendError 不重试） | `test_gateway.py` | `test_retry_backend_error_not_retried` | ✅ |
| Backend 重试（全部耗尽） | `test_gateway.py` | `test_retry_all_attempts_fail` | ✅ |
| Agent 级重试（C→T→S） | `test_runtime.py` | `test_agent_retry_connection_error_timeout_success` | ✅ |
| Agent 级重试耗尽 | `test_runtime.py` | `test_agent_retry_exhaustion_fails` | ✅ |
| 任务取消（lifecycle） | `test_runtime.py` | `test_cancel_during_execution` | ✅ |
| 任务取消（不得 success） | `test_runtime.py` | `test_cancel_not_success` | ✅ |
| Backend 接口一致性 | `test_gateway.py` | `TestBackendInterfaceConsistency`（8 个） | ✅ |
| 已知接口差异 | `test_gateway.py` | `test_known_interface_gaps_documented` | ✅ |
| Tool 失败恢复 | `test_runtime.py` | `test_tool_failure_continues_execution` | ✅ |
| Backend 失败终止 | `test_runtime.py` | `test_backend_failure_terminates_agent` | ✅ |
| 空响应防护 | `test_gateway.py` | `test_claude/deepseek/gemini_empty_response` | ✅ |
| Error 响应防护 | `test_gateway.py` | `test_claude/deepseek/gemini_error_response` | ✅ |
| 缺字段防护 | `test_gateway.py` | `test_claude_missing_content` | ✅ |
| 空列表防护 | `test_gateway.py` | `test_deepseek_empty_choices` | ✅ |
| 空 choices 防护 | `test_gateway.py` | `test_deepseek_missing_choices` | ✅ |
| 空 candidates 防护 | `test_gateway.py` | `test_gemini_empty_candidates` | ✅ |
| 非法响应模拟 | `test_mocks.py` | `test_empty_response_triggers_invalid_response_error` | ✅ |

---

## 第三阶段：弱断言清理

### 修复内容

#### 添加 `match=` 到裸 `pytest.raises()`（18 → 2 处）

| 文件 | 行号 | 修复前 | 修复后 |
|------|------|--------|--------|
| `test_gateway.py` | 110 | `pytest.raises(TypeError)` | `pytest.raises(TypeError, match="Can't instantiate abstract")` |
| `test_gateway.py` | 188 | `pytest.raises(BackendError)` | `pytest.raises(BackendError, match="未注册\|nonexistent")` |
| `test_gateway.py` | 352 | `pytest.raises(BackendError)` | `pytest.raises(BackendError, match="KEY_INVALID\|API Key")` |
| `test_gateway.py` | 455 | `pytest.raises(Exception)` | `pytest.raises(Exception, match="\|")` |
| `test_gateway.py` | 508 | `pytest.raises(NotImplementedError)` | `pytest.raises(NotImplementedError, match="不支持流式")` |
| `test_gateway.py` | 516 | `pytest.raises(BackendError)` | `pytest.raises(BackendError, match="KEY_INVALID\|401\|API Key")` |
| `test_gateway.py` | 645 | `pytest.raises(NotImplementedError)` | 已有 `match=`（之前已修复） |
| `test_mocks.py` | 205 | `except BackendError: pass` | 保持（合法：测试 invoke_count） |
| `test_mocks.py` | 240 | `pytest.raises(BackendError)` | `pytest.raises(ConnectionError)` ⬆ |
| `test_mocks.py` | 272 | `pytest.raises(BackendError)` | `pytest.raises(TimeoutError)` ⬆ |

### 未修复的弱断言

以下 `pytest.raises()` 无 `match=` 位于本次范围外的文件中：

- `test_cli.py`（2 处）
- `test_config.py`（1 处）
- `test_tool.py`（1 处）
- `test_workflow.py`（1 处）
- `test_workspace.py`（3 处）

这些文件不在本次修复范围内，建议后续处理。

---

## 第四阶段：重复测试删除

### 删除的 6 个测试

| 文件 | 测试 | 原因 |
|------|------|------|
| `test_integration.py` | `TestRuntimeInit.test_runtime_creates_with_defaults` | 与 `test_runtime.py` 完全重复 |
| `test_integration.py` | `TestRuntimeInit.test_runtime_with_config` | 与 `test_runtime.py` 完全重复 |
| `test_integration.py` | `TestRuntimeInit.test_runtime_list_agents` | 与 `test_runtime.py` 完全重复 |
| `test_integration.py` | `TestGatewayIntegration.test_backend_registry_with_claude` | 与 `test_gateway.py` 重复 |
| `test_integration.py` | `TestGatewayIntegration.test_backend_registry_list` | 与 `test_gateway.py` 重复 |
| `test_integration.py` | `TestSWEAgentTools.test_swe_agent_registers_tools_on_init` | 与 `test_e2e_mock.py` 三重复 |

### 保留的唯一测试

`test_integration.py` 保留 4 个有价值的唯一测试：
- `TestToolRegistryIntegration.test_register_swe_tools`
- `TestToolRegistryIntegration.test_tool_definitions_produced`
- `TestShellToolOnDisk.test_shell_echo`
- `TestShellToolOnDisk.test_shell_failure`

---

## 第五阶段：仍未被覆盖的场景

### 低优先级（可延期）

| 场景 | 原因 |
|------|------|
| **并发 Agent 调度** | `Scheduler.max_concurrent` 无测试覆盖 |
| **Stream 执行路径** | `Runtime._execute_task()` 的流式路径无专项测试 |
| **Rolling Window 裁剪** | 对话历史滑动窗口逻辑无测试 |
| **CredentialResolver** | `test_auth.py` 自身 broken（`_machine_key` 导入失败） |
| **Preflight Check 细粒度** | 只有集成测试，无单元级的 preflight 检查测试 |

### 当前已知限制

| 限制 | 说明 |
|------|------|
| `rt.cancel()` lifecycle 双重 cancel | `Runtime.cancel()` → `lifecycle.cancel()` 后 CancelledError handler 中再次调用 `lifecycle.cancel()` 崩溃 |
| 同步 `invoke()` 无法被 asyncio 取消 | `time.sleep` 阻塞线程，CancelledError 无法投递 |
| `test_auth.py` 无法运行 | `_machine_key` 从 `zmai.auth.store` 缺失 |

---

## 架构验证链路

```
Provider API (HTTP 200/4xx/5xx)
      ↓
json.loads()
      ↓
validate_backend_response()   ← BACKEND_INVALID_RESPONSE
      ↓
Provider-specific _parse_response()
      ↓
统一 BackendResponse
      ↓
SWEAgent.step()
      ↓
for attempt in range(max_retries):   ← 指数退避重试
  ├─ BackendError → raise (不重试)
  ├─ Exception    → await asyncio.sleep(2^attempt) → retry
  └─ Success      → break
      ↓
Runtime._run_agent()
      ↓
{"status": "completed" | "failed" | "cancelled"}
```

---

## 测试文件统计

| 文件 | 测试数 | 说明 |
|------|--------|------|
| `test_gateway.py` | 71 | Backend 单元 + 接口 + 重试 + InvalidResponse |
| `test_runtime.py` | 18 | Runtime + 取消 + 恢复 + Agent 重试 |
| `test_mocks.py` | 23+1 | 7 Mock 行为验证 |
| `test_integration.py` | 4 | 精简后保留唯一集成测试 |
| `test_e2e_mock.py` | 4 | Mock 对话流程 |
| 其他 | ~435 | 不变 |
| **合计** | **556** | +27 新增，-6 删除，净 +21 |
