# ZMAI SWE Agent Benchmark — Phase 1 项目审计报告

> 日期: 2026-08-21
> 范围: 仅审计，不修改任何核心 Agent 架构
> 目的: 客观评估 ZMAI 是否具备运行可复现、可量化 SWE Benchmark 实验的条件

---

## 1. 结论摘要

ZMAI 具备**部分**运行 SWE-bench 实验的条件。SWE-bench Lite 数据已缓存、独立判定链路（run_smoke.py）已能跑通 2/5 实例，但存在 3 个关键缺口，**必须修复后才能进行正式 Benchmark**：

| # | 缺口 | 影响 | 严重度 |
|---|------|------|--------|
| G1 | **Token 用量全链路未接线** | result.json 无法得到 input/output tokens | 🔴 高 |
| G2 | **无 Baseline Agent** | 无法回答 "与 Baseline 相比提升多少" | 🔴 高 |
| G3 | **模块级统计未落盘**（failure_parser / loop_guard / test_guard 触发次数） | 无法回答 "哪个机制带来提升" | 🟡 中 |
| G4 | run_smoke.py 指标采集读取了错误的日志路径 | tool_calls 恒为 null | 🟡 中 |

---

## 2. 组件审计

### 2.1 Agent 主流程 ✅

- 入口: `src/zmai/runtime/runtime.py` → `Runtime.run()` → `_run_agent()`
- 主体: `src/zmai/swe/agent.py` → `SWEAgent`（initialize → step 循环 → finalize）
- 循环控制: `while step_count < ctx.max_steps`，step 返回 `complete`/`fail` 提前退出，耗尽标记 `timed_out`
- finalize 状态优先级: timeout > step_failed > replan 耗尽 > tool_fail 比例 > verification 失败 > COMPLETED
- **问题**: `Runtime.run()` 返回 dict 仅含 `{status, output, steps, error}`，`context.metadata["swe_stats"]`（含 loopguard_blocks、fixdriving_activations 等）未透出

### 2.2 Planner ✅

- `src/zmai/swe/planner.py`: `generate_plan()` / `parse_plan_response()` — 通用 Plan JSON 生成
- `src/zmai/swe/plan_agent.py`: `PlanAgent` — 只读规划 SubAgent（结合 PlanModeGuard）
- `src/zmai/swe/fix_planner.py`: `FixPlanner` — 由 FailureIssue 生成修复步骤（用于测试失败后的修复闭环）
- `src/zmai/swe/plan_guard.py`: `PlanModeGuard` — 规划确认前禁止写操作
- 备注: `auto_plan` 配置开关控制，当前 smoke 未开启（直接执行模式）

### 2.3 Tool Calling ✅

- `src/zmai/tool/base.py`: `Tool` / `ToolCall` / `ToolDefinition` / `ToolContext` / `ToolResult`
- `src/zmai/tool/registry.py`: `ToolRegistry.execute_tool()` 容错分发（工具不存在返回结构化错误）
- `src/zmai/swe/tools.py`: 8 个工具（read_file / write_file / edit / grep / shell_exec / git / show_to_user / open_in_browser）
- 工具调用有 `duration_ms` 计时

### 2.4 Memory ✅（但 benchmark 中可能被记忆污染）

- `src/zmai/memory/manager.py`: `MemoryManager`（Working + Long-term）
- `SWEAgent.step()` 注入 memory context（最多 10 条）
- **Benchmark 关注点**: Long-term Memory 按 `agent_id` 持久化到 `~/.zmai/memory/<agent_id>`。若不同 run 复用相同 agent_id，记忆会跨任务泄漏 → 需要 run_id 隔离

### 2.5 Gateway ✅（Token 数据源存在）

- `src/zmai/gateway/base.py`: `BackendResponse.usage: TokenUsage`（input/output/cache tokens）
- DeepSeek/Claude/Gemini 后端均解析 `usage`（DeepSeekBackend 从 API 响应 `prompt_tokens`/`completion_tokens` 解析 ✅）
- **问题 (G1)**: `SWEAgent.step()` 拿到 `response.usage` 后**未做任何持久化**——既不写入 ExecutionLog，也不写入 metadata/result

