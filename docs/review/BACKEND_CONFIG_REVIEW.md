# Backend Config Review — 默认 Backend 选择策略

**Date**: 2026-07-17  
**Goal**: 只能存在一个默认 Backend；Runtime 不允许硬编码 Claude 作为默认；必须读取用户配置；无配置则进入首次启动向导。

---

## 1. 当前默认 Backend 选择链

```
PluginRegistry._auto_select_default()
  │
  ├── 1. Config gateway.default_backend（非 "auto"）
  │     └── 来源: zmai.json → "gateway": {"default_backend": "auto"}
  │        ~/.zmai/config.json → "gateway": {"default_backend": "claude"}
  │
  ├── 2. 第一个有环境变量凭据的 Backend
  │     └── ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY
  │
  └── 3. 第一个已注册的 Backend ← 🔴 问题在此
        └── BACKEND_METADATA 定义顺序: claude → deepseek → gemini
            结果: 总是 claude
```

### 触发条件

| 场景 | 是否触发第 3 级 | 结果 |
|---|---|---|
| `zmai.json` 设了 `gateway.default_backend` | ❌ | 用户指定的值 |
| `~/.zmai/config.json` 有配置 | ❌ | 向导保存的值 |
| 环境变量设了 Key | ❌ | 对应的 Backend |
| 以上都无 | ✅ **触发** | **硬编码 Claude** 🔴 |

---

## 2. 违反项

| 要求 | 当前行为 | 位置 |
|---|---|---|
| 只能有一个默认 Backend | `_default` 只有一个，符合 | ✅ |
| 不允许硬编码 Claude | 无配置时第 3 级回退选 claude | 🔴 `plugin.py:418-420` |
| 必须读取用户配置 | 第 1 级读取 Config，第 2 级读取 env | ✅ |
| 无配置时进入向导 | `_should_show_wizard()` 判断 | ✅ `main.py:722-726` |

### 唯一问题

`_auto_select_default()` 第 3 级：

```python
# gateway/plugin.py:418-420
if self._backends:
    self._default = next(iter(self._backends))
```

`self._backends` 是一个 `dict`，插入顺序为 claude → deepseek → gemini。
`next(iter(...))` 返回 `"claude"`。

当用户没有任何配置时，`_default = "claude"` 被设置。后续 `gateway.get()` 调用会尝试实例化 `ClaudeBackend` → 发现没有 API Key → 触发 Fallback 切换到 DeepSeek（如果有 Key）→ 或者 Preflight 报错。

虽然最终会失败或 Fallback，但 `_default` 被错误地设为无凭据的 Backend。

---

## 3. 修复方案

### 方案 A：第 3 级改为不设默认

```python
# gateway/plugin.py:418-420
# 原代码:
if self._backends:
    self._default = next(iter(self._backends))

# 改为:
self._default = None  # 不设默认，让调用者处理
```

这样当用户没有配置时，`_default = None`。后续 `get()` 调用会抛出 `BackendError("未设置默认 Backend")`。

**优点**: 简单，不隐藏问题  
**缺点**: 用户看到"未设置默认 Backend"而不是引导

### 方案 B：第 3 级设为 "auto" 标记

```python
self._default = "auto"  # 标记为需要自动选择
```

在 `get()` 中处理 `"auto"` 标记，触发 AutoSelector。

**优点**: 语义清晰  
**缺点**: 需要额外逻辑处理 `"auto"` 字符串

### 推荐方案：A

因为：
1. Preflight Check 已经在 `run()` 之前检查 Backend 状态
2. 无配置时，`_should_show_wizard()` 会触发首次配置向导
3. 非交互模式下，`get()` 会抛出清晰错误
4. `_default = None` 让 Preflight 输出 "未选择 Backend" → 引导用户配置

---

## 4. 完整流程（修复后）

```
用户执行 zmai "task"
  │
  ├── _should_show_wizard()
  │     ├── 有 env var / AuthStore → False → 继续
  │     └── 无配置 → True → 首次配置向导 → 用户选择 → 保存 → 继续
  │
  ├── PluginRegistry(config)
  │     ├── Config 有 gateway.default_backend → 使用该值
  │     ├── 环境变量有 Key → 使用对应 Backend
  │     └── 以上都无 → _default = None  ← 不硬编码
  │
  ├── preflight.check(backend=None)
  │     ├── backend=None → _default=None → NO_BACKEND_SELECTED
  │     └── 输出: "未选择 Backend。请执行 zmai --backend <name>"
  │
  └── gateway.get(backend)
        ├── backend 指定了 → 使用该值
        └── backend=None → _default=None → BackendError("未设置默认 Backend")
```

---

## 5. 文件修改

| 文件 | 修改 |
|---|---|
| `src/zmai/gateway/plugin.py:418-420` | 第 3 级 `_default = next(iter(...))` → `_default = None` |
