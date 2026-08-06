# main.py 架构依赖映射

> 文件: `src/zmai/cli/main.py`
> 行数: 1441
> 审计方式: 只读分析

---

## 1. 文件总览

```
main.py (1441 行)
 ├── 模块导入          行 1-30     13 imports
 ├── 全局状态          行 28-30    3 个模块级变量
 ├── 辅助函数          行 33-127   7 个函数
 ├── 认证              行 130-280  3 个函数
 ├── 参数解析          行 283-341  3 个函数
 ├── 任务执行          行 344-636  4 个函数
 ├── REPL 交互模式     行 638-722  2 个函数
 ├── 子命令            行 725-753  1 个函数
 ├── Auth 子命令       行 756-1261 9 个函数
 ├── Plugin 子命令     行 1264-1318 1 个函数
 ├── Doctor 子命令     行 1321-1329 1 个函数
 └── 主入口            行 1331-1440 1 个函数
```

---

## 2. 所有函数清单

### 2.1 辅助函数 (`_` 前缀, 33-127)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_sanitize_error` | 35 | 脱敏错误消息 | `_oneshot_run`, `_repl_run`, `main` | — | 纯函数，无外部依赖 |
| `_offer_auth_fix` | 56 | 交互式 API Key 修复 | `_repl_run`, `_oneshot_run` | `_first_run_wizard` | 仅在 tty 时交互 |
| `_now_iso` | 70 | ISO 时间戳 | `_save_session` | `datetime` | 3 行内部函数 |
| `_save_session` | 75 | 保存任务到 session | `_repl_run`, `_oneshot_run`, `_cmd_interactive` | `_now_iso`, `SESSION_DIR` | 全局状态 `SESSION_DIR` |
| `_load_latest_session` | 84 | 读取上次任务 | `_cmd_interactive` | `SESSION_DIR` | 全局状态 `SESSION_DIR` |
| `_ensure_utf8` | 93 | 配置 stdout/stderr 编码 | `main` | — | |
| `_cleanup_old_workspaces` | 101 | 清理过期 workspace | `main` | `zmai.workspace.Workspace` | 延迟导入 |

### 2.2 认证函数 (130-280)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_should_show_wizard` | 132 | 判断是否需要配置向导 | `main` | `print_error`, `print_warning`, `AuthStore` | 延迟导入 |
| `_first_run_wizard` | 152 | 交互式配置向导 | `_offer_auth_fix`, `_run_auth`, `main` | `AuthStore`, `CredentialResolver` | 最长函数 ~110 行 |
| `_inject_auth_credentials` | 265 | 注入凭据到环境变量 | — | `CredentialResolver` | **未被任何函数调用** 🔴 |

### 2.3 参数解析函数 (283-341)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_print_help` | 285 | 打印帮助信息 | `main` | — | 纯打印 |
| `_build_parser` | 316 | 构建 argparse 解析器 | `main` | — | 返回 `ArgumentParser` |
| `_get_theme` | 333 | 根据 args+config 确定 Theme | `_repl_run`, `_oneshot_run`, `_cmd_interactive`, `main` | `Theme` | |

### 2.4 任务执行函数 (344-636)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_repl_run` | 346 | REPL 模式单次执行 | `_cmd_interactive` | `_save_session`, `_sanitize_error`, `_offer_auth_fix`, `Runtime.run`, `print_success`, `print_error` | 返回 dict，不 exit |
| `_run_plan_only` | 392 | `zmai plan <task>` | `main` | `generate_plan`, `Runtime`, `Theme`, `find_project_root`, `Config` | 使用 `runtime._gateway` 私有 API 🔴 |
| `_run_benchmark` | 474 | `zmai benchmark <list\|run\|report>` | `main` | `BenchmarkRunner` | 延迟导入 |
| `_run_eval` | 526 | `zmai eval <list\|run\|report>` | `main` | `EvalHarness` | 延迟导入 |
| `_oneshot_run` | 581 | 单次执行 + exit | `main` | `_save_session`, `_get_theme`, `_sanitize_error`, `_offer_auth_fix`, `Runtime.run`, `print_json`, `print_success`, `print_error` | 执行后 `sys.exit()` |

### 2.5 REPL 函数 (638-722)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_setup_readline` | 640 | 配置 readline | `_cmd_interactive` | `HISTORY_FILE` | Windows 上静默跳过 |
| `_cmd_interactive` | 654 | REPL 主循环 | `main` | `_get_theme`, `_setup_readline`, `_load_latest_session`, `_repl_run`, `Runtime.get_info`, `print_info` | 内部嵌套 `_handle_builtin` |