### 2.6 SubAgent ✅

- `PlanAgent`（规划期，只读）是唯一 SubAgent
- 无通用多 Agent 框架，但 benchmark 用不到

### 2.7 FailureParser ✅

- `src/zmai/swe/failure.py`: `parse_test_failure()` → `FailureIssue`（error_type / semantic / hints / candidate_files / line / expected/actual）
- 8 条语义规则 + 兜底 Generic
- 首次测试失败时注入 `[Repair Plan]` 消息

### 2.8 LoopGuard ✅

- `src/zmai/swe/loop_guard.py`: 检测 identical_calls / identical_failures / no_progress（threshold=5）
- 触发后注入 `[LoopGuard][LoopRecovery]`，超过 recover_limit(2) 进入 force_edit
- `_stats(context, loopguard_blocks=1)` 计数，但**未落盘** (G3)

### 2.9 TestGuard ✅（内嵌于 agent.py）

- `src/zmai/swe/agent.py` (654-710 行): `baseline_test_count` + `scope_complete` 判定 partial_green / full_green
- partial_green → 强制完整套件重跑；full_green + exit0 → 计 `test_success_count`
- `completion.py` `CompletionState` 负责跨轮完成判定
- 有 `_eval_blocking_completion`（EvalGuard）防 SWE-bench 下"未修改即完成"

### 2.10 Retry ✅

- `agent.py` step(): `retry.max_attempts`(默认 3)，指数退避 1s/2s/4s；`BackendError` 立即透传不重试

### 2.11 SWE-bench Runner（两套并存 ⚠️）

| 模块 | 路径 | 状态 |
|------|------|------|
| 旧版 fixture Runner | `src/zmai/benchmark/runner.py` | 基于 5 个自建 fixture，Mock 后端 |
| 新版 Orchestrator | `src/zmai/eval/benchmark.py` `BenchmarkRunner` | 统一 loader→harness→collector |
| SWE-bench 数据/评测 | `src/zmai/eval/swebench.py` | 下载/解析/建仓/`evaluate_instance`（stash 方案，**有冲突风险**） |
| 独立 Smoke Runner | `benchmarks/results/swebench_lite/run_smoke.py` | **最完整**：test_before→agent→diff→干净重放→FTP/PTP 独立判定 |

- `evaluate_instance()` 用 `git stash` 方案（stash agent 改动 → reset → apply test_patch → stash pop），若 agent 改动与 test_patch 冲突会 pop 失败 → **建议改用 run_smoke.py 的"干净副本重放 diff"方案**

### 2.12 CLI ✅

- `src/zmai/cli/eval_cmd.py`: `zmai eval`（list/run/report/swebench/humaneval/custom）+ `zmai benchmark`（list/run/report）

### 2.13 测试体系 ✅

- `tests/` 下约 50 个测试文件，含 `test_swe*.py`、`test_loop_guard.py`、`test_swe_failure_planner.py`、`test_benchmark.py`、`test_eval.py`
- 独立判定测试存在（fixtures verification command + EvalHarness 外部验证）
- 全量此前通过（commit 记录 1311 passed）

### 2.14 日志系统 ✅

- `src/zmai/execution/log.py`: `ExecutionLog` / `StepRecord`（phase/action/tool_name/tool_input/tool_output/success/duration_ms/metadata）
- 持久化到 `<workspace>/.state/execution_log.json`
- 有脱敏 + 截断

### 2.15 Token / Tool Call / Runtime 统计能力 ⚠️

| 指标 | 现状 | 落盘? |
|------|------|-------|
| success/failure | run_smoke result.json | ✅ |
| iterations (steps) | `Runtime.run()` 返回 steps | ✅ |
| tool_calls | ExecutionLog 有 tool_call 记录；但 run_smoke 读错路径 | ⚠️ (G4) |
| tokens | Backend 解析了 usage，但 Agent 层丢弃 | ❌ (G1) |
| runtime | duration_seconds | ✅ |
| tests_before/after | ftp_before/ftp_after（原始文本，非计数） | ⚠️ |
| failure_parser_used | 无 | ❌ (G3) |
| loop_guard_triggered | `swe_stats.loopguard_blocks` 未透出 | ❌ (G3) |
| test_guard_triggered | 无 | ❌ (G3) |

