# main.py 模块分解分析

> 审计方式: 只读分析，不修改代码
> 文件: `src/zmai/cli/main.py`
> 行数: 1236 行
> 原则: 只有存在明确边界的职责才建议拆分，不按行数机械拆分

---

## 一、全景统计

### 顶层函数（共 28 个）

| # | 函数 | 行号 | 行数 | 职责 | 所属领域 |
|---|------|------|------|------|---------|
| 1 | `_sanitize_error` | 34-52 | 19 | 脱敏错误消息，移除 HTTP 细节 | 辅助 |
| 2 | `_offer_auth_fix` | 55-66 | 12 | API Key 错误时交互式询问修复 | Auth |
| 3 | `_now_iso` | 69-71 | 3 | 返回 ISO 格式 UTC 时间戳 | 辅助 |
| 4 | `_save_session` | 74-80 | 7 | 保存最近一次任务到 JSON 文件 | Session |
| 5 | `_load_latest_session` | 83-89 | 7 | 读取最近一次任务 | Session |
| 6 | `_ensure_utf8` | 92-97 | 6 | 重新配置 stdout/stderr 编码 | 辅助 |
| 7 | `_cleanup_old_workspaces` | 100-126 | 27 | 清理过期 Agent 工作区 | 辅助 |
| 8 | `_should_show_wizard` | 131-148 | 18 | 判断是否需要显示配置向导 | Auth |
| 9 | `_first_run_wizard` | 151-261 | 111 | **首次配置向导（4 步骤交互）** | Auth |
| 10 | `_inject_auth_credentials` | 264-279 | 16 | 将凭据注入环境变量（兼容用） | Auth |
| 11 | `_print_help` | 284-310 | 27 | 打印帮助信息 | CLI 解析 |
| 12 | `_build_parser` | 313-326 | 14 | 构建 ArgumentParser | CLI 解析 |
| 13 | `_get_theme` | 329-337 | 9 | 根据配置和参数选择 Theme | 输出 |
| 14 | `_repl_run` | 342-385 | 44 | REPL 模式下执行单个任务 | REPL |
| 15 | `_oneshot_run` | 388-439 | 52 | 命令行模式执行单个任务 | 任务执行 |
| 16 | `_setup_readline` | 444-455 | 12 | 配置 readline 历史与补全 | REPL |
| 17 | `_cmd_interactive` | 458-526 | 69 | REPL 主循环（含内建命令） | REPL |
| 18 | `_run_config` | 531-557 | 27 | config 子命令（get/set/list） | Config |
| 19 | `_run_auth_status` | 563-605 | 43 | 显示所有 Backend 认证状态 | Auth |
| 20 | `_run_auth_test` | 608-771 | 164 | **测试 Backend API Key 有效性** | Auth |
| 21 | `_classify_http_error` | 774-778 | 5 | HTTP 状态码分类 | Auth |
| 22 | `_safe_print_error_body` | 781-796 | 16 | 安全输出错误响应 | Auth |
| 23 | `_print_test_failure` | 799-841 | 43 | 测试失败详细输出 | Auth |
| 24 | `_run_auth` | 844-976 | 133 | **Auth 子命令路由（8 个子命令）** | Auth |
| 25 | `_print_setup_hint_if_needed` | 979-993 | 15 | 无配置时输出设置提示 | Auth |
| 26 | `_print_auth_debug` | 996-1054 | 59 | 打印 Auth 调试信息（启动时） | Auth |
| 27 | `_find_auth_key` | 1057-1065 | 9 | 统一查找 API Key | Auth |
| 28 | `_run_plugin` | 1068-1122 | 55 | plugin 子命令（list/install/remove） | Plugin |
| 29 | `_run_doctor` | 1125-1132 | 8 | doctor 子命令 | Doctor |
| 30 | `main` | 1137-1231 | 95 | **主入口：路由 + 初始化 + 分派** | 入口 |

### 类

无。全部代码为模块级函数。

### 模块级常量

| 名称 | 值 | 用途 |
|------|-----|------|
| `SESSION_DIR` | `~/.zmai/sessions` | 会话文件目录 |
| `HISTORY_FILE` | `~/.zmai/history` | readline 历史文件 |
| `AUTHORS` | `"xijingliu"` | 作者标识 |

---

## 二、按职责领域统计

| 领域 | 函数数 | 总行数 | 占比 | 函数列表 |
|------|--------|--------|------|---------|
| **Auth** | 11 | 578 | 47% | `_offer_auth_fix`, `_should_show_wizard`, `_first_run_wizard`, `_inject_auth_credentials`, `_run_auth_status`, `_run_auth_test`, `_classify_http_error`, `_safe_print_error_body`, `_print_test_failure`, `_run_auth`, `_print_setup_hint_if_needed`, `_print_auth_debug`, `_find_auth_key` |
| **REPL** | 3 | 125 | 10% | `_repl_run`, `_setup_readline`, `_cmd_interactive` |
| **任务执行** | 1 | 52 | 4% | `_oneshot_run` |
| **CLI 解析** | 2 | 41 | 3% | `_print_help`, `_build_parser` |
| **Config** | 1 | 27 | 2% | `_run_config` |
| **Plugin** | 1 | 55 | 4% | `_run_plugin` |
| **Doctor** | 1 | 8 | 1% | `_run_doctor` |
| **Session** | 2 | 14 | 1% | `_save_session`, `_load_latest_session` |
| **输出** | 1 | 9 | 1% | `_get_theme` |
| **主入口** | 1 | 95 | 8% | `main` |
| **辅助** | 3 | 52 | 4% | `_sanitize_error`, `_now_iso`, `_ensure_utf8`, `_cleanup_old_workspaces` |
| **总计** | 30 | 1236 | 100% | |

