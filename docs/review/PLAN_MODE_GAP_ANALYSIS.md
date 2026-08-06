# Plan Mode Gap Analysis

> 审计日期: 2026-07-26
> 审计方式: 只读，不修改代码
> 文件: `src/zmai/swe/planner.py`, `src/zmai/swe/models.py`, `src/zmai/swe/agent.py`,
>        `src/zmai/agent/base.py`, `src/zmai/runtime/lifecycle.py`,
>        `src/zmai/cli/main.py`, `src/zmai/prompt/engine.py`, `src/zmai/prompt/templates.py`,
>        `src/zmai/swe/tools.py`

---

## 1. 目标状态机

完整 Plan Mode 应有以下状态转换：

```
CREATED
    ↓
PLANNING          ← 规划阶段：分析任务，生成 Plan
    ↓
PLAN_READY        ← Plan 已完成，等待确认/授权
    ↓
USER_CONFIRMATION ← 用户确认 Plan（或自动跳过）
    ↓
EXECUTING         ← 按 Plan 执行
    ↓
VERIFYING         ← 验证执行结果
    ↓
COMPLETED/FAILED
```

---

## 2. 逐项检查结果

### 2.1 `auto_plan`

| 项目 | 状态 | 详情 |
|------|------|------|
| config flag 存在 | ✅ 已实现 | `context.config.get("auto_plan", False)` — `swe/agent.py:223` |
| 通过 CLI 传递 | ✅ 已实现 | `zmai --plan <task>` → `config["auto_plan"] = True` — `cli/main.py:608` |
| 调用 planner | ✅ 已实现 | `generate_plan(task, backend, config)` — `agent.py:231` |
| Plan 存储到 metadata | ✅ 已实现 | `context.metadata["execution_plan"]` — `agent.py:232` |
| 注入到 system prompt | ✅ 已实现 | `_PLAN_EXECUTION_PROMPT + format_plan_summary(plan)` — `agent.py:252` |
| Plan 未完成时 replan | ✅ 已实现 | `replan_count < MAX_REPLANS` → 重新生成 — `agent.py:358-374` |
| Plan 完成检查触发 | ⚠️ 有条件 | 仅在 `response.tool_calls` 为 falsy 时触发 — `agent.py:357` |
| Plan step 状态更新 | ❌ **未实现** | `Plan.mark_step()` 定义在 `models.py:89` 但从未被调用 |
| Plan 完成判定 | ❌ **失效** | 因 mark_step 未调用，`is_finished` 永远 False，导致 replan 死循环 |

**结论**: `auto_plan` 的生成和注入完整，但 **Plan step 状态从不更新**，导致 Plan 永不会被判定为完成。每次非 tool_call 返回都会触发 replan（最多 3 次后耗尽），然后降级为正常执行。

---

### 2.2 Plan 数据结构

| 项目 | 状态 | 位置 |
|------|------|------|
| `Plan` dataclass | ✅ 已实现 | `swe/models.py:44-116` |
| `PlanStep` dataclass | ✅ 已实现 | `swe/models.py:14-41` |
| JSON 序列化/反序列化 | ✅ 已实现 | `to_dict()` / `from_dict()` |
| 格式验证 | ✅ 已实现 | `validate_plan_dict()` — `models.py:128-154` |
| 可读格式化 | ✅ 已实现 | `format_plan_summary()` — `models.py:157-185` |
| `is_finished` 属性 | ✅ 已实现 | — `models.py:77-79` |
| `current_step` 属性 | ✅ 已实现 | — `models.py:82-87` |
| `mark_step()` 方法 | ✅ 已定义但 **未调用** | — `models.py:89-94` |
| `MAX_REPLANS` 常量 | ✅ 已实现 (值=3) | — `models.py:121` |

**结论**: 数据结构完整。**唯一缺口是 `mark_step()` 从未被 SWEAgent 或任何代码调用。**

---

### 2.3 PlanAgent

| 项目 | 状态 |
|------|------|
| 存在 `PlanAgent` 类 | ❌ **不存在** |
| 存在独立的 Plan 逻辑单元 | ❌ **不存在** |