---

## 3. 六个关键确认项

### 3.1 当前 SWE-bench Lite / 自建 Bug Benchmark 能否运行

- **SWE-bench Lite**: 数据已缓存（300 实例），5 个仓库已 clone（flask/requests/xarray/pylint/seaborn），隔离 venv `_evalenv` 已建（pytest 8.4）。run_smoke.py 已跑通 **2/5** 实例的完整链路（均判定 `no_change` 失败）。✅ 可运行，但结果不理想。
- **自建 fixture**: 5 个任务（task_001~005）可被旧 Runner / EvalHarness 运行。✅

### 3.2 当前已缓存的任务

| 来源 | 数量 | 位置 |
|------|------|------|
| SWE-bench Lite instances | 300 | `~/.zmai/swebench_data/lite_instances.json` |
| Clone 仓库 | 5 | `benchmarks/results/swebench_lite/repos/` |
| 单实例 task.json | 5 | `benchmarks/results/swebench_lite/<instance>/task.json` |
| 自建 fixture | 5 | `tests/fixtures/swe_tasks/task_00X_*/` |

### 3.3 当前测试是否能独立判断成功/失败

- **是**（run_smoke.py 方案）：base_commit + test_patch 跑 FTP 应 FAIL（验证可复现）→ agent 改完 → 干净副本重放 agent diff + test_patch → FTP 应 PASS 且 PTP 应 PASS → 才判 resolved。
- 判定独立于 Agent 自报状态。✅
- 但 `swebench.py::evaluate_instance()` 的 stash 方案有冲突风险，不建议作为正式判定。

### 3.4 是否已存在 baseline

- **否**。代码库中无任何 "baseline / 精简 agent" 概念。需要 Phase 4 自建：相同任务/环境/模型/工具，去掉 ZMAI 增强机制（FailureParser/LoopGuard/TestGuard/Memory/Planner）的简单 ReAct Agent。

### 3.5 是否能记录 success/failure/iterations/tool_calls/tokens/runtime/tests_before/tests_after/failure_parser/loop_guard/test_guard

部分能，见 2.15 表格。**tokens 与三个模块级计数器不能**，需修复。

---

## 4. 发现的具体问题（供 Phase 3 修复）

### G1 — Token 未接线（高）
- `SWEAgent.step()` 中 `response.usage` 被丢弃；ExecutionLog 的 `StepRecord` 无 token 字段；`Runtime.run()` 返回 dict 无 token。
- 修复方向（不破坏 Agent 架构）: 在 step() 内将 `response.usage` 写入 `context.metadata` 累积 + 写入 ExecutionLog metadata；Runner 从这两个来源读取。

### G2 — 无 Baseline（高）
- Phase 4 需实现一个配置开关或独立 Agent 类，使用相同 8 个工具 + 相同系统提示的基础版本，去掉 ZMAI 增强。

### G3 — 模块触发计数未落盘（中）
- `swe_stats`（loopguard_blocks / fixdriving_activations / test_success_count 等）只存在 `context.metadata`，`Runtime.run()` 未返回。
- 修复方向: Runtime 返回 dict 透出 `swe_stats`；或 Runner 直接读 ExecutionLog 的 `loop_guard` / `fix_driving` / `completion_guard` phase 记录。

### G4 — run_smoke 指标读错路径（中）
- `run_smoke.py::collect_agent_metrics()` 从 `Path(tempfile.gettempdir()) / agent_id` 读，但 ExecutionLog 实际落在 `workspace/<agent_id>/.state/`（由 Runtime 的 `self._workspace.prepare()` 决定，root 默认 `./workspace`）。
- 实测 result.json 中 `tool_calls` / `test_runs` 均为 null —— 证实该 bug。