### 关键发现

**Auth 占了近一半（47%）。** 这是拆分的首要候选。Auth 的代码量（578 行）本身就相当于一个独立模块。

---

## 三、模块依赖关系

### main.py 的 import 依赖

```
main.py 直接导入:
  ├── zmai（版本号）
  ├── zmai.cli.context
  ├── zmai.cli.detector
  ├── zmai.cli.formatters
  ├── zmai.config
  ├── zmai.config.sources
  ├── zmai.errors
  ├── zmai.runtime
  └── zmai.auth （运行时延迟导入）
  └── zmai.gateway （运行时延迟导入）
  └── zmai.workspace （运行时延迟导入）
```

所有导入的模块都是 `zmai.*`，没有从外部包导入。这意味着拆分时**不会产生循环 import 问题**，因为拆出来的函数仍然是调用这些模块。

### 内部函数调用关系

```
main()
  ├── _ensure_utf8()
  ├── _should_show_wizard() → _first_run_wizard()
  ├── _build_parser() → _print_help()
  ├── _get_theme()
  ├── _cleanup_old_workspaces()
  ├── _print_auth_debug()
  ├── _oneshot_run() → _save_session() / _sanitize_error() / _offer_auth_fix()
  ├── _cmd_interactive() → _setup_readline() / _load_latest_session() / _repl_run()
  ├── _run_config()
  ├── _run_auth() → _run_auth_status() / _run_auth_test() / _first_run_wizard()
  ├── _run_plugin()
  └── _run_doctor()

_run_auth() 内部子路由:
  ├── _run_auth_status()
  ├── _first_run_wizard()  ← 与 main() 共享
  ├── _run_auth_test() → _classify_http_error() / _safe_print_error_body()
  └── _print_setup_hint_if_needed()
```

**关键依赖：** `_first_run_wizard()` 被 `main()` 和 `_run_auth()` 两个入口共享。这不能直接搬走，要么留在 `main.py`，要么挪到 `auth.py` 后从 `main.py` import。

---

## 四、推荐拆分方案

### 边界分析

| 模块 | 行数 | 独立边界 | 循环 import 风险 | 推荐度 |
|------|------|---------|-----------------|--------|
| Auth | 578行 / 11函数 | **清晰** — 全部是 `zmai.auth.*` 操作 | 低 — auth 函数调 gateway/config，不反向依赖 cli | ⭐⭐⭐ 强烈推荐 |
| REPL | 125行 / 3函数 | **清晰** — 完整的交互循环 | 低 — 只调 formatters 和 runtime | ⭐⭐⭐ 强烈推荐 |
| Session | 14行 / 2函数 | **清晰** — 独立的 JSON 文件读写 | 无 — 只调 json/os | ⭐⭐⭐ 强烈推荐 |
| Plugin | 55行 / 1函数 | **清晰** — 独立的子命令 | 低 | ⭐⭐ 推荐 |
| Config | 27行 / 1函数 | **清晰** — 独立的子命令 | 低 | ⭐⭐ 推荐 |
| Doctor | 8行 / 1函数 | **清晰** — 委托给 doctor 模块 | 低 | ⭐ 可选 |

### 建议目录结构

```
cli/
├── __init__.py
├── main.py           # 主入口 + CLI 解析 + 任务执行 + 辅助（约 280 行）
│                      # 保留: main, _build_parser, _print_help, _get_theme,
│                      #        _oneshot_run, _sanitize_error, _ensure_utf8,
│                      #        _cleanup_old_workspaces, _now_iso
│                      # 保留函数: 被 main() 直接调用且多入口共享的
│
├── auth.py            # 新增 — Auth 子命令全部逻辑（约 578 行 → ~580 行）
│                      # 移动: _run_auth, _run_auth_status, _run_auth_test,
│                      #        _first_run_wizard, _should_show_wizard,
│                      #        _offer_auth_fix, _inject_auth_credentials,
│                      #        _print_auth_debug, _find_auth_key,
│                      #        _print_setup_hint_if_needed,
│                      #        _classify_http_error, _safe_print_error_body,
│                      #        _print_test_failure
│
├── repl.py            # 新增 — REPL 交互模式（约 125 行 → ~130 行）
│                      # 移动: _cmd_interactive, _setup_readline, _repl_run
│                      # 注意: _repl_run 被 _cmd_interactive 独占调用
│
├── session.py         # 新增 — 会话持久化（约 14 行 → ~20 行）
│                      # 移动: _save_session, _load_latest_session
│                      # 注意: SESSION_DIR 常量也需移动
│
├── commands.py        # 新增 — 独立子命令（约 90 行 → ~100 行）
│                      # 移动: _run_config, _run_plugin, _run_doctor
│
├── context.py         # 已有
├── detector.py        # 已有
├── doctor.py          # 已有
└── formatters.py      # 已有
```

