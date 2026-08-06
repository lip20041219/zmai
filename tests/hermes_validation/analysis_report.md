# ZMAI 项目分析报告 (analysis_report.md)

**生成时间**: 2026-08-06
**分析者**: Hermes Agent (自动化测试工程师)
**分析范围**: ZMAI 根目录 D:\desk\ZMAI

---

## 1. 项目结构总览

```
D:\desk\ZMAI\
├── run_agent.py          # Claude Code 无头调用入口 (--workspace/--prompt/--skip-permissions)
├── main.py               # 3 字节空壳
├── pyproject.toml        # 零依赖 SWE agent runtime (pytest dev 依赖)
├── zmai.json             # 运行时配置 (max_iterations=25, timeout=300)
├── process_result.json   # 最近一次 run_agent.py 输出（含权限拒绝记录）
├── DOCTOR.md             # zmai doctor 报告 (7/9 checks passed)
├── agent_workspace/      # Agent 工作流示例 (app/calculator.py + tests)
├── workspace/            # 数百个历史 agent 运行工作区 (.state/execution_log.json)
├── src/zmai/             # 核心包 (14 个子模块)
│   ├── agent/            # Agent 基类 (AgentContext/AgentAction/AgentResult)
│   ├── swe/              # ★ SWE Agent: agent/planner/plan_agent/tools/verifier/
│   │                     #   loop_guard/completion/scanner/context/github
│   ├── runtime/          # ★ Runtime: runtime/lifecycle/scheduler/state/preflight
│   ├── gateway/          # ★ Backend: backends/{deepseek,claude,gemini}, registry, tool_router
│   ├── auth/             # ★ Credential Store: store/store_base/store_keyring/store_wincred/resolver
│   ├── cli/              # CLI: main/auth_cmd/config_cmd/doctor/eval_cmd/github_cmd/plugin_cmd
│   ├── context/          # ContextManager/memory/pruner/window
│   ├── eval/             # benchmark/harness/loader/reporter/swebench
│   ├── tool/             # ToolRegistry/base
│   ├── execution/        # ExecutionLog
│   ├── workflow/         # WorkflowEngine
│   ├── workspace/        # Workspace/docker
│   ├── issue/            # GitHub issue agent
│   └── memory/           # MemoryManager (long-term/working)
└── tests/                # 45+ 测试文件, 1171 个测试函数
```

## 2. 核心模块分析

### 2.1 run_agent.py (无头调用入口)
- 构造 `claude -p <prompt> --output-format json --allowedTools <白名单>` 命令
- 白名单默认: `Read,Edit(<ws>/**),Write(<ws>/**),Bash(*)`（workspace 边界限定）
- `--skip-permissions` → `--dangerously-skip-permissions`（无边界）
- subprocess cwd=workspace, encoding=utf-8, timeout=3600 硬编码
- 结果写入 process_result.json

### 2.2 SWE Agent (src/zmai/swe/agent.py, 868 行)
- 5 阶段强制工作流: Discover → Run Tests First → Analyze → Modify → Verify
- CompletionState 跨轮累积完成判定 + test_success_count≥1 硬终止防御
- LoopGuard 循环检测 (identical_calls/identical_failures/no_progress, 阈值 5)
- Read-limit 强制 (8 次读取未跑测试 → 干预)
- 自动验证 (auto_generate_checks) + 客观验证通过才标记 objective_met
- finalize() 按优先级判定: TIMEOUT > FAILED(step) > FAILED(replan) > FAILED(tools) > FAILED(verify) > COMPLETED
- Windows 平台命令映射注入系统提示

### 2.3 Runtime (src/zmai/runtime/runtime.py, 394 行)
- preflight check → lifecycle → workspace.prepare → backend 解析
- auto_plan 模式: PLANNING → PLAN_READY → 确认 → EXECUTING
- 执行循环: `while step_count < ctx.max_steps`（max_steps 来自 config runtime.max_iterations）
- max_steps 耗尽 → timed_out → TIMEOUT 状态
- 异常/取消路径完整 (CancelledError → cancelled)

