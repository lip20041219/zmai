# 自主停止机制测试报告 (autostop_report.md)

**测试时间**: 2026-08-06
**测试方法**: ZMAI Runtime + DeepSeek backend (真实 API 调用)
**max_iterations**: 5

---

## 结论

**是否存在无限循环：NO**

两个场景均验证 Agent 不会无限循环、不会重复修改、不会无意义调用模型：

| 场景 | 任务 | 最终状态 | 实际 turn 数 | 实际 tool 调用数 | 停止原因 |
|------|------|----------|-------------|-----------------|----------|
| A | 修复 string_utils（可完成） | **completed** | 4 | 4 | 测试通过 + CompletionState → 自动 complete |
| B | 让引用不存在函数的测试通过（不可完成） | **timeout** | 5 | 9 | 达到 max_iterations=5 → 强制终止 |

---

## 场景 A：可完成任务（完成即停）

- 任务：修复 `app/string_utils.py` 中 `to_upper` 的 BUG（`s.lower()` → `s.upper()`）
- 结果：**completed**, 4 步（< 5），耗时 6.66s
- Tool 调用：shell_exec×2（跑测试 + 验证）、read_file×1、edit×1
- 停止原因：修复后 pytest 全绿 → CompletionState.should_complete() → 立即 complete
- **验证点：任务完成后没有出现第 5 步、没有重复修改、没有多余模型调用**

Tool 序列:
```
1. shell_exec  python -m pytest tests/     # Phase 2: 先跑测试（失败确认）
2. read_file   app/string_utils.py         # Phase 3: 分析失败原因
3. edit        修复 to_upper               # Phase 4: 修改代码
4. shell_exec  python -m pytest tests/     # Phase 5: 验证全绿 → 停止
```

## 场景 B：不可完成任务（强制截断）

- 任务：让引用不存在的 `transform_external_ai` 的测试通过（禁止创建该函数、禁止改测试）
- 结果：**timeout**, 恰好 5 步（= max_iterations），耗时 9.02s
- Tool 调用：shell_exec×2、read_file×7（全部成功，无失败重试风暴）
- 停止原因：`while step_count < ctx.max_steps` 循环在第 5 步耗尽 → timed_out=True → finalize 判定 TIMEOUT
- 附加防护：LoopGuard 同时触发 `no_progress`（连续 5 步无代码修改）→ 注入策略变更提示
- **验证点：双层防护（max_steps 硬上限 + LoopGuard）均生效，未出现无限循环**

---

## 防护机制验证明细

### 1. max_iterations 硬上限 (Runtime)
- 位置: `src/zmai/runtime/runtime.py` `while step_count < ctx.max_steps`
- 行为: 步数耗尽 → `ctx.metadata["timed_out"]=True` → finalize 返回 TIMEOUT
- 实测: 场景 B 第 5 步后立即终止, error="达到最大执行步数 (5)，任务未完成"

### 2. CompletionState 完成即停 (SWEAgent)
- 位置: `src/zmai/swe/agent.py` step() 开头 + 工具循环后
- 行为: 测试全绿 (exit 0 + verify_test_output) → should_complete() → 立即 complete，不再调用 backend
- 实测: 场景 A 第 4 步全绿后直接 complete，无第 5 步

### 3. 硬终止防御 (test_success_count ≥ 1)
- 行为: 一旦出现过 ≥1 次测试全绿，即使后续 CompletionState 被修改重置，也强制 complete
- 实测: 场景 A 第 4 步 pytest 全绿 → 立即终止

### 4. LoopGuard 循环检测
- 位置: `src/zmai/swe/loop_guard.py`, 阈值 5
- 行为: identical_calls / identical_failures / no_progress 三模式
- 实测: 场景 B 触发 no_progress (5 步无修改) → 注入 "[LoopGuard] 检测到循环执行模式" 提示

### 5. 超时兜底 (run_agent.py)
- 位置: `run_agent.py` subprocess timeout=3600（Claude Code 无头入口）
- 行为: 1 小时硬超时，防止极端死循环

---

## 结论与风险

- 自主停止机制 **全部通过**（5 项防护验证 4 项实测生效，1 项为外层入口兜底）
- 无无限循环、无重复修改、无无意义模型调用
- 残余风险: max_steps 耗尽时 Agent 处于"未完成任务"状态（timeout），需要调用方决定重试策略；这不是循环问题而是任务不可达的终止方式
