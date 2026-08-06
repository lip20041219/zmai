# Auth CLI Behavior — `zmai auth` 子命令设计

> 审查日期：2026-07-18
> 问题：`zmai auth` 无参数时无条件进入首次配置向导，即使用户已配置

---

## 目录

1. [当前状态](#1-当前状态)
2. [核心问题](#2-核心问题)
3. [设计目标](#3-设计目标)
4. [新命令结构](#4-新命令结构)
5. [行为矩阵](#5-行为矩阵)
6. [详细行为规范](#6-详细行为规范)
7. [显示规范](#7-显示规范)
8. [修改清单](#8-修改清单)

---

## 1. 当前状态

### 1a. 当前子命令结构

```
zmai auth                         → _first_run_wizard()        ← 🔴 问题
zmai auth list                    → AuthStore.list_backends()
zmai auth switch <backend>        → AuthStore.set_active_backend()
zmai auth update <backend> [...]  → AuthStore.set_backend()
zmai auth remove <backend>        → AuthStore.remove_backend()
zmai auth doctor                  → PluginRegistry (显示状态)
zmai auth status                  → CredentialResolver (显示凭据状态)
zmai auth test <backend>          → HTTP 验证
```

### 1b. 当前 `_run_auth()` 入口

```python
def _run_auth(argv: list[str]) -> None:
    store = AuthStore()
    if not argv:                    # ← 无子命令
        _first_run_wizard()         # ← 始终进入向导
        return
    sub = argv[0]
    # ... 分发到 list/switch/update/remove/doctor/status/test
```

### 1c. 问题

| 场景 | 用户输入 | 当前行为 | 期望行为 |
|------|---------|---------|---------|
| 已配置 | `zmai auth` | 进入配置向导 | 显示认证状态 |
| 未配置 | `zmai auth` | 进入配置向导 | 显示"未配置"或进入向导 |
| 已配置 | `zmai auth status` | 显示凭据状态 | ✅ 正确 |
| 未配置 | `zmai auth status` | 显示 "Configured: No" | ✅ 正确 |

---

## 2. 核心问题

### 问题 1：`zmai auth` 无参数行为错误

当前 `if not argv: _first_run_wizard()` 的设计假设：
- 用户运行 `zmai auth` 时**总是**需要进行首次配置

但实际用户行为：
- 用户可能已配置，想查看状态
- 用户可能已配置，想了解可用子命令
- 用户只是忘记了命令语法

### 问题 2：没有明确"主动配置"的命令

当前：
- `zmai auth` → 进入向导（副作用）
- `zmai auth update <backend>` → 更新特定 Backend

缺少：
- `zmai auth setup` → 明确表示"我要配置"的命令

### 问题 3：`list` 和 `status` 职责模糊

| 命令 | 当前行为 | 数据来源 |
|------|---------|---------|
| `zmai auth list` | AuthStore.list_backends() | 加密文件（已配置的 Backend） |
| `zmai auth status` | CredentialResolver.get_status() | 所有来源（file + config + env） |

- `list` 只显示加密文件中已保存的 Backend
- `status` 显示所有注册 Backend 的完整认证状态
- 用户难以区分两者差异

### 问题 4：`doctor` 命令在 auth 子命令下重复

`zmai auth doctor` 和 `zmai doctor` 功能重叠，但实现不同：
- `zmai doctor` → `Doctor` 类（完整诊断）
- `zmai auth doctor` → `PluginRegistry` + `_find_auth_key()`（仅凭据状态）

---

## 3. 设计目标

### 3a. 原则

1. **`zmai auth` 无参数时显示状态** — 不修改任何配置，不进入向导
2. **`zmai auth setup` 进入配置向导** — 明确表示"我要配置"
3. **`zmai auth status` 保持现有行为** — 显示完整凭据状态
4. **所有子命令不产生副作用** — 除非用户明确请求写操作
5. **向后兼容** — 保持现有子命令不变，仅修改无参数行为 + 新增 `setup`

### 3b. 副作用分类

| 类型 | 命令 | 副作用 |
|------|------|--------|
| 无副作用（只读） | `zmai auth`（新） | 无 |
| 无副作用（只读） | `zmai auth status` | 无 |
| 无副作用（只读） | `zmai auth list` | 无 |
| 无副作用（只读） | `zmai auth test` | 无（只 HTTP GET） |
| **有副作用（写）** | `zmai auth setup` | 写入 credentials 文件 |
| **有副作用（写）** | `zmai auth update` | 写入 credentials 文件 |
| **有副作用（写）** | `zmai auth switch` | 修改 active_backend |
| **有副作用（写）** | `zmai auth remove` | 删除 credentials |

### 3c. 命令分类

```
只读命令（无副作用）:
  zmai auth                  → 显示默认状态（同 status）
  zmai auth status           → 显示凭据状态（来源 + 冲突）
  zmai auth list             → 显示已保存的凭据列表
  zmai auth test <provider>  → 测试 Key 有效性

写命令（有副作用）:
  zmai auth setup            → 首次配置向导
  zmai auth update <provider>  → 更新指定 Backend 凭据
  zmai auth switch <provider>  → 切换默认 Backend
  zmai auth remove <provider>  → 删除凭据
```

---

## 4. 新命令结构

### 4a. 完整命令树

```
zmai auth
  ├──（无参数） → 显示认证状态摘要（同 status）
  │
  ├── setup        → 交互式配置向导（原 _first_run_wizard 行为）
  │
  ├── status       → 显示所有 Backend 的凭据状态 + 来源 + 冲突
  │
  ├── list         → 列出已保存凭据（加密文件中已配置的）
  │
  ├── update <name> [key] [model] [base_url] [timeout] [max_tokens] [temperature]
  │                → 更新凭据
  │
  ├── switch <name> → 切换默认 Backend
  │
  ├── remove <name> → 删除凭据
  │
  ├── test <name>   → HTTP 验证 Key 有效性
  │
  └──（未知子命令） → 显示帮助 + 可用子命令列表
```

### 4b. 过渡期兼容

```
v1（当前）:  zmai auth → 进入向导（破坏性）
v2（新）:    zmai auth → 显示状态（安全）
             zmai auth setup → 进入向导（新增）

向后兼容:
  zmai auth update <name>   → ✅ 不变
  zmai auth status          → ✅ 不变
  zmai auth test <name>     → ✅ 不变
  zmai auth list            → ✅ 不变
  zmai auth switch <name>   → ✅ 不变
  zmai auth remove <name>   → ✅ 不变
  zmai auth doctor          → ✅ 保留（标记弃用）
```

### 4c. 帮助文本更新

```python
# 当前:
print("  zmai auth                   Manage authentication")
print("  zmai auth status            Show credentials status for all backends")
print("  zmai auth test <backend>    Test API key validity via HTTP")

# 新:
print("  zmai auth                   Show authentication status")
print("  zmai auth setup             Set up authentication interactively")
print("  zmai auth status            Show credentials status with source details")
print("  zmai auth update <backend>  Update API key for a backend")
print("  zmai auth test <backend>    Test API key validity via HTTP")
print("  zmai auth list              List saved credentials")
print("  zmai auth switch <backend>  Switch default backend")
print("  zmai auth remove <backend>  Remove saved credentials")
```

---

## 5. 行为矩阵

### 5a. 无条件参数

| 场景 | 当前行为 | 新行为 |
|------|---------|--------|
| 已配置 + 有凭据 | 进入向导 | `_run_auth_status()` |
| 已配置 + 凭据损坏 | 进入向导 | `_run_auth_status()`（显示错误） |
| 未配置 | 进入向导 | `_run_auth_status()`（显示 "no backends configured" + 提示） |

### 5b. 有子命令

| 子命令 | 参数 | 当前行为 | 新行为 |
|--------|------|---------|--------|
| `setup` | 无 | ❌ 不存在 | ✅ `_first_run_wizard()` |
| `status` | 无 | ✅ `_run_auth_status()` | ✅ 不变 |
| `list` | 无 | ✅ `store.list_backends()` | ✅ 不变 |
| `update` | `<name>` [+ 可选] | ✅ `store.set_backend()` | ✅ 不变 |
| `switch` | `<name>` | ✅ `store.set_active_backend()` | ✅ 不变 |
| `remove` | `<name>` | ✅ `store.remove_backend()` | ✅ 不变 |
| `test` | `<name>` | ✅ `_run_auth_test()` | ✅ 不变 |
| `doctor` | 无 | ✅ `PluginRegistry` 显示 | ✅ 保留（标记弃用） |
| *未知* | — | `AttributeError`（崩溃） | 显示帮助 + 子命令列表 |

### 5c. 未配置时的行为

```
$ zmai auth
  Authentication Status
  ────────────────────

  Claude (Anthropic)
    Configured : No
    Source     : missing

  DeepSeek
    Configured : No
    Source     : missing

  Gemini (Google)
    Configured : No
    Source     : missing

  No backends configured.
  Use `zmai auth setup` to configure, or
  set environment variables:
    ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY
```

---

## 6. 详细行为规范

### 6a. `zmai auth`（无参数）

```python
def _run_auth(argv):
    if not argv:
        # 新行为：显示认证状态（不进入向导）
        _run_auth_status()
        # 如果没有配置任何 Backend，额外输出提示
        _print_setup_hint_if_needed()
        return
```

**`_print_setup_hint_if_needed()`** 逻辑：
```python
def _print_setup_hint_if_needed() -> None:
    """如果没有配置任何 Backend，输出配置提示。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.gateway.plugin import discover_plugins
    resolver = CredentialResolver()
    any_configured = False
    for plugin in discover_plugins():
        if resolver.get_status(plugin.name).configured:
            any_configured = True
            break
    if not any_configured:
        print()
        print("  No backends configured.")
        print("  Use `zmai auth setup` to configure, or set environment variables:")
        print("    ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY")
```

### 6b. `zmai auth setup`

```python
elif sub == "setup":
    # 进入交互式配置向导
    configured = _first_run_wizard()
    if not configured:
        sys.exit(1)
```

**语义**：
- `setup` 表示"我要从零开始配置"
- 与旧的 `zmai auth`（无参数）行为一致
- 允许用户覆盖已有的凭据

### 6c. `zmai auth status`

**保持不变**：
```python
elif sub == "status":
    _run_auth_status()
```

`_run_auth_status()` 当前已使用 `CredentialResolver.get_status()` 提供完整的状态信息。

### 6d. 未知子命令处理

```python
else:
    # 当前：AttributeError（因为没有匹配的 elif，函数结束）
    # 新：显示帮助
    print(f"  Unknown subcommand: '{sub}'")
    print(f"  Usage: zmai auth <setup|status|list|update|switch|remove|test>")
    print(f"  Use `zmai auth` (no arguments) to show status.")
    sys.exit(1)
```

### 6e. `zmai auth doctor` 标记弃用

```python
elif sub == "doctor":
    # 已弃用：请使用 `zmai doctor`
    import warnings
    warnings.warn(
        "`zmai auth doctor` is deprecated. Use `zmai doctor` instead.",
        DeprecationWarning,
    )
    # 但仍然执行
    ...
```

---

## 7. 显示规范

### 7a. `zmai auth` 默认输出

已配置时：
```
  Authentication Status
  ────────────────────

  Claude (Anthropic)
    Configured : Yes
    Source     : environment

  DeepSeek
    Configured : Yes
    Source     : credentials file
    Warning    : environment has a different key.

  Gemini (Google)
    Configured : No
    Source     : missing

  Tip: Use `zmai auth setup` to configure additional backends.
```

未配置时：
```
  Authentication Status
  ────────────────────

  Claude (Anthropic)
    Configured : No
    Source     : missing

  DeepSeek
    Configured : No
    Source     : missing

  Gemini (Google)
    Configured : No
    Source     : missing

  No backends configured.
  Use `zmai auth setup` to configure, or set environment variables:
    ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY
```

### 7b. 凭据损坏时

```
  Authentication Status
  ────────────────────

  Claude (Anthropic)
    Configured : Yes
    Source     : environment

  DeepSeek
    Configured : No
    Status     : corrupted (credentials file)
    Source     : missing

  Gemini (Google)
    Configured : No
    Source     : missing

  Warning: The credentials file is corrupted.
  Run `zmai auth setup` or `zmai auth update deepseek` to reconfigure.
```

---

## 8. 修改清单

### 8a. 修改文件

| 文件 | 改动 |
|------|------|
| `src/zmai/cli/main.py:_run_auth()` | 无参数时调用 `_run_auth_status()` 而非 `_first_run_wizard()` |
| `src/zmai/cli/main.py:_run_auth()` | 新增 `elif sub == "setup": _first_run_wizard()` |
| `src/zmai/cli/main.py:_run_auth()` | 新增 `else:` 处理未知子命令 |
| `src/zmai/cli/main.py:_run_auth()` | `doctor` 子命令标记弃用（可选） |
| `src/zmai/cli/main.py:_print_help()` | 更新子命令列表 |
| `src/zmai/cli/main.py:` | 新增 `_print_setup_hint_if_needed()` 辅助函数 |

### 8b. 不修改的文件

| 文件 | 原因 |
|------|------|
| `_run_auth_status()` | 行为保持不变 |
| `_first_run_wizard()` | 行为保持不变，仅调用时机改变 |
| `_run_auth_test()` | 行为保持不变 |
| `store.set_backend()` | 底层写入逻辑不变 |

### 8c. 删除的内容

无删除。仅修改行为 + 新增子命令。

### 8d. 迁移注意事项

| 关注点 | 说明 |
|--------|------|
| 用户脚本 | 如果用户脚本执行 `zmai auth` 期望进入向导 → 改为 `zmai auth setup` |
| 交互式用户 | `zmai auth` 不再修改配置，需要配置时用 `zmai auth setup` |
| 帮助文本 | 更新后用户可发现新子命令 |

---

## 附录 A：`_run_auth()` 新代码结构

```python
def _run_auth(argv: list[str]) -> None:
    from zmai.auth import AuthStore
    try:
        store = AuthStore()
    except CredentialError as e:
        print_error(str(e))
        sys.exit(1)

    if not argv:
        # 默认行为：显示状态，不进入向导
        _run_auth_status()
        _print_setup_hint_if_needed()
        return

    sub = argv[0]

    if sub == "setup":
        # 交互式配置向导（原无参数行为）
        configured = _first_run_wizard()
        if not configured:
            sys.exit(1)

    elif sub == "status":
        _run_auth_status()

    elif sub == "list":
        theme = Theme.dark()
        rows = []
        for b in store.list_backends():
            active = "*" if b["active"] else " "
            rows.append([
                f"{active} {b['name']}",
                b.get("model", "") or "-",
                b["key_preview"],
                str(b.get("timeout", "")) or "-",
                str(b.get("temperature", "")) or "-",
                b["verified_at"][:10] or "-",
            ])
        if rows:
            print_table(["Backend", "Model", "Key", "Timeout", "Temp", "Verified"],
                       rows, theme)
        else:
            print("no backends configured. run zmai auth setup.")

    elif sub == "switch":
        if len(argv) < 2:
            print("usage: zmai auth switch <backend>", file=sys.stderr)
            sys.exit(1)
        name = argv[1]
        if store.set_active_backend(name):
            info = store.get_backend(name)
            m = f" ({info.get('model', '')})" if info and info.get('model') else ""
            print(f"switched to {name}{m}")
        else:
            print(f"backend '{name}' not configured", file=sys.stderr)
            sys.exit(1)

    elif sub == "update":
        if len(argv) < 2:
            print("usage: zmai auth update <backend> [...]", file=sys.stderr)
            sys.exit(1)
        name = argv[1]
        existing = store.get_backend(name) or {}
        key = argv[2] if len(argv) > 2 else ""
        model_input = argv[3] if len(argv) > 3 else ""
        base_url = argv[4] if len(argv) > 4 else ""
        timeout = int(argv[5]) if len(argv) > 5 and argv[5].isdigit() else 0
        max_tokens = int(argv[6]) if len(argv) > 6 and argv[6].isdigit() else 0
        temperature = float(argv[7]) if len(argv) > 7 else 0.0
        if not key:
            sys.stderr.write(f"  API Key ({name})")
            if existing.get("api_key"):
                sys.stderr.write(f" [current: {existing['api_key'][:7]}...]")
            sys.stderr.write(": ")
            key = input().strip()
        if not key:
            print("API Key required", file=sys.stderr)
            sys.exit(1)
        store.set_backend(name, key, model=model_input, base_url=base_url,
                          timeout=timeout, max_tokens=max_tokens,
                          temperature=temperature, make_active=True)
        print(f"{name} saved as default")

    elif sub == "remove":
        if len(argv) < 2:
            print("usage: zmai auth remove <backend>", file=sys.stderr)
            sys.exit(1)
        if store.remove_backend(argv[1]):
            print(f"removed {argv[1]}")
        else:
            print(f"backend '{argv[1]}' not found", file=sys.stderr)

    elif sub == "test":
        if len(argv) < 2:
            print("usage: zmai auth test <backend>", file=sys.stderr)
            sys.exit(1)
        _run_auth_test(argv[1])

    elif sub == "doctor":
        # 已弃用：请使用 `zmai doctor`
        print("  Note: `zmai auth doctor` is deprecated. Use `zmai doctor` instead.",
              file=sys.stderr)
        _run_auth_doctor()

    else:
        print(f"  Unknown subcommand: '{sub}'")
        print(f"  Usage: zmai auth <setup|status|list|update|switch|remove|test>")
        sys.exit(1)


def _print_setup_hint_if_needed() -> None:
    """如果没有配置任何 Backend，输出配置提示。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.gateway.plugin import discover_plugins
    resolver = CredentialResolver()
    any_configured = False
    for plugin in discover_plugins():
        if resolver.get_status(plugin.name).configured:
            any_configured = True
            break
    if not any_configured:
        print()
        print("  No backends configured.")
        print("  Use `zmai auth setup` to configure, or set environment variables:")
        print("    ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY")
```

## 附录 B：测试场景

| # | 场景 | 命令 | 期望结果 |
|---|------|------|---------|
| 1 | 已配置一个或多个 Backend | `zmai auth` | 显示状态摘要，不修改配置 |
| 2 | 无任何 Backend 配置 | `zmai auth` | 显示状态 + "No backends configured" 提示 |
| 3 | 凭据文件损坏 | `zmai auth` | 显示状态 + 错误信息，不进入向导 |
| 4 | 首次运行 | `zmai auth setup` | 进入交互式配置向导 |
| 5 | 已配置后重新配置 | `zmai auth setup` | 进入交互式配置向导（覆盖已有） |
| 6 | 查看状态 | `zmai auth status` | 显示完整凭据状态 |
| 7 | 查看已保存列表 | `zmai auth list` | 显示加密文件中已配置的 Backend |
| 8 | 更新凭据 | `zmai auth update deepseek` | 交互式输入新 Key |
| 9 | 更新凭据（命令行） | `zmai auth update deepseek sk-xxx` | 直接更新 |
| 10 | 删除凭据 | `zmai auth remove deepseek` | 删除成功 |
| 11 | 切换默认 | `zmai auth switch deepseek` | 切换成功 |
| 12 | 测试 Key | `zmai auth test deepseek` | HTTP 验证 |
| 13 | 未知子命令 | `zmai auth unknown` | 显示帮助信息 |
| 14 | 未登录 | `zmai auth`（无 env 无 file） | 显示 "No backends configured" + 提示 |

---

*报告自动生成于 2026-07-18 · 基于 `src/zmai/cli/main.py` 分析*
