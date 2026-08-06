# ZMAI 项目测试总结 (ZMAI_TEST_REPORT.md)

**生成时间**: 2026-08-06
**测试工程师**: Hermes Agent (自动化测试)
**项目根目录**: D:\desk\ZMAI

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| OS | Windows 10 (MECHREVO 机械革命) |
| Python | 3.11.9 (.venv) |
| pytest | 9.1.1 |
| claude CLI | 2.1.169 (未登录, authMethod=none) |
| LLM Backend | DeepSeek (credential store, 真实 API 调用) |
| 配置 | zmai.json (max_iterations=25, timeout=300) |
| 测试数据目录 | tests/hermes_validation/ |

## 2. 测试数量

- 全量回归收集: **1252** 个测试
- 重点模块专项: auth/credential store/gateway/runtime/loop_guard/termination + 新增 EditTool 回归 = **196** 个
- SWE 修复验证: 2 个 demo (swe_fix_demo, autostop_demo) × 真实 Agent 运行
- 自主停止验证: 2 个场景 (可完成 / 不可完成) × 真实 Agent 运行

## 3. 通过数量

- 全量回归通过: **1244**
- 重点模块专项: **196 / 196**
- SWE 修复: before 2 failed → after **4/4 passed**
- 自主停止场景 A: completed (4 步); 场景 B: timeout (5 步, 符合预期)

## 4. 失败数量

- 全量回归失败: **1** (`test_init_with_unwritable_dir`)
  - Windows 管理员权限环境特例: 测试假设 `C:\Windows\System32` 不可写, 当前 shell 以管理员运行导致该目录可写, `pytest.raises(WorkspaceError)` 未触发
  - 非代码 bug, Linux CI 应通过; 已清理测试副作用目录, 记录为 P2 环境依赖

## 5. 发现 Bug

| ID | 严重级 | 模块 | 描述 | 状态 |
|----|--------|------|------|------|
| BUG-001 | **P0** | swe/tools.py EditTool | **replace_lines/insert 行拼接损坏**: LLM 的 new_text 无结尾换行时, 下一行被拼接到替换行 (`return a * bdef divide(...)`), 产生 SyntaxError, 直接导致 SWE Agent 修复失败并超时 | ✅ 已修复 |
| BUG-002 | P0 | run_agent.py | **resolve_workspace() 接口缺失**: 测试期望 CLI --workspace > env ZMAI_WORKSPACE > 默认 三级优先级解析, 代码未实现 (7 个回归测试失败) | ✅ 已修复 |
| BUG-003 | P0 | run_agent.py | **main() 被调用方 sys.argv 污染**: 作为模块被 import 后调用 main() 时, argparse 解析 pytest 参数抛 SystemExit | ✅ 已修复 |
| BUG-004 | P1 | pytest 配置 | **hermes_validation demo 数据污染全量收集**: 含故意损坏的测试被 pytest 收集导致 2 个失败 | ✅ 已修复 (norecursedirs) |
| BUG-005 | P1 | auth | Claude/Gemini API Key 缺失, 仅 DeepSeek 可用 (与 DOCTOR.md 一致) | 环境限制, 无法自动修复 (需用户提供 key) |
| BUG-006 | P2 | run_agent.py | LOG_FILE 硬编码 process_result.json, 多实例并发互相覆盖 | 未修复 (低风险) |
| BUG-007 | P2 | run_agent.py | timeout=3600 硬编码, 与 zmai.json timeout 无关联 | 未修复 (低风险) |
| BUG-008 | P2 | tests/workspace | test_init_with_unwritable_dir 依赖 Windows System32 不可写假设, 管理员权限下失效 | 环境依赖, 无法修复 |

## 6. 修复内容

1. **src/zmai/swe/tools.py** — EditTool 新增 `_normalize_new_lines()`: new_text 缺失结尾换行时自动补 `\n`, 杜绝行拼接; replace_lines/insert 两处使用
2. **run_agent.py** — 新增 `resolve_workspace(argv)`: CLI --workspace > env ZMAI_WORKSPACE > 默认三级优先级; 新增 `_entry_argv()`: import 调用时用空 argv 避免 sys.argv 污染; `main(argv)` 支持显式参数
3. **pyproject.toml** — pytest `norecursedirs = ["hermes_validation"]`, 排除验证 demo 数据
4. **tests/test_edit_tool_newline.py** — 新增 4 个 EditTool 换行规范化回归测试 (P0 修复防回退)

## 7. SWE Agent 能力验证结果

**是否成功自动修复 Bug：YES**

真实调用 ZMAI Runtime + DeepSeek backend, 对 swe_fix_demo 执行 ISSUE.md 任务:

