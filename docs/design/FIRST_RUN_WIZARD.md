# First Run Wizard — 设计文档

**Date**: 2026-07-17  
**Status**: Implemented

---

## 1. 动机

用户第一次启动 ZMAI 时，没有任何 Backend 配置，直接进入任务执行会立即失败。

目标：**第一次启动时自动进入配置向导**，选择默认模型 → 输入 API Key → 保存 → 正常运行。

---

## 2. 触发条件

```
zmai "hello"
  │
  ├── 是否有 ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY？
  │     ├── 有 → 跳过向导，正常运行
  │     └── 无 → 继续检查
  │
  ├── AuthStore 是否有已配置的 Backend？
  │     ├── 有 → 跳过向导，正常运行
  │     └── 无 → 进入 First Run Wizard
  │
  └── 终端是否为交互模式（isatty）？
        ├── 是 → 显示向导
        └── 否 → 跳过向导（非交互模式）
```

如果处于非交互模式（管道、脚本），跳过向导，后续调用 `gateway.get()` 时会触发 Fallback 错误。

---

## 3. 用户界面

### 3a. 完整流程

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
           ZMAI — 首次配置向导

  请选择默认模型：

    [1] DeepSeek     — deepseek-chat（默认）
    [2] Claude       — claude-sonnet-4-6
    [3] Gemini       — gemini-2.0-flash

  请输入编号 [1-3]，直接回车选择 DeepSeek：
  █

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

用户选择后：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  请输入 DeepSeek API Key：
  (输入内容不可见)
  █

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

配置完成：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ 配置完成

  当前默认：DeepSeek (deepseek-chat)
  配置文件：~/.zmai/credentials

  现在可以开始使用了：
    zmai "你的任务描述"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 3b. 简化流程（非交互模式或 `--json`）

```
No backends configured.
Set ANTHROPIC_API_KEY or run: zmai auth update <name>
```

---

## 4. 实现

### 4a. `_first_run_wizard()` — 新函数

替换原有的 `_run_init_wizard()`。

```python
def _first_run_wizard() -> bool:
    """首次配置向导。返回 True 表示配置成功。"""
    from zmai.gateway.backends import BACKEND_DEFAULT_CONFIG, get_available_backends

    theme = Theme.dark()
    all_backends = get_available_backends()

    # 构建可选列表
    backend_list = [
        (name, info["label"], info["default_model"])
        for name, info in all_backends.items()
    ]

    sep = "━" * 50

    # ── Step 1: 选择模型 ──────────────────────────
    print(f"\n  {sep}", file=sys.stderr)
    print(f"  ZMAI — 首次配置向导", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  请选择默认模型：\n", file=sys.stderr)
    for i, (name, label, model) in enumerate(backend_list, 1):
        default_mark = "（默认）" if i == 1 else ""
        print(f"    [{i}] {label:12s} — {model}{default_mark}", file=sys.stderr)

    print(f"", file=sys.stderr)
    print(f"  请输入编号 [1-{len(backend_list)}]，直接回车选择 {backend_list[0][1]}：", file=sys.stderr)
    print(f"  {sep}", file=sys.stderr)

    try:
        sel = input("  ").strip()
    except (EOFError, KeyboardInterrupt):
        sel = ""
    print(file=sys.stderr)

    if sel:
        try:
            idx = max(0, min(int(sel) - 1, len(backend_list) - 1))
        except ValueError:
            idx = 0
    else:
        idx = 0

    name, label, default_model = backend_list[idx]
    defaults = BACKEND_DEFAULT_CONFIG.get(name, {})

    # ── Step 2: 输入 API Key ───────────────────────
    import getpass
    print(f"  请输入 {label} API Key：", file=sys.stderr)
    print(f"  (输入内容不可见)", file=sys.stderr)
    try:
        key = getpass.getpass("  ").strip()
    except (EOFError, KeyboardInterrupt):
        key = ""
    print(file=sys.stderr)

    if not key:
        print(f"  API Key 为空，配置取消。\n", file=sys.stderr)
        return False

    # ── Step 3: 保存配置 ────────────────────────────
    from zmai.auth import AuthStore
    from zmai.auth.store import AUTH_DIR

    store = AuthStore()
    store.set_backend(
        name, key,
        model=default_model,
        base_url=defaults.get("base_url", ""),
        timeout=defaults.get("timeout", 0),
        max_tokens=defaults.get("max_tokens", 0),
        temperature=defaults.get("temperature", 0.0),
        make_active=True,
    )

    # 写入全局配置
    cfg_path = AUTH_DIR / "config.json"
    cfg_path.write_text(json.dumps({
        "version": 1,
        "cli": {"theme": "dark", "trust_mode": True},
        "gateway": {"default_backend": name},
        "backends": {
            name: {
                "model": default_model,
                "base_url": defaults.get("base_url", ""),
                "timeout": defaults.get("timeout", 120),
                "max_tokens": defaults.get("max_tokens", 4096),
                "temperature": defaults.get("temperature", 0.7),
            }
        },
    }, indent=2), encoding="utf-8")

    # ── 完成 ──────────────────────────────────────────
    print(f"  {sep}", file=sys.stderr)
    print(f"  ✓ 配置完成", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  当前默认：{label} ({default_model})", file=sys.stderr)
    print(f"  配置文件：{cfg_path}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  现在可以开始使用了：", file=sys.stderr)
    print(f"    zmai \"你的任务描述\"", file=sys.stderr)
    print(f"  {sep}\n", file=sys.stderr)

    return True
```