### 2.6 Config 子命令 (727-753)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_run_config` | 727 | `zmai config <get\|set\|list>` | `main` | `Config`, `Theme`, `print_table` | |

### 2.7 Auth 子命令 (756-1261)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_run_auth_status` | 759 | 显示认证状态 | `_run_auth` | `CredentialResolver`, `discover_plugins`, `source_label` | |
| `_run_auth_test` | 804 | 测试 API Key | `_run_auth` | `CredentialResolver`, `discover_plugins`, `urllib` | 直接使用 urllib 🔴 |
| `_classify_http_error` | 970 | HTTP 状态码映射 | `_run_auth_test` | — | |
| `_safe_print_error_body` | 977 | 安全输出 error body | `_run_auth_test` | — | |
| `_print_test_failure` | 995 | 测试失败详情 | — | — | **未被任何函数调用** 🔴 |
| `_run_auth` | 1040 | `zmai auth <sub>` 路由 | `main` | 全部 `_run_auth_*`, `_first_run_wizard` | 最大路由函数 ~130 行 |
| `_print_setup_hint_if_needed` | 1175 | 输出设置提示 | `_run_auth` | `CredentialResolver` | |
| `_print_auth_debug` | 1192 | 调试认证信息 | `main` | `CredentialResolver`, `AuthStore`, `get_backend_info` | 使用 ANSI 转义码 |
| `_find_auth_key` | 1253 | 查找 API Key | `_run_auth`(doctor) | `CredentialResolver` | |

### 2.8 Plugin 子命令 (1264-1318)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_run_plugin` | 1264 | `zmai plugin <list\|install\|remove>` | `main` | `shutil`, `Path` | |

### 2.9 Doctor 子命令 (1321-1329)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `_run_doctor` | 1321 | `zmai doctor` | `main` | `Doctor` | 延迟导入 |

### 2.10 主入口 (1331-1440)

| 函数 | 行 | 职责 | 调用者 | 被调用者 | 备注 |
|------|-----|------|--------|----------|------|
| `main` | 1333 | 主入口 | `__main__` | 全部 `_run_*` 函数 | ~107 行的路由 + 初始化 |

---

## 3. 全局状态

| 变量 | 行 | 类型 | 用途 | 被访问的函数 |
|------|-----|------|------|-------------|
| `SESSION_DIR` | 28 | `Path.home() / ".zmai" / "sessions"` | 会话持久化目录 | `_save_session`, `_load_latest_session` |
| `HISTORY_FILE` | 29 | `Path.home() / ".zmai" / "history"` | readline 历史文件 | `_setup_readline` |
| `AUTHORS` | 30 | `"xijingliu"` | 作者标识 | **仅定义，不读取** 🔴 |

---

## 4. 外部依赖 (import)

| 模块 | 使用位置 | 是否标准库 |
|------|----------|-----------|
| `argparse` | `_build_parser` | ✅ |
| `asyncio` | `_repl_run`, `_oneshot_run` | ✅ |
| `json` | 多处 | ✅ |
| `logging` | 模块级 | ✅ |
| `os` | 多处 | ✅ |
| `shutil` | `_run_plugin` | ✅ |
| `sys` | 全部函数 | ✅ |
| `time` | `_cleanup_old_workspaces` | ✅ |
| `pathlib.Path` | 全局 | ✅ |
| `typing` | 类型标注 | ✅ |
| `zmai.__version__` | `_build_parser` | 项目内 |
| `zmai.cli.context` | `main` | 项目内 |
| `zmai.cli.detector` | `main`, `_run_plan_only` | 项目内 |
| `zmai.cli.formatters` | 多处 | 项目内 |
| `zmai.config` | `main`, `_run_plan_only` | 项目内 |
| `zmai.config.sources` | `main`, `_run_plan_only` | 项目内 |
| `zmai.errors` | `_run_auth` | 项目内 |
| `zmai.runtime` | `main`, `_run_plan_only` | 项目内 |
| `zmai.swe.planner` | `_run_plan_only` | 项目内 |

---

## 5. 职责聚类 + 提取目标