### 2.4 Auth / Credential Store (src/zmai/auth/)
- store.py (17KB): 加密凭证存储
- store_keyring.py: keyring 回退
- store_wincred.py (11KB): Windows Credential Manager
- resolver.py: 多来源解析
- DOCTOR.md: DeepSeek PASS (source: Credential Store), Claude/Gemini Missing Key

### 2.5 Backend (src/zmai/gateway/backends/)
- deepseek.py / claude.py / gemini.py 三后端
- zmai.json: default_backend=auto, claude 模型 claude-sonnet-4-6, deepseek 模型 deepseek-chat

## 3. 已知 Bug

| ID | 严重级 | 模块 | 描述 | 证据 |
|----|--------|------|------|------|
| BUG-001 | **P0** | run_agent.py | **--allowedTools 白名单在 Windows 下不生效**：Agent 的 Edit/Write/PowerShell/Bash 全部被拒（6 次 permission_denials），即使路径在白名单内。Agent 无法写文件 → 任务必然失败 | process_result.json (2026-08-06T13:20): 6 个 permission_denials, Agent 回复"无法写入文件，写操作权限尚未被授予" |
| BUG-002 | P1 | auth | Claude (Anthropic) / Gemini (Google) API Key 缺失，仅 DeepSeek 可用 | DOCTOR.md: "FAIL Claude — Missing Key", "FAIL Gemini — Missing Key" |
| BUG-003 | P2 | run_agent.py | LOG_FILE 固定 "process_result.json"，多实例并发互相覆盖 | 代码硬编码 line 17/148 |
| BUG-004 | P2 | run_agent.py | timeout=3600 硬编码，与 zmai.json timeout=300 无关联 | line 93 |

## 4. 潜在 Bug / 风险

| ID | 级别 | 模块 | 风险 |
|----|------|------|------|
| RISK-001 | P0 | run_agent.py | BUG-001 根因待确认：可能是 (a) allowedTools 路径模式 `C:\...` 与 `D:/...` 分隔符不匹配 (b) Claude Code 2.x 语法变更 (c) 绝对路径 vs resolve 后路径不一致 |
| RISK-002 | P1 | swe/agent.py | CompletionState + test_success_count≥1 硬终止：若首次 pytest 全绿但任务未完成（如还需生成报告），会误判完成 |
| RISK-003 | P1 | swe/agent.py | loop_guard 在 step 内检测；若 LLM 每轮返回不同参数的同义命令（如 cd + ls），signature 不匹配可能漏检 |
| RISK-004 | P2 | runtime/runtime.py | max_steps 耗尽标记 timed_out，但 timeout 配置 (300s) 未参与循环中断（仅靠 step 内 tool timeout） |
| RISK-005 | P2 | workspace/ | 历史遗留数百个 workspace/test_* 目录，无清理机制 |

## 5. 缺少测试的位置

- run_agent.py 本身无单元测试（仅 test_run_agent_workspace/encoding 覆盖部分）
- **真实无头端到端测试缺失**：没有用真实 claude CLI + 真实 workspace 的 E2E 验证（BUG-001 因此漏网）
- --skip-permissions 路径无 E2E 测试
- max_iterations 真实 backend 的自动停止测试缺失（test_autostop 用 mock backend）
- Windows Credential Manager 真实存储测试缺失（store_wincred 依赖 win32 环境）
- 并发 run_agent.py 实例测试缺失

## 6. 测试环境事实

- claude CLI 2.1.169 已安装 (C:\Users\MECHREVO\.local\bin\claude)
- Python 3.11.9, .venv 含 pytest.exe
- DeepSeek API key 已配置于 ~/.zmai/credentials (加密存储, DOCTOR PASS)
- ~/.zmai/config.json 存在
- git 仓库存在但 No commits yet

## 7. 测试重点建议 (P0/P1)

1. **P0**: BUG-001 复现 → 定位根因 → 修复 → 验证 SWE Agent 真实自动修复能力
2. **P0**: 自主停止机制真实验证 (max_iterations 限制 + 完成即停)
3. **P1**: 全量 pytest 回归 (1171 测试)
4. **P1**: Credential Store / API Key 配置流程验证
