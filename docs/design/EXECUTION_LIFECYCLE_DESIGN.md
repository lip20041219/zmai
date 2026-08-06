# SWE Agent 执行生命周期优化设计

> 版本: 1.0
> 日期: 2026-07-21
> 目标: 所有复杂任务遵循 Understand → Plan → Execute → Verify → Report
> 约束: 不修改现有 Runtime 核心代码

---

## 目录

- [1. 当前流程诊断](#1-当前流程诊断)
- [2. 目标流程设计](#2-目标流程设计)
- [3. 简单 vs 复杂任务分级](#3-简单-vs-复杂任务分级)
- [4. 五阶段详细设计](#4-五阶段详细设计)
- [5. 执行日志规范](#5-执行日志规范)
- [6. 验证规范](#6-验证规范)
- [7. 失败处理规范](#7-失败处理规范)
- [8. 与现有架构的集成方案](#8-与现有架构的集成方案)
- [9. 实现建议](#9-实现建议)

---

## 1. 当前流程诊断

### 1.1 现有执行流程

```
Runtime.run()
  │
  ├─ Preflight Check
  ├─ 初始化生命周期 / 工作区 / Backend
  │
  └─ SWEAgent 循环 (max 100 步)
       │
       ├─ step() — 单轮 LLM 调用 → 执行工具 → 返回 Action
       │     ① 拼接 messages + tool_defs + memory_context
       │     ② backend.invoke(request)
       │     ③ 如果 LLM 返回 tool_calls → 逐个执行
       │     ④ 返回 AgentAction.cont() / .complete() / .fail()
       │
       └─ 循环直到 complete / fail / max_steps
            │
            └─ finalize() → 返回 AgentResult
```

### 1.2 现有问题

| 问题 | 表现 | 后果 |
|------|------|------|
| ❌ 无阶段划分 | 所有步骤混在一个 loop 里 | 没有 Understand/Plan/Verify 概念 |
| ❌ 无任务分级 | 简单和复杂任务同样流程 | 简单任务被过度复杂化 |
| ❌ 无执行记录 | Action/Tool/Result/Error 未结构化存储 | 无法审计和回溯 |
| ❌ LLM 自判完成 | Agent 说"完成了"就结束 | 没有客观验证，容易假成功 |
| ❌ 验证缺失 | 没有 Verify 环节 | HTML 写完了但打不开 → 仍然 report 成功 |
| ❌ 失败模糊 | 耗尽 max_steps 或抛出异常 | 用户不知道到底缺什么能力 |

### 1.3 当前 System Prompt 中的"伪阶段"

当前 `_BASE_SYSTEM_PROMPT` 定义了 4 个阶段：

```
第 1 阶段：理解  — 搜索代码，阅读文件
第 2 阶段：执行  — 写代码、改文件
第 3 阶段：交付  — 展示结果
第 4 阶段：确认  — 询问是否满意
```

这**只是 Prompt 文本**，代码层面没有任何强制机制：
- Agent 可以选择性忽略
- 没有 Plan 阶段
- 没有 Verify 阶段
- 无法区分简单/复杂任务
- 没有结构化日志

---

## 2. 目标流程设计

### 2.1 五阶段生命周期

```
┌──────────────────────────────────────────────────────────────────┐
│                    任务入口 (task)                                 │
└─────────────────┬────────────────────────────────────────────────┘
                  │
          ┌───────▼───────┐
          │  分级决策器     │ ← 判断简单/复杂
          └───┬───┬───────┘
              │   │
       简单    │   │ 复杂
              │   │
    ┌─────────▼┐ ┌▼──────────────────────────────────────────────┐
    │ 直接执行  │ │            五阶段流程                           │
    └────┬─────┘ │                                                │
         │       │  ┌──────────┐  ┌──────┐  ┌───────┐  ┌──────┐  │
         │       │  │ ① 理解   │→│ ② 规划 │→│ ③ 执行 │→│ ④ 验证 │  │
         │       │  │Understand│  │ Plan  │  │Execute│  │Verify│  │
         │       │  └──────────┘  └──────┘  └───────┘  └──┬───┘  │
         │       │                                         │      │
         │       │                                  ┌──────▼──┐  │
         │       │                                  │ ⑤ 报告   │  │
         │       │                                  │ Report   │  │
         │       │                                  └─────────┘  │
         │       └────────────────────────────────────────────────┘
         │
    ┌────▼─────┐
    │  最终输出  │
    └──────────┘
```

### 2.2 阶段转换条件

```
Understand ──(充分理解)──→ Plan
Plan        ──(计划就绪)──→ Execute
Execute     ──(执行完成)──→ Verify
Verify      ──(验证通过)──→ Report
Verify      ──(验证失败)──→ Execute (修复循环, 最多 N 次)
Report      ──(报告完成)──→ 结束
```

### 2.3 失败路径

```
任意阶段 ──(无法继续)──→ Report(失败) ──→ 结束
任意阶段 ──(max_retries)──→ Report(超时/失败) ──→ 结束
```

---

## 3. 简单 vs 复杂任务分级

### 3.1 分级规则

| 等级 | 判定条件 | 执行方式 |
|------|---------|---------|
| 🟢 **简单** | 单步操作，无需多步推理 | 直接执行（现有流程） |
| 🔵 **复杂** | 涉及多个文件、多步操作、有依赖关系 | 五阶段全流程 |

### 3.2 具体判定标准

**简单任务特征**（满足任意 2 条即为简单）：

- 操作目标单一（只读一个文件、只写一个文件、只执行一条命令）
- 无需搜索代码
- 无需多步骤推理
- 无需生成多种产出
- 无外部依赖（数据库、API、浏览器等）

**复杂任务特征**（满足任意 1 条即为复杂）：

- 涉及 2 个以上文件的操作
- 需要先理解现有代码再修改
- 包含"创建 HTML 并打开"这类多步交付
- 需要搜索/阅读/理解代码后再操作
- 涉及外部系统（浏览器、数据库、HTTP API）
- 用户明确要求"分析"、"设计"、"规划"、"重构"
- 步骤之间有关联依赖

### 3.3 简单任务示例

```
- "读取 /tmp/test.txt"
- "在 output/ 创建 hello.txt"
- "执行 git status"
- "搜索包含 'TODO' 的代码"
```

### 3.4 复杂任务示例

```
- "创建一个 HTML 文件并可以直接打开"
  → 需要：创建文件 → 写入内容 → 验证文件存在 → 验证内容有效 → 打开浏览器

- "找到项目中的 bug 并修复"
  → 需要：搜索代码 → 理解逻辑 → 定位 bug → 修改 → 验证

- "重构这个模块，添加单元测试"
  → 需要：阅读代码 → 设计方案 → 重构 → 写测试 → 验证测试通过

- "生成一份数据库分析报告"
  → 需要：连接数据库 → 查询数据 → 分析 → 生成报告 → 验证报告
```

---

## 4. 五阶段详细设计

### 4.1 阶段 ①：Understand（理解）

**目标**：收集完成任务所需的全部信息。

**必须产出的信息**：

```
{
  "phase": "understand",
  "task_statement": "用户任务的重新陈述（确认理解正确）",
  "key_files": ["需要读取的文件路径列表"],
  "key_areas": ["需要搜索的关键词/领域"],
  "known_info": ["已知的输入/约束"],
  "unknown_info": ["需要进一步确认的信息"],
  "complexity": "simple | complex",
  "completion_criteria": ["任务完成的判断标准"]
}
```

**典型动作**：

```
Tool: read_file     — 读取关键文件
Tool: grep          — 搜索相关代码
Tool: shell_exec    — 查看目录结构
Tool: show_to_user  — 向用户确认理解是否正确（可选）
```

**完成条件**：所有 unknown_info 已解决（或明确标记为无法解决），可开始规划。

---

### 4.2 阶段 ②：Plan（规划）

**目标**：制定可执行的步骤计划。

**仅复杂任务需要 Plan**。简单任务跳过此阶段。

**必须产出的信息**：

```
{
  "phase": "plan",
  "plan": [
    {
      "step": 1,
      "action": "具体操作描述",
      "tool": "预计使用的工具名",
      "expected_outcome": "期望结果",
      "verification": "如何验证此步骤成功"
    }
  ],
  "total_steps": 3,
  "estimated_complexity": "low | medium | high",
  "risk_points": ["可能出问题的环节"],
  "rollback_strategy": "如果某步失败怎么办"
}
```

**Plan 必须包含每个步骤的 verification 字段**，保证 Execute 完成后可以验证。

**典型动作**：

```
Tool: show_to_user  — 展示计划给用户确认（可选）
```

**完成条件**：计划包含所有步骤，每步有明确的验证方式。

---

### 4.3 阶段 ③：Execute（执行）

**目标**：按计划执行每一步。

**每一步必须记录结构化日志**（见第 5 章）：

```
Step N/N: <action>
  Tool:      <tool_name>
  Params:    <关键参数>
  Result:    <success|fail>
  Output:    <摘要>
  Error:     <错误信息（如果有）>
  Next Step: <继续 | 修复 | 跳过 | 终止>
```

**执行规则**：

1. 严格按 Plan 步骤顺序执行。
2. 每步执行后记录结构化日志。
3. 如果某步失败：
   - 尝试修复（最多重试 2 次）。
   - 如果无法修复，标记该步为 `failed`，决定是否跳过或终止。
4. 如果 Plan 在执行过程中发现遗漏，允许补充步骤（追加到 Plan 末尾），但需标记 `[on_the_fly]`。

**典型动作**：

```
Tool: write_file    — 写入文件
Tool: edit          — 修改代码
Tool: shell_exec    — 执行命令
Tool: git           — 提交代码
Tool: open_in_browser — 打开浏览器
```

**完成条件**：所有 Plan 步骤已执行完毕（成功或明确标记失败）。

---

### 4.4 阶段 ④：Verify（验证）

**目标**：客观验证任务是否真正完成。

**这是最关键的新增阶段**。Agent 不能自判完成，必须执行客观检查。

**验证规则**：

1. **验证必须使用工具，不能凭记忆**。
2. **每个 completion_criteria 必须有对应的验证动作**。
3. **验证失败 → 返回 Execute 修复（最多 2 轮修复循环）**。
4. **修复 2 轮仍失败 → 按真实失败报告**。

**验证清单模板**：

```
Verify: <验证项>
  Method:     read_file | shell_exec | open_in_browser | ...
  Expect:     <预期值>
  Actual:     <实际值>
  Result:     PASS | FAIL
```

**HTML 任务验证示例**：

```
用户要求："创建一个 HTML 文件并可以直接打开"

验证清单:
  ✅ 1. 文件存在
       Method: read_file → stat
       Result: PASS (index.html 已创建)

  ✅ 2. 文件内容有效
       Method: read_file → 检查基本 HTML 结构
       Expect: <!DOCTYPE html>, <html>, <body> 等标签存在
       Result: PASS (有效的 HTML 结构)

  ✅ 3. 文件路径正确
       Method: read_file → 确认在工作区内
       Result: PASS (位于 output/index.html)

  ✅ 4. 浏览器可打开
       Method: open_in_browser
       Result: PASS (浏览器正常打开)

  ✅ 5. 内容包含所需信息
       Method: read_file → grep 关键词
       Result: PASS (所有需求内容已包含)
```

**修复循环逻辑**：

```
Verify ──(失败)──→ Execute(修复) ──→ Verify
                          │
                    retry_count > 2
                          │
                     ┌────▼────┐
                     │ 报告失败  │
                     └─────────┘
```

**完成条件**：所有验证项通过，或明确判定无法通过。

---

### 4.5 阶段 ⑤：Report（报告）

**目标**：向用户输出最终结果。

**必须包含的信息**：

```
{
  "phase": "report",
  "task": "原始任务描述",
  "status": "success | partial | failed",
  "summary": "一句话结果摘要",
  "phases": {
    "understand": { "duration_ms": ..., "files_read": [...] },
    "plan": { "duration_ms": ..., "total_steps": ... },
    "execute": { "duration_ms": ..., "steps_completed": ..., "steps_failed": ... },
    "verify": { "duration_ms": ..., "checks": { "passed": ..., "failed": ... } }
  },
  "deliverables": [
    {"file": "output/index.html", "description": "生成的 HTML 页面"}
  ],
  "issues": [
    {"severity": "warn|error", "message": "遇到的困难"}
  ],
  "verification_summary": "PASS | PARTIAL | FAIL"
}
```

**最终动作**：

```
- 成功   → show_to_user(status=success, summary)
- 部分   → show_to_user(status=partial, summary, 哪些部分未完成)
- 失败   → show_to_user(status=failed, 原因, 建议)
```

---

## 5. 执行日志规范

### 5.1 单步日志结构

```json
{
  "step_number": 3,
  "total_steps": 5,
  "phase": "execute",
  "action": "创建 HTML 文件",
  "tool": "write_file",
  "params": {"path": "output/index.html", "content_length": 1024},
  "result": "success",
  "output_summary": "written output/index.html (1024 chars)",
  "error": null,
  "duration_ms": 15,
  "next_step": "continue"
}
```

### 5.2 StepResult 数据类型

```python
@dataclass
class StepRecord:
    step_number: int
    total_steps: int
    phase: str                    # "understand" | "plan" | "execute" | "verify" | "report"
    action: str                   # 人类可读的操作描述
    tool: str | None              # 使用的工具名（如有）
    params: dict[str, Any]        # 关键参数
    result: str                   # "success" | "fail" | "skip"
    output_summary: str           # 输出摘要（前 200 字符）
    error: str | None             # 错误信息
    duration_ms: int              # 耗时
    next_step: str                # "continue" | "retry" | "skip_step" | "abort"
```

### 5.3 日志存储

- 每步记录追加到 `AgentContext.metadata["execution_log"]`（`list[StepRecord]`）
- 任务完成后写入 `workspace/<agent_id>/.state/execution_log.json`
- 可用于审计、调试、复现

---

## 6. 验证规范

### 6.1 通用验证模板

| 验证类型 | 验证方法 | 适用场景 |
|---------|---------|---------|
| `file_exists` | `read_file` + stat | 文件必须存在 |
| `file_content` | `read_file` + 模式匹配 | 内容必须符合预期 |
| `file_valid` | `read_file` + 语法检查 | HTML/JSON/代码必须有效 |
| `cmd_success` | `shell_exec` | 命令必须返回 code 0 |
| `browser_open` | `open_in_browser` | 浏览器能正常打开 |
| `url_reachable` | `shell_exec curl` | URL 可访问 |
| `result_visible` | `show_to_user` + 用户确认 | 用户能看到结果 |

### 6.2 常见任务的验证清单

**HTML 页面任务**：
```
□ 文件存在
□ DOCTYPE 声明存在
□ <html> 标签完整闭合
□ CSS/JS 引用路径正确
□ 所有链接可访问
□ 浏览器可打开
□ 内容包含用户需求
```

**代码修改任务**：
```
□ 语法正确（无 SyntaxError）
□ 核心逻辑无编译错误
□ 原有测试仍可通过
□ 修改符合需求描述
□ 无未闭合的括号/标签
```

**Shell 执行任务**：
```
□ exit code = 0
□ 无 stderr 错误
□ stdout 包含预期输出
□ 副作用正确（文件生成、目录创建等）
```

**Git 操作任务**：
```
□ git status 符合预期
□ commit 包含正确文件
□ commit message 合理
□ 无未解决的冲突
```

### 6.3 修复循环上限

```
max_verify_retries = 2
```

超过上限仍未通过 → 按失败报告，必须包含：
- 失败的验证项
- 尝试过的修复方法
- 为什么无法修复

---

## 7. 失败处理规范

### 7.1 失败分类

| 类型 | 含义 | 处理方式 |
|------|------|---------|
| `TOOL_ERROR` | 工具执行报错 | 重试 1 次，仍失败则标记为失败步骤 |
| `CAPABILITY_GAP` | 缺少必要能力 | 立即终止，报告缺失能力 |
| `VERIFY_FAIL` | 验证未通过 | 进入修复循环（最多 2 轮） |
| `MAX_RETRIES` | 修复次数超限 | 终止，报告验证失败详情 |
| `UNEXPECTED` | 未知错误 | 终止，报告原始错误 |

### 7.2 失败报告模板

```
任务: <原始任务描述>

状态: ❌ 失败

失败原因:
  阶段: execute 第 3 步
  操作: 打开 HTML 文件
  工具: open_in_browser
  错误: 浏览器打开超时（10s）

已完成的步骤:
  ✅ 第 1 步: 创建 HTML 文件 (write_file)
  ✅ 第 2 步: 写入内容 (write_file)
  ❌ 第 3 步: 打开浏览器 (open_in_browser)

验证结果:
  ✅ 文件存在
  ✅ 文件内容有效
  ❌ 浏览器可打开
    - 尝试 1: open_in_browser → 超时
    - 尝试 2: shell_exec start → 失败 (Access Denied)
    - 无法修复: 当前环境无可用浏览器

建议:
  - 手动打开 output/index.html
  - 或安装默认浏览器后重试
```

### 7.3 禁止行为

- ❌ 禁止在验证失败后假装成功
- ❌ 禁止在耗尽重试后说"已完成"
- ❌ 禁止隐式捕获致命错误后继续（如 Tool 未注册）
- ❌ 禁止在 Report 中遗漏失败信息

---

## 8. 与现有架构的集成方案

### 8.1 集成点（不改现有核心代码）

**方案 A：在 SWEAgent 内部增强（推荐）**

修改范围仅限 `swe/agent.py`，不触动 `agent/base.py`、`runtime/runtime.py`。

```python
class SWEAgent(Agent):
    async def step(self, context: AgentContext) -> AgentAction:
        # [新增] 阶段状态机
        phase = context.metadata.get("execution_phase", "understand")

        if phase == "understand":
            return await self._do_understand(context)
        elif phase == "plan":
            return await self._do_plan(context)
        elif phase == "execute":
            return await self._do_execute(context)
        elif phase == "verify":
            return await self._do_verify(context)
        elif phase == "report":
            return await self._do_report(context)
```

每个 `_do_*` 方法内部：
1. 调用 LLM（可带阶段专用 System Prompt）
2. 执行工具
3. 判断阶段转换条件
4. 记录结构化日志到 `context.metadata["execution_log"]`
5. 返回 `AgentAction.cont()` 或阶段切换信号

**方案 B：独立的 Lifecycle Controller（更干净）**

新增 `src/zmai/swe/lifecycle.py`，不修改任何现有文件。

```python
class ExecutionLifecycle:
    """执行生命周期控制器。
    
    在 SWEAgent 外部编排五阶段流程，
    通过修改 context.metadata 控制 Agent 行为。
    """
```

### 8.2 不改动的文件清单

| 文件 | 改动要求 |
|------|---------|
| `runtime/runtime.py` | ❌ 不改 |
| `agent/base.py` | ❌ 不改 |
| `tool/base.py` | ❌ 不改 |
| `tool/registry.py` | ❌ 不改 |
| `swe/tools.py` | ❌ 不改（工具实现不变） |
| `gateway/*` | ❌ 不改 |
| `swe/agent.py` | ✅ 可改（SWEAgent 内部重组） |

### 8.3 System Prompt 增强（仅 SWEAgent 内部）

新增分阶段 System Prompt，在 `_build_system_prompt()` 基础上按阶段附加指令：

```python
_PHASE_PROMPTS = {
    "understand": """
## 当前阶段：理解 (Understand)
你的目标是充分理解任务所需的所有信息。
- 读取关键文件
- 搜索相关代码
- 识别未知信息
当你确认已充分理解任务后，进入规划阶段。
""",
    "plan": """
## 当前阶段：规划 (Plan)
你的目标是制定详细的执行计划。
- 列出每一步的具体操作
- 每一步标注使用的工具
- 每一步标注如何验证
计划必须包含验证方法。
""",
    "execute": """
## 当前阶段：执行 (Execute)
严格按照计划执行每一步。
- 每一步后记录结果
- 失败时最多重试 2 次
- 需要补充步骤时标记 [on_the_fly]
执行完所有步骤后进入验证阶段。
""",
    "verify": """
## 当前阶段：验证 (Verify)
验证任务是否真正完成。
- 对每个交付物执行客观检查
- 使用工具验证，不要凭记忆
- 验证失败 → 修复 → 再验证（最多 2 轮）
所有项通过后进入报告阶段。
""",
    "report": """
## 当前阶段：报告 (Report)
向用户输出最终结果。
- 成功 → 展示成果
- 部分成功 → 展示已完成的，说明未完成的
- 失败 → 说明原因和建议
""",
}
```

---

## 9. 实现建议

### 9.1 实现顺序

```
Phase 1: StepRecord 数据结构 + 执行日志
├── 定义 StepRecord dataclass
├── 在 context.metadata["execution_log"] 中记录
├── 任务完成后持久化到 .state/execution_log.json

Phase 2: 简单/复杂任务分级
├── 实现分级判定函数（关键词规则）
├── context.metadata["complexity"] = "simple" | "complex"

Phase 3: Verify 阶段
├── 在 execute 阶段后追加 verify 循环
├── 实现验证清单（文件存在、内容有效、浏览器可开等）
├── 实现修复循环（最多 2 次）

Phase 4: Plan 阶段（仅复杂任务）
├── 在 execute 前插入 plan 阶段
├── Plan 必须包含每步的 verification 字段

Phase 5: Understand 阶段（仅复杂任务）
├── 在 plan 前插入 understand 阶段
├── 收集信息 → 确定 completion_criteria

Phase 6: Report 阶段
├── 结构化最终报告
├── 阶段耗时统计
```

### 9.2 文件规划

```
src/zmai/swe/
├── __init__.py
├── agent.py          # [修改] SWEAgent → 五阶段状态机
├── tools.py          # [不改] 工具实现
├── lifecycle.py      # [新增] 生命周期控制器（可选方案 B）
├── phases/
│   ├── __init__.py
│   ├── understand.py  # [新增] Understand 阶段逻辑
│   ├── plan.py        # [新增] Plan 阶段逻辑
│   ├── execute.py     # [新增] Execute 阶段逻辑
│   ├── verify.py      # [新增] Verify 阶段逻辑
│   └── report.py      # [新增] Report 阶段逻辑
└── models.py          # [新增] StepRecord 等数据类

src/zmai/swe/
├── templates/
│   └── phase_prompts.py  # [新增] 分阶段 System Prompt
```

### 9.3 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 阶段切换方式 | `context.metadata["execution_phase"]` 字符串标记 | 不改 AgentContext，最小侵入 |
| 简单任务路径 | 跳过 Plan/Verify，直接 Execute | 节省 Token |
| 验证标准 | 硬编码 + LLM 补充 | 常见验证项可脱离 LLM，减少幻觉 |
| 修复上限 | 2 次 | 平衡质量与成本 |
| 执行日志 | JSON 文件 + 内存列表 | 可审计，可复现 |
| Plan 存储 | `context.metadata["execution_plan"]` | 运行时可见 |

---

> **文档结束**
>
> 本设计将当前 SWE Agent 的"单循环无阶段"流程重构为
> **Understand → Plan → Execute → Verify → Report** 五阶段生命周期，
> 并严格区分简单/复杂任务路径。
>
> 最小实现（Phase 1 + Phase 3）即可解决"假成功"问题。
> 完整实现后，每个任务的每个步骤都可审计、可验证、可追溯。
