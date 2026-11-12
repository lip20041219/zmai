# ZMAI 场景交付基线审计（SC-M0）

- 审计日期：2026-11-12
- 依据：`ZMAI_AIC_场景创新_工程优化与交付报告_v2.0.md`
- 分支：`feature/evidence-runtime`
- HEAD：`31be1e7` (`feat: implement evidence runtime M1 P0-1 to P0-4`)
- 工作树：有未提交修改和未跟踪文件；其中包括本地 Runtime 终态收尾与既有开发改动。本审计未清理、暂存、提交或覆盖这些内容。
- 远端状态：`origin/feature/evidence-runtime` 显示本地落后 2 个提交；本轮没有 fetch、rebase 或 merge。

## 结论

本地工作树已经具备 Runtime 对 SWE Agent 终态的统一判定出口：`SWEAgent.finalize()` 返回非终态结果载荷，Runtime 根据共享 `CompletionLedger` 调用 `decide_terminal()`，再用同一状态更新 lifecycle 并返回 `Runtime.run()` 结果。CLI 的 JSON 输出和普通摘要都消费该返回状态。无证据时不会把旧的 `completed` 或 `candidate` 建议直接返回。

SC-M0 的代码链路、字段来源和本地任务入口审计完成。此结论只针对当前本地工作树；HEAD 本身尚未包含这些未提交改动，跨平台 CI、真实模型任务和正式场景复现均未在本轮执行。

## 真实终态调用链

1. `Runtime.run()` 创建一个 `CompletionLedger`，并把它交给 SWE Agent context。Runtime 在 AgentAction 失败、最大步数/墙钟耗尽、取消或异常发生时记录终态事实。
2. `SWEAgent.step()` 继续使用 `CompletionState` 控制 Agent 何时停止继续请求模型；完成与候选前的证据门仍执行。`_shadow_terminal()` 保留步骤级影子审计。`CompletionState` 和步骤级影子结果不直接决定 Runtime 对外终态。
3. `SWEAgent.finalize()` 当前返回 `AgentState.EXECUTING` 的结果载荷，保留诊断和 metadata，不生成新的最终状态，也不反向补写失败/超时事实。
4. Runtime 在最终出口调用 `decide_terminal(run_ledger)`。若结果是 `continue`，Runtime 写入“终态证据不足”的失败事实，再由判定器得出 `failed`；不会回退使用 `finalize()` 的旧状态建议。
5. Runtime 将判定映射为 `AgentState`，写入 `terminal_legacy_status` 与最终 `evidence_decision`/`evidence_ledger`/`evidence_shadow`，然后更新 lifecycle 并返回字典结果。
6. `src/zmai/cli/main.py` 的 one-shot 和 REPL 都调用 `Runtime.run()`。`--json` 直接序列化该结果；普通 CLI 摘要也读同一个 `status`。SDK 侧可直接调用 `Runtime.run()` 并获得同一字典结果；此处没有单独的 `AgentResult` SDK 导出层。

### 终态出口矩阵

| 情况 | 事实来源 | `decide_terminal()` / Runtime 结果 | 验证状态 |
|---|---|---|---|
| 完整范围完成 | 有效 full-scope green，或无 pytest 项目全部客观检查通过 | `completed`；Runtime 更新完成 lifecycle | 已跑真实 SWEAgent Runtime full-scope 测试；见下方测试记录 |
| 候选 | 修改后有效局部 green、无已知新增失败且覆盖不完整 | `candidate`；不升级为 completed | 已跑真实 SWEAgent Runtime candidate 测试及 Runtime 旧状态建议覆盖测试 |
| 无证据的完成/候选建议 | ledger 无可用完成/候选依据 | 首次判定 `continue`，Runtime 记录证据不足失败事实，再判为 `failed` | Runtime 定向测试覆盖旧状态建议被拒绝 |
| 显式失败 | Runtime 收到 `AgentAction.fail` 时写入 failed 事实；计划重试耗尽在 `step()` 发出失败动作 | `failed` | Runtime 失败及计划耗尽定向测试通过 |
| 超时 | 墙钟或最大步数路径写入 timeout/budget 事实 | `timeout` | Runtime 定向测试通过 |
| 取消 | Runtime 捕获 `CancelledError` 时记录 cancelled | `cancelled` | Runtime 定向测试通过 |
| 异常 | Runtime 捕获异常并写入 exception/fatal 事实 | 判定为 `failed`，保留错误信息 | Runtime 定向测试通过 |

判定器优先处理预算耗尽、fatal error 和取消事实；随后才评估无 pytest 客观验证及测试证据。证据污染/stale 不进入可用证据集。`candidate` 只由有效且非完整范围证据支持。

### 关键源码位置

