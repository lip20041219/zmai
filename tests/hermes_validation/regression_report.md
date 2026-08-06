# 回归测试报告 (regression_report.md)

**测试时间**: 2026-08-06
**命令**: `pytest -q` (全量) + 重点模块单独验证
**环境**: Windows 10, Python 3.11.9, pytest 9.1.1

---

## 总体结果

| 指标 | 数值 |
|------|------|
| 收集测试总数 | 1252 |
| 通过 | **1244** |
| 失败 | **1** |
| 跳过 | 7 |
| 耗时 | 503.97s (8m23s) |

失败用例（唯一）: `tests/test_workspace.py::TestWorkspaceInit::test_init_with_unwritable_dir`
- 原因: **Windows 管理员权限环境特例** — 测试假设 `C:\Windows\System32` 不可写，
  但当前 shell 以管理员权限运行，该目录可写（实测 mkdir 成功），
  因此 `pytest.raises(WorkspaceError)` 未触发。
- 处置: 非代码 bug。修改测试会破坏其设计意图（Linux CI 走只读路径分支）。
  已清理测试副作用目录 `C:\Windows\System32\zmai_test`。记录为 P2 环境依赖。

---

## Auth / Credential Store 覆盖

`tests/test_credential_store.py` + `tests/test_auth.py` — **全部通过**

- NullCredentialStore 不可用时正确返回 None / raise Unavailable
- store 选择: 平台适配（Windows → WinCred, 其他 → keyring）
- keyring fallback 链
- set/get/delete/exists/list 完整 CRUD
- provider 隔离
- **API Key 不泄露**: to_dict 排除 api_key、日志不打印 key
- 加密存储读写（~/.zmai/credentials）

## Backend 覆盖

`tests/test_gateway.py` — **全部通过**

- Backend ABC 抽象（不可实例化、子类必须定义 name）
- 具体 backend invoke/stream
- Registry: register/get/set_default/instance_caching
- ToolRouter: execute/definitions/timeout/非存在工具
- backends: deepseek / claude / gemini 三后端注册与路由
- zmai doctor 实测: DeepSeek PASS (Credential Store), Claude/Gemini Missing Key (无 key, 预期)

## Runtime 覆盖

`tests/test_runtime.py` + `tests/test_loop_guard.py` + `tests/test_termination.py` — **全部通过**

- Runtime 初始化 / get_info / list_agents
- run() 返回结构 / backend 调用 / on_progress / memory persisted
- **timeout**: max_steps 耗尽 → timeout 状态
- **retry**: 连接错误重试成功 / 重试耗尽失败
- **loop guard**: identical_calls / identical_failures / no_progress 三模式
- **termination**: finalize 状态优先级 (timed_out > step_failed > replan > tools > verify > completed)
- cancel 语义

## CLI 覆盖（run_agent.py 实测）

`tests/test_run_agent_workspace.py` + `tests/test_run_agent_encoding.py` — **全部通过 (7/7)**

| CLI 功能 | 结果 |
|----------|------|
| `--help` | PASS — 完整 usage 输出 |
| `--workspace` | PASS — 目录解析 + 不存在时报错退出 (exit 1) |
| `--prompt` | PASS — 提示词文件加载 |
| `--skip-permissions` | PASS — 命令构造含 `--dangerously-skip-permissions` |
| 默认白名单 | PASS — `--allowedTools Read,Edit(<ws>/**),Write(<ws>/**),Bash(*)` |
| 优先级 | PASS — CLI > env ZMAI_WORKSPACE > 默认 (修复后) |
| UTF-8 编码 | PASS — 中文/emoji 输出不崩溃 |

## 本轮修复验证

| 修复项 | 验证 |
|--------|------|
| run_agent.py 新增 resolve_workspace() | 5 个 workspace 测试转绿 |
| run_agent.py main(argv) 兼容 | 2 个 encoding 测试转绿 |
| EditTool 换行规范化 | 新增 4 个回归测试全绿 |
| pytest 收集排除 hermes_validation | demo 数据不再污染全量收集 |

---

## 风险清单

- P2: `test_init_with_unwritable_dir` 在 Windows 管理员权限下不可复现（环境依赖）
- P2: Claude/Gemini backend 缺 API Key，无真实调用验证（仅 mock/路由验证）
- P3: 全量耗时 8 分钟，CI 可考虑并行 (pytest-xdist)
