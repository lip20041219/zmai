# Backend Routing Review

**Date**: 2026-07-17  
**Goal**: Verify that once Runtime selects a backend, it **never automatically switches** to a different backend.

---

## Routing Architecture Overview

```
CLI / REPL
  │
  │  backend=<name> | None
  ▼
Runtime.run(agent_id, task, backend, ...)
  │
  │  self._gateway.get(backend)        ← single resolution point
  ▼
backend_inst: Backend
  │
  │  captured by closure _run_agent()
  ▼
ctx = AgentContext(backend=backend_inst)   ← frozen into context
  │
  │  passed to SWEAgent
  ▼
SWEAgent.step()
  │
  │  context.backend.invoke(request)       ← no re-resolution
  ▼
API call (always same instance)
```

**There is only one resolution point**: `Runtime._gateway.get(backend)` at `runtime.py:121`.

---

## 1. Resolution Point — `BackendRegistry.get()`

**File**: `gateway/registry.py:66-92`

```python
def get(self, name: str | None = None) -> Backend:
    resolved = name or self._default
    if resolved is None:
        raise BackendError("未设置默认 Backend，且未指定 Backend 名称")
    if resolved not in self._backends:
        raise BackendError(f"Backend 未注册: {resolved}")
    if resolved not in self._instances:
        cls = self._backends[resolved]
        config = self._configs.get(resolved, {})
        self._instances[resolved] = cls(config=config)
    return self._instances[resolved]
```

### Behavior

| `name` parameter | result |
|---|---|
| `"deepseek"` | resolves to `self._backends["deepseek"]` → `DeepSeekBackend` |
| `"claude"` | resolves to `self._backends["claude"]` → `ClaudeBackend` |
| `None` | falls back to `self._default` |

### ✅ No auto-switch

- If a specific `name` is given, it is used **exactly** — no fallback to default, no "best match".
- If the name is not registered, a `BackendError` is raised — no silent redirect.
- Instances are **cached** (`self._instances`); the same instance is returned on every subsequent `get()` call for that name.

---

## 2. Entry Points — Two Callers of `Runtime.run()`

### 2a. `_oneshot_run()` — `--backend` flag (CLI mode)

**File**: `cli/main.py:303-350`

```python
async def run(
    agent_id=f"agent_{os.getpid()}",
    task=task,
    backend=args.backend if getattr(args, "backend", None) else None,
    ...
)
```

- If user passes `--backend deepseek`, `args.backend = "deepseek"`.
- This is passed **directly** to `Runtime.run(backend="deepseek")`.
- `BackendRegistry.get("deepseek")` resolves exactly to `DeepSeekBackend`.
- **No ambiguity. No default fallback.**

### 2b. `_repl_run()` — REPL mode

**File**: `cli/main.py:261`

```python
async def run(
    agent_id=...,
    task=task,
    backend=None,    # always None
    ...
)
```

- REPL mode **always** passes `backend=None`.
- `BackendRegistry.get(None)` → `resolved = self._default`.
- The default is set once during `Runtime.__init__()` and never changes.
- **Result**: every REPL agent uses the same default backend. No mid-session switch.

---

## 3. Runtime Initialization — Default Selection

Two private methods run during `Runtime.__init__()`:

### 3a. `_register_available_backends()` (`runtime.py:344-366`)

- Iterates `BACKEND_METADATA`, checks credentials, registers matching backends.
- First registered backend becomes `self._default` (due to `register()` — `self._default is None`).

### 3b. `_auto_select_default_backend()` (`runtime.py:368-382`)

- **Overrides** the default based on priority:
  1. `zmai.json` → `gateway.default_backend` (if not `"auto"`)
  2. First backend with env var credential (in `BACKEND_METADATA` order)
  3. First registered backend (fallback)

### ✅ Both run **once** in `Runtime.__init__()`

Neither is called during or after agent execution. The `_default` value is frozen after initialization.

---

## 4. Agent Execution — No Re-resolution

### `Runtime.run()` (`runtime.py:105-186`)

```
Line 121: backend_inst = self._gateway.get(backend)   ← single resolution
Line 131: ctx = AgentContext(backend=backend_inst)      ← frozen
```

- `backend_inst` is captured by the `_run_agent()` closure.
- It is stored in `AgentContext.backend`.
- The `AgentContext` is passed to `SWEAgent.initialize()`, `.step()`, `.finalize()`.

### `SWEAgent.step()` (`swe/agent.py:171-247`)