### main.py 拆分后的样子（保留约 280 行）

```python
"""CLI entry - zmai <task> or zmai (REPL)."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from zmai import __version__ as zmai_version
from zmai.cli.commands import run_config, run_plugin, run_doctor
from zmai.cli.auth import (
    should_show_wizard, first_run_wizard, print_auth_debug,
    run_auth, offer_auth_fix,
)
from zmai.cli.repl import cmd_interactive
from zmai.cli.session import save_session, load_latest_session
from zmai.cli.context import build_context
from zmai.cli.detector import _find_root as find_project_root
from zmai.cli.formatters import Theme, print_error, print_json, print_success
from zmai.config import Config
from zmai.config.sources import CLISource, EnvSource, FileSource
from zmai.runtime import Runtime


def _sanitize_error(msg: str) -> str:
    ...  # 保留在 main.py（被 _oneshot_run 独占调用，行数少）


def _now_iso() -> str:
    ...  # 辅助函数，保留


def _ensure_utf8() -> None:
    ...  # 辅助函数，保留


def _cleanup_old_workspaces(...) -> int:
    ...  # 仅被 main() 调用，保留


def _build_parser() -> argparse.ArgumentParser:
    ...  # 仅被 main() 调用，保留


def _get_theme(...) -> Theme:
    ...  # 仅被 main() 调用，保留


def _oneshot_run(...) -> None:
    ...  # 任务执行，保留


def main(argv=None) -> None:
    ...  # 主入口，保留
```

---

## 五、各领域的边界分析

### Auth（578 行）— 边界最清晰

**当前依赖：** 全部是 `zmai.auth.*`、`zmai.gateway.*`、`zmai.cli.formatters`、`zmai.config`。  
**不依赖：** `zmai.runtime`、`zmai.cli.detector`、`zmai.cli.context`。  
**冲突：** `_first_run_wizard()` 被 `main()` 和 `_run_auth("setup")` 两个入口共享。搬走的话 `main()` 需要 `from zmai.cli.auth import first_run_wizard`。**这是安全的。**

**拆分收益：** Auth 从 578 行独立后，main.py 直接减少 47% 的代码。Auth 逻辑有完整的测试覆盖（`test_auth.py`、`test_credential_store.py`），搬移风险低。

### REPL（125 行）— 边界清晰

**当前依赖：** `zmai.cli.formatters`、`zmai.runtime`、`zmai.cli.session`（隐含）。  
**不依赖：** `zmai.auth.*`、`zmai.gateway.*`。  
**注意：** `_repl_run()` 内联定义了 `on_progress` 回调。搬移时可以把回调作为参数传入。

**拆分收益：** REPL 是完整自洽的交互循环，与 CLI 的 one-shot 模式是两条独立路径。

### Session（14 行）— 最简单

**当前依赖：** `json`、`os`、`pathlib`，无其他 ZMAI 模块。  
**拆分收益：** 极低风险。但 14 行代码本身不值得单独文件——它是"拆的时候顺手带走"那种。

### Plugin（55 行）+ Config（27 行）+ Doctor（8 行）— 可合并为 commands.py

这三个都是"子命令处理函数"，结构一致：解析 argv → 调用相应模块 → 输出结果。  
**拆分收益：** 整理到一起后，main.py 的 `main()` 中的子命令分发逻辑可以简化为 `commands.dispatch(cmd, rest)` 模式。

---

## 六、风险与建议

### 不推荐立即拆的原因

1. **当前功能稳定** — `main()` 的 95 行入口逻辑经过了测试验证
2. **`_first_run_wizard()` 被两个入口共享** — 需要确认 `main()` 中调用它的路径在拆后仍然正确
3. **CLI 行为测试密集** — `test_cli.py` 中有针对子命令的集成测试，拆分后 import 路径变化可能影响测试

### 推荐的拆分顺序

```
第 1 步: session.py      (14 行，纯搬移，无风险)
第 2 步: auth.py         (578 行，可独立测试)
第 2b 步: 更新 test_cli.py import
第 3 步: repl.py         (125 行，独立交互循环)
第 4 步: commands.py     (90 行，合并 3 个小命令)
第 5 步: 精简 main.py    (保留约 280 行)
```

### 不拆的部分

- `_sanitize_error`（19 行） — 仅被 `_oneshot_run` 调用，留在 `main.py` 即可
- `_ensure_utf8`（6 行）— 辅助函数
- `_cleanup_old_workspaces`（27 行）— 仅被 `main()` 调用
- `_build_parser`（14 行）— CLI 基础设施
- `_get_theme`（9 行）— 仅被 `main()` 调用
- `_oneshot_run`（52 行）— 核心执行逻辑，留在 `main.py`
- `main`（95 行）— 主入口，必须保留
