# CLI Error Handling Review

**Date**: 2026-07-17  
**Goal**: 所有 Backend 配置错误在到达用户之前转换为可读信息；禁止出现 HTTP 401、invalid x-api-key、request_id。

---

## 1. 当前错误传播链

```
Backend.invoke()
  │  urllib.request.urlopen()
  │    ↓ HTTP 401
  │  urllib.error.HTTPError
  │  e.read() → {"type":"error",
  │               "error":{"type":"authentication_error",
  │                        "message":"invalid x-api-key"},
  │               "request_id":"req_011Cd7..."}
  │
  ▼
BackendError("claude API HTTP 401: {原始 JSON}")   ← 🔴 HTTP 状态码 + 原始 JSON
  │                                                   ← 🔴 invalid x-api-key
  │                                                   ← 🔴 request_id
  ▼
SWEAgent.step()
  │  except Exception as e:
  │    return AgentAction.fail(str(e))               ← 原样传递
  ▼
Runtime._run_agent()
  │  if action.type == "fail":
  │    raise RuntimeError(action.error)              ← 原样传递
  │  except Exception as e:
  │    return {"status": "failed", "error": str(e)}  ← 原样传递
  ▼
CLI _oneshot_run() / _repl_run()
  │  err = result.get("error", "")
  │  print_error(err, theme)                         ← 🔴 直接输出到终端
  ▼
用户看到:
  ✗ [BACKEND_ERROR] claude API HTTP 401: {"type":"error",
      "error":{"type":"authentication_error",
      "message":"invalid x-api-key"},
      "request_id":"req_011Cd7..."}
```

### Preflight 路径（双重输出）

```
Preflight.check()
  │  pf.print() ──────────────→ 友好消息输出到 stderr ✓
  │  return {"error": pf.reason}  ← "API_KEY_MISSING"
  ▼
CLI print_error("API_KEY_MISSING")  ← 🔴 原始 code 又输出一次
  ▼
用户同时看到:
  Preflight Check 失败         (来自 pf.print())
  ✗ API_KEY_MISSING            (来自 print_error)
```

---

## 2. 违反项清单

| 要求 | 当前行为 | 违反位置 | 严重度 |
|---|---|---|---|
| 不出现 HTTP 401 | `f"API HTTP {e.code}"` | 各 Backend invoke() | 🔴 |
| 不出现 error_body | `f"{error_body}"` 含完整 JSON | 各 Backend invoke() | 🔴 |
| 不出现 request_id | JSON 中包含 request_id 字段 | 各 Backend invoke() | 🔴 |
| 不出现 invalid x-api-key | JSON 中包含该消息 | 各 Backend invoke() | 🔴 |
| 错误应转换 | 直接透传原始字符串 | SWEAgent → Runtime → CLI | 🔴 |
| Preflight 无双重输出 | pf.print() + print_error() | CLI main.py | 🟡 |

### 各 Backend 的违规格式

| Backend | 行 | 当前消息格式 | 问题 |
|---|---|---|---|
| `claude.py` | 200 | `f"claude API HTTP {e.code}: {error_body}"` | HTTP 401 + 原始 JSON |
| `claude.py` | 263 | (stream 路径) 同上 | 同上 |
| `deepseek.py` | 102 | `f"deepseek API HTTP {e.code}: {err}"` | HTTP 401 + 原始 JSON |
| `gemini.py` | 90 | `f"Gemini API HTTP {e.code}: {err_body}"` | HTTP 401 + 原始 JSON |
| `gemini.py` | 147 | (stream 路径) 同上 | 同上 |

---

## 3. 完整用户可见错误路径

### 路径 A: Backend.invoke() 返回 HTTP 错误

```
Backend.invoke()
  → BackendError("claude API HTTP 401: {...request_id...}")
  → str(e) = "claude API HTTP 401: {\"type\":\"error\",\"error\":{...}}"
  → AgentAction.fail("claude API HTTP 401: {...}")
  → RuntimeError("claude API HTTP 401: {...}")
  → {"status": "failed", "error": "claude API HTTP 401: {...}"}
  → print_error("claude API HTTP 401: {...}")
  → 用户看到原始 API 错误
```

