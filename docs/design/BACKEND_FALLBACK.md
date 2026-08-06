# Backend Fallback — 设计文档

**Date**: 2026-07-17  
**Status**: Implemented

---

## 1. 动机

用户执行任务时，如果当前 Backend 没有配置 API Key，直接抛出 `HTTP 401` 错误，用户体验差。

目标：**自动寻找第一个可用的 Backend 继续执行**，而不是直接报错。

---

## 2. 行为

```
用户执行 zmai "task"
         │
         ▼
PluginRegistry.get("claude")
         │
         ├── Claude: ANTHROPIC_API_KEY 不存在
         │
         ├── Fallback: DeepSeek → DEEPSEEK_API_KEY 存在
         │     │
         │     │  日志: "Backend fallback: claude → deepseek"
         │     │
         ▼
         └── 使用 DeepSeekBackend 继续执行任务
```

### 2a. 有 Fallback 可用

```
PluginRegistry.get("claude")
  │
  ├── Claude: 无 API Key
  ├── DeepSeek: 有 API Key ✓  ← 第一个可用
  │
  ├── 日志: "Backend fallback: claude → deepseek"
  ├── 更新默认: default = "deepseek"
  └── 返回 DeepSeekBackend 实例
```

用户无感知，任务继续执行。

### 2b. 无任何 Fallback 可用

```
PluginRegistry.get("claude")
  │
  ├── Claude: 无 API Key
  ├── DeepSeek: 无 API Key
  └── Gemini: 无 API Key
  │
  └── 抛出 BackendError: "未发现可用模型。请配置 API Key。"
```

用户看到友好错误。

---

## 3. 架构

### 3a. PluginRegistry.get() — 修改点

**文件**: `gateway/plugin.py:271-310`

```python
def get(self, name=None):
    resolved = name or self._default

    # 实例已缓存 → 直接返回
    if resolved in self._instances:
        return self._instances[resolved]

    # 构建配置
    plugin = self._plugins.get(resolved)
    if plugin:
        cfg = self._build_config(plugin)
        if not cfg.get("api_key"):
            # ← 没有 API Key，触发 Fallback
            return self._fallback(failed=resolved)
        self._configs[resolved] = cfg

    return super().get(name)
```

### 3b. PluginRegistry._fallback() — 新增

**文件**: `gateway/plugin.py:312-340`

```python
def _fallback(self, failed):
    for name, plugin in self._plugins.items():
        if name == failed:
            continue                    # 跳过当前失败的
        if name in self._instances:
            self._default = name         # 已缓存 → 直接用
            return self._instances[name]
        cfg = self._build_config(plugin)
        if cfg.get("api_key"):
            self._configs[name] = cfg
            self._default = name         # 更新默认
            return super().get(name)     # 实例化

    raise BackendError("未发现可用模型。请配置 API Key。")
```

### 3c. Preflight Check — 联动

**文件**: `runtime/preflight.py:120-148`

Preflight 检查到当前 Backend 无 API Key 时，不再直接报错，而是先查询是否有 Fallback 可用：

```
Preflight: claude 无 API Key
  ├── _has_fallback("claude") → True  → 通过（由 get() 处理切换）
  └── _has_fallback("claude") → False → 报错 "未检测到 ANTHROPIC_API_KEY"
```

---

## 4. 完整流程

```
Runtime.run(backend="claude")
  │
  ├── preflight.check("claude")
  │     ├── ANTHROPIC_API_KEY 为空
  │     ├── _has_fallback("claude") → True (DeepSeek 有 Key)
  │     └── 通过 → 继续
  │
  ├── lifecycle.initialize()
  ├── workspace.prepare()
  │
  ├── gateway.get("claude")
  │     ├── _build_config("claude") → api_key 为空
  │     ├── _fallback("claude")
  │     │     ├── DeepSeek: api_key 存在 ✓
  │     │     ├── 日志: "Backend fallback: claude → deepseek"
  │     │     ├── _default = "deepseek"
  │     │     └── return DeepSeekBackend(config={...})
  │     │
  │     └── 返回 DeepSeekBackend
  │
  └── SWEAgent.step()
        └── backend.invoke()  →  DeepSeek API  ← 正常运行
```

---

## 5. 边界情况

| 场景 | 行为 |
|---|---|
| `get("claude")` + Claude 有 Key | 正常使用 Claude，无 Fallback |
| `get("claude")` + Claude 无 Key + DeepSeek 有 Key | Fallback → DeepSeek，更新默认 |
| `get("claude")` + 都无 Key | 抛出 "未发现可用模型" |
| `get(None)` + 默认 Backend 无 Key + 其他有 Key | Fallback |
| 第一个 Fallback 实例已缓存 | 直接返回缓存实例（无需重建配置） |
| `--backend claude` 显式指定 | 仍触发 Fallback（不阻塞用户任务） |
| Fallback 后再次调用 `get(None)` | 返回已切换的默认 Backend |

---

## 6. 文件修改

| 文件 | 修改 |
|---|---|
| `src/zmai/gateway/plugin.py` | `get()` 中新增 `_fallback()` 调用；新增 `_fallback()` 方法；新增 `BackendError` import |
| `src/zmai/runtime/preflight.py` | Key 检查中新增 `_has_fallback()` 判断；新增 `_has_fallback()` 辅助函数 |

---

## 7. 日志输出

```
# Fallback 发生时
WARNING  Backend fallback: claude 无 API Key，尝试其他 Backend
INFO     Backend fallback: claude → deepseek

# 无可 Fallback 时
ERROR    BackendError: 未发现可用模型。请配置 API Key。
```

---

## 8. 与 Preflight Check 的关系

```
Preflight Check                    Backend Fallback
────────────────────               ────────────────
Request 前检查                      Request 时处理
可离线判断                         需要实例化时触发
Key 不存在 → 查 Fallback           Key 不存在 → 调 _fallback()
有 Fallback → 通过                 有 Fallback → 切换 Backend
无 Fallback → 输出友好错误          无 Fallback → 抛出异常
```