- Runtime 创建账本与事实记录器：`src/zmai/runtime/runtime.py:154`、`src/zmai/runtime/runtime.py:188`。
- Runtime Agent 执行与统一最终映射：`src/zmai/runtime/runtime.py:309`、`src/zmai/runtime/runtime.py:417`、`src/zmai/runtime/runtime.py:473`。
- 运行时终态事实与判定规则：`src/zmai/swe/evidence.py:150`、`src/zmai/swe/evidence.py:162`、`src/zmai/swe/evidence.py:210`、`src/zmai/swe/evidence.py:272`。
- SWE 步骤级完成证据守卫及结果载荷：`src/zmai/swe/agent.py:1133`、`src/zmai/swe/agent.py:4456`。
- `CompletionState`（循环停止信号）：`src/zmai/swe/completion.py:25`。
- `AgentResult`（Runtime 内部中间载荷）：`src/zmai/agent/base.py:108`。
- CLI 任务参数、REPL/one-shot Runtime 调用和 JSON 输出：`src/zmai/cli/main.py:179`、`src/zmai/cli/main.py:239`、`src/zmai/cli/main.py:366`、`src/zmai/cli/main.py:392`。
- 日志脱敏边界与 Benchmark 报告器：`src/zmai/execution/log.py:76`、`src/zmai/execution/log.py:130`、`src/zmai/eval/reporter.py:31`。
- Runtime 终态权威及无证据拒绝用例：`tests/test_runtime_terminal_facts.py:127`、`tests/test_runtime_terminal_facts.py:138`；真实完成/候选/no-pytest Runtime 用例：`tests/test_swe_evidence_decision.py:293`、`tests/test_swe_evidence_decision.py:324`、`tests/test_swe_evidence_decision.py:352`。

## 数据字段来源矩阵

| 报告所需信息 | 当前来源 | 缺口 / 约束 |
|---|---|---|
| 最终状态、判定原因 | Runtime 返回顶层 `status`；`metadata.evidence_decision`；`terminal_legacy_status` 仅审计 | 可作为后续报告的权威状态源；报告不得覆盖它 |
| Agent 标识 | `agent_id`；CLI one-shot 默认使用 PID 组合值 | 没有独立、全局稳定的 `run_id` 字段。测试证据 `run_id` 是由 agent_id、step、记录计数生成的局部标识 |
| 任务描述 | Runtime 参数；ExecutionLog 保存 task | `AgentResult` 没有统一 task/project 概览字段；task 和日志可能含用户敏感信息 |
| 项目路径、提交 SHA、仓库名 | 执行期间 `config.project_path` 和本地 Git 工具可读 | 不在最终 Runtime 返回的稳定项目对象中；报告需要按需查询并脱敏，缺失时标 unknown |
| Patch、修改文件、patch SHA | 修改直接落在本地项目工作树；Git 工具可执行 diff | 当前没有同 run patch 文件、changed-file 清单或 patch hash 的结构化导出 |
| 测试命令、通过/失败数、范围、覆盖 | `EvidenceRecord`：command、scope、coverage、exit_code、passed/failed、failed_ids、executed_tests 等 | 只对实际采集并保留的测试运行负责；不得据此推断隐藏验收 |
| 完整性、nonce、独立回放 | `EvidenceRecord.integrity`、replay、fingerprints；guard receipt/snapshot 在运行核验过程中使用 | 报告需明确记录是否适用/是否通过，不能把缺失值推断为通过 |
| 无 pytest 的客观验证 | ledger 的 `objective_verification_required` 与 `objective_checks` | 只有真实通过的检查支持完成；模型文本不是证据 |
| 步数、工具统计、Token | `AgentResult.steps`、`swe_stats`、`token_usage` | JSON 结果没有总耗时；CLI 文本摘要单独测 elapsed，但没有写回 Runtime JSON |
| 外部验收 FTP/PTP | 当前仅存在独立评测/历史记录，不是运行时 ledger 的事实 | 仅在独立评测真实执行后由外部记录；不可由 Agent 填写 |
| `next_action`、面向用户的风险摘要 | 可由最终状态、unverified_scope、decision reason 派生 | 当前无统一字段；后续报告需由证据事实映射，不能增加第二套终态规则 |

## 已有导出与脱敏能力

- `src/zmai/eval/reporter.py` 的 `ScoreReporter` 能导出 Benchmark 聚合统计的 JSON/Markdown/CSV；它不是单次 SWE Agent 修复报告，不能复用为一运行一报告的完整实现。
- `ExecutionLog` 会把工具入参中匹配敏感键的值遮蔽，并截断单项工具输出；Runtime 将执行日志写到 workspace 的 `.state/execution_log.json`。它不是 patch 导出器，也不完整脱敏 task、工具输出、error 或 metadata。
- Runtime `_redact_error()` 和 CLI `_sanitize_error()` 主要处理错误字符串中的常见密钥形式。`zmai --json` 使用通用 JSON 序列化直接输出整个 Runtime 结果；目前没有针对任务文本、绝对路径、命令、日志输出和嵌套证据 metadata 的统一报告脱敏层。
- 因此，SC-M1 报告导出必须实施自己的字段白名单/脱敏处理；不能把 `ExecutionLog` 或错误字符串脱敏当成报告安全保证。

