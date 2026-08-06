# Backend 调用失败诊断报告

> **错误**: Claude API HTTP 401 — `authentication_error` `invalid x-api-key`
> **方法**: 静态代码分析，未修改代码
> **日期**: 2026-07-17

---

## 一、Backend 实际选择过程

### 1.1 调用链

```
main()                              ← 用户入口
  ├── _inject_auth_credentials()    ← 将 AuthStore 凭证注入环境变量
  │
  └── Runtime.__init__()
        ├── _register_available_backends()   ← 注册有凭证的 Backend
        └── _auto_select_default_backend()   ← 选择默认 Backend
              │
              ▼
Runtime.run(backend=None)           ← 未指定 backend
  └── self._gateway.get(None)       ← 获取默认 Backend 实例
        │
        ▼
ClaudeBackend.__init__(config)      ← config 来自注册时的参数
  └── self._api_key = config.get("api_key", os.environ["ANTHROPIC_API_KEY"])
        │
        ▼
ClaudeBackend.invoke()
  └── _post()
        └── header: x-api-key = self._api_key  ← 实际发送的密钥
```

### 1.2 Backend 注册流程

```python
# runtime.py:344-366 — _register_available_backends()
for name, info in get_available_backends().items():  # claude → deepseek 顺序
    ① 检查 os.environ["ANTHROPIC_API_KEY"]         ← 环境变量
    ② 如果没有 → 检查 AuthStore().get_backend("claude") ← 加密文件
    ③ 如果都没有 → continue（不注册）
    ④ 注册: self._gateway.register(name, cls, config={"model": model})
                                                    ↑
                                      注意: config 只有 model，没有 api_key！
```

### 1.3 Backend 选择流程

```python
# runtime.py:368-382 — _auto_select_default_backend()
① config.get("gateway.default_backend", "auto")
   ├── 如果设置且有效 → 使用该 backend
   └── "auto" → ②

② 遍历 BACKEND_METADATA（claude 先于 deepseek）
   for name, info in get_available_backends().items():
       if os.environ.get(info["env_api_key"]):   ← 检查 env 是否有密钥
           set_default(name)
           return                                 ← 第一个匹配即返回

③ 如果都没有环境变量 → 选第一个已注册的
```

**关键**: `BACKEND_METADATA` 的 dict 顺序是 `claude` → `deepseek`。
如果两个 API Key 都设置了环境变量，**Claude 永远优先于 DeepSeek**。

---

## 二、API Key 来源完整追踪

### 2.1 三层读取优先级

```
ClaudeBackend.__init__():
  self._api_key = config.get("api_key",             ① Config（最高）
                   os.environ.get("ANTHROPIC_API_KEY", ② 环境变量
                   ""))                               ③ 空（默认）
```

### 2.2 _register_available_backends 传入的 config

```python
# runtime.py:364
self._gateway.register("claude", ClaudeBackend, config={"model": model})
```

**config 只有 `{"model": "claude-sonnet-4-6"}`，没有 `api_key`**。

这意味着 `ClaudeBackend.__init__()` 中的 `config.get("api_key")` 返回 `None`，
回退到 `os.environ.get("ANTHROPIC_API_KEY")`。

### 2.3 _inject_auth_credentials 的行为

```python
# main.py:191-206
for name, info in get_available_backends().items():
    if os.environ.get(info["env_api_key"]):   # 已有环境变量 → 跳过
        continue
    auth_info = AuthStore().get_backend(name)
    if auth_info and auth_info.get("api_key") and not auth_info.get("from_env"):
        os.environ[env_key] = auth_info["api_key"]  # 将 AuthStore 的 key 注入 env
```

**如果环境变量已设置，AuthStore 的值不会覆盖它。**

### 2.4 AuthStore.get_backend 的环境变量覆盖

```python
# auth/store.py:123-135
def get_backend(self, name):
    env_key = os.environ.get(f"{name.upper()}_API_KEY")  # 也检查 env
    if env_key:
        return {"api_key": env_key, "from_env": True}     # 直接返回 env 值
    return self._data.get("backends", {}).get(name)       # 返回文件中的值
```

**即使调用 AuthStore，如果 ANTHROPIC_API_KEY 环境变量已设置，返回的也是环境变量的值。**

---

## 三、x-api-key 的实际值来源

```
HTTP Header x-api-key 的来源:
  ClaudeBackend._api_key
    ├── config.get("api_key")         → None（注册时没传）
    └── os.environ["ANTHROPIC_API_KEY"]
          ├── 用户 shell 中 export     → 用户手动设置的
          ├── _inject_auth_credentials → AuthStore 注入的
          └── 两者都没有               → 空字符串，请求会 401
```

**实际发送的值取决于运行环境中 `ANTHROPIC_API_KEY` 的值。**

---

## 四、Routing 流程

### 4.1 用户是否指定了 backend

```python
# CLI: zmai --backend claude "task"
# 或者: zmai "task"（使用默认）

# runtime.py:109, 121
async def run(self, agent_id, task, backend=None, ...):
    backend_inst = self._gateway.get(backend)  # None → 使用默认
```

