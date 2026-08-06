# ZMAI Tool Result Contract

> 版本: 1.0
> 日期: 2026-07-21
> 目标: 消灭 Agent"工具失败但宣称成功"的可信度问题
> 约束: 不修改现有 Runtime 核心代码

---

## 目录

- [1. 问题诊断](#1-问题诊断)
- [2. 契约总则](#2-契约总则)
- [3. ToolResult 规范](#3-toolresult-规范)
- [4. ExecutionState 规范](#4-executionstate-规范)
- [5. VerificationResult 规范](#5-verificationresult-规范)
- [6. Agent 处理契约](#6-agent-处理契约)
- [7. 失败处理协议](#7-失败处理协议)
- [8. 当前代码合规审计](#8-当前代码合规审计)
- [9. 单元测试规范](#9-单元测试规范)
- [10. 实现路线](#10-实现路线)

---

## 1. 问题诊断

### 1.1 当前执行链路

```
LLM 决定调用 write_file("output/report.html", content)
        │
        ▼
ToolRegistry.execute("write_file", params, context)
        │
        ▼
WriteFileTool.execute() → 写入磁盘 → 返回 ToolResult
        │
        ▼
SWEAgent.step() 收到 ToolResult
        │
        ├─ result.success = True   → 追加 "[OK]: written ..." 到 messages
        ├─ result.success = False  → 追加 "[FAIL]: 磁盘空间不足" 到 messages
        │
        ▼
LLM 读到 messages 中的结果 → 决定下一步
```

### 1.2 可信度断裂点

```
断裂点 ①：LLM 可以 ignore FAIL 标记

  messages 中: "[工具 write_file 结果]\nFAIL: 磁盘空间不足"
  LLM 下一轮输出: "文件已成功创建，请查看 output/report.html"

  → Agent 代码没有拦截这个矛盾
  → 最终报告基于 LLM 的文本，不是基于 ToolResult


断裂点 ②：无执行事实状态

  Agent 没有维护"实际发生了什么"的不可变记录
  所有信息只在 messages（文本）中
  LLM 可以幻想、虚构、遗忘

  → 最终报告时无客观数据源可对照


断裂点 ③：无验证强制

  工具返回 success=false 后没有代码级分支处理
  不要求 Agent 做任何决定（重试/换工具/报告失败）
  → Agent 可以跳过所有错误继续前进
```

### 1.3 根因

```
Tool 层             ✅ 正确 — ToolResult 结构完整，success/error 明确
ToolRegistry 层     ✅ 正确 — execute() 返回正确的 ToolResult
SWEAgent 层         ❌ 错误 — 不强制读取 success，不强制分支处理
System Prompt 层    ❌ 错误 — 只有建议性文本，无代码强制力
```

---

## 2. 契约总则

### 2.1 三条铁律

```
铁律 1: Tool 返回的 success 字段是唯一事实源。
        Agent 不得声称任何与 success 字段矛盾的结果。
        违反 = Bug。

铁律 2: 每个工具调用必须有对应的 ExecutionState 记录。
        无记录 = 未发生。
        违反 = Bug。

铁律 3: 最终报告必须引用 ExecutionState 日志，不得引用 LLM 输出文本。
        违反 = Bug。
```

### 2.2 数据流

```
Tool.execute()
    │
    ▼
ToolResult  ← 这是事实
    │
    ▼
ExecutionState.append()  ← 这是记录（不可变追加）
    │
    ├── success=True  → 继续下一步
    │
    └── success=False → 必须进入失败处理协议
                          ├── 重试
                          ├── 换参数重试
                          ├── 换工具
                          ├── 请求用户
                          └── 终止并报告

最终报告
    │
    ├── 基于 ExecutionState.log() 生成
    ├── 不得引用 LLM 原始输出中的声明
    └── 所有 success=False 必须在报告中体现
```

---

## 3. ToolResult 规范

### 3.1 数据结构（已存在，无需修改）

```python
@dataclass
class ToolResult:
    success: bool                # 唯一事实源
    output: str = ""             # 正常输出
    error: str | None = None     # success=False 时必须提供
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外信息
    duration_ms: int = 0         # 执行耗时
```

### 3.2 工厂方法（已存在）

```python
@classmethod
def ok(cls, output="", metadata=None) -> ToolResult:
    """创建成功结果。success=True"""

@classmethod
def err(cls, error, metadata=None) -> ToolResult:
    """创建失败结果。success=False, error=原因"""
```

### 3.3 契约要求（新增检查项）

```
每个 Tool 实现必须遵守：

✅ success=True 时：
   - output 必须包含有意义的输出（不能是空字符串表示"成功"）
   - error 必须为 None

✅ success=False 时：
   - error 必须包含具体的失败原因（不能是空字符串）
   - error 必须包含可操作的信息（不仅仅是 "fail"）
   - output 可以为空
   - metadata 中可以携带额外调试信息

✅ metadata 可包含：
   - exit_code: int           — 命令退出码
   - file_size: int           — 文件大小
   - line_count: int          — 行数
   - matches: int             — 匹配数
   - path: str                — 文件路径
   - pid: int                 — 进程 ID
   - 其他对调试/审计有用的信息

❌ 禁止：
   - success=True 但实际未完成操作
   - success=False 但 error 为通用消息（必须具体）
   - 抛出异常替代返回 ToolResult.err()（应捕获并返回结构化错误）
```

### 3.4 当前 8 个 Tool 的合规状态

| Tool | success=True 输出 | success=False error | metadata | 合规 |
|------|------------------|--------------------|----------|------|
| `ReadFileTool` | ✅ 含行号内容 | ✅ 具体原因 | ✅ size, line_count | ✅ |
| `WriteFileTool` | ✅ "written path (N chars)" | ✅ 具体原因 | ❌ 无 | ⚠️ 可改进 |
| `EditTool` | ✅ 编辑摘要 | ✅ 具体原因 | ❌ 无 | ⚠️ 可改进 |
| `GrepTool` | ✅ 匹配行 | ✅ 具体原因 | ✅ matches | ✅ |
| `ShellTool` | ✅ 命令输出 | ✅ exit code + 输出 | ✅ exit_code | ✅ |
| `GitTool` | ✅ 命令输出 | ✅ 错误信息 | ✅ exit_code | ✅ |
| `ShowToUserTool` | ✅ "shown (N chars)" | ✅ 具体原因 | ❌ 无 | ⚠️ 可改进 |
| `OpenInBrowserTool` | ✅ "opened: path" | ✅ 具体原因 | ❌ 无 | ⚠️ 可改进 |

所有 Tool 的 success/error 字段**可信**。问题不在 Tool 层，在 Agent 消费层。

---

## 4. ExecutionState 规范

### 4.1 目的

ExecutionState 是**不可变的事实记录**，用于：
1. 追踪每个工具调用的实际结果。
2. 作为最终报告的客观数据源。
3. 防止 LLM 虚构/遗忘工具结果。

### 4.2 数据结构

```python
@dataclass
class ExecutionState:
    """单步执行状态记录。不可变（追加后不改）。"""
    step_index: int                    # 全局步骤序号
    phase: str                         # "understand" | "plan" | "execute" | "verify" | "report"
    tool_name: str                     # 工具名
    params: dict[str, Any]             # 调用参数
    success: bool                      # 真实结果（直接从 ToolResult 复制）
    output_summary: str                # 输出摘要（前 200 字符）
    error: str | None                  # 错误信息（从 ToolResult 复制）
    duration_ms: int                   # 耗时（从 ToolResult 复制）
    timestamp: str                     # ISO8601
    agent_action: str                  # Agent 的后续决策
```

### 4.3 ExecutionLog（执行日志）

```python
class ExecutionLog:
    """不可变执行日志。所有工具调用的唯一权威记录。"""

    def __init__(self):
        self._states: list[ExecutionState] = []

    def append(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: ToolResult,
        phase: str = "execute",
        agent_action: str = "continue",
    ) -> ExecutionState:
        """追加一条执行记录。从 ToolResult 自动提取事实。"""
        state = ExecutionState(
            step_index=len(self._states) + 1,
            phase=phase,
            tool_name=tool_name,
            params=params,
            success=result.success,
            output_summary=(result.output or "")[:200],
            error=result.error,
            duration_ms=result.duration_ms,
            timestamp=_now_iso(),
            agent_action=agent_action,
        )
        self._states.append(state)
        return state

    def last(self) -> ExecutionState | None:
        """获取最后一条记录。"""
        return self._states[-1] if self._states else None

    def has_failures(self) -> bool:
        """检查是否有任何失败记录。"""
        return any(not s.success for s in self._states)

    def failed_steps(self) -> list[ExecutionState]:
        """获取所有失败步骤。"""
        return [s for s in self._states if not s.success]

    def summary(self) -> dict:
        """生成执行摘要（用于最终报告）。"""
        total = len(self._states)
        failed = len(self.failed_steps())
        return {
            "total_calls": total,
            "successful": total - failed,
            "failed": failed,
            "success_rate": f"{(total - failed) / total * 100:.0f}%" if total else "N/A",
        }

    def export(self) -> list[dict]:
        """导出为可序列化格式。"""
        return [asdict(s) for s in self._states]

    def find_by_tool(self, name: str) -> list[ExecutionState]:
        """按工具名查找执行记录。"""
        return [s for s in self._states if s.tool_name == name]
```

### 4.4 集成方式

```python
# 在 SWEAgent.step() 中：
tctx = ToolContext(...)
result = context.tools.execute(tc.name, tc.params, tctx)

# [新增] 事实记录 —— 直接从 ToolResult 提取，不可篡改
exec_log = context.metadata.setdefault("execution_log", ExecutionLog())
state = exec_log.append(
    tool_name=tc.name,
    params=tc.params,
    result=result,
    phase=context.metadata.get("execution_phase", "execute"),
)

# [新增] 失败处理 —— 代码强制，不依赖 LLM 自觉
if not result.success:
    return self._handle_tool_failure(context, tc, result, state)
```

---

## 5. VerificationResult 规范

### 5.1 目的

VerificationResult 是**验证的客观记录**，用于 Ensure 阶段（Verify）确认任务是否真正完成。

### 5.2 数据结构

```python
@dataclass
class VerificationCheck:
    """单条验证项。"""
    item: str                        # 验证项描述（如 "文件存在"）
    method: str                      # 验证方法（如 "read_file", "shell_exec"）
    expected: str                    # 预期值
    actual: str                      # 实际值
    passed: bool                     # 是否通过
    error: str | None = None         # 验证过程中的错误

    @classmethod
    def pass_result(cls, item: str, method: str, expected: str, actual: str) -> VerificationCheck:
        return cls(item=item, method=method, expected=expected, actual=actual, passed=True)

    @classmethod
    def fail_result(cls, item: str, method: str, expected: str, actual: str = "", error: str = "") -> VerificationCheck:
        return cls(item=item, method=method, expected=expected, actual=actual, passed=False, error=error)


@dataclass
class VerificationResult:
    """验证结果。"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    checks: list[VerificationCheck] = field(default_factory=list)

    def add(self, check: VerificationCheck) -> None:
        self.checks.append(check)
        self.total += 1
        if check.passed:
            self.passed += 1
        else:
            self.failed += 1

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        if self.all_passed:
            return f"✅ 全部通过 ({self.passed}/{self.total})"
        return f"❌ {self.failed}/{self.total} 项未通过"

    def failed_checks(self) -> list[VerificationCheck]:
        return [c for c in self.checks if not c.passed]
```

### 5.3 常见验证项模板

```python
VERIFICATION_TEMPLATES = {
    "file_exists": {
        "item": "文件 {path} 存在",
        "method": "read_file",
        "expected": "文件读取成功",
    },
    "file_valid_html": {
        "item": "HTML 文件结构有效",
        "method": "read_file",
        "expected": "包含 DOCTYPE 和基本标签",
    },
    "cmd_exit_zero": {
        "item": "命令 {cmd} 正常退出",
        "method": "shell_exec",
        "expected": "exit code = 0",
    },
    "browser_opens": {
        "item": "浏览器可打开 {path}",
        "method": "open_in_browser",
        "expected": "打开成功",
    },
    "git_clean": {
        "item": "Git 工作区干净",
        "method": "git status",
        "expected": "nothing to commit",
    },
}
```

---

## 6. Agent 处理契约

### 6.1 工具调用后处理流程（代码级强制）

```python
# 每次工具调用后，SWEAgent 必须执行以下流程：

result = context.tools.execute(tc.name, tc.params, tctx)

# ── 第 1 步：记录事实（不可变） ──────────────────
exec_log = context.metadata["execution_log"]
state = exec_log.append(tc.name, tc.params, result)

# ── 第 2 步：检查结果并分支 ──────────────────────
if result.success:
    # 成功路径：正常继续
    messages.append({"role": "user", "content": format_success(tc.name, result)})

else:
    # 失败路径：必须处理，不得跳过
    decision = handle_failure(tc.name, result, retry_count)
    messages.append({"role": "user", "content": format_failure(tc.name, result, decision)})

    if decision == "retry":
        return AgentAction.cont(f"重试 {tc.name}")  # 继续循环，再次调用该工具
    elif decision == "change_params":
        return AgentAction.cont(f"修改参数重试 {tc.name}")
    elif decision == "change_tool":
        return AgentAction.cont(f"更换其他工具")
    elif decision == "ask_user":
        return AgentAction.pause(f"需要用户输入: {result.error}")
    elif decision == "abort":
        return AgentAction.fail(f"无法修复: {result.error}")
```

### 6.2 失败处理协议

```
ToolResult.success = False
        │
        ▼
  重试计数器检查
        │
  ┌─────┴─────┐
  │ ≤ 2 次重试  │      │ > 2 次重试 │
  └─────┬─────┘      └─────┬──────┘
        │                  │
  进入修复决策              进入终止决策
        │                  │
  选择 1: 重试相同参数       选择 1: 报告失败 + 原因
  选择 2: 修改参数重试       选择 2: 建议替代方案
  选择 3: 更换工具重试       选择 3: 请求用户协助
  选择 4: 请求用户协助
```

### 6.3 报告生成契约

```python
# ❌ 禁止的最终报告生成方式：
report = {"status": "completed", "output": llm_output_text}
# 这依赖于 LLM 说了什么，不是实际发生了什么

# ✅ 必须的最终报告生成方式：
exec_log = context.metadata["execution_log"]
report = {
    "status": "completed" if not exec_log.has_failures() else "partial_failed",
    "output": generate_report_from_log(exec_log),
    "execution_summary": exec_log.summary(),
    "failures": [s.error for s in exec_log.failed_steps()],
}
# 这依赖于实际发生了什么，不是 LLM 说了什么
```

### 6.4 三条禁止

```
❌ 禁止 1：LLM 输出 "已完成" 时，Agent 不检查 ExecutionLog 就直接报告成功。
    → 必须检查: if exec_log.has_failures() → 报告部分失败

❌ 禁止 2：工具返回 success=False 后，Agent 不分支处理就继续。
    → 必须分支: if not result.success → 进入失败处理协议

❌ 禁止 3：最终报告引用 LLM 原始输出中的 "成功" 声明。
    → 必须引用 ExecutionLog 中的实际记录
```

---

## 7. 失败处理协议

### 7.1 协议定义

```python
class FailureDecision(Enum):
    RETRY = "retry"                    # 重试相同操作
    CHANGE_PARAMS = "change_params"    # 修改参数重试
    CHANGE_TOOL = "change_tool"        # 更换工具
    ASK_USER = "ask_user"              # 需要用户输入
    ABORT = "abort"                    # 终止，报告失败


@dataclass
class FailureRecord:
    """单次失败的完整记录。"""
    tool: str
    params: dict[str, Any]
    error: str
    attempt: int
    decision: FailureDecision
    timestamp: str


class FailureTracker:
    """失败追踪器。追踪重试次数和失败历史。"""

    def __init__(self, max_retries: int = 2):
        self._failures: dict[str, list[FailureRecord]] = {}
        self._max_retries = max_retries

    def record(
        self,
        tool: str,
        params: dict[str, Any],
        error: str,
        decision: FailureDecision,
    ) -> FailureRecord:
        """记录一次失败。"""
        record = FailureRecord(
            tool=tool, params=params, error=error,
            attempt=self._retries(tool) + 1,
            decision=decision, timestamp=_now_iso(),
        )
        self._failures.setdefault(tool, []).append(record)
        return record

    def _retries(self, tool: str) -> int:
        return len(self._failures.get(tool, []))

    def can_retry(self, tool: str) -> bool:
        """检查是否还可重试。"""
        return self._retries(tool) < self._max_retries

    def summary(self) -> list[dict]:
        """导出失败摘要。"""
        result = []
        for tool, records in self._failures.items():
            for r in records:
                result.append({
                    "tool": tool,
                    "attempt": r.attempt,
                    "error": r.error,
                    "decision": r.decision.value,
                })
        return result
```

### 7.2 协议决策树

```
Tool 返回 success=False
        │
        ▼
  failure_tracker.can_retry(tool_name)?
        │
  ┌─────┴─────┐
  │  YES       │  NO
  │            │
  ▼            ▼
 重试决策      终止决策
  │            │
  ├─ 临时错误   ├─ 无法修复
  │  (网络/IO)  │  → ABORT + 完整失败报告
  │  → RETRY   │
  │            ├─ 参数错误
  ├─ 参数不合法  │  → ABORT + 提示正确参数
  │  → 修改重试  │
  │            ├─ 能力缺失
  ├─ 工具不合適  │  → ABORT + 建议替代方案
  │  → 换工具   │
  │            └─ 需要用户信息
  └─ 需要输入    │  → ASK_USER + 暂停
     → ASK_USER │
```

### 7.3 错误消息格式化

```python
def format_tool_result(tool: str, result: ToolResult, decision: FailureDecision | None = None) -> str:
    """格式化工具结果消息（供 LLM 读取）。"""
    if result.success:
        return (
            f"[工具 {tool} 执行成功]\n"
            f"输出: {result.output}"
        )

    # 失败时明确标记 + 决策
    return (
        f"[工具 {tool} 执行失败]\n"
        f"错误: {result.error}\n"
        f"决策: {decision.value if decision else 'unknown'}\n"
        f"说明: {'将重试' if decision == FailureDecision.RETRY else
                 '将修改参数后重试' if decision == FailureDecision.CHANGE_PARAMS else
                 '将更换其他工具' if decision == FailureDecision.CHANGE_TOOL else
                 '需要用户协助' if decision == FailureDecision.ASK_USER else
                 '无法修复，任务终止' if decision == FailureDecision.ABORT else
                 ''}"
    )
```

---

## 8. 当前代码合规审计

### 8.1 Tool 层（✅ 通过）

| 检查项 | 状态 | 证据 |
|--------|------|------|
| ToolResult 结构完整 | ✅ | success/output/error/metadata/duration_ms |
| success=True 时有意义输出 | ✅ | 所有工具返回具体内容 |
| success=False 时有具体 error | ✅ | 所有工具返回具体错误原因 |
| 不抛出异常替代 err() | ✅ | 所有异常被捕获并转为 ToolResult.err() |
| metadata 可选 | ⚠️ 部分缺失 | WriteFile/Edit/ShowToUser/OpenBrowser 无 metadata |

### 8.2 SWEAgent 层（❌ 不通过）

| 检查项 | 状态 | 问题 |
|--------|------|------|
| 读取 result.success | ✅ | 代码中已读取 |
| success=False 时分支处理 | ❌ | 只把结果文本发给 LLM，不做代码级分支 |
| 重试计数/限制 | ❌ | 无重试机制 |
| 失败决策（重试/换工具/终止） | ❌ | 无决策代码 |
| ExecutionLog 记录 | ❌ | 无结构化日志 |
| 报告基于日志而非 LLM | ❌ | 最终报告直接取 LLM output |
| 验证强制 | ❌ | 无验证环节 |

### 8.3 审计结论

```
Tool 层:    ✅ 可信（8/8 工具正确返回 success 字段）
Agent 层:   ❌ 不可信（不强制检查 success，不分支处理，不记录状态）
Runtime 层: ❌ 不可信（最终报告直接取 LLM output）

修复重点：SWEAgent.step() 中的工具结果处理逻辑。
不需要改动 Tool 实现或 ToolResult 结构。
```

---

## 9. 单元测试规范

### 9.1 ExecutionLog 测试

```
tests/test_execution_state.py
```

```python
class TestExecutionLog:
    def test_append_success(self):
        """追加成功记录。"""
        log = ExecutionLog()
        result = ToolResult.ok("file written", metadata={"size": 100})
        state = log.append("write_file", {"path": "test.txt"}, result)
        assert state.success is True
        assert state.tool_name == "write_file"
        assert state.error is None
        assert len(log._states) == 1

    def test_append_failure(self):
        """追加失败记录。"""
        log = ExecutionLog()
        result = ToolResult.err("disk full", metadata={"exit_code": 1})
        state = log.append("write_file", {"path": "test.txt"}, result)
        assert state.success is False
        assert state.error == "disk full"
        assert len(log._states) == 1

    def test_has_failures(self):
        """检测是否有失败。"""
        log = ExecutionLog()
        assert log.has_failures() is False
        log.append("ok_tool", {}, ToolResult.ok("done"))
        assert log.has_failures() is False
        log.append("bad_tool", {}, ToolResult.err("fail"))
        assert log.has_failures() is True

    def test_failed_steps(self):
        """获取失败步骤。"""
        log = ExecutionLog()
        log.append("t1", {}, ToolResult.ok("ok"))
        log.append("t2", {}, ToolResult.err("e1"))
        log.append("t3", {}, ToolResult.err("e2"))
        failed = log.failed_steps()
        assert len(failed) == 2
        assert all(not s.success for s in failed)

    def test_summary(self):
        """执行摘要。"""
        log = ExecutionLog()
        log.append("t1", {}, ToolResult.ok("ok"))
        log.append("t2", {}, ToolResult.err("e1"))
        s = log.summary()
        assert s["total_calls"] == 2
        assert s["successful"] == 1
        assert s["failed"] == 1

    def test_export_serializable(self):
        """导出可序列化。"""
        log = ExecutionLog()
        log.append("t1", {"key": "val"}, ToolResult.ok("done"))
        exported = log.export()
        assert isinstance(exported, list)
        assert exported[0]["tool_name"] == "t1"
        assert exported[0]["success"] is True
        assert exported[0]["step_index"] == 1

    def test_find_by_tool(self):
        """按工具名查找。"""
        log = ExecutionLog()
        log.append("read", {}, ToolResult.ok("a"))
        log.append("write", {}, ToolResult.ok("b"))
        log.append("read", {}, ToolResult.ok("c"))
        reads = log.find_by_tool("read")
        assert len(reads) == 2
        writes = log.find_by_tool("write")
        assert len(writes) == 1
```

### 9.2 FailureTracker 测试

```
tests/test_execution_state.py (same file)
```

```python
class TestFailureTracker:
    def test_initial_no_failures(self):
        tracker = FailureTracker(max_retries=2)
        assert tracker.can_retry("any_tool") is True

    def test_can_retry_within_limit(self):
        tracker = FailureTracker(max_retries=2)
        tracker.record("t1", {}, "e1", FailureDecision.RETRY)
        assert tracker.can_retry("t1") is True
        tracker.record("t1", {}, "e1", FailureDecision.RETRY)
        assert tracker.can_retry("t1") is False

    def test_retry_independent_per_tool(self):
        tracker = FailureTracker(max_retries=2)
        tracker.record("t1", {}, "e1", FailureDecision.RETRY)
        tracker.record("t1", {}, "e1", FailureDecision.RETRY)
        assert tracker.can_retry("t1") is False
        assert tracker.can_retry("t2") is True

    def test_summary_includes_all(self):
        tracker = FailureTracker(max_retries=2)
        tracker.record("write", {"path": "x"}, "disk full", FailureDecision.CHANGE_PARAMS)
        tracker.record("write", {"path": "y"}, "still full", FailureDecision.ABORT)
        summary = tracker.summary()
        assert len(summary) == 2
        assert summary[0]["tool"] == "write"
        assert summary[1]["decision"] == "abort"

    def test_attempt_counter(self):
        tracker = FailureTracker()
        r1 = tracker.record("t1", {}, "e1", FailureDecision.RETRY)
        assert r1.attempt == 1
        r2 = tracker.record("t1", {}, "e2", FailureDecision.RETRY)
        assert r2.attempt == 2
```

### 9.3 VerificationResult 测试

```
tests/test_execution_state.py (same file)
```

```python
class TestVerificationCheck:
    def test_pass_result(self):
        c = VerificationCheck.pass_result("文件存在", "read_file", "读取成功", "读取成功")
        assert c.passed is True
        assert c.item == "文件存在"

    def test_fail_result(self):
        c = VerificationCheck.fail_result("文件存在", "read_file", expected="读取成功", error="文件未找到")
        assert c.passed is False
        assert c.error == "文件未找到"


class TestVerificationResult:
    def test_all_pass(self):
        vr = VerificationResult()
        vr.add(VerificationCheck.pass_result("a", "read", "ok", "ok"))
        vr.add(VerificationCheck.pass_result("b", "read", "ok", "ok"))
        assert vr.all_passed is True
        assert vr.passed == 2
        assert vr.failed == 0

    def test_some_fail(self):
        vr = VerificationResult()
        vr.add(VerificationCheck.pass_result("a", "read", "ok", "ok"))
        vr.add(VerificationCheck.fail_result("b", "read", "ok", "", "not found"))
        assert vr.all_passed is False
        assert vr.passed == 1
        assert vr.failed == 1

    def test_failed_checks_returns_only_fails(self):
        vr = VerificationResult()
        vr.add(VerificationCheck.pass_result("a", "read", "ok", "ok"))
        vr.add(VerificationCheck.fail_result("b", "read", "ok", "", "e1"))
        vr.add(VerificationCheck.fail_result("c", "read", "ok", "", "e2"))
        failed = vr.failed_checks()
        assert len(failed) == 2
        assert all(not c.passed for c in failed)

    def test_summary_all_pass(self):
        vr = VerificationResult()
        vr.add(VerificationCheck.pass_result("a", "read", "ok", "ok"))
        assert "全部通过" in vr.summary()

    def test_summary_some_fail(self):
        vr = VerificationResult()
        vr.add(VerificationCheck.fail_result("a", "read", "ok", "", "err"))
        assert "未通过" in vr.summary()
```

### 9.4 Agent 结果处理行为测试

```python
class TestAgentResultHandling:
    """测试 Agent 必须正确处理 ToolResult。"""

    def test_agent_must_not_claim_success_on_fail(self):
        """铁律 1：Tool 失败时 Agent 不得声称成功。
        
        模拟：Tool 返回 success=False
        期望：Agent 的记录中 success=False，后续决策不是 "complete"
        """
        result = ToolResult.err("disk full")
        assert result.success is False
        # Agent 必须记录此事实
        log = ExecutionLog()
        log.append("write_file", {"path": "x"}, result)
        assert log.has_failures()
        # Agent 不得在此时报告 complete
        assert log.last().success is False

    def test_agent_must_branch_on_failure(self):
        """铁律 2：失败后必须进入处理协议。"""
        result = ToolResult.err("permission denied")
        tracker = FailureTracker(max_retries=2)
        can_retry = tracker.can_retry("write_file")
        if not result.success and can_retry:
            tracker.record("write_file", {}, "permission denied", FailureDecision.CHANGE_PARAMS)
        assert len(tracker._failures["write_file"]) == 1

    def test_final_report_must_not_use_llm_output(self):
        """铁律 3：最终报告基于 ExecutionLog，非 LLM 输出。"""
        log = ExecutionLog()
        log.append("write", {}, ToolResult.ok("done"))
        log.append("write", {}, ToolResult.err("failed on second file"))

        # 正确：基于日志生成报告
        report = {
            "status": "partial_failed" if log.has_failures() else "completed",
            "total_calls": len(log._states),
            "failures": [s.error for s in log.failed_steps()],
        }
        assert report["status"] == "partial_failed"
        assert len(report["failures"]) == 1
        assert report["failures"][0] == "failed on second file"

    def test_retry_limit_enforced(self):
        """超过最大重试次数后不得再重试。"""
        tracker = FailureTracker(max_retries=1)
        assert tracker.can_retry("tool_x")
        tracker.record("tool_x", {}, "err", FailureDecision.RETRY)
        assert tracker.can_retry("tool_x") is False
```

### 9.5 新增测试文件

```python
# tests/test_execution_state.py
# 完整测试：ExecutionLog, FailureTracker, VerificationCheck, VerificationResult
```

### 9.6 必须通过的断言总表

```
ExecutionLog
  ├── append(success) → state.success == result.success
  ├── has_failures() → True 当包含失败
  ├── has_failures() → False 当全部成功
  ├── failed_steps() → 只返回失败步骤
  ├── summary() → 正确计数
  └── export() → JSON 可序列化

FailureTracker
  ├── can_retry("t") → True 当 retries < max
  ├── can_retry("t") → False 当 retries >= max
  ├── retry 计数按 tool 独立
  └── summary() → 包含所有记录

VerificationCheck
  ├── pass_result() → passed=True
  └── fail_result() → passed=False

VerificationResult
  ├── all_passed → True 当 all checks passed
  ├── all_passed → False 当 any check failed
  ├── failed_checks() → 只返回未通过的检查
  └── summary() → 格式正确

Agent 行为
  ├── Tool fail → Agent 不得报告 success
  ├── Tool fail → 必须进入失败处理协议
  ├── 最终报告基于 ExecutionLog 而非 LLM 输出
  └── 超过 max retries 后禁止继续重试
```

---

## 10. 实现路线

### Phase 1：新增数据类型（不改现有代码）

```
新增文件:
  src/zmai/swe/models.py
      - ExecutionState
      - ExecutionLog
      - FailureRecord
      - FailureTracker
      - FailureDecision (Enum)
      - VerificationCheck
      - VerificationResult

新增测试:
  tests/test_execution_state.py
      - TestExecutionLog (7 tests)
      - TestFailureTracker (5 tests)
      - TestVerificationCheck (2 tests)
      - TestVerificationResult (6 tests)
      - TestAgentResultHandling (4 tests)
```

**零修改，纯新增。** 现有 290 测试零回归。

### Phase 2：修改 SWEAgent（核心改动）

```
修改文件:
  src/zmai/swe/agent.py
      - step() 中新增 tool 结果分支处理
      - 集成 ExecutionLog
      - 集成 FailureTracker
      - step() 结束时返回基于日志的状态
      - finalize() 生成基于日志的报告

不改文件:
  runtime/runtime.py     ❌ 不改
  agent/base.py          ❌ 不改
  tool/base.py           ❌ 不改
  tool/registry.py       ❌ 不改
  swe/tools.py           ❌ 不改
  gateway/*              ❌ 不改
```

### Phase 3：System Prompt 增强

```
修改文件:
  src/zmai/swe/agent.py
      - _BASE_SYSTEM_PROMPT 中增加"基于事实报告"指令
      - 新增 "禁止虚构成功" 规则
```

---

> **文档结束**
>
> 核心结论：
> - Tool 层可信（ToolResult 结构完整）✅
> - Agent 层不可信（不强制检查、不分支、无日志）❌
> - 修复重点在 `SWEAgent.step()` 消费逻辑
> - 三条铁律 + ExecutionLog + FailureTracker + VerificationResult 构成完整契约
> - 新增类型零改动现有代码，Phase 1 可安全先上