### 路径 B: Preflight Check 失败

```
preflight.check("claude")
  → pf.print() → 友好消息 (✓)
  → {"status": "failed", "error": "API_KEY_MISSING"}
  → print_error("API_KEY_MISSING")
  → 用户看到原始 code (🔴 双重输出)
```

### 路径 C: BackendRegistry.get() 失败

```
gateway.get("nonexistent")
  → BackendError("Backend 未注册: nonexistent")
  → 此错误被 Runtime.run() 的 try/except 捕获
  → {"status": "failed", "error": "Backend 未注册: nonexistent"}
  → print_error("Backend 未注册: nonexistent")  ← 这是友好的 (✓)
```

---

## 4. 修复方案

### 4a. Backend 层 — 拦截 HTTP 错误

每个 Backend 的 `invoke()` 应将 HTTPError 映射为友好消息，不传递原始响应体。

**当前（claude.py:197-202）**:
```python
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    raise BackendError(
        f"{self.name} API HTTP {e.code}: {error_body}",
        status_code=e.code,
    )
```

**目标**:
```python
except urllib.error.HTTPError as e:
    friendly = _map_http_error(e.code, self.name, self._model)
    raise BackendError(friendly, status_code=e.code)
```

映射函数 `_map_http_error()`:

| HTTP 状态码 | 用户看到的消息 |
|---|---|
| 401 | `Claude 未配置：ANTHROPIC_API_KEY 无效或未设置。请执行 zmai auth update claude` |
| 403 | `Claude API Key 无权限。请检查 API Key 权限` |
| 404 | `模型 claude-sonnet-4-6 不存在。请检查 model 配置` |
| 429 | `Claude 请求过于频繁，请稍后重试` |
| 5xx | `Claude 服务暂时不可用，请稍后重试` |
| 其他 | `Claude 调用失败，请稍后重试` |

### 4b. CLI 层 — 拦截透传错误

在 `_oneshot_run()` 和 `_repl_run()` 中，对 `result.get("error")` 做二次脱敏：

```python
err = result.get("error", "")
if err and ("HTTP" in err or "BACKEND_ERROR" in err):
    err = f"{backend_name} 调用失败，请检查配置。"
```

### 4c. Preflight — 消除双重输出

Preflight 已通过 `pf.print()` 输出友好消息，不应再让 `print_error` 输出原始 code。

在 `Runtime.run()` 中，当 preflight 失败时直接返回无 error 的 dict：

```python
if not pf.passed:
    pf.print()
    return {"status": "failed"}  # ← 不传 error，避免 print_error 再输出一次
```

---

## 5. 各层修复优先级

| 层 | 修复 | 优先级 | 影响范围 |
|---|---|---|---|
| Backend | `HTTPError` → `_map_http_error()` | P0 | 所有 HTTP 错误 |
| CLI | error 字符串二次脱敏 | P1 | `print_error` 兜底 |
| Runtime | Preflight 不传 error | P1 | 消除双重输出 |

---

## 6. 文件修改清单

| 文件 | 修改 |
|---|---|
| `gateway/backends/claude.py` | HTTPError 处理 → `_map_http_error()` |
| `gateway/backends/deepseek.py` | HTTPError 处理 → `_map_http_error()` |
| `gateway/backends/gemini.py` | HTTPError 处理 → `_map_http_error()` |
| `gateway/errors.py` | **新增** `_map_http_error()` 共享函数 |
| `runtime/runtime.py` | Preflight 失败时不传 error 字段 |
| `cli/main.py` | `print_error` 前做二次脱敏 |

---

## 7. 验证方法

| 场景 | 用户看到 | 不应看到 |
|---|---|---|
| `ANTHROPIC_API_KEY` 过期 | `Claude 未配置：ANTHROPIC_API_KEY 无效` | `HTTP 401` / `invalid x-api-key` / `request_id` |
| `DEEPSEEK_API_KEY` 未设置 | Preflight: 检测到缺失，提示配置 | `BACKEND_ERROR` / `HTTP` |
| 模型名错误 | `模型 xxx 不存在` | `404` / `not found` |
| 无任何 Backend | Preflight: 未发现可用模型 | `BACKEND_ERROR` |
