# SWE-bench Lite Round 1 报告

> 日期: 2026-08-21
> 范围: SWE-bench Lite smoke，5 个实例（flask/requests/xarray/pylint/seaborn）
> 模型: deepseek（DeepSeek Chat）
> 判定方式: 独立 run_smoke.py（test_before 验证可复现 → agent 运行 → git diff → 干净副本重放 → FTP/PTP 独立判定）

---

## 1. 结果摘要

**Resolved 1/5 (20%)**，adapter 判定链路全链路跑通。这是 ZMAI SWE Agent 首次在真实 SWE-bench 实例上完成修复（flask-4992）。

| # | Instance | 结果 | FTP-before | FTP-after | PTP | 耗时 | 步骤 |
|---|---|---|---|---|---|---|---|
| 1 | pallets__flask-4992 | ✅ resolved | FAIL ✅ | PASS | PASS (18/18) | 271s | 39 |
| 2 | psf__requests-3362 | ❌ test_failure | FAIL ✅ | FAIL | FAIL | 659s | 0* |
| 3 | pydata__xarray-4248 | ❌ no_change | FAIL ✅ | - | - | 1213s | 60 |
| 4 | pylint-dev__pylint-5859 | ❌ test_failure | FAIL ✅ | FAIL | FAIL | 426s | 0* |
| 5 | mwaskom__seaborn-3010 | ❌ infrastructure_failure | FAIL ✅ | - | - | 6s | 0 |

\* requests/pylint 的 agent 运行被 HTTP 402/网络中断，status=0 但已产生 diff；xarray 耗尽了 max_steps=60。

## 2. 关键指标

| 指标 | 值 |
|---|---|
| Resolve Rate | 20% (1/5) |
| 平均耗时（含 infra 失败） | 515s |
| 平均耗时（agent 实际执行） | 437s |
| 平均步骤（agent 实际执行） | 33 |
| 平均工具调用（agent 实际执行） | 31 |
| 失败分类 | no_change 1, test_failure 2, infra 1 |

## 3. 里程碑：首次 resolved（flask-4992）

**flask-4992**：`Config.from_file()` 缺少 `text` 参数兼容 tomllib。

- 修复内容: `src/flask/config.py` 添加 `text: bool = True` 参数 + 按 `text` 选择文件打开模式
- 步骤: 39 步（EvalGuard 拦截"测试全绿即完成"1 次）
- 判定: FTP test_config_from_file_toml PASS，PTP 18/18 PASS
- 意义: 证明独立判定链路 + EvalGuard 守卫有效；agent 能定位、修复并验证真实 SWE-bench 缺陷

## 4. 失败根因分析

### 4.1 Agent 编辑能力缺陷（2/4 失败）——最高优先级

- **pylint-5859**: agent 编辑 `pylint/checkers/misc.py` 产生**语法损坏**的 diff（未闭合字符串字面量 → SyntaxError），eval 阶段 ImportError
- **requests-3362**: agent 编辑 `requests/utils.py` 产生**语法损坏**的 diff（IndentationError: unexpected indent），eval 阶段无法导入
- **xarray-4248**: agent 60 步内未产生任何代码修改（反复读文件未构造有效 edit）

共同模式: agent 在构造 `edit` 替换字符串时语法精度不足，或未能从读取内容构造正确的 edit。这与 `temp/state.json` 中记录的"短路径/编辑能力"问题一脉相承。

### 4.2 基础设施失败（1/5）

- **seaborn-3010**: deepseek HTTP 402（配额耗尽），agent 未执行。requests/pylint 的 agent 运行也在中途被 HTTP 402/网络错误中断（但已保存 diff 并独立判定）。
- 影响: 2/5 实例的 agent 运行被中断，需等配额恢复或切换后端重跑。

### 4.3 Harness 验证结论（健康）

- 5/5 实例的 `test_before`（base + test_patch 跑 FTP）均为 **FAIL(符合预期)** ✅ —— 任务可复现
- 5/5 实例的独立判定（diff 重放）均正常执行，无 patch 冲突
- EvalGuard（eval.require_code_change）在 flask 上真实拦截了提前完成判定
- 结论: **harness 判定环境健康，0/5 失败归因于 harness**。当前瓶颈在 agent 能力 + 后端配额。

## 5. 对后续轮次的建议

1. **配额**: 等待 deepseek 配额恢复或配置备用后端，减少 infrastructure_failure 干扰
2. **Agent 编辑可靠性**: 这是最大瓶颈（3/4 agent 执行失败的实例要么没改、要么改错语法）。建议:
   - 审计 `edit` 工具替换字符串构造（长替换易出语法错误 → 分段 edit）
   - 增加"编辑后自检"提示：要求 agent 编辑后运行 `python -m py_compile` 或 pytest 冒烟
   - xarray 类零修改耗尽步数 → 依赖 LoopGuard 升级为 force_edit 更早
3. **指标补全**: 当前 result.json 无 token 统计（G1 未接线）、无模块级计数落盘（G3）——正式 Benchmark 前需补
4. **Round 2 目标**: 在 Round 1 基础上跑 10 个实例，重点观察 agent 编辑修复后的提升

## 6. 文件清单

- Harness: `benchmarks/results/swebench_lite/run_smoke.py`
- Agent 改动: `src/zmai/swe/agent.py`（EvalGuard: eval.require_code_change）
- 原始审计: `docs/benchmark/AUDIT.md`
- 进度表: `benchmarks/results/swebench_lite/PROGRESS.md`
- 单实例结果: `benchmarks/results/swebench_lite/<instance_id>/result.json`
