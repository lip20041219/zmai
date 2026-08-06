# Preflight Check — 设计文档

**Date**: 2026-07-17  
**Status**: Implemented

---

## 1. 动机

用户调用 `zmai "task"` 后，Runtime 直接调用 `Backend.invoke()`，如果 API Key 无效或未设置，错误在发送 HTTP 请求后才暴露：

```
Backend 调用失败: [BACKEND_ERROR] claude API HTTP 401: invalid x-api-key
```

这对用户体验不友好 — 浪费了一次 HTTP 请求，且错误信息不直观。

---

## 2. 设计目标

在任何任务开始前，**不发送 HTTP 请求**，先检查：

| # | 检查项 | 失败时的提示 |
|---|---|---|
| 1 | Backend 是否已选择 | 列出可用 Backend，提示如何指定 |
| 2 | Backend 是否已注册 | 显示已注册列表，提示 `zmai auth update` |
| 3 | API Key 是否存在 | 显示对应的 env var 名，提示配置方法 |
| 4 | API Key 是否为空 | 提示 Key 是空字符串 |
| 5 | Config 是否有效 | 提示配置格式错误 |

---

## 3. 架构

```
Runtime.run(backend="claude")
  │
  ├── preflight.check("claude", gateway, config)
  │     │
  │     ├── 1. backend=None → NO_BACKEND_SELECTED ──→ 输出错误，返回
  │     ├── 2. "claude" not in registry → BACKEND_NOT_REGISTERED ──→ 输出错误，返回
  │     ├── 3. ANTHROPIC_API_KEY 为空 → API_KEY_MISSING ──→ 输出错误，返回
  │     ├── 4. API Key 是空白字符串 → API_KEY_EMPTY ──→ 输出错误，返回
  │     └── 5. Config 格式错误 → CONFIG_ERROR ──→ 输出错误，返回
  │
  │  passed=true → 继续
  │
  ├── lifecycle.initialize()
  ├── workspace.prepare()
  ├── gateway.get(backend)
  ├── backend.invoke()      ← 此时才可能发生 HTTP 401
  └── ...
```

---

## 4. 错误输出示例

### API Key 缺失

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Preflight Check 失败

  当前 Backend：Claude (Anthropic)
  未检测到 ANTHROPIC_API_KEY。

  请执行：
    zmai auth update claude

  或者切换到其他 Backend：
    zmai --backend deepseek <task>
    zmai --backend gemini <task>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Backend 未注册

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Preflight Check 失败

  Backend 'nonexistent' 未注册。

  当前已注册的 Backend：claude, deepseek, gemini

  请执行：
    zmai auth update nonexistent

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 未选择 Backend

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Preflight Check 失败

  未选择 Backend。

  请执行：
    zmai auth list                    # 查看可用 Backend
    zmai --backend <name> <task>      # 指定 Backend
  或者设置环境变量：
    ANTHROPIC_API_KEY  /  DEEPSEEK_API_KEY  /  GEMINI_API_KEY

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 5. 实现

### 5a. 新文件

`src/zmai/runtime/preflight.py`

| 符号 | 类型 | 说明 |
|---|---|---|
| `PreflightResult` | class | 检查结果，含 `passed`, `message`, `reason`, `backend_name` |
| `check()` | function | 主检查函数 |
| `_resolve_env_key_name()` | function | 从 PluginRegistry / BACKEND_METADATA 查 env var 名 |
| `_resolve_label()` | function | 获取 Backend 可读名称 |
| `_find_api_key()` | function | 依次检查 env var → AuthStore |
| `_missing_key_result()` | function | 构造带备选方案的错误信息 |
| `_check_config()` | function | 检查 Config 格式 |

### 5b. 修改文件

`src/zmai/runtime/runtime.py` — 在 `run()` 开头添加：

```python
from zmai.runtime.preflight import check as preflight_check

pf = preflight_check(backend, self._gateway, self._config)
if not pf.passed:
    pf.print()
    return {"status": "failed", "agent_id": agent_id, "error": pf.reason}
```

---

## 6. 边界情况

| 场景 | 行为 |
|---|---|
| `ANTHROPIC_API_KEY` 设置了但无效（过期/错误） | Preflight 检测到 Key 存在 → 通过。`invoke()` 时返回 401。Preflight 不做 HTTP 请求，无法验证 Key 的有效性 |
| `ANTHROPIC_API_KEY` 在 env var 和 AuthStore 都有 | Preflight 优先检查 env var → 找到 → 通过 |
| `ANTHROPIC_API_KEY` 只在 AuthStore 中 | Preflight 检查 env var 为空 → 回退到 AuthStore → 找到 → 通过 |
| 多个 Backend 都注册了 | Preflight 只检查当前选择的 Backend |
| `backend=auto` | `backend_name` 为 `"auto"`，不在 registry 中 → Preflight 返回 `BACKEND_NOT_REGISTERED`（此场景需结合 AutoSelector 先在外部解析） |

---

## 7. 不做什么

- **不做 API Key 有效性验证** — 那需要发送 HTTP 请求，违反 Preflight 的设计目标
- **不做网络连通性检查** — 同理
- **不修改 Backend 代码** — Backend 的 `invoke()` 仍然做自己的参数校验
- **不替代 `zmai doctor`** — Preflight 是每次任务前的快速检查，doctor 是全面诊断