### G5 — 结果字段不齐（中）
- 当前 result.json 缺少: `input_tokens` / `output_tokens` / `total_tokens` / `failure_parser_used` / `loop_guard_triggered` / `test_guard_triggered`；`tests_before`/`tests_after` 是文本非计数。
- 与任务要求的 result.json schema 有差距。

### G6 — 记忆污染风险（低）
- Long-term Memory 按 `agent_id` 持久化，跨 run 复用 agent_id 会泄漏前一次运行记忆。Benchmark 每次 run 需唯一 agent_id（如 `eval_<run_id>_<task>`）或禁用 memory。

### G7 — evaluate_instance 方案脆弱（低）
- `swebench.py` 的 stash 方案在 agent 改动与 test_patch 冲突时不可靠，正式 Benchmark 应采用 run_smoke 的"干净副本重放 diff"方案。

---

## 5. 对任务集规模可行性的初步判断

- SWE-bench Lite: 300 实例、12 个仓库（astropy/django/matplotlib/seaborn/flask/requests/xarray/pylint/pytest/scikit-learn/sphinx/sympy）。仅 clone 了 5 个，其余需联网 clone。
- 每个实例需 era 匹配的依赖环境（flask/requests 已通过 `_evalenv` + PYTHONPATH 方案解决；其余 3 个未验证——PROGRESS.md 标注为"待验证"）。
- 5 实例 Smoke 可行（依赖已基本就绪）；20–50 实例正式 Benchmark 需要逐仓库建环境，工作量大且存在环境失败风险（如实记为 environment error）。

---

## 6. 审计结论与建议

1. **结论**: ZMAI 的独立判定链路（run_smoke.py 方案）是可信的，判定逻辑正确。但统计能力（token / 模块计数器）与 Baseline 缺失，**当前尚不能产出符合任务要求的完整量化报告**。
2. **建议顺序**:
   - Phase 2: 任务集固定为已 clone 的 5 实例（flask/requests/xarray/pylint/seaborn）做 Smoke，后续再扩。
   - Phase 3: 新 Runner 复用 run_smoke 的独立判定链路 + 补齐 G1/G3/G4/G5/G6。
   - Phase 4: 实现 Baseline（去增强的 ReAct Agent）。
   - Phase 5: ZMAI 以现有 SWEAgent 最小适配接入。
3. **当前 smoke 的真实结果**（如实记录）: flask-4992 与 requests-3362 均因 agent 未产出代码修改而失败（`no_change`），这是 ZMAI 当前能力的真实反映，不是 harness 误判。

---

## 7. 附录：审计中检查的源文件清单

| 组件 | 文件 |
|------|------|
| Agent 主流程 | `src/zmai/swe/agent.py`, `src/zmai/runtime/runtime.py` |
| Planner | `src/zmai/swe/planner.py`, `plan_agent.py`, `fix_planner.py`, `plan_guard.py` |
| Tool Calling | `src/zmai/tool/base.py`, `registry.py`, `src/zmai/swe/tools.py` |
| Memory | `src/zmai/memory/manager.py`, `working.py`, `long_term.py` |
| Gateway | `src/zmai/gateway/base.py`, `registry.py`, `backends/deepseek.py` |
| FailureParser | `src/zmai/swe/failure.py` |
| LoopGuard | `src/zmai/swe/loop_guard.py` |
| TestGuard | `src/zmai/swe/agent.py` (654-710), `src/zmai/swe/completion.py` |
| SWE-bench Runner | `src/zmai/eval/swebench.py`, `src/zmai/eval/benchmark.py`, `src/zmai/benchmark/runner.py`, `benchmarks/results/swebench_lite/run_smoke.py` |
| CLI | `src/zmai/cli/eval_cmd.py` |
| 日志 | `src/zmai/execution/log.py` |
| 统计 | `src/zmai/eval/collector.py`, `src/zmai/eval/reporter.py` |
| 测试 | `tests/test_swe*.py`, `test_loop_guard.py`, `test_benchmark.py`, `test_eval.py` 等 |
