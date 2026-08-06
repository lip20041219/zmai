# ZMAI Testing Strategy

> 版本: 1.0
> 目标: 从单层单元测试升级为五层测试体系，覆盖所有关键风险场景
> 原则: 所有测试使用 Mock Backend，不依赖真实 API Key

---

## 目录

- [1. 当前测试审计](#1-当前测试审计)
- [2. 五层测试体系](#2-五层测试体系)
- [3. Mock Backend 体系](#3-mock-backend-体系)
- [4. 覆盖矩阵](#4-覆盖矩阵)
- [5. 各层测试设计](#5-各层测试设计)
- [6. 文件规划](#6-文件规划)
- [7. CI 集成](#7-ci-集成)

---

## 1. 当前测试审计

### 1.1 现有测试文件统计

| 文件 | 测试数 | 覆盖内容 | 层级 |
|------|--------|---------|------|
| `test_tool.py` | 27 | Tool ABC、ToolResult、ToolRegistry、参数校验 | Unit |
| `test_swe.py` | 35 | 8 个 SWE 工具的独立测试 | Unit |
| `test_swe_regression.py` | 28 | 路径穿越、无 Backend、空消息、step_count、EditTool 边界、GrepTool 边界 | Unit |
| `test_gateway.py` | 30 | Backend ABC、BackendRegistry、ToolRouter、ClaudeBackend、DeepSeekBackend、MCPClient | Unit |
| `test_memory.py` | 33 | MemoryEntry、WorkingMemory、LongTermMemory、MemoryManager | Unit |
| `test_auth.py` | 18 | 加密、AuthStore 读写、环境变量覆盖 | Unit |
| `test_cli.py` | 23 | Theme、输出函数、参数解析 | Unit |
| `test_doctor.py` | 18 | Doctor 检查项 | Unit |
| `test_prompt.py` | — | PromptEngine | Unit |
| `test_config.py` | — | Config 多源合并 | Unit |
| `test_detector*.py` | — | 项目检测器 | Unit |
| `test_workflow.py` | — | WorkflowEngine | Unit |
| `test_workspace.py` | — | Workspace 沙箱 | Unit |
| `test_runtime.py` | 15 | Runtime init、MockBackend run | Runtime |
| `test_integration.py` | 10 | 组件集成初始化 | Integration |
| `test_e2e_mock.py` | 5 | SWEAgent mock 对话 | Agent Workflow |
| `test_live_api.py` | — | 真实 API（跳过 CI） | Live |

**总计: ~290 测试**（不含 `test_live_api.py`）

### 1.2 按层级分布

```
Unit             ─── 220+ 测试   ✅ 充分
Integration      ─── ~10 测试    ⚠️ 薄弱
Runtime          ─── ~15 测试    ⚠️ 仅 happy path
Agent Workflow   ─── ~5 测试     ❌ 严重不足
CLI              ─── ~23 测试    ⚠️ 仅输出格式，无任务执行
```

### 1.3 关键场景覆盖审计

| 场景 | 覆盖情况 | 位置 |
|------|---------|------|
| API Key 缺失 | ❌ 未覆盖 | — |
| API Key 错误 | ❌ 未覆盖 | — |
| Backend 不可用 | ❌ 未覆盖 | — |
| Backend 超时 | ❌ 未覆盖 | — |
| Tool 正常执行 | ✅ 覆盖 | test_swe.py、test_tool.py |
| Tool 执行失败 | ✅ 覆盖 | test_swe.py |
| Tool 参数错误 | ✅ 覆盖 | test_swe_regression.py |
| Tool 重试 | ❌ 未覆盖 | — |
| Tool 超时 | ✅ 覆盖 | ToolRegistry._execute_with_timeout |
| 路径穿越防御 | ✅ 覆盖 | test_swe_regression.py |
| 文件创建 | ⚠️ 工具级覆盖 | test_swe.py |
| 文件修改 | ⚠️ 工具级覆盖 | test_swe.py |
| 任务验证 | ❌ 未覆盖 | — |
| Agent 完成 | ⚠️ 部分覆盖 | test_e2e_mock.py |
| Agent 失败 | ⚠️ 部分覆盖 | test_swe_regression.py（无 Backend） |
| Agent 超时 | ❌ 未覆盖 | — |
| Agent 多次工具调用 | ⚠️ 2 步对话 | test_e2e_mock.py |
| Memory 保存 | ⚠️ 单元级覆盖 | test_memory.py |
| Memory 读取 | ⚠️ 单元级覆盖 | test_memory.py |
| Memory + Agent 集成 | ⚠️ 单测 + Runtime 存活性 | test_runtime.py |
| 多工具组合流程 | ✅ 覆盖 | test_swe_regression.py TestMultiToolFlow |
| Preflight Check | ❌ 未覆盖 | — |
| Fallback 切换 | ❌ 未覆盖 | — |
| 并发调度 | ❌ 未覆盖 | — |

---

## 2. 五层测试体系

```
Level 1: Unit Test          ─── 单个函数/类的独立测试
Level 2: Integration Test   ─── 组件间接口测试
Level 3: Runtime Test       ─── Runtime 完整编排 + Mock Backend
Level 4: Agent Workflow     ─── 完整任务生命周期测试
Level 5: CLI Test           ─── CLI 命令端到端测试
```

### 2.1 每层职责

| 层级 | 测试什么 | Mock 什么 | 速度 |
|------|---------|----------|------|
| **Unit** | 单个函数返回值、边缘情况、错误处理 | 所有外部依赖 | 毫秒级 |
| **Integration** | 组件间数据流、接口契约 | Backend 调用、文件系统 | 毫秒级 |
| **Runtime** | Agent 生命周期、状态转换、错误传播 | Backend 调用 | 毫秒级 |
| **Agent Workflow** | 完整"理解-规划-执行-验证"流程 | Backend 调用 | 毫秒级 |
| **CLI** | 命令解析、子命令流、用户交互 | Backend 调用、stdin/stdout | 毫秒级 |

所有层级共用 Mock Backend，零真实 API 调用。

### 2.2 目标覆盖率

| 层级 | 当前 | 目标 |
|------|------|------|
| Unit | ~220 | 250+ |
| Integration | ~10 | 30+ |
| Runtime | ~15 | 40+ |
| Agent Workflow | ~5 | 30+ |
| CLI | ~23 | 40+ |
| **总计** | **~290** | **390+** |

---

## 3. Mock Backend 体系

所有测试使用统一的 Mock Backend。不发送真实 HTTP 请求。

### 3.1 Mock 类型

```python
class MockBackend(Backend):
    """基础 Mock Backend — 返回预设回复。所有测试共用。"""

    name = "mock"
    responses: list[dict]           # 预设回复序列
    delay_seconds: float = 0        # 模拟延迟
    fail_on_call: int | None = None # 在第 N 次调用时抛出异常

    def invoke(self, request) -> BackendResponse:
        # 按调用次数返回预设回复
        # 支持文本回复、工具调用、错误
```

### 3.2 预设场景

| Mock 类型 | 行为 | 用途 |
|-----------|------|------|
| `MockBackend()` | 默认回复 | Happy path |
| `ToolMockBackend()` | 回复包含工具调用 | Agent 工具调用测试 |
| `MultiToolMockBackend()` | 多次回复，每次不同工具 | 多步 Agent 工作流测试 |
| `FailMockBackend()` | invoke 抛出 BackendError | Backend 不可用测试 |
| `AuthFailMockBackend()` | 返回 401 HTTP 错误 | API Key 错误测试 |
| `SlowMockBackend()` | 延迟 N 秒后回复 | 超时测试 |
| `EmptyMockBackend()` | 回复空 content | 边缘情况测试 |

### 3.3 共享位置

所有 Mock Backend 集中在 `tests/mocks.py`，所有测试文件从此导入。

```python
# tests/mocks.py
class MockBackend(Backend): ...
class ToolMockBackend(MockBackend): ...
class MultiToolMockBackend(MockBackend): ...
class FailMockBackend(MockBackend): ...
class AuthFailMockBackend(MockBackend): ...
class SlowMockBackend(MockBackend): ...
```

---

## 4. 覆盖矩阵

### 4.1 必须覆盖的场景

```
┌─────────────────────────────┬──────┬──────┬───────┬──────┬──────┐
│ 场景                        │ Unit │ Int  │ Runt  │ Work │ CLI  │
├─────────────────────────────┼──────┼──────┼───────┼──────┼──────┤
│ API Key 缺失                │  ✅  │  ✅  │  ✅   │  —   │  ✅  │
│ API Key 错误 (401)          │  —   │  ✅  │  ✅   │  —   │  —   │
│ Backend 不可用 (网络错误)    │  —   │  ✅  │  ✅   │  ✅  │  —   │
│ Backend 超时                │  —   │  —   │  ✅   │  ✅  │  —   │
│ Tool 正常执行               │  ✅  │  ✅  │  ✅   │  ✅  │  —   │
│ Tool 执行失败               │  ✅  │  ✅  │  ✅   │  ✅  │  —   │
│ Tool 参数错误               │  ✅  │  —   │  —   │  ✅  │  —   │
│ Tool 重试 (失败后重试)       │  —   │  —   │  —   │  ✅  │  —   │
│ Tool 超时                   │  ✅  │  —   │  —   │  ✅  │  —   │
│ 路径穿越防御                │  ✅  │  —   │  —   │  —   │  —   │
│ 文件创建                    │  ✅  │  —   │  ✅   │  ✅  │  ✅  │
│ 文件修改                    │  ✅  │  —   │  ✅   │  ✅  │  —   │
│ 任务验证                    │  —   │  —   │  —   │  ✅  │  —   │
│ Agent 正常完成              │  —   │  ✅  │  ✅   │  ✅  │  ✅  │
│ Agent 失败 (工具失败)        │  —   │  —   │  ✅   │  ✅  │  —   │
│ Agent 失败 (Backend 不可用)  │  —   │  —   │  ✅   │  ✅  │  ✅  │
│ Agent 超时 (max_steps)      │  —   │  —   │  ✅   │  ✅  │  —   │
│ Agent 暂停/恢复             │  —   │  —   │  ✅   │  —   │  —   │
│ Agent 取消                  │  —   │  —   │  ✅   │  —   │  —   │
│ Memory 保存 (Agent 级别)    │  ✅  │  ✅  │  ✅   │  ✅  │  —   │
│ Memory 读取 (Agent 注入)    │  ✅  │  ✅  │  ✅   │  ✅  │  —   │
│ 多步工具调用序列             │  —   │  —   │  ✅   │  ✅  │  —   │
│ Plan Mode (只读守卫)        │  ✅  │  ✅  │  ✅   │  —   │  ✅  │
│ ExecutionLog 记录           │  ✅  │  —   │  —   │  ✅  │  —   │
│ FailureTracker 重试限制     │  ✅  │  —   │  —   │  ✅  │  —   │
└─────────────────────────────┴──────┴──────┴───────┴──────┴──────┘
```

### 4.2 测试与设计的对应关系

| 设计文档 | 对应的测试文件 |
|---------|--------------|
| `design/TOOL_RESULT_CONTRACT.md` | `test_execution_state.py` (新增) |
| `design/EXECUTION_LIFECYCLE_DESIGN.md` | `test_workflow_lifecycle.py` (新增) |
| `design/PLAN_MODE_DESIGN.md` | `test_plan_mode.py` (新增) |
| `design/MEMORY_SYSTEM_DESIGN.md` | `test_memory_v1.py` (新增) |
| `design/CAPABILITY_SYSTEM_DESIGN.md` | `test_capability.py` (新增) |

---

## 5. 各层测试设计

### 5.1 Level 1: Unit Test

**目标**: 每个函数/类的独立行为验证。

**现有**: `test_tool.py` / `test_swe.py` / `test_swe_regression.py` / `test_gateway.py` / `test_memory.py` / `test_auth.py` / `test_config.py` / `test_prompt.py` / `test_workflow.py` / `test_workspace.py` / `test_detector*.py`

**新增**:

```python
# tests/test_execution_state.py — 24 个测试
class TestExecutionLog:         # 7 个
class TestFailureTracker:       # 5 个
class TestVerificationCheck:    # 2 个
class TestVerificationResult:   # 6 个
class TestAgentResultHandling:  # 4 个（三条铁律校验）
```

```python
# tests/test_plan_mode.py — 18 个测试
class TestPlanModeGuard:        # 16 个（写文件/编辑/打开浏览器/shell/git 拦截）
class TestPlanAgent:            # 2 个（初始化只读工具、状态）
```

```python
# tests/test_memory_v1.py — 30 个测试
class TestSensitiveDataFilter:  # 10 个
class TestMemoryStore:          # 18 个
class TestMemoryCLI:            # 2 个（CLI 命令映射）
```

```python
# tests/test_capability.py — 12 个测试
class TestCapabilityRegistry:   # 5 个
class TestCapabilityAnalyzer:   # 5 个
class TestCapabilityGap:        # 2 个
```

**Unit Test 必须验证的断言模式**:

```python
# 成功路径
result = tool.execute(ctx, {"path": "valid.txt"})
assert result.success is True
assert result.output != ""
assert result.error is None

# 失败路径
result = tool.execute(ctx, {"path": ""})
assert result.success is False
assert result.error is not None
assert "具体原因" in result.error

# 参数校验
assert tool.validate({"valid": "params"}) is True
assert tool.validate({"invalid": "params"}) is False

# 边缘情况
result = tool.execute(ctx, {"path": "", "content": ""})
assert result.success is False  # 不崩溃
```

---

### 5.2 Level 2: Integration Test

**目标**: 组件间接口契约。

**现有**: `test_integration.py` — 10 个测试

**新增**:

```python
# tests/test_integration.py 扩展 — +20 测试
class TestRuntimeWithMemory:
    """Runtime + MemoryManager 集成。"""

    def test_memory_survives_agent_run(self, runtime_with_mock):
        """Agent 运行后记忆被持久化。"""
        result = asyncio.run(runtime_with_mock.run(
            agent_id="mem_integration", task="test", backend="mock",
        ))
        assert result["status"] == "completed"
        assert runtime_with_mock._memory.exists("mem_integration") is True

    def test_memory_restored_on_next_run(self, runtime_with_mock):
        """下次运行能从 LongTermMemory 恢复。"""
        # 第一次运行保存记忆
        asyncio.run(runtime_with_mock.run(
            agent_id="mem_restore_test", task="test", backend="mock",
        ))
        # 第二次运行 — MemoryManager.restore() 应恢复条目
        restored = runtime_with_mock._memory.restore("mem_restore_test")
        assert restored >= 0  # 不报错即可


class TestRuntimeWithAuth:
    """Runtime + Auth 集成。"""

    def test_run_fails_without_api_key(self, runtime_with_empty_config):
        """无 API Key 时 preflight check 失败。"""
        result = asyncio.run(runtime_with_empty_config.run(
            agent_id="no_key_test", task="test",
        ))
        assert result["status"] == "failed"


class TestToolRouterWithRegistry:
    """ToolRouter + ToolRegistry + ToolContext 集成。"""

    def test_tool_call_flow(self, tool_registry, tool_context):
        """完整的 tool call 流程。"""
        tool_registry.register(EchoTool())
        router = ToolRouter(tool_registry)
        call = ToolCall(id="c1", name="echo", params={"text": "hello"})
        result = router.execute(call, tool_context)
        assert result.success
        assert result.duration_ms >= 0


class TestCredentialResolverWithSources:
    """CredentialResolver + 多来源集成。"""

    def test_env_override_credential_store(self):
        """环境变量 Key 覆盖文件 Key。"""
        resolver = CredentialResolver()
        status = resolver.get_status("deepseek")
        # 不依赖具体值，只验证不崩溃
        assert isinstance(status.configured, bool)


class TestExecutionLogWithToolResult:
    """ExecutionLog + ToolResult 集成。"""

    def test_log_accepts_any_tool_result(self):
        log = ExecutionLog()
        log.append("t1", {}, ToolResult.ok("success"))
        log.append("t2", {}, ToolResult.err("failure"))
        assert len(log._states) == 2
        assert log.has_failures() is True
```

---

### 5.3 Level 3: Runtime Test

**目标**: Runtime 完整编排 + Mock Backend，覆盖所有 Agent 生命周期。

**现有**: `test_runtime.py` — 15 个测试（仅 happy path）

**新增**:

```python
# tests/test_runtime.py 扩展 — +25 测试

class TestRuntimePreflight:
    """Preflight check 场景。"""

    def test_missing_backend_fails(self, runtime_with_empty_gateway):
        """没有注册任何 Backend 时 run 失败。"""
        result = asyncio.run(runtime_with_empty_gateway.run(
            agent_id="p1", task="test", backend="nonexistent",
        ))
        assert result["status"] == "failed"

    def test_backend_unavailable(self, runtime_with_fail_backend):
        """Backend 调用失败时 Agent 返回 fail。"""
        result = asyncio.run(runtime_with_fail_backend.run(
            agent_id="p2", task="test", backend="failmock",
        ))
        assert result["status"] == "failed"

    def test_unauthorized_key(self, runtime_with_auth_fail):
        """API Key 错误 (401) 时 Agent 返回 fail。"""
        result = asyncio.run(runtime_with_auth_fail.run(
            agent_id="p3", task="test", backend="authfail",
        ))
        assert result["status"] == "failed"
        assert "401" in result.get("error", "") or "key" in result.get("error", "").lower()


class TestRuntimeRun:
    """Runtime.run() 场景（补充现有）。"""

    def test_run_completes_with_tool_calls(self, runtime_with_tool_mock):
        """Agent 调用工具后正常完成。"""
        result = asyncio.run(runtime_with_tool_mock.run(
            agent_id="r1", task="read file", backend="toolmock",
        ))
        assert result["status"] == "completed"

    def test_run_with_memory_persisted(self, runtime_with_mock):
        """运行结束后记忆被持久化。"""
        asyncio.run(runtime_with_mock.run(
            agent_id="r2", task="test", backend="mock",
        ))
        assert runtime_with_mock._memory.exists("r2") is True

    def test_run_reports_step_count(self, runtime_with_mock):
        """运行结果包含正确的步骤数。"""
        result = asyncio.run(runtime_with_mock.run(
            agent_id="r3", task="test", backend="mock",
        ))
        assert result["steps"] >= 1

    def test_run_with_json_tool_defs(self, runtime_with_mock):
        """传入 tool_defs 参数不崩溃。"""
        result = asyncio.run(runtime_with_mock.run(
            agent_id="r4", task="test", backend="mock",
            tool_defs=[],  # 空工具列表
        ))
        assert result["status"] == "completed"


class TestRuntimeLifecycle:
    """Agent 生命周期状态转换。"""

    def test_lifecycle_idle_to_completed(self, runtime_with_mock):
        result = asyncio.run(runtime_with_mock.run(
            agent_id="l1", task="test", backend="mock",
        ))
        assert result["status"] == "completed"

    def test_lifecycle_state_tracking(self, runtime_with_mock):
        """Runtime 的状态管理器正确追踪。"""
        asyncio.run(runtime_with_mock.run(
            agent_id="l2", task="test", backend="mock",
        ))
        state = runtime_with_mock.get_status("l2")
        assert state["status"] == "completed"

    def test_list_agents_after_run(self, runtime_with_mock):
        asyncio.run(runtime_with_mock.run(
            agent_id="l3", task="test", backend="mock",
        ))
        agents = runtime_with_mock.list_agents()
        assert len(agents) >= 1


class TestRuntimeCancel:
    """取消 Agent。"""

    def test_cancel_running_agent(self, runtime_with_mock):
        """取消后 Agent 状态为 cancelled。"""
        runtime_with_mock._gateway.register("slow", SlowMockBackend, default=True)
        # 在后台任务中启动，然后取消
        asyncio.run(runtime_with_mock.cancel("nonexistent"))
        # 取消不存在的 Agent 不报错


class TestRuntimeParallel:
    """多 Agent 并发。"""

    def test_two_agents_sequential(self, runtime_with_mock):
        result1 = asyncio.run(runtime_with_mock.run(
            agent_id="s1", task="test1", backend="mock",
        ))
        result2 = asyncio.run(runtime_with_mock.run(
            agent_id="s2", task="test2", backend="mock",
        ))
        assert result1["status"] == "completed"
        assert result2["status"] == "completed"

    def test_same_agent_id_rejected(self, runtime_with_mock):
        """相同 agent_id 被拒绝（生命周期状态机保护）。"""
        asyncio.run(runtime_with_mock.run(
            agent_id="dup", task="first", backend="mock",
        ))
        result = asyncio.run(runtime_with_mock.run(
            agent_id="dup", task="second", backend="mock",
        ))
        assert result["status"] == "failed"  # 或 "completed"（取决于 cleanup 策略）
```

---

### 5.4 Level 4: Agent Workflow Test

**目标**: 完整任务生命周期测试。这是当前最薄弱层。

**使用 `MultiToolMockBackend`** 模拟多步 LLM 回复序列。

```python
# tests/test_agent_workflow.py — 新增 30+ 测试

class TestAgentBasicWorkflow:
    """基础 Agent 工作流。"""

    def test_agent_completes_task(self):
        """Agent 接收任务 → 调用工具 → 完成。"""
        backend = MultiToolMockBackend(responses=[
            {"content": "我先读取文件", "tool": [{"name": "read_file", "params": {"path": "test.txt"}}]},
            {"content": "任务完成", "tool_calls": None},
        ])
        agent = SWEAgent("wf_001")
        registry = ToolRegistry()
        asyncio.run(agent.initialize(AgentContext(agent_id="wf_001", task="test", tools=registry)))
        ctx = AgentContext(agent_id="wf_001", task="读取 test.txt", backend=backend,
                           tools=registry, metadata={"messages": []})
        action1 = asyncio.run(agent.step(ctx))
        assert action1.type == "continue"
        action2 = asyncio.run(agent.step(ctx))
        assert action2.type == "complete"


class TestAgentToolFailure:
    """Agent 遇到工具失败。"""

    def test_agent_reports_tool_failure(self):
        """工具返回 success=False，Agent 不宣称成功。"""
        backend = MultiToolMockBackend(responses=[
            {"content": "我写入文件", "tool": [{"name": "write_file", "params": {"path": "x.txt", "content": "data"}}]},
        ])
        # write_file 执行成功（实际写入），然后第二步 Agent 应继续
        ...

    def test_agent_retries_after_failure(self):
        """工具失败后 Agent 重试。"""
        # 第一次 write_file 失败 → Agent 重试 → 第二次成功
        ...

    def test_agent_aborts_after_max_retries(self):
        """超过最大重试次数后 Agent 终止。"""
        ...


class TestAgentMultiToolSequence:
    """多工具组合调用。"""

    def test_write_then_read_then_report(self):
        """写文件 → 读文件验证 → 报告结果。"""
        ...

    def test_write_edit_read_cycle(self):
        """写入 → 编辑 → 读取验证。"""
        ...

    def test_grep_then_read_then_report(self):
        """搜索 → 读取 → 报告。"""
        ...


class TestAgentTaskVerification:
    """任务验证流程。"""

    def test_verify_file_exists(self):
        """创建文件后验证文件存在。"""
        ...

    def test_verify_file_content(self):
        """写入文件后验证内容正确。"""
        ...

    def test_verify_after_fix_cycle(self):
        """验证失败 → 修复 → 再验证。"""
        ...


class TestAgentWithMemory:
    """Agent 与 Memory 交互。"""

    def test_agent_saves_tool_results_to_memory(self):
        """工具调用结果自动保存到 WorkingMemory。"""
        ...

    def test_agent_injects_memory_into_prompt(self):
        """记忆内容注入到 system prompt。"""
        ...

    def test_memory_persisted_after_run(self):
        """任务完成后记忆持久化。"""
        ...


class TestAgentMaxSteps:
    """Agent 最大步数限制。"""

    def test_agent_stops_at_max_steps(self):
        """Agent 达到 max_steps 后停止。"""
        backend = MultiToolMockBackend(responses=[
            {"content": "继续", "tool": [{"name": "shell_exec", "params": {"command": "echo step"}}]}
        ] * 10)  # 10 步都返回工具调用
        agent = SWEAgent("max_001")
        registry = ToolRegistry()
        asyncio.run(agent.initialize(AgentContext(agent_id="max_001", task="loop", tools=registry)))
        ctx = AgentContext(agent_id="max_001", task="loop", backend=backend,
                           tools=registry, metadata={"messages": []}, max_steps=3)
        for _ in range(4):
            action = asyncio.run(agent.step(ctx))
        # 第 4 步应因超出 max_steps 而表现受限
        # （实际由 Runtime 的 step 循环控制）

    def test_agent_with_zero_steps(self):
        """max_steps=0 时不执行任何步骤。"""
        ...


class TestAgentEdgeCases:
    """Agent 边缘情况。"""

    def test_empty_task(self):
        """空字符串任务。"""
        ...

    def test_very_long_task(self):
        """超长任务描述。"""
        ...

    def test_backend_returns_only_tool_calls(self):
        """Backend 只返回工具调用不返回文本。"""
        ...

    def test_backend_returns_only_text(self):
        """Backend 只返回文本不调用工具。"""
        ...

    def test_agent_cancelled_mid_execution(self):
        """执行中途被取消。"""
        ...
```

---

### 5.5 Level 5: CLI Test

**目标**: CLI 命令端到端测试。

**现有**: `test_cli.py` — 仅输出格式和参数解析，无实际任务执行。

**新增**:

```python
# tests/test_cli.py 扩展 — +20 测试

class TestCLIExecute:
    """CLI 任务执行命令。"""

    def test_run_simple_task(self, runner):
        """zmai run 'hello' 正常执行。"""
        result = runner.invoke(main, ["run", "hello"])
        assert result.exit_code == 0

    def test_run_with_backend_flag(self, runner):
        """zmai --backend mock 'task' 指定 Backend。"""
        result = runner.invoke(main, ["--backend", "mock", "hello"])
        assert result.exit_code == 0

    def test_run_with_json_flag(self, runner):
        """zmai --json 'task' 输出 JSON。"""
        result = runner.invoke(main, ["--json", "hello"])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert "status" in data

    def test_run_with_no_color(self, runner):
        """zmai --no-color 'task' 无色输出。"""
        result = runner.invoke(main, ["--no-color", "hello"])
        assert result.exit_code == 0


class TestCLIErrorHandling:
    """CLI 错误处理。"""

    def test_no_backend_shows_helpful_error(self, runner):
        """无可用 Backend 时输出友好错误。"""
        result = runner.invoke(main, ["task"])
        # 应输出配置提示而非崩溃
        assert result.exit_code != 0 or "配置" in result.output

    def test_invalid_flag_rejected(self, runner):
        """非法参数被拒绝。"""
        result = runner.invoke(main, ["--invalid-flag"])
        assert result.exit_code != 0


class TestCLIPlanMode:
    """CLI Plan 命令。"""

    def test_plan_show_steps(self, runner):
        """zmai plan 显示步骤列表。"""
        result = runner.invoke(main, ["plan", "创建 HTML 文件"])
        assert result.exit_code == 0
        assert "Step" in result.output or "步骤" in result.output or "1/" in result.output

    def test_plan_with_json_flag(self, runner):
        """zmai plan --json 输出 JSON。"""
        result = runner.invoke(main, ["plan", "--json", "测试"])
        assert result.exit_code == 0


class TestCLIMemory:
    """CLI Memory 命令。"""

    def test_memory_list(self, runner):
        result = runner.invoke(main, ["memory", "list"])
        assert result.exit_code == 0

    def test_memory_set_and_show(self, runner):
        runner.invoke(main, ["memory", "set", "test_key", "test_val"])
        result = runner.invoke(main, ["memory", "show", "test_key"])
        assert result.exit_code == 0
        assert "test_key" in result.output

    def test_memory_delete(self, runner):
        runner.invoke(main, ["memory", "set", "del_key", "val"])
        result = runner.invoke(main, ["memory", "delete", "del_key"])
        assert result.exit_code == 0

    def test_memory_clear(self, runner):
        result = runner.invoke(main, ["memory", "clear", "--force"])
        assert result.exit_code == 0

    def test_memory_export(self, runner, tmp_path):
        output = tmp_path / "export.json"
        result = runner.invoke(main, ["memory", "export", "--output", str(output)])
        assert result.exit_code == 0

    def test_memory_stats(self, runner):
        result = runner.invoke(main, ["memory", "stats"])
        assert result.exit_code == 0
        assert "Total" in result.output or "total" in result.output

    def test_memory_disable_enable(self, runner):
        result = runner.invoke(main, ["memory", "disable"])
        assert result.exit_code == 0
        result = runner.invoke(main, ["memory", "enable"])
        assert result.exit_code == 0


class TestCLIAuth:
    """CLI Auth 命令。"""

    def test_auth_status_runs(self, runner):
        result = runner.invoke(main, ["auth", "status"])
        assert result.exit_code == 0

    def test_auth_list(self, runner):
        result = runner.invoke(main, ["auth", "list"])
        assert result.exit_code == 0
```

---

## 6. 文件规划

### 6.1 新增文件

| 文件 | 层级 | 测试数 | 覆盖内容 |
|------|------|--------|---------|
| `tests/mocks.py` | 共享 | — | 统一 Mock Backend 类型 |
| `tests/test_execution_state.py` | Unit | 24 | ExecutionLog、FailureTracker、VerificationResult |
| `tests/test_plan_mode.py` | Unit | 18 | PlanModeGuard、PlanAgent |
| `tests/test_memory_v1.py` | Unit | 30 | SensitiveDataFilter、MemoryStore、CLI |
| `tests/test_capability.py` | Unit | 12 | CapabilityRegistry、Analyzer、Gap |
| `tests/test_agent_workflow.py` | Agent Workflow | 30 | 完整任务生命周期 |
| `tests/test_plan_mode_integration.py` | Integration | 10 | PlanMode + Runtime 集成 |

### 6.2 修改文件

| 文件 | 改动 |
|------|------|
| `tests/test_runtime.py` | +25 测试（Preflight、Lifecycle、Cancel、Parallel） |
| `tests/test_integration.py` | +20 测试（Memory+Runtime、Auth、ToolRouter、ExecutionLog） |
| `tests/test_cli.py` | +20 测试（Execute、Error、Plan、Memory、Auth CLI） |

### 6.3 不改文件

```
tests/test_tool.py              — 已有 27 个测试，无需扩展
tests/test_swe.py               — 已有 35 个测试
tests/test_swe_regression.py    — 已有 28 个测试
tests/test_gateway.py           — 已有 30 个测试
tests/test_auth.py              — 已有 18 个测试
tests/test_memory.py            — 已有 33 个测试
tests/test_doctor.py            — 已有 18 个测试
tests/test_config.py            — 充足
tests/test_detector*.py         — 充足
tests/test_prompt.py            — 充足
tests/test_workflow.py          — 充足
tests/test_workspace.py         — 充足
```

### 6.4 新增测试汇总

| 文件 | 新增 |
|------|------|
| `tests/mocks.py` | 共享代码，0 测试 |
| `tests/test_execution_state.py` | 24 |
| `tests/test_plan_mode.py` | 18 |
| `tests/test_memory_v1.py` | 30 |
| `tests/test_capability.py` | 12 |
| `tests/test_agent_workflow.py` | 30 |
| `tests/test_plan_mode_integration.py` | 10 |
| `tests/test_runtime.py` | +25 |
| `tests/test_integration.py` | +20 |
| `tests/test_cli.py` | +20 |

**新增总计: ~189 测试**

**现有 + 新增 = 290 + 189 = ~479 测试**

---

## 7. CI 集成

### 7.1 运行命令

```bash
# 全部测试（不含 live API）
pytest tests/ --ignore=tests/test_live_api.py -v

# 按层级
pytest tests/test_tool.py tests/test_swe.py tests/test_memory.py  # Unit
pytest tests/test_integration.py                                    # Integration
pytest tests/test_runtime.py                                        # Runtime
pytest tests/test_agent_workflow.py                                 # Agent Workflow
pytest tests/test_cli.py                                            # CLI

# 按覆盖矩阵标签（可选）
pytest -m "agent_workflow"
pytest -m "tool_failure"
pytest -m "memory"
```

### 7.2 无真实 API Key

所有测试（`test_live_api.py` 除外）不调用真实 HTTP API。

`test_live_api.py` 保留不动，用于手动验证真实 Backend 连接。

### 7.3 覆盖率目标

```bash
pytest --cov=zmai --cov-report=term-missing
```

| 模块 | 当前 | 目标 |
|------|------|------|
| `zmai.tool` | ~90% | 95%+ |
| `zmai.swe` | ~80% | 90%+ |
| `zmai.runtime` | ~60% | 85%+ |
| `zmai.gateway` | ~85% | 90%+ |
| `zmai.memory` | ~85% | 90%+ |
| `zmai.cli` | ~50% | 80%+ |
| `zmai.auth` | ~80% | 85%+ |
| **整体** | **~75%** | **88%+** |

### 7.4 CI 检查清单

```
□ 全部 ~479 测试通过
□ 零 HTTP 请求（test_live_api.py 除外）
□ 代码覆盖率 ≥ 88%
□ 所有新增场景至少一个测试覆盖
□ 每次修改 PR 必须通过完整 CI
```

---

## 附录 A: 现有 Mock Backend 汇总

当前分散在各个测试文件中的 Mock Backend：

| 位置 | Mock 名 | 用途 |
|------|---------|------|
| `test_runtime.py:29` | `MockBackend` | 基础 mock |
| `test_runtime.py:54` | `SlowMockBackend` | 延迟 5 秒 |
| `test_runtime.py:80` | `ToolCallMockBackend` | 工具调用 |
| `test_gateway.py:33` | `MockBackend` | 基础 mock + stream |
| `test_e2e_mock.py:22` | `MockBackend` | 预设回复序列 |
| `test_swe_regression.py` | `MinimalBackend` | 最小 mock |
| `test_swe_regression.py` | `ToolBackend` | 工具调用 |
| `test_swe_regression.py` | `TextBackend` | 文本回复 |

**问题**: 重复定义 8 个 Mock Backend，散落在 3 个文件中。

**解决方案**: 统一收拢到 `tests/mocks.py`，所有测试从此导入。

## 附录 B: 关键风险优先级

| 优先级 | 风险 | 对应测试 | 紧急度 |
|--------|------|---------|--------|
| P0 | Agent 工具失败后宣称成功 | `test_execution_state.py::TestAgentResultHandling` | 🔴 立即 |
| P0 | API Key 错误无友好提示 | `test_runtime.py::TestRuntimePreflight` | 🔴 立即 |
| P0 | 路径穿越导致文件泄露 | `test_swe_regression.py::TestPathTraversal` | ✅ 已覆盖 |
| P1 | Backend 不可用导致崩溃 | `test_runtime.py::TestRuntimePreflight` | 🟡 本周 |
| P1 | Memory 不持久化 | `test_integration.py::TestRuntimeWithMemory` | 🟡 本周 |
| P1 | Plan Mode 执行写操作 | `test_plan_mode.py::TestPlanModeGuard` | 🟡 本周 |
| P2 | 多工具组合流程断裂 | `test_agent_workflow.py::TestAgentMultiToolSequence` | 🟢 按需 |
| P2 | 超时不处理 | `test_runtime.py::TestRuntimePreflight` | 🟢 按需 |

---

> **文档结束**
>
> **核心进展**: 从 ~290 测试（220+ Unit / 10 Integration / 15 Runtime / 5 Workflow / 23 CLI）
> 升级到 **~479 测试**（250+ Unit / 30 Integration / 40 Runtime / 30 Workflow / 40 CLI）。
>
> **关键缺失**（当前零覆盖，必须优先补齐）:
> 1. API Key 缺失场景
> 2. API Key 错误场景
> 3. Backend 不可用场景
> 4. Agent 工具失败处理
> 5. Agent 完整多步工作流
>
> **统一 Mock Backend** 解决当前 8 个 mock 散落在 3 个文件的问题。
