# ZMAI M2 / P1-1 时间预算守卫实施状态

日期：2026-11-12
分支：`feature/evidence-runtime`
状态：P1-1 收尾完成；定向验证通过。

## 实现摘要

- `ShellTool.execute()` 在启动测试子进程前，只对范围可静态证明为全量的测试命令检查剩余墙钟。
- 真实测试时长从 `ToolResult.duration_ms` 写入 `EvidenceRecord`；匹配同一规范化命令的最近实测记录，按 `×1.5` 估算。过期记录的耗时仍可用来估算，过期记录不能用于终态证据。
- 无同命令实测耗时时不推断执行成本，允许首次运行；现有墙钟裁剪和超时机制继续约束该次运行。`TEST_COMMAND_TIMEOUT` 是执行上限，不能作为预计耗时。
- 预算不足时返回 `[TIME_BUDGET_GUARD]`，不启动子进程、不标记 Runtime 超时。仅当 Agent 标记存在候选测试结果，且账本仍有有效、全绿、非全量、完整性通过且无新增回归的记录时提示 `SUBMIT_CANDIDATE`；真正提交仍由 M1 原有独立回放与安全门禁决定。
- 预算充足、无历史、无墙钟 deadline、定向测试及无法证明范围的命令沿用原执行路径。

## 调用链

`Runtime.run()` 在 SWE Agent 运行配置中设置 `_wall_deadline_monotonic` → `SWEAgent.step()` 将共享 deadline、`CompletionLedger` 和候选证据提示放入 `ToolContext` → `ToolRegistry.execute()` 调用 `ShellTool.execute()` → 全量测试启动前比较剩余墙钟与成本估算 → 预算不足则将结构化拒绝反馈交回 Agent；预算充足则继续现有超时裁剪和测试执行 → Agent 依据 `ToolResult.duration_ms` 创建 `EvidenceRecord`。

该守卫不写终态、不更改 `decide_terminal()`，也不直接生成 candidate。

## 本轮涉及文件

- `src/zmai/tool/base.py`：为工具上下文传递现有证据账本与候选证据提示。
- `src/zmai/swe/evidence.py`：证据记录增加测试耗时字段。
- `src/zmai/swe/agent.py`：向工具传账本，并把测试工具实际耗时写入证据。
- `src/zmai/swe/tools.py`：收尾修复无历史误拦截，并允许过期实测耗时参与成本估算；证据判定仍使用有效记录。
- `src/zmai/swe/delivery_report.py`：在报告白名单中加入测试耗时字段。
- `tests/test_swe_time_budget_guard.py`：补充无历史短测试与过期耗时边界回归。
- `tests/test_swe_delivery_report.py`：重复使用目录的 fixture 改为幂等创建，不改变报告功能。

以上文件存在任务开始前的其它未提交改动；本轮只增添上述关联代码，没有清理或覆盖其它改动。

## 验证

使用仓库解释器 `D:\desk\ZMAI\.venv\Scripts\python.exe`。系统默认 `python` 指向不含 pytest 的 Hermes 环境，故未用于测试。

通过：

- `python -m pytest tests/test_swe_time_budget_guard.py tests/test_swe_delivery_report.py -q`（仓库 `.venv` 解释器）：11 passed，含先前失败的报告状态用例。
- M1 候选、过期/污染证据及完整性门禁定向用例：5 passed，9 deselected。

合法 candidate 的现有 Runtime 用例单独运行超过 25 秒仍未结束；把子进程 PATH 指向仓库 `.venv` 后再次运行仍未结束，均已中断，不计为通过。候选证据门禁由上面的定向用例验证；合法 candidate 的完整 Runtime 提交路径尚未由本轮测试确认。完整项目回归与真实模型行为尚未验证。

未运行全量 pytest、SWE-bench、E2E 或真实模型调用；未 Push。

## 验收判断

**预算守卫阻止有历史依据的高成本测试，且无历史短测试可启动：YES。** Agent 真实工具分发链上的进程替身在预算不足时未被调用；无历史短测试进程替身被调用。

**M1 可信完成判定未降低。** 守卫不产生完成或候选决定；无有效候选证据只提示继续低成本验证，候选提交仍须经过已有回放、完整性及回归门禁。完整项目回归和真实模型行为尚未验证。