当前 Plan 逻辑直接嵌入在 `SWEAgent.step()` 中（`swe/agent.py:209-231`）：

```python
# ── 自动规划阶段 ──────────────────────────────────
auto_plan = context.config.get("auto_plan", False)
plan: Plan | None = context.metadata.get("execution_plan")

if auto_plan and plan is None:
    plan = generate_plan(...)
    context.metadata["execution_plan"] = plan
```

Plan 生成只是 SWEAgent step 内部的一个 if 分支，没有独立的 Agent 类或状态机来管理 Plan 生命周期。

**缺少**: `PlanAgent` 类 — 应作为一个独立的 Agent 阶段（PLANNING），生成 Plan 后转换到 PLAN_READY，等待 USER_CONFIRMATION，再进入 EXECUTING。

---

### 2.4 PlanModeGuard

| 项目 | 状态 |
|------|------|
| 存在 `PlanModeGuard` 类 | ❌ **不存在** |
| 任何 Plan 权限/守卫机制 | ❌ **不存在** |

Guard（守卫）的核心职责:
- Plan 生成后拦截执行，等待用户确认
- 检查 Tool 权限
- 阻止未授权的 Plan 步骤执行

当前没有任何 Plan 确认或守卫机制。Plan 生成后直接被注入 system prompt 并继续执行，没有拦截点。

**缺少**: `PlanModeGuard` 类 — 应负责:
1. PLAN_READY 状态下的 Plan 持有
2. 用户确认接口 (`confirm_plan`)
3. 拒绝 Plan 的回退路径 (`reject_plan`)

---

### 2.5 Tool 权限

| 项目 | 状态 | 位置 |
|------|------|------|
| 集中式 Tool 权限系统 | ❌ **不存在** | — |
| `on_confirm` 回调定义 | ✅ 已定义 | `swe/tools.py:536-537` (ShellTool), `swe/tools.py:582-583` (GitTool) |
| `on_confirm` 被实际传递 | ❌ **从不生效** | 构造 ToolContext 时从未传入 `on_confirm` |

当前工具权限现状:
- `ShellTool.execute()` 从 `context.config.get("on_confirm")` 读取确认回调
- `GitTool.execute()` 同理
- 但 SWEAgent.step() 构造的 ToolContext 只传入 `config=context.config`（用户配置），不包含 `on_confirm`
- 因此 `on_confirm` 回调 **永远为 None**，确认功能形同虚设

**缺少**: 
1. 全局 ToolPermission 注册表
2. Plan 级别的工具权限声明（哪些 tool 在哪些步骤可用）
3. `on_confirm` 的传递链路

---

### 2.6 Agent 状态

#### 枚举定义 (`agent/base.py`)

| AgentState | 存在 | 使用情况 |
|------------|------|----------|
| CREATED | ✅ | ✅ `SWEAgent.__init__` 设初始值 |
| PLANNING | ✅ | ❌ **从未进入**（Runtime 和 Agent 均不设置） |
| EXECUTING | ✅ | ❌ **从未进入**（Agent.state 始终为 CREATED） |
| VERIFYING | ✅ | ❌ **从未进入** |
| COMPLETED | ✅ | ❌ 不通过 state 设置，由 LifecycleManager 管理 |
| FAILED | ✅ | ❌ 同上 |
| CANCELLED | ✅ | ❌ 同上 |
| TIMEOUT | ✅ | ❌ 同上 |

**关键问题**: `Agent.state`（SWEAgent 实例的 `self.state`）在 `__init__` 中设为 `CREATED` 后 **永不更新**。代码注释承认了这点:

> `agent.py:183-184`: "状态由 Runtime 的 LifecycleManager 统一管理，Agent 自身不维护独立状态"

但基类文档化了完整的状态转换规则，实际上这些规则只在 LifecycleManager 中部分执行。

#### LifecycleManager 状态转换 (`runtime/lifecycle.py`)