```
当前 main.py (1441 行)
 │
 ├── A. CLI 入口 + 路由      ~50 行   ← 应留在 main.py
 │    └── main(), _build_parser(), _print_help()
 │
 ├── B. 任务执行              ~200 行  ← 可提取到 cli/task.py
 │    └── _repl_run(), _oneshot_run(), _cmd_interactive(),
 │        _run_plan_only(), _setup_readline()
 │        + _save_session(), _load_latest_session()
 │        + _cleanup_old_workspaces()
 │
 ├── C. 认证子命令            ~520 行  ← 应已位于 cli/auth.py
 │    └── _run_auth(), _run_auth_status(), _run_auth_test(),
 │        _first_run_wizard(), _should_show_wizard(),
 │        _offer_auth_fix(), _inject_auth_credentials(),
 │        _print_setup_hint_if_needed(), _print_auth_debug(),
 │        _find_auth_key(), _classify_http_error(),
 │        _safe_print_error_body(), _print_test_failure()
 │
 ├── D. Config 子命令         ~30 行   ← 可提取到 cli/config_cmd.py
 │    └── _run_config()
 │
 ├── E. 评测子命令 (benchmark+eval)  ~110 行 ← 可提取到 cli/eval_cmd.py
 │    └── _run_benchmark(), _run_eval()
 │
 ├── F. Plugin 子命令          ~60 行  ← 可提取到 cli/plugin_cmd.py
 │    └── _run_plugin()
 │
 ├── G. Doctor 子命令          ~10 行  ← 已位于 cli/doctor.py
 │    └── _run_doctor()
 │
 ├── H. 辅助函数               ~50 行  ← 分散到各自模块
 │    └── _sanitize_error(), _ensure_utf8(), _now_iso(),
 │        _get_theme()
 │
 └── I. 死代码                ~30 行  🔴
      ├── _inject_auth_credentials() — 从未调用
      ├── _print_test_failure() — 从未调用
      └── AUTHORS — 仅定义
```

---

## 6. 潜在循环依赖风险

| 路径 | 风险 | 说明 |
|------|------|------|
| `cli/main.py` → `runtime` → `swe/agent` → `cli/main` | ❌ **不存在** | Runtime 和 SWEAgent 不反向依赖 CLI |
| `cli/main` → `cli/context` → `cli/detector` → `cli/main` | ⚠️ **无** | 当前无反向引用 |
| 提取 `_run_auth` 到 `cli/auth_cmd.py` | ✅ **安全** | `auth_cmd` 只依赖 `zmai.auth.*`，不依赖 `cli.main` |
| 提取 `_oneshot_run` 到 `cli/task.py` | ⚠️ **注意 Theme** | `_oneshot_run` 依赖 `Theme`（在 `cli/formatters.py`），无循环 |
| 提取 `_run_benchmark` 到 `cli/eval_cmd.py` | ✅ **安全** | 只依赖 `zmai.benchmark` 和 `zmai.eval` |

**结论**: 无循环依赖风险。所有 `_run_*` 函数都是单向调用树。

---

## 7. 私有 API 使用

| 位置 | 行 | 私有 API | 问题 |
|------|-----|----------|------|
| `_run_plan_only` | 419 | `runtime._gateway.get()` | 访问 Runtime 私有属性 `_gateway` 🔴 |
| `main` | 1417 | `runtime._gateway.default_name` | 同上 🔴 |
| `main` | 1414 | `project_ctx.summary()` | 若 `project_ctx` 为 None 会 AttributeError |
| `_run_auth`(doctor) | 1139 | `PluginRegistry().list_plugins()` | 每次调用重建 PluginRegistry |

---

## 8. 死代码

| 函数 | 行 | 原因 |
|------|-----|------|
| `_inject_auth_credentials` | 265 | 文档说"默认不执行"，整个函数无调用者 |
| `_print_test_failure` | 995 | 定义了完整的 HTTP 测试失败输出逻辑，但 `_run_auth_test` 使用内联代码而非此函数 |
| `AUTHORS` | 30 | 仅定义，程序中不读取 |

---

## 9. 重定位建议

```
建议新文件结构:

cli/
  ├── __init__.py
  ├── main.py                ← 仅保留入口路由 + 参数解析（~150 行）
  ├── task.py                ← _repl_run, _oneshot_run, _cmd_interactive（~200 行）
  ├── auth_cmd.py            ← _run_auth 及全部 auth 子函数（~520 行）
  ├── config_cmd.py          ← _run_config（~30 行）
  ├── eval_cmd.py            ← _run_benchmark + _run_eval（~110 行）
  ├── plugin_cmd.py          ← _run_plugin（~60 行）
  ├── context.py             ← 已有
  ├── detector.py            ← 已有
  ├── formatters.py          ← 已有
  └── doctor.py              ← 已有
```

拆分后 `main.py` 从 1441 行减少到约 150 行。
