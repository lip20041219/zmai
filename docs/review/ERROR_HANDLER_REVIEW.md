# Error Handler Review

**Date**: 2026-07-17  
**Goal**: HTTP 401 等可预测错误应在发送请求前拦截，用户永远不应看到原始 API 错误。

---

## 1. 当前错误传播链

```
Backend.invoke()
  │
  │  urllib.request.urlopen()
  │    ↓ HTTP 401
  │  urllib.error.HTTPError
  │
  │  e.read() → 原始 API 响应体
  │    {"type":"error","error":{"type":"authentication_error",
  │     "message":"invalid x-api-key"},
  │     "request_id":"req_011Cd7..."}
  │
  ▼
BackendError("claude API HTTP 401: {原始响应体}")
  │  status_code=401
  │
  ▼
SWEAgent.step()
  │
  │  except Exception as e:
  │    return AgentAction.fail(str(e))
  │    → 原始消息被传递
  │
  ▼
Runtime._run_agent()
  │
  │  action.type == "fail" and action.error
  │    raise RuntimeError(action.error)
  │
  ▼
CLI main()
  │
  │  print_error(err)
  │
  ▼
用户看到:
  [BACKEND_ERROR] claude API HTTP 401: {"type":"error",
    "error":{"type":"authentication_error",
    "message":"invalid x-api-key"},
    "request_id":"req_011Cd7oepHSwjL3WuMsJ2LcL"}
```

### 问题

| 问题 | 示例 | 严重性 |
|---|---|---|
| HTTP 状态码暴露 | `HTTP 401` | 🔴 用户不需要知道 HTTP |
| API 错误结构暴露 | `"error":{"type":"authentication_error"}` | 🔴 原始 JSON |
| Request ID 暴露 | `"request_id":"req_011Cd7..."` | 🔴 调试信息 |
| Key 名称不显示 | 没有提示 `ANTHROPIC_API_KEY` | 🔴 无操作指引 |
| 备选方案不显示 | 没有提示 `--backend deepseek` | 🟡 无恢复路径 |

---

## 2. 错误分类

### 2a. 可预测错误（应在发送请求前捕获）

| HTTP 状态码 | 含义 | 拦截时机 | 转换后的消息 |
|---|---|---|---|
| `401` | API Key 无效/未设置 | Preflight Check + invoke 时捕获 | `{Backend} 未配置：{ENV_KEY} 无效或未设置` |
| `403` | API Key 无权限 | invoke 时捕获 | `{Backend} 无权限，请检查 API Key 权限` |
| `404` | 模型不存在 | invoke 时捕获 | `模型 {model} 不存在，请检查 model 配置` |
| `429` | 速率限制 | invoke 时捕获 | `{Backend} 请求过于频繁，请稍后重试` |
| `400` | 请求参数错误 | invoke 时捕获 | `{Backend} 请求参数错误：{精简原因}` |
| `500` | 服务端错误 | invoke 时捕获 | `{Backend} 服务暂时不可用，请稍后重试` |
| 网络错误 | DNS/连接失败 | invoke 时捕获 | `{Backend} 网络连接失败，请检查网络和 base_url` |

### 2b. 不可预测错误（透传但脱敏）

- 非标准 HTTP 状态码 → 通用 `{Backend} 调用失败，请稍后重试`
- JSON 解析错误 → `{Backend} 响应格式异常`
- 超时 → `{Backend} 请求超时`

---

## 3. 设计：统一错误映射

### 3a. ErrorMapper

每个 Backend 内部将 HTTP 错误映射为友好消息，**不将原始响应体传递出去**。

```python
# 当前（错误）：
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    raise BackendError(
        f"{self.name} API HTTP {e.code}: {error_body}",  # ← 原始 JSON + request_id
        status_code=e.code,
    )

# 目标（正确）：
except urllib.error.HTTPError as e:
    status = e.code
    friendly = _map_http_error(status, self.name, self._model)
    raise BackendError(friendly, status_code=status)
```

### 3b. 映射函数

```python
def _map_http_error(status: int, backend_name: str, model: str = "") -> str:
    """将 HTTP 状态码映射为友好的错误消息。

    Args:
        status: HTTP 状态码。
        backend_name: Backend 名称（如 "claude"）。
        model: 模型名称（如 "claude-sonnet-4-6"）。

    Returns:
        用户可读的错误消息，不含 HTTP 细节。
    """
    env_key = f"{backend_name.upper()}_API_KEY"

    mapping = {
        401: (
            f"{backend_name.title()} 未配置：{env_key} 无效或未设置。\n"
            f"请执行：zmai auth update {backend_name}"
        ),
        403: (
            f"{backend_name.title()} API Key 无权限。\n"
            f"请检查 API Key 是否具有模型访问权限。"
        ),
        404: (
            f"模型 '{model}' 不存在。\n"
            f"请检查 model 配置是否正确。"
        ),
        429: (
            f"{backend_name.title()} 请求过于频繁。\n"
            f"请等待一段时间后重试。"
        ),
    }

    if status in mapping:
        return mapping[status]

    # 5xx — 服务端错误
    if 500 <= status < 600:
        return f"{backend_name.title()} 服务暂时不可用，请稍后重试。"

    # 400（不含 401/403/404/429）— 请求错误
    if 400 <= status < 500:
        return f"{backend_name.title()} 请求参数错误。"

    # 其他
    return f"{backend_name.title()} 调用失败（HTTP {status}）。"
```

### 3c. 各 Backend 修改点

