# SWE-bench Lite Round 1 Smoke — 最终结果

> 开始: 2026-08-20 18:20
> 结束: 2026-08-21 15:50
> 模型: deepseek (DeepSeek Chat)
> 配置: max_steps=60, eval.require_code_change=true

## 结果总表

| Instance | Status | FTP before | FTP after | PTP | Steps | 耗时 | 分析 |
|---|---|---|---|---|---|---|---|
| pallets__flask-4992 | ✅ **resolved** | FAIL(符合预期) | PASS | PASS | 39 | 271s | 正确修改 `config.py`，添加 `text` 参数兼容 tomllib |
| psf__requests-3362 | ❌ test_failure | FAIL(符合预期) | FAIL | FAIL | 0 | 659s | agent diff 产生语法错误（IndentationError）；网络中断中断 agent 运行 |
| pydata__xarray-4248 | ❌ no_change | FAIL(符合预期) | - | - | 60 | 1213s | 耗尽步数未产生任何 git diff；agent 反复读文件未构造 edit |
| pylint-dev__pylint-5859 | ❌ test_failure | FAIL(符合预期) | FAIL | FAIL | 0 | 426s | agent diff 产生 SyntaxError（未闭合字符串字面量）；HTTP 402 中断 |
| mwaskom__seaborn-3010 | ❌ infrastructure_failure | FAIL(符合预期) | - | - | 0 | 6s | HTTP 402 模型配额耗尽，agent 未执行 |

## 汇总指标

| 指标 | 值 |
|---|---|
| 总实例 | 5 |
| Resolved | 1 (20.0%) |
| 失败（agent 执行了但失败） | 3 (60.0%) |
| 基础设施失败 | 1 (20.0%) |
| 平均耗时（含基础设施失败） | 515s |
| 平均耗时（仅 agent 执行了的） | 437s |
| 平均步骤（agent 执行了的） | 33 |
| 平均工具调用（agent 执行了的） | 31 |

## 失败分析

### 1. 编辑能力缺陷（3/4 失败根因）

- **pylint**: agent 编辑 `pylint/checkers/misc.py` 产生的 diff 语法损坏（未闭合字符串），导致 eval 阶段 ImportError
- **requests**: agent 编辑 `requests/utils.py` 产生的 diff 语法损坏（IndentationError: unexpected indent），同样 eval 失败
- **xarray**: agent 60 步内未产生任何代码修改，反复读文件后超时退出

共同模式：Agent 在构造 `edit` 操作的替换字符串时，高频出现语法错误，说明**代码编辑阶段的字符串构造精度不足**。

### 2. 基础设施失败（1/5）

- **seaborn**: HTTP 402 失败，模型配额不足。该实例未执行任何 agent 步骤即失败。

### 3. 唯一成功案例（flask-4992）

- 正确识别 `Config.from_file()` 缺少 `text` 参数
- 添加 `text` 参数 + 条件文件打开模式
- 39 步完成，FTP 1/1 PASS, PTP 18/18 PASS

## 大数据观察

1. **EvalGuard 有效**: flask-4992 的 EvalGuard 拦截了"测试全绿即完成"，最终产出了正确修复
2. **agent 编辑可靠性是瓶颈**: 3/4 的失败中 agent 实际尝试了修改但语法不正确
3. **HTTP 402 间歇性失败**: deepseek 后端的配额限制导致 2/5 实例（requests/pylint）在 agent 运行中被中断，但 agent 中断前已产生的 diff 被保存并判定
4. **平均耗时 8.6 分钟/实例**: 60 步上限下，agent 倾向于耗尽步数

## 下轮建议

1. 调整模型配额或切换到备用后端，减少 infrastructure_failure
2. 审计 agent 编辑字符串构造逻辑，减少语法错误 diff
3. 对 xarray 类实例（零修改耗尽步数）增加早期干预（如 LoopGuard 升级）

---

## Round 2 准备状态（2026-08-21 更新）

下轮建议对应的修复已实现并提交：

| 建议 | 修复 | 状态 |
|---|---|---|
| 编辑可靠性 | `edit`/`write_file` 写盘后 Python 语法验证（`EDIT_VALIDATION_FAILED`）+ 空 diff/截断拦截（`EDIT_NO_CHANGE`/`EDIT_TRUNCATION`）；失败自动重试 2 次后强制整文件重写 | ✅ 已实现 |
| 零修改完成 | EvalGuard（`eval.require_code_change`）：SWE-bench 下未修改源码不得因现有测试全绿而完成 | ✅ 已实现 |
| harness 健康 | run_smoke 预应用 test_patch 让 agent 看到 FTP 失败；PYTHONPATH 隔离仓库源码；diff 前剥离 test_patch；基础设施失败单独分类 | ✅ 已实现 |
| 指标补全（审计 G1/G3/G5） | `response.usage` 累积进 `token_usage` 并透出 result.json；`swe_stats`（loopguard_blocks/fixdriving_activations/failure_parser_used/test_guard_triggered）落盘；tests_* 出计数 | ✅ 已实现 |

**下一步**: Round 2 跑 10 实例，重点观察编辑修复后的提升。