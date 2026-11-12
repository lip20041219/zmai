# ZMAI M2 / P1-2 Candidate 验证升级状态

日期：2026-11-12
分支：`feature/evidence-runtime`
结论：**YES，具备基于新证据的独立升级路径**；仅接受可绑定补丁身份的 candidate。

## 阻塞用例根因

先前超过 25 秒未结束的 `test_runtime_submits_candidate_after_target_green_and_unknown_broad_failure`，用 faulthandler 定位在 `Runtime.run() → capture_workspace() → git status`，尚未执行 Agent。临时项目没有自己的 Git 根，`git status` 扫描了上级工作区；它不是 Agent 死循环。把该用例的临时项目初始化为独立 Git 仓库后，快照耗时恢复正常。另一个环境问题是系统默认 `python` 指向不含 pytest 的 Hermes 环境；用例现将仓库测试解释器目录放在 PATH 前面。该 Runtime 用例复测 1 passed，约 3.82 秒；与升级用例合跑 7 passed，约 13.85 秒。

## 实现

- 新增 `zmai verify <report.json> --project <path> --command <full-test-command>`，同一函数 `verify_candidate()` 可供 SDK 调用。
- 要求原报告终态与判定均为 candidate，且含有效、独立回放通过的局部证据；要求候选补丁在干净 Git 基线上有可核对的 patch SHA-256。验证前核对补丁，验证后再次核对代码身份；常见 pytest 缓存等运行产物不计为代码改动。
- 命令必须通过现有全量范围分类和回放安全检查。入口使用本地脚本 Backend 触发新的 `Runtime.run()`，不调用模型 API；Runtime 仍负责 nonce、验收文件守卫、证据账本、独立回放和 `decide_terminal()`。
- 只有新运行返回 completed、判定为 completed、对应完整范围记录有效且回放可信、补丁未变时，才在原报告旁写 `verification.json`，包含新运行 ID、证据 ID 和新交付报告位置。原 `report.json` 仍为 candidate。
- 测试失败、范围不足、候选补丁过期或运行期间改变时写 rejected 和原因；同一补丁及命令重复验证复用已完成记录。
- 证据身份不可确定的候选（例如报告 patch 被遮蔽或运行前工作树已脏）拒绝升级，需要重新建立可归因的候选报告。

## 修改文件

- `src/zmai/swe/candidate_verify.py`：独立验证入口及升级记录。
- `src/zmai/cli/main.py`：CLI 路由与帮助。
- `tests/test_swe_candidate_verify.py`：真实 Runtime 运行及拒绝边界。
- `tests/test_swe_candidate_submission.py`：独立 Git 测试夹具和实际可用的 Python PATH。

## 定向结果

仓库 `.venv` 解释器执行：

- `python -m pytest tests/test_swe_candidate_verify.py -q`：**6 passed**。覆盖合法升级、测试失败、过期补丁、重复验证、范围缩小及 shell 组合拒绝、验证后代码变化。
- `python -m pytest tests/test_swe_candidate_verify.py tests/test_swe_candidate_submission.py::test_runtime_submits_candidate_after_target_green_and_unknown_broad_failure -q`：**7 passed**（在最终安全命令检查前执行；该检查后 6 项升级回归再次通过）。
- `python -m zmai verify --help`：正常显示命令参数。

未运行仓库全量 pytest、SWE-bench、E2E 或真实模型 API；未 Push。

## 调用链

`zmai verify` / `verify_candidate()` → 校验原 candidate 报告与工作区补丁 → 本地验证 Backend → 新 `Runtime.run()` → `SWEAgent.step()` → `ShellTool.execute()` → nonce 与验收守卫 → `EvidenceRecord` / `CompletionLedger` → 独立回放 → `decide_terminal()` → 验证后补丁身份复核 → 独立 `verification.json`。
