# ZMAI 测试体系与持续修复路线图

> 状态标记约定：[x] 已完成（有代码/测试/commit/报告证据）、[~] 进行中、[ ] 计划中。
> 本文所有数字均来自仓库内证据（`test-results/`、`docs/`、`benchmarks/results/`），无虚构数据。
> 最近更新：2026-09-05

---

## 1. 测试目标

1. **核心功能回归可信**：任何改动不得破坏已通过的单元/集成/运行测试基线。
2. **跨平台一致**：在 GitHub Actions（ubuntu + windows × py3.10–3.12）上保持全绿。
3. **Agent 行为可量化**：用 SWE-bench Lite 提供可复现、可对比的修复能力指标。
4. **问题闭环可追溯**：测试发现问题 → 分类 → Issue → 定位 → 修复 → 回归测试 → CI/Benchmark 验证 → 更新证据。

## 2. 当前测试体系

### 2.1 单元与集成测试

- 全量收集 **1320 items**，最终基线 **1311 passed / 9 skipped / 0 failed**（`docs/testing/FULL_TEST_REPORT.md`，证据日志已入库）。
- `tests/` 下约 50+ 个 `test_*.py`，覆盖：auth / config / CLI / context / credential store / detectors / doctor / e2e-mock /
  edit tool / execution / gateway / integration / live_api（CI 排除）/ memory / plan mode / planning / prompt /
  runtime / run-agent（encoding / workspace）/ swe / 等。
- 共享设施：`tests/conftest.py`（mock key 注入）、`tests/mocks.py`（Mock backend）、`tests/fixtures/`。
- `tests/hermes_validation` 由独立驱动脚本运行，不参与全量收集（`pyproject.toml` `norecursedirs`）。

### 2.2 CI（`.github/workflows/test.yml`）

- 矩阵：`{ubuntu-latest, windows-latest} × {3.10, 3.11, 3.12}`。
- 步骤：`ruff check` → `mypy src/zmai/`（continue-on-error，**非门禁**）→ `pytest tests/`（`--tb=short`，排除 `test_live_api.py`）。
- `publish.yml`：release 触发 PyPI 构建，与测试链路独立。

### 2.3 SWE-bench Benchmark

- harness 已入库：`src/zmai/benchmark/runner.py`、`src/zmai/eval/benchmark.py`、`src/zmai/eval/swebench.py`；
  smoke 脚本 `benchmarks/results/swebench_lite/run_smoke.py`；5 个 Lite 实例缓存（flask/requests/xarray/pylint/seaborn）。
- 判定链路：test_before 复现（FTP FAIL 符合预期）→ agent 运行 → git diff → 干净副本重放 → FTP/PTP 独立判定；EvalGuard 守卫提前完成。

## 3. 测试与问题修复闭环

```
测试/benchmark 发现问题
  → 分类（逻辑 bug / 陈旧测试 / 编辑可靠性 / 基础设施）
  → GitHub Issue 记录
  → 定位根因
  → 修复（含回归测试）
  → GitHub Actions / CI 验证
  → Benchmark 复验
  → 更新测试报告与证据归档
```

## 4. 当前已完成内容

### Phase 1 — 全量回归基线 [x]

- pytest 全量回归 3 轮演进均归档：Round1 1310/1/9 → Round2（语义修正）1311/0/9+2w → Final（编码修复）**1311/0/9/0w**。
  证据：`test-results/full-test-output{,-round2,-final}.txt`、`docs/testing/FULL_TEST_REPORT.md`、README（commit `0ae324a`）。
- 完成项：陈旧测试 `test_append_empty_content` 语义修正；`issue/agent.py` GBK 编码缺陷修复（Round2→Final）。

### Phase 2 — CI 稳定 [x]

- 修复链入库：mock key 注入、flask 依赖、py3.10 兼容、跨平台 UTF-8 写入、8.3 短路径、ruff 全量清零
  （commits `3c6e7e7`→`4aac20e` 系列）。CI 矩阵测试全绿。

### Phase 3 — SWE-bench harness 建立与 Round 1 smoke [x]