### 4b. `_should_show_wizard()` — 检测函数

```python
def _should_show_wizard() -> bool:
    """是否需要显示首次配置向导。"""
    # 环境变量已有 Key → 跳过
    for _name, info in get_available_backends().items():
        if os.environ.get(info["env_api_key"]):
            return False
    # AuthStore 已有配置 → 跳过
    try:
        if AuthStore().list_backends():
            return False
    except Exception:
        pass
    # 仅在交互终端显示
    return sys.stdin.isatty() and sys.stderr.isatty()
```

### 4c. main() 集成

```python
def main(argv=None):
    _ensure_utf8()
    argv = argv or sys.argv[1:]

    # 子命令直接执行
    if argv and argv[0] in ("config", "auth", "doctor", "plugin"):
        ...  # 子命令处理
        return

    # First Run Wizard
    if _should_show_wizard():
        configured = _first_run_wizard()
        if not configured:
            sys.exit(0)  # 用户取消，直接退出
        _inject_auth_credentials()

    # 正常启动
    ...
```

---

## 5. 文件修改

| 文件 | 修改 |
|---|---|
| `src/zmai/cli/main.py` | 重写 `_run_init_wizard()` → `_first_run_wizard()`；重写 `_should_show_init_wizard()` → `_should_show_wizard()` |

---

## 6. 边界情况

| 场景 | 行为 |
|---|---|
| 环境变量已设置 | 跳过向导，直接运行 |
| AuthStore 已有配置 | 跳过向导，直接运行 |
| 非交互模式（管道） | 跳过向导，提示手动配置 |
| 用户输入无效编号 | 使用默认（第一个） |
| 用户中断 (Ctrl+C) | 取消配置，退出 |
| 用户输入空 Key | 提示取消，退出 |
| 所有 Backend 都配置后 | 不再显示向导 |

---

## 7. 输出示例

### 首次启动

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
           ZMAI — 首次配置向导

  请选择默认模型：

    [1] DeepSeek     — deepseek-chat（默认）
    [2] Claude       — claude-sonnet-4-6
    [3] Gemini       — gemini-2.0-flash

  请输入编号 [1-3]，直接回车选择 DeepSeek：
  2

  请输入 Claude API Key：
  (输入内容不可见)
  ◆◆◆◆◆◆◆◆◆◆◆◆

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ 配置完成

  当前默认：Claude (claude-sonnet-4-6)
  配置文件：~/.zmai/credentials

  现在可以开始使用了：
    zmai "你的任务描述"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

zmai>
```

### 用户取消

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
           ZMAI — 首次配置向导
  ...

  API Key 为空，配置取消。
```