| 转换 | 存在 | 被 Runtime 调用 |
|------|------|----------------|
| CREATED → PLANNING | ✅ 在 `_TRANSITIONS` 中 | ❌ **从未调用** (`lifecycle.plan()` 存在但 Runtime 不用) |
| CREATED → EXECUTING | ✅ | ✅ `Runtime.run()` line 130 |
| PLANNING → EXECUTING | ✅ | ❌ 因从未进入 PLANNING |
| EXECUTING → VERIFYING | ✅ | ❌ **从未调用** |
| EXECUTING → COMPLETED | ✅ | ✅ |
| EXECUTING → FAILED | ✅ | ✅ |
| EXECUTING → TIMEOUT | ✅ | ✅ (`lifecycle.timeout()`) |
| VERIFYING → COMPLETED | ✅ | ❌ 从未进入 VERIFYING |
| VERIFYING → FAILED | ✅ | ❌ 同上 |
| VERIFYING → EXECUTING | ✅ | ❌ 同上 |

**缺少的状态转换**: 以下 LifecycleManager 支持但从不进入的路径:
- `created → planning` — 有转换规则，但 `lifecycle.plan()` 从未被调用
- `executing → verifying` — 有转换规则，但 `lifecycle.verify()` 从未被调用
- `verifying → completed` — 有转换规则，但没有触发点

---

### 2.7 CLI 入口

| 命令 | 状态 | 位置 |
|------|------|------|
| `zmai plan <task>` | ✅ Plan Only | `_run_plan_only()` — `main.py:392-471` |
| `zmai --plan <task>` | ✅ Auto Plan | `_oneshot_run()` — `main.py:608` |
| Plan 展示 | ✅ 格式化输出 | `_run_plan_only()` line 436-458 |
| Plan 保存到 session | ✅ JSON 文件 | `_run_plan_only()` line 462-471 |
| Plan 确认流程 | ❌ **无** | 没有 `zmai plan confirm` 或交互式确认 |

**CLI 缺口**:
- `zmai plan` 只有 PLAN ONLY 模式（生成并展示，不执行）
- `zmai --plan` 是 AUTO PLAN 模式（生成后直接执行，无确认）
- 没有 `zmai plan confirm <task>` 交互式工作流

---

## 3. 完整 Plan Mode 缺失全景

### 需要新增

```
缺失组件                    优先级    说明
────────────────────────────────────────────────────────
PlanAgent 类                P0      独立 PLANNING 阶段的 Agent 封装
PlanModeGuard 类            P0      Plan 权限/确认/守卫机制
PLAN_READY 状态              P0      两个新增 AgentState/Lifecycle 状态
USER_CONFIRMATION 状态       P0
```

### 需要修复

```
缺失修复                    优先级    说明
────────────────────────────────────────────────────────
Plan.mark_step() 调用链路   P0      在 SWEAgent 执行工具后调用 mark_step()
Agent.state 同步             P1      SWEAgent.state 应与 LifecycleManager 同步
on_confirm 回调传递          P1      ToolContext 需传入 on_confirm 回调
VERIFYING 状态触发           P1      验证阶段应有独立的 lifecycle.verify() 调用
lifecycle.plan() 集成       P2      Runtime 应在 auto_plan 时调用 lifecycle.plan()
EXECUTING → VERIFYING       P2      执行完成后显式转换到 VERIFYING
```

### 当前实际执行路径 vs 目标路径

**当前**:
```
CREATED → EXECUTING → [plan inline] → LLM call → tool loop → ... → finalize → COMPLETED/FAILED
```

**需要**:
```
CREATED → PLANNING → [PlanAgent.generate()] → PLAN_READY → [Guard.confirm()] →
USER_CONFIRMATION → [用户确认/自动确认] → EXECUTING → [step loop] →
VERIFYING → [独立验证] → COMPLETED/FAILED
```

### 具体缺失项对照