## 可复现的本地故障输入流程

当前 CLI 没有 `--project-path`、`--failure-log` 或 CI artifact 导入参数。已有最短入口是从目标仓库根目录启动 CLI：项目根通过当前工作目录探测，`project_path` 设为启动目录，任务描述可要求 Agent 复现 pytest 失败。

```powershell
Set-Location <目标 Python 仓库根目录>
zmai --json "先运行项目的 pytest 复现当前失败；根据真实失败定位并修复业务代码，不改测试；修改后运行适当测试并说明未覆盖范围。"
```

该入口依赖本机已安装/可运行的 `zmai`、有效 backend 配置和模型 API；本轮没有运行此命令或调用真实模型。对于已有日志，可在任务文本中提供经授权且脱敏的 traceback，但目前没有专门的失败日志导入接口。若未来需要离线日志入口，应在确认 CLI 参数边界后再单独实现。

## 历史数据与版本标签

| 数据 | 来源与版本 | 本轮口径 |
|---|---|---|
| P0 Runtime / guard 定向测试 36 passed | 2026-11-12 本地工作树定向命令（此前本地收尾轮次）；无真实模型 | 工程行为回归，不等于真实修复率；原始 pytest 输出未另存到报告文件 |
| 四个 SWE-bench Lite 主机探针 | `benchmarks/results/swebench_lite/HOST_PROBE_REPORT_2026-11-10.md`，DeepSeek Flash；样本 4 个 | 历史独立 FTP/PTP 数据，1/4 resolved；不与其他批次合算，也不是当前 HEAD 新跑结果 |
| README CI 6/6、1753 collected | README 明确标注来源 commit `c4e2e5a` | 历史 CI 记录；当前 HEAD/脏工作树没有对应 CI 证明，不可表述为当前版本已通过 CI |
| 当前分支 CI、真实 Agent run、用户访谈 | 没有本轮新结果 | 未执行/未提供；保持 unknown 或 not_run |

## 已实现、待测、未实现

### 已实现（本地工作树）

- SWE Agent 的终态由 Runtime 基于 `decide_terminal()` 映射；旧状态仅作为审计字段。
- 完整范围、候选、失败、超时、取消、异常及无 pytest 客观验证的数据结构和出口已接线。
- 没有可信依据的 completed/candidate 请求不能通过 Runtime 出口；安全门禁与关键正向路径已有定向测试。
- CLI one-shot/REPL 与直接 Runtime 调用使用同一顶层状态源。

### 待测 / 有边界

- 当前 M1 变更仍在脏工作树；不等同于已提交版本或远端版本。跨平台 CI 和干净 checkout 验证未执行。
- 最近一次本地定向测试结果为 36 passed，但没有独立保存完整 stdout/stderr 文件；后续正式结果台账应保留原始日志和运行 SHA。
- 真实模型修复闭环、耗时/成本统计、patch/commit 哈希、脱敏报告、安全路径写入、报告损坏不影响 Runtime 等尚未端到端验收。
- CLI 的一次性运行进程退出码只区分 `completed`（0）与其他状态（3）；候选/失败/超时的精细区别要读取 JSON `status`，不可只看退出码。

### 未实现

- 单次运行 `report.json` / `report.md` 交付生成器及稳定 schema。
- 同一 run 的 patch 文件、changed files 与 hash 的持久化/引用。
- 专门的离线 pytest/CI 日志导入入口、`zmai verify`、报告 HTML UI。
- 当前版本真实复现主案例、守卫消融、外部独立验收，以及用户访谈原始记录。

## SC-M0 验收与下一阶段

| 条目 | 结论 | 依据 |
|---|---|---|
| 当前 Runtime 是 SWE 最终状态权威 | 通过（本地工作树） | `finalize → Runtime decide_terminal → lifecycle → Runtime.run dict → CLI` 调用链一致 |
| candidate 不升级 completed；旧 completed/candidate 无证据 fail-closed | 通过（本地定向验证） | Runtime 旧状态建议覆盖和无证据拒绝测试 |
| 不新增第二个 Guard/Controller | 通过 | 沿用 EvidenceLedger、现有 completion/evidence 守卫和 Runtime 出口 |
| 本地输入流程明确 | 通过（入口存在，未执行） | 从目标仓库根目录运行 `zmai --json <task>`；真实 backend 运行待用户执行 |
| 当前 branch/CI/真实模型效应已验证 | 未通过/未执行 | 脏工作树、HEAD 落后远端 2 个提交、无本轮 CI/真实模型执行 |

**SC-M0 状态：通过，限制为本地事实审计。SC-M1 可解锁。** 下阶段应只实现最小可信修复报告，先复用 Runtime result、EvidenceLedger 和现有 Git 工作树，不改判定规则、不新增 Controller/Guard；所有字段缺失显示 unknown/unverified，外部验收默认 `not_run`。
