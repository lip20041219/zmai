# ZMAI SC-M1 可信修复报告：开发状态

- 日期：2026-11-12
- 分支：`feature/evidence-runtime`
- 基线：当前工作树在开发前已包含未提交修改；未清理、暂存、提交或覆盖这些内容。
- 本轮测试：已编写定向回归用例，按要求未执行。

## 结论

SC-M1 报告已接入 `Runtime.run()` 的 Agent 执行出口。Runtime 先依据现有 `CompletionLedger` / `decide_terminal()` 得到终态，再把既有结果、证据账本、执行统计和 `ExecutionLog` 白名单摘要写成 `report.json`、`report.md`。报告异常被捕获并转换为报告元数据状态，不改写原任务的 `status`、错误或证据判定。

执行阶段报告保存于 Agent 工作区 `output/delivery/<run_id>/`，`output/` 会在 Runtime 清理时保留；执行未开始的 preflight/lifecycle 失败保存于 workspace `.reports/<run_id>/output/delivery/<run_id>/`。Runtime 返回值顶层提供 `workspace_directory` 与相对报告路径；存在 `metadata` 时也附加 `metadata.delivery_report`。SDK 可从 `Runtime.run()` 结果读取，CLI `--json` 输出相同结果，普通 CLI/REPL 摘要显示 Markdown 路径。

## 实际调用链

`Runtime.run()` 创建运行 ID 并捕获项目 Git 基线 → Agent 执行并向 `CompletionLedger` 写入事实/证据、将 `ExecutionLog` 落盘 → Runtime 依据现有判定结果构造返回字典 → `attach_delivery_report()` 读取完成结果、账本和日志白名单字段 → 比较运行前后 Git 状态 → 安全写入 JSON、Markdown 及满足条件的 patch → 将相对报告路径附加到 Runtime 返回值 → CLI/SDK 消费结果。

报告不重新计算 `completed/candidate/failed/timeout/cancelled`，也不更改 M1 的判定规则。Runtime 调度后的正常结果、异常、超时、取消和调度失败都经过报告出口；Preflight、生命周期创建、工作区初始化以及 Plan 失败/拒绝路径也会生成报告。若报告写入失败，返回结果中的 `delivery_report.status` 为 `failed`，任务原状态保持不变。

## 修改文件

- `src/zmai/swe/delivery_report.py`：白名单 schema、敏感值/本机路径脱敏、Git 前后快照、可信文件归属、patch 导出与哈希、日志摘要、Markdown/JSON 原子写入。
- `src/zmai/runtime/runtime.py`：为各 Runtime 返回路径接入同一报告附加出口；报告异常不改变任务终态。
- `src/zmai/cli/formatters.py`：普通 CLI/REPL 摘要展示报告路径；`--json` 沿用 Runtime 返回值。
- `tests/test_swe_delivery_report.py`：报告终态、脏工作树、敏感字段回归用例。
- `tests/test_runtime_terminal_facts.py`：真实 Runtime 出口、CLI 摘要路径、无 pytest 客观检查、报告写入异常回归断言。该文件在本轮前已是未跟踪文件，本轮在原内容上增补。
- `docs/competition/SC-M1_IMPLEMENTATION_STATUS.md`：本轮开发状态及本地验证说明。

## 报告字段与安全边界

报告包含 Runtime 最终状态/判定理由/旧状态审计字段、任务摘要、运行 ID、步数/耗时/Token、SWE 统计、执行日志事件白名单、修改文件、测试范围/计数/完整性、无 pytest 客观检查、未验证风险、下一步操作，以及固定为 `not_run` 的外部验收状态。缺失值使用 `unknown`、`null` 或空记录显式呈现。

报告只序列化预设字段；不导出原始工具输入、工具输出或完整日志。执行日志按 agent_id 和开始时间校验新鲜度，并只保留白名单事件字段。任务、测试命令、错误、路径和 patch 内容经过脱敏。脱敏会改变 patch 时，报告将其标为 `redacted` 和 `directly_applicable: false`；不会将其描述为可直接应用的补丁。文件写入使用同目录临时文件和原子替换，并检查输出路径留在配置的 workspace 与 Agent 工作区内。

若运行前 Git 工作树为脏状态，运行前已存在的修改路径被列为来源不明；只列出运行后新出现且可归因的路径，patch 状态为 `withheld_dirty_baseline`。Runtime Agent 工作区会从项目 Git 快照和 patch 中排除，避免日志/报告混入代码变更。若 Git 根目录、HEAD、diff 或未跟踪文件不能安全确认，patch 不导出并记录原因。若 `.gitignore` 在任务期间变化，也不导出 patch。修改清单与 patch 仅覆盖 Git 可见状态；未被 Git 跟踪的忽略文件无法从这些快照证明。

## 定向测试

新增/修正覆盖：正常完成、合法 candidate、失败、超时、取消、异常、无 pytest 客观验证、无证据旧状态仍失败、已有脏工作树不导出 patch、Runtime workspace 排除、密钥/路径/日志脱敏、报告写入异常不改变 Runtime 终态、CLI 摘要可见报告路径。

请在本地执行：

```powershell
python -m pytest tests/test_swe_delivery_report.py tests/test_runtime_terminal_facts.py -q
```

上述命令本轮未运行；没有将其结果记作通过。未运行全量 pytest、E2E、SWE-bench 或真实模型调用。

## 尚未验证

- 本机实际 CLI `--json` 和普通终端输出，以及外部 SDK 调用方对返回字段的兼容性；由定向测试覆盖返回值/摘要路径，但本轮未运行。
- Windows/WSL、多并发运行及强制进程终止期间的真实文件系统行为。
- 非 Git 项目、Git 子模块/重命名、超大或二进制未跟踪文件等边界；这些边界在代码中标记为 unknown/不导出 patch，未做运行验证。
- 敏感信息识别依赖当前规则集；未知凭据格式或未识别的本地路径仍可能需要扩充规则。
- 当前分支此前已有脏改动；SC-M1 的运行时测试及真实模型闭环均待本地执行。