| 阶段 | 当前代码 | 目标 |
|------|----------|------|
| PLANNING | `AgentState.PLANNING` 存在但从未进入；plan 生成是 step() 内的 inline 代码 | `PlanAgent` 独立运行，生成 Plan 后显式转换到 PLAN_READY |
| PLAN_READY | 不存在 | Plan 生成后进入此状态，Plan 被 Guard 持有，等待确认 |
| USER_CONFIRMATION | 不存在 | 用户 `zmai plan confirm <task>` 或 BYPASS 模式 |
| EXECUTING | Runtime 直接 `lifecycle.execute()`；没有 Plan 驱动的步骤分解 | 按 Plan step 逐个执行，每步更新 `mark_step()` |
| VERIFYING | `_auto_verify()` 在 step() 末尾作为普通方法调用 | 独立的 VERIFYING 阶段，`lifecycle.verify()` 触发，有专门的 verify loop |
| COMPLETED | ✅ 正常 | ✅ |

---

## 4. 当前 Prompt 模板 vs 实际使用

PromptEngine 定义了 5 种 PromptType:
- `SYSTEM` — ✅ 在 `_build_system_prompt()` 中使用
- `PLANNER` — ⚠️ 定义了模板但 `swe/planner.py:24` 使用硬编码的 `_PLAN_SYSTEM_PROMPT`，未使用 PromptEngine
- `EXECUTOR` — ❌ 定义了模板但从未使用（SWEAgent 不依赖 PromptEngine）
- `VERIFIER` — ❌ 定义了模板但从未使用（验证使用 `_auto_verify()` 而非 LLM 调用）
- `REPORT` — ❌ 定义了模板但从未使用

**PromptEngine 与 Plan 系统完全解耦** — 定义了规划/执行/验证/报告的模板组件，但实际代码未使用。

---

## 5. 测试覆盖

| 测试 | 状态 | 文件 |
|------|------|------|
| Plan 模型创建/序列化 | ✅ | `test_planning.py:TestPlanModel` |
| Plan 格式验证 | ✅ | `test_planning.py:TestPlanValidation` |
| Plan JSON 解析 | ✅ | `test_planning.py:TestPlanParsing` |
| Plan 生成（Mock Backend） | ✅ | `test_planning.py:TestPlanGeneration` |
| 重新规划逻辑 | ✅ | `test_planning.py:TestPlanReplanning` |
| auto_plan config | ✅ | `test_planning.py:TestPlanExecution` |
| Plan-only CLI 输出 | ✅ | `test_planning.py:TestPlanOnlyMode` |
| Plan 步骤状态更新 | ❌ **无测试**（因为 mark_step() 从未调用） |
| PlanAgent | ❌ **无测试**（不存在） |
| PlanModeGuard | ❌ **无测试**（不存在） |
| Plan 确认流程 | ❌ **无测试**（不存在） |
| Plan 工具权限 | ❌ **无测试**（不存在） |
| VERIFYING 状态转换 | ❌ **无测试**（不存在） |

---

## 6. 总结

**当前 Plan 系统是"一层皮"**: 能生成 Plan、解析 Plan、注入到 Prompt，但：

1. **Plan step 状态从不更新** — `mark_step()` 定义在 `models.py:89` 但 SWEAgent 从未调用。Plan 的 `is_finished` 永远返回 `False`。
2. **无独立 PLANNING 阶段** — Plan 生成是 step() 内部的 if 分支，不是独立的状态/Agent。
3. **无 PLAN_READY / USER_CONFIRMATION** — 这两个状态完全不存在于代码中。
4. **无 PlanAgent 类** — 所有 Plan 逻辑散落在 SWEAgent.step() 中。
5. **无 PlanModeGuard 类** — 没有 Plan 确认/权限/守卫机制。
6. **Tool 权限形同虚设** — `on_confirm` 定义在 ShellTool/GitTool 中但从不传递。
7. **Agent.state 永不更新** — 基类定义了完整状态机，但 SWEAgent 从不使用。
8. **VERIFYING 是留空阶段** — LifecycleManager 支持 but Runtime 从不触发。

**实现度评估**: 约 20%。定义了状态枚举和数据模型，但核心流程（状态转换、Plan 追踪、确认、权限、独立验证）未接入实际执行路径。