```python
if not context.backend:
    return AgentAction.fail("无可用 Backend，请配置 API Key")
request = BackendRequest(...)
response = context.backend.invoke(request)    # ← always the same instance
```

- **No call** to `BackendRegistry.get()` anywhere in `SWEAgent`.
- **No call** to `Runtime._gateway.get()` anywhere in `step()`.
- Every step uses `context.backend` — the instance set once at agent creation.
- On failure, returns `AgentAction.fail()` — **no fallback**, no retry with another backend.

### `Runtime._execute_task()` (`runtime.py:188-266`)

- This method is **dead code** — defined but never called from anywhere.
- Even if it were called, it receives `backend: Backend` (an instance, not a name) and uses it consistently with no re-resolution.

---

## 5. Failure Handling — No Auto-Switch

| Layer | On failure | Switches backend? |
|---|---|---|
| `runtime.run()` → `_run_agent()` | `raise RuntimeError(action.error)` | ❌ No — propagation only |
| `SWEAgent.step()` | `return AgentAction.fail(str(e))` | ❌ No — returns error |
| `Runtime._execute_task()` (dead code) | `return {"status": "failed", ...}` | ❌ No — returns error |

**No "fallback to another backend" logic exists anywhere in the codebase.**

---

## 6. Cache Stability — `BackendRegistry._instances`

```python
self._instances: dict[str, Backend] = {}
```

- Instances are cached by name.
- A cached instance is **only invalidated** when `register()` is called again for the same name (line 58-59):
  ```python
  if name in self._instances:
      del self._instances[name]
  ```
- `register()` is only called from `_register_available_backends()` during `__init__()`.
- **Never called during agent execution.**

---

## 7. Summary of All Code Paths That Resolve a Backend

| Location | Code | When | Can switch mid-execution? |
|---|---|---|---|
| `runtime.py:121` | `self._gateway.get(backend)` | Once per `run()` call | N/A — entry point |
| `runtime.py:364` | `self._gateway.register(...)` | `__init__()` only | ❌ Never |
| `runtime.py:371,378,382` | `self._gateway.set_default(...)` | `__init__()` only | ❌ Never |
| `doctor.py:179` | `reg.register(...)` | `zmai doctor` CLI only | ❌ Not in Runtime process |
| `auth/store.py:137` | `set_backend(...)` | `zmai auth` CLI only | ❌ Not in Runtime process |

---

## 8. Edge Cases Analysis

### 8a. Concurrent agents requesting different backends

```
Runtime._gateway is shared, but:
  Agent A requests backend="claude"  → cached ClaudeBackend at _instances["claude"]
  Agent B requests backend="deepseek" → cached DeepSeekBackend at _instances["deepseek"]
```

Each agent gets the correct backend. **No cross-contamination.**

### 8b. AuthStore credential change mid-session

`zmai auth switch deepseek` modifies `~/.zmai/credentials` on disk.
- This affects **a new** `Runtime` process.
- The running `Runtime` already has its `BackendRegistry` initialized with frozen `_default` and cached instances.
- **No hot-reload mechanism** exists to pick up disk changes.

### 8c. REPL multiple tasks

Each REPL task calls `Runtime.run(backend=None)`:
- `BackendRegistry.get(None)` → `self._default`
- `self._default` never changes (set once in `__init__()`)
- All REPL tasks use the **same** default backend instance.

### 8d. Config reload

`Config.reload()` exists (`config/config.py:52`) but is **never called** by `Runtime`.
No mechanism triggers re-registration or default re-selection.

---

## Conclusion

**✅ Once Runtime selects a backend, it never automatically switches to another Backend.**

| Guarantee | Status |
|---|---|
| `backend=deepseek` → exactly `DeepSeekBackend` | ✅ Verified |
| `backend=claude` → exactly `ClaudeBackend` | ✅ Verified |
| `backend=None` → consistent default, never changes mid-session | ✅ Verified |
| No fallback/retry on failure to different backend | ✅ Verified |
| No background re-registration or re-selection | ✅ Verified |
| No mid-session config reload affecting backend | ✅ Verified |
| No hot-reload from credential store changes | ✅ Verified |
| `_execute_task()` is dead code, unused | ✅ Confirmed |

### One Weakness (Informational)

In **REPL mode** (`cli/main.py:261`), `backend=None` is **always** passed — there is no way for the user to specify a backend per-task in REPL. The default is frozen for the session. This is a **feature limitation** (no per-task backend choice in REPL), not a routing instability. It would not cause an automatic switch — only a consistent default.