### 4.2 默认 Backend 的确定

```python
# registry.py:80
resolved = name or self._default  # name=None 时用默认
```

### 4.3 可能的 Routing 错误

| 场景 | 结果 |
|------|------|
| `ANTHROPIC_API_KEY` 和 `DEEPSEEK_API_KEY` 都设置 | **Claude 被选为默认**（因为 claude 在 dict 中在前） |
| `ANTHROPIC_API_KEY` 过期/错误，但 `DEEPSEEK_API_KEY` 正确 | Claude 被选中，401 |
| `zmai.json` 中 `gateway.default_backend = "claude"` | 强制使用 Claude |
| 用户想用 DeepSeek 但没设 `DEEPSEEK_API_KEY` | Claude 被选中（如果其 key 存在） |

---

## 五、根因分析

### 5.1 直接原因

**请求中发送的 `x-api-key` 被 Anthropic API 拒绝（401 authentication_error）。**

可能的原因按概率排序：

| 概率 | 原因 | 证据 |
|------|------|------|
| **极高** | 当前选中的是 `claude` backend，但 ANTHROPIC_API_KEY 是错误的或已过期 | 错误消息明确说是 "invalid x-api-key" |
| **高** | 用户意图使用 DeepSeek，但由于 `BACKEND_METADATA` 中 claude 在前，Claude 被自动选为默认 | 两个 key 都设置时 Claude 优先 |
| **中** | `AuthStore` 中存储了旧的/错误的 API Key，`_inject_auth_credentials` 将其注入环境变量 | 注入逻辑不验证 key 的有效性 |
| **低** | XOR 加密/解密损坏了 API Key | 概率低，但测试覆盖不足 |
| **低** | `zmai.json` 中 `gateway.default_backend` 强制指定了 claude | 需要检查配置文件 |

### 5.2 间接原因（系统缺陷）

| 缺陷 | 位置 | 说明 |
|------|------|------|
| **配置不传递 api_key** | `runtime.py:364` | `_register_available_backends` 只传 `model` 不传 `api_key`，导致 Backend 必须依赖环境变量 |
| **Backend 优先级隐含** | `runtime.py:368-382` | `_auto_select_default_backend` 依赖 dict 顺序，Claude 永远优先于 DeepSeek |
| **无密钥验证机制** | — | 没有任何地方在运行时验证 API Key 的有效性 |
| **无错误恢复** | `claude.py:110` | 401 不会被重试（`BackendError` 直接抛出，不重试） |

---

## 六、最小修复方案

> ⚠️ 以下为建议，不修改代码

| # | 修复 | 位置 | 说明 |
|---|------|------|------|
| **P0** | 检查当前环境变量 | 用户 shell | `echo $ANTHROPIC_API_KEY` — 确认 key 值正确且未过期 |
| **P0** | 确认用户意图 | CLI | `zmai config get gateway.default_backend` — 查看配置是否强制指定了 claude |
| **P0** | 切换到 DeepSeek | CLI | `zmai auth switch deepseek` — 或设置 `DEEPSEEK_API_KEY` 并取消设置 `ANTHROPIC_API_KEY` |
| **P1** | api_key 应通过 config 传递 | `runtime.py:364` | 注册时将 `api_key` 加入 config：`config={"model": model, "api_key": api_key}` |
| **P1** | DeepSeek 优先 | `runtime.py:368-382` | 将 DeepSeek 放在 BACKEND_METADATA 首位，或按配置优先级而非 dict 顺序 |
| **P2** | 添加密钥验证 | `zmai doctor` | doctor 命令增加 API Key 连通性验证 |
| **P2** | 401 不应重试 | `claude.py:93-94` | 401 认证错误应直接抛出，不应重试（当前已正确处理） |

### 6.1 紧急恢复步骤

```bash
# 1. 确认当前选中的 Backend
zmai config get gateway.default_backend

# 2. 查看已注册的 Backend
zmai auth list

# 3. 如果 ANTHROPIC_API_KEY 错误，取消设置使 DeepSeek 优先
unset ANTHROPIC_API_KEY

# 4. 或者显式指定使用 DeepSeek
zmai --backend deepseek "你的任务"
```

---

## 七、附录：调用链完整代码路径

| 步骤 | 文件:行 | 关键代码 |
|------|---------|---------|
| 凭证注入 | `main.py:191-206` | `_inject_auth_credentials()` |
| Backend 注册 | `runtime.py:344-366` | `_register_available_backends()` |
| 默认选择 | `runtime.py:368-382` | `_auto_select_default_backend()` |
| Backend 获取 | `runtime.py:121` | `backend_inst = self._gateway.get(backend)` |
| Backend 实例化 | `registry.py:87-90` | `cls(config=config)` |
| API Key 读取 | `claude.py:51-54` | `self._api_key = config.get("api_key", os.environ.get(...))` |
| HTTP 请求 | `claude.py:243-252` | `header: "x-api-key": self._api_key` |
| 错误处理 | `claude.py:257-262` | `BackendError(f"...HTTP {e.code}: {error_body}")` |

---

*Diagnosis by `claude` — 基于静态代码分析，未修改代码*