- harness、缓存、独立判定、EvalGuard 全链路跑通。
- Round 1 smoke：**resolved 1/5（flask-4992，FTP 1/1 + PTP 18/18 PASS）**；失败归因健康，0/5 归因 harness。
  证据：`benchmarks/results/swebench_lite/PROGRESS.md`、`docs/benchmark/ROUND1_REPORT.md`、`docs/benchmark/AUDIT.md`。

## 5. 当前进行中的工作

### Phase 4 — 正式 Benchmark 量化运行（Round 2+）[~]

- Round 2 预备与审计缺口修复已入库（`e60dba5`：G1 token 链路 / G3 模块级统计 / G5 相关 gap fix）。
- 首次 Round 2 实跑（`benchmarks/results/swebench_lite/round2_summary.json`，8 实例）**全部失败**：
  `infrastructure_failure` ×4（DeepSeek API Key 403 权限不足）+ `environment` ×4（clone 网络中断），0 resolved。
- 该轮结果与驱动脚本 `run_round2.py` **均未提交**，属 WIP，不作为有效量化结果。

> 阻塞项：有效后端 key + 稳定网络。此条件满足前，Round 2 无法产出可信数字。

## 6. 后续测试与修复路线

### Phase 5 — SWE Agent 失败案例归因与修复 [ ]

- **目标**：消化 Round 1 已知失败类别并加回归保障。
- 子任务：
  - 编辑可靠性：pylint-5859 / requests-3362 产生语法损坏 diff（SyntaxError/IndentationError）→ 定位 edit 替换字符串构造缺陷；
    增加“每次 edit 后文件可解析”的守卫与回归测试。
  - no_change：xarray-4248 60 步未产生 diff → 审计读-改循环与定位策略。
  - 基础设施：API Key 前置检查 / 配额不足的显式报错与重试策略。
- 完成标准：对上述失败实例重跑，语法损坏/no_change 类不再出现，或能以回归测试锁定根因。

### Phase 6 — 对照与门禁强化 [ ]

- 建立 Baseline Agent（AUDIT G2）用于回答“相比 Baseline 提升多少”。
- 评估 mypy 是否转为门禁；引入覆盖率阈值（需先确认基线）。
- 可选：CI 增加轻量 benchmark smoke gate（mock backend，不消耗真实 key）。

## 7. Benchmark 验证路线

| 阶段 | 状态 | 内容 | 判定 |
|---|---|---|---|
| Round 1 smoke | [x] | 5 实例 | resolved 1/5（flask-4992） |
| Round 2+ 正式量化 | [~] | 实例集扩大（≥10），多失败类统计 | 待有效 key/网络；当前 8 实例全部 infra，不采信 |
| 后续轮 | [ ] | 修复驱动复验 + 能力分层归因（failure_parser/loop_guard/test_guard 计数） | G1/G3 已接线，G2 baseline 待建 |

## 8. 回归测试策略

1. 每次修复必须伴随最小回归测试（无框架依赖，遵循既有单测风格）。
2. 改动涉及共享函数/工具时，先 grep 所有调用方，在共同路径修根因，而非只补报告路径。
3. 本地全量（1320 items，约 23–28 分钟）与 CI 矩阵均可复现；证据日志统一入 `test-results/`。
4. SWE 相关修复需在 Benchmark 复验后才标记完成（防止“测试全绿即完成”的假成功）。
5. Benchmark 结果只接受已提交、可复现的轮次；WIP/被 infra 阻塞的运行不入证据。

## 9. 测试证据（现有）

| 证据 | 路径 | 内容 |
|---|---|---|
| 全量回归日志 | `test-results/full-test-output*.txt`（3 份，已入库） | Round1/2/Final 原始输出 |
| 全量报告 | `docs/testing/FULL_TEST_REPORT.md` | 演进与最终 1311/0/9 |
| 运行指南 | `docs/testing/TESTING.md` | 全量/单文件运行方法 |
| SWE Round 1 | `benchmarks/results/swebench_lite/PROGRESS.md`、`docs/benchmark/ROUND1_REPORT.md` | 1/5 resolved |
| Benchmark 审计 | `docs/benchmark/AUDIT.md` | G1–G4+ 缺口 |
| Round 2 WIP | `benchmarks/results/swebench_lite/round2_summary.json`（未提交） | 8 实例全 infra，不作数 |
| CI 配置 | `.github/workflows/test.yml`、`publish.yml` | 矩阵/门禁现状 |