| 验证点 | 结果 |
|--------|------|
| 1. 读取任务 (ISSUE.md) | ✅ 读取并理解 |
| 2. 分析原因 (先跑测试) | ✅ Phase 2: 先 pytest 确认 2 failed, 再读源码 |
| 3. 修改文件 (app/calculator.py) | ✅ edit 修复 add/multiply |
| 4. 运行测试验证 | ✅ pytest 4/4 passed |
| 5. 自动停止 | ✅ status=completed, 6 步, 无多余循环 |

结果: **before 2 failed, 2 passed → after 4 passed** (修复前失败日志 before_fix_result.txt, 修复后 after_fix_result.txt)
过程中发现并修复了 BUG-001 (EditTool 行拼接), 修复后重跑成功 — 验证了"发现 bug → 修复 → 重测通过"闭环。

附加: run_agent.py 无头调用流程已执行 (claude CLI 未登录 → status=failed 记录在案, 属环境限制; CLI 参数解析/工作区检查/JSON 报告生成均正常)。

## 8. 自主停止验证结果

**是否存在无限循环：NO**

max_iterations=5, 两个真实场景:

| 场景 | 最终状态 | 实际 turn | 实际 tool 调用 | 停止原因 |
|------|----------|-----------|---------------|----------|
| A: 可完成修复任务 | completed | 4 (<5) | 4 | CompletionState 测试全绿 → 自动 complete |
| B: 不可完成对抗任务 | timeout | 5 (=max) | 9 | max_iterations 硬截断 + LoopGuard no_progress 双防护 |

验证结论:
- ✅ 无无限循环、无重复修改、无无意义模型调用
- ✅ 完成即停 (场景 A 第 4 步全绿立即 complete, 无第 5 步)
- ✅ 无法完成时 5 步强制终止 (场景 B)
- ✅ 双层防护: Runtime max_steps 硬上限 + SWEAgent CompletionState/LoopGuard
- ✅ run_agent.py 外层 3600s 超时兜底存在

## 9. 开源前剩余风险

| 风险 | 级别 | 说明 |
|------|------|------|
| run_agent.py 依赖 claude CLI 登录态 | **P0** | 无头调用要求本机 claude CLI 已登录或有 Anthropic 兼容端点 (ANTHROPIC_BASE_URL+AUTH_TOKEN); 未登录时静默失败 (status=failed), 建议增加登录检测与友好报错 |
| --allowedTools 白名单权限 | **P0** | 此前实测 (process_result.json) 显示 Edit/Write 被拒 6 次; 本次因 claude CLI 未登录无法复测, 需在登录环境回归验证白名单路径匹配 (Windows 路径分隔符 `D:/` vs `D:\`) |
| Claude/Gemini backend 无真实 key | P1 | 仅 DeepSeek 有真实调用验证; 开源前应至少验证一个 Anthropic 兼容端点 |
| EditTool 其他模式健壮性 | P2 | 已修 replace_lines/insert; regex_replace 的旧模式建议审计 (LLM 提供的正则跨行匹配风险) |
| 测试时长 | P3 | 全量 8 分钟, CI 建议 pytest-xdist 并行 |
| workspace/ 历史残留 | P3 | 数百个历史 test_* 目录无清理策略 |

---

## 交付物清单

```
tests/hermes_validation/
├── analysis_report.md              # 第一阶段: 项目分析
├── before_fix_result.txt           # 修复前 pytest 失败日志
├── after_fix_result.txt            # 修复后 pytest 通过日志
├── swe_fix_result.json             # SWE 修复 Agent 运行详情
├── autostop_report.md              # 第四阶段: 自主停止报告
├── autostop_report.json            # 自主停止原始数据
├── regression_report.md            # 第五阶段: 回归报告
├── regression_pytest_output.txt    # 全量 pytest -v 输出 (首次)
├── regression_final_output.txt     # 全量 pytest 输出 (修复后)
├── run_agent_with_key.py           # claude CLI 环境配置包装 (测试用)
├── run_swe_fix_test.py             # SWE 修复测试驱动
├── run_autostop_test.py            # 自主停止测试驱动
├── swe_fix_demo/                   # 修复 demo (ISSUE.md + app + tests)
├── autostop_demo/                  # 停止验证 demo A
└── autostop_demo_b/                # 停止验证 demo B (对抗任务)

代码修复:
├── src/zmai/swe/tools.py           # EditTool 换行规范化 (P0)
├── run_agent.py                    # resolve_workspace + main(argv) (P0)
├── pyproject.toml                  # pytest norecursedirs (P1)
└── tests/test_edit_tool_newline.py # 新增回归测试
```