| Backend | 文件 | 行 | 当前代码 | 修改为 |
|---|---|---|---|---|
| Claude | `claude.py` | 197-202 | `f"{self.name} API HTTP {e.code}: {error_body}"` | `_map_http_error(e.code, self.name, self._model)` |
| Claude (stream) | `claude.py` | 260-265 | 同上 | 同上 |
| DeepSeek | `deepseek.py` | 100-102 | `f"{self.name} API HTTP {e.code}: {err}"` | 同上 |
| Gemini | `gemini.py` | 88-91 | `f"Gemini API HTTP {e.code}: {err_body}"` | 同上 |

---

## 4. 错误消息输出规范

### 4a. 禁止输出的内容

```
❌ HTTP 状态码:    "HTTP 401"
❌ API 错误类型:   "authentication_error"
❌ 原始消息体:     "invalid x-api-key"
❌ Request ID:     "req_011Cd7oepHSwjL3WuMsJ2LcL"
❌ 完整 JSON:      '{"type":"error","error":{...}}'
```

### 4b. 应输出的内容

```
✅ Backend 名称:   "Claude 未配置"
✅ 环境变量名:     "ANTHROPIC_API_KEY"
✅ 操作指引:       "请执行：zmai auth update claude"
✅ 备选方案:       "或者：zmai --backend deepseek <task>"
```

### 4c. 输出格式示例

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Claude 未配置：ANTHROPIC_API_KEY 无效或未设置。

  请执行：
    zmai auth update claude

  或者切换到其他 Backend：
    zmai --backend deepseek <task>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 5. Preflight Check vs Backend Error Mapping

两者互补，覆盖不同阶段：

```
Preflight Check                  Backend Error Mapping
─────────────────                ─────────────────────
Key 不存在         → 拦截          Key 无效/过期     → 拦截
Key 为空字符串     → 拦截          Key 权限不足      → 拦截
Backend 未注册     → 拦截          模型不存在        → 拦截
Config 格式错误    → 拦截          速率限制          → 拦截
                                  服务端错误         → 拦截
                                  网络错误           → 拦截

不发送 HTTP 请求                   已发送 HTTP 请求（但用户看不到原始响应）
```

Preflight 拦截**可离线判断**的错误（Key 不存在）。
Backend Error Mapping 拦截**需要 API 交互后才知道**的错误（Key 无效、过期、权限不足）。

---

## 6. 实现建议

### 6a. 共享错误映射函数

将 `_map_http_error` 放在 `gateway/base.py` 或新建 `gateway/errors.py`，所有 Backend 共用。

```python
# zmai/gateway/errors.py

BACKEND_HTTP_ERROR_MAP: dict[int, str] = {
    401: "{name} 未配置：{env_key} 无效或未设置。\n请执行：zmai auth update {name}",
    403: "{name} API Key 无权限。\n请检查 API Key 是否具有模型访问权限。",
    404: "模型 '{model}' 不存在。\n请检查 model 配置是否正确。",
    429: "{name} 请求过于频繁，请等待后重试。",
}


def friendly_http_error(status: int, name: str, model: str = "") -> str:
    """将 HTTP 错误映射为友好消息，不泄露内部细节。"""
    if status in BACKEND_HTTP_ERROR_MAP:
        msg = BACKEND_HTTP_ERROR_MAP[status]
        return msg.format(name=name.title(), model=model, env_key=f"{name.upper()}_API_KEY")
    if 500 <= status < 600:
        return f"{name.title()} 服务暂时不可用，请稍后重试。"
    if 400 <= status < 500:
        return f"{name.title()} 请求参数错误。"
    return f"{name.title()} 调用失败（HTTP {status}）。"
```

### 6b. 修改各 Backend 的 HTTPError 处理

每个 Backend 中：

```python
# 改前
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    raise BackendError(
        f"{self.name} API HTTP {e.code}: {error_body}",
        status_code=e.code,
    )

# 改后
except urllib.error.HTTPError as e:
    raise BackendError(
        friendly_http_error(e.code, self.name, self._model),
        status_code=e.code,
    )
```

### 6c. 修改 propagate 路径

在 `swe/agent.py` 中，BackendError 已经包含友好消息，直接传递即可：

```python
# swe/agent.py:207-209 — 无需修改
except Exception as e:
    logger.error("Backend 调用失败: %s", e)
    return AgentAction.fail(str(e))
```

但日志仍会记录原始错误。建议在 Backend 中记录原始错误体到 debug 日志：

```python
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    logger.debug("%s API 原始错误 [%d]: %s", self.name, e.code, error_body)
    raise BackendError(
        friendly_http_error(e.code, self.name, self._model),
        status_code=e.code,
    )
```

这样原始信息保留在日志中用于调试，用户始终只看到友好消息。

---

## 7. 文件修改清单

| 文件 | 修改 |
|---|---|
| `src/zmai/gateway/errors.py` | **新增** — `friendly_http_error()` 函数 |
| `src/zmai/gateway/backends/claude.py` | HTTPError 处理 → 调用 `friendly_http_error()` |
| `src/zmai/gateway/backends/deepseek.py` | 同上 |
| `src/zmai/gateway/backends/gemini.py` | 同上 |

Runtime、SWE Agent、CLI — **无需修改**。

---

## 8. 验证

| 场景 | 预期行为 |
|---|---|
| `ANTHROPIC_API_KEY` 未设置 | Preflight 拦截，不发送请求 |
| `ANTHROPIC_API_KEY` 设置但过期 | Backend 返回 401 → 映射为 "Claude 未配置" |
| `ANTHROPIC_API_KEY` 设置但无权限 | Backend 返回 403 → 映射为 "Claude 无权限" |
| 模型名拼写错误 | Backend 返回 404 → 映射为 "模型不存在" |
| 频繁调用触发限流 | Backend 返回 429 → 映射为 "请求过于频繁" |
| 服务端故障 | Backend 返回 5xx → 映射为 "服务暂时不可用" |
