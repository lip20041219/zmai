# ZMAI Architecture Review

> Principal AI Agent Architect review of all modules.
>
> No new features. No refactoring. Minimal fixes only.

---

## P0 — Must Fix

### P0.1 MemoryManager.restore() is a no-op

**File:** `src/zmai/memory/manager.py:49-56`

```python
def restore(self, agent_id: str) -> None:
    lm = self.long_term(agent_id)
    wm = self.working(agent_id)
    for ns in lm.list_namespaces():
        pass  # <-- does nothing
```

**Impact:** Memory claims to support "restore" but doesn't. Every session starts empty regardless of past work.

**Fix:** Implement the loop body — read entries from LongTermMemory, write to WorkingMemory.

```python
def restore(self, agent_id: str) -> int:
    lm = self.long_term(agent_id)
    wm = self.working(agent_id)
    count = 0
    for ns in lm.list_namespaces():
        entries = lm._load_namespace(ns)
        for key, entry in entries.items():
            wm.store(key, entry.value, ns)
            count += 1
    return count
```

---

### P0.2 LifecycleManager blocks REPL reuse; Runtime never calls `remove`

**File:** `src/zmai/runtime/lifecycle.py:38-39`, `src/zmai/runtime/runtime.py:118`

**Impact:** REPL creates a new agent_id per task (`repl_pid_N`), so this is currently worked around. But `Runtime.run()` never calls `self._lifecycle.remove(agent_id)`, meaning lifecycle state leaks across the process lifetime. If any code reuses an agent_id, it fails.

**Fix:** In `Runtime.run()` finalize/error handlers, add `self._lifecycle.remove(agent_id)` after completion/failure so the state is cleaned up. This is a one-line addition and makes agent_id reuse safe.

```python
# After line 153 or 161:
finally:
    self._lifecycle.remove(agent_id)
```

---

## P1 — Should Fix

### P1.1 ClaudeBackend always registered unconditionally

**File:** `src/zmai/runtime/runtime.py:96-97`

```python
self._gateway.register("claude", ClaudeBackend,
                       config=self._config.get("gateway.backends.claude", {}))
```

**Impact:** ClaudeBackend is always registered even if the user doesn't have an Anthropic API key. This means `_auto_select_default_backend()` can select "claude" as default even when it can never work.

**Fix:** Only register ClaudeBackend if `ANTHROPIC_API_KEY` is set, similar to how DeepSeek is registered in `_register_available_backends()`. This is a 10-line change.

---

### P1.2 Two system prompt builders with different identity formats

**File:** `src/zmai/runtime/runtime.py:37-78`, `src/zmai/swe/agent.py:133-149`

Both files have `_build_system_prompt()` but with different identity formats:

| Builder | Identity format |
|---------|----------------|
| `runtime.py:42` | `"你运行在 {name} (模型: {model})上。当前操作系统: {platform}。"` |
| `swe/agent.py:142` | `"你运行在 {provider.upper()} Backend 上。当前模型: {model}。"` |

The `runtime.py` version is only used in `_execute_task()` (which is never called by the main execution path). This dead code creates confusion.

**Fix:** Remove `_build_system_prompt()` from `runtime.py` entirely. The `swe/agent.py` version is the one that actually runs. Dead code removal only.

---

### P1.3 ToolRouter creates ToolContext without project_path

**File:** `src/zmai/gateway/tool_router.py:58-65`

```python
exec_context = ToolContext(
    agent_id=context.agent_id,
    workspace_path=context.workspace_path,
    config={**context.config, ...},
    timeout=effective_timeout,
    # project_path NOT passed
)
```

**Impact:** Tools executed through ToolRouter (`_execute_task` path) lose the project path context. `WriteFileTool` falls back to workspace sandbox for path resolution.

The SWE Agent creates ToolContext correctly (with `project_path=context.config.get("project_path")`) but ToolRouter creates its own copy without it.

**Fix:** Add `project_path` to the new ToolContext in ToolRouter:

```python
exec_context = ToolContext(
    ...,
    project_path=context.project_path,
)
```

---

### P1.4 PromptEngine.render_system() defaults `backend_name` to `"claude"`

**File:** `src/zmai/prompt/engine.py:163`

```python
def render_system(
    self,
    agent_name: str = "ZMAI Agent",
    ...
    backend_name: str = "claude",  # <-- hardcoded
    ...
) -> str:
```

**Impact:** If any code calls `render_system()` without passing `backend_name`, the prompt says "Backend: claude" even if DeepSeek is active.

**Fix:** Change default to empty string `""`. The template outputs nothing for empty backend_name.

---

### P1.5 Auto-register only checks DEEPSEEK_API_KEY

**File:** `src/zmai/runtime/runtime.py:299-309`

```python
def _register_available_backends(self) -> None:
    if _os.environ.get("DEEPSEEK_API_KEY"):
        self._gateway.register("deepseek", DeepSeekBackend, ...)
    # No check for ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
```

**Impact:** Users with `ANTHROPIC_API_KEY` set don't get Claude auto-registered. They need zmai.json config.

**Fix:** Add checks for `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` in the same function.

---

### P1.6 `_inject_auth_credentials()` mutates global os.environ

**File:** `src/zmai/cli/main.py` (search for `os.environ[env_key]`)

```python
os.environ[env_key] = info["api_key"]
```

**Impact:** API keys are written to global process environment. Any subprocess spawned by any library inherits these keys. This is a leak.

**Fix:** Instead of setting os.environ, pass API keys directly to Backend constructors through Runtime. This requires Runtime to accept credentials at construction or `run()` time.

---

### P1.7 MemoryManager default path is relative `./zmai_memory`

**File:** `src/zmai/memory/manager.py:19`

```python
long_term_root: str | Path = "./zmai_memory",
```

**Impact:** Memory persists to CWD-relative path. If user runs zmai from different directories, they get different memory stores. Memory is supposed to be global (`~/.zmai/memory/`).

**Fix:** Change default to `Path.home() / ".zmai" / "memory"`.

---

## P2 — Should Fix Eventually

### P2.1 PromptEngine.render_system() takes string not Backend object

**File:** `src/zmai/prompt/engine.py:160-187`

`render_system()` accepts `backend_name: str` but not the full Backend object. This means `model`, `provider` are lost. Only the name string is passed.

**Fix:** Add optional `backend: Backend | None = None` parameter. If provided, extract `name`, `model`, `provider` and inject all three into the template variables.

---

### P2.2 BackendRegistry caches instances but set_default doesn't invalidate

**File:** `src/zmai/gateway/registry.py:98-110`

```python
def set_default(self, name: str) -> None:
    if name not in self._backends:
        raise BackendError(...)
    self._default = name
    # instances cache NOT touched
```

If the user calls `set_default("deepseek")` after already having called `get("claude")`, the claude instance stays in the cache. Next `get()` returns the correct backend because it uses `self._default`, but stale instances accumulate.

**Fix:** Clear `self._instances` when `set_default` is called, or don't cache instances at all (create fresh each time).

---

### P2.3 Version number hardcoded in argparse, not from pyproject.toml

**File:** `src/zmai/cli/main.py` — `version="ZMAI v0.1.0"`

Version is duplicated between `pyproject.toml` and `main.py`. They will drift.

**Fix:** Read version from `importlib.metadata.version("zmai")` instead of hardcoding.

---

### P2.4 Session save uses bare `except: pass`

**File:** `src/zmai/cli/main.py:27-33`

```python
def _save_session(task: str) -> None:
    try:
        ...
    except Exception:
        pass  # silent failure
```

If session save fails (disk full, permissions), the error is swallowed silently. User loses session continuity without knowing.

**Fix:** Log the exception instead of ignoring it.

---

### P2.5 Workspace agent directories accumulate

**File:** `src/zmai/workspace/workspace.py:382-433`

Each `_repl_run` creates a new agent workspace with `prepare()`. The `cleanup()` at `runtime.py:154` keeps output but the agent directory tree persists. Over hundreds of REPL turns, this grows unbounded.

**Fix:** In Runtime.run() cleanup, call `self._workspace.remove(agent_id)` instead of `cleanup()` when the agent is in REPL mode. Or set a max age and auto-gc in prepare().

---

### P2.6 REPL `/status` uses `runtime.get_info()` which reads `runtime._gateway.default_name`

**File:** `src/zmai/cli/main.py` (REPL `/status` handler)

Accesses private attribute `runtime._gateway.default_name`. Breaks encapsulation (from the PRODUCT_REVIEW finding).

**Fix:** Add a public `default_backend` property to Runtime.

---

## Summary

| ID | Issue | Severity | Fix | Impact |
|----|-------|----------|-----|--------|
| P0.1 | `restore()` is no-op | **P0** | 3 lines | Memory doesn't restore |
| P0.2 | LifecycleManager state leaks | **P0** | 1 line | agent_id reuse unsafe |
| P1.1 | Claude always registered | P1 | 10 lines | Wrong default backend |
| P1.2 | Two system prompt builders | P1 | Delete lines | Dead code, confusion |
| P1.3 | ToolRouter drops project_path | P1 | 1 line | Tools lose project root |
| P1.4 | render_system defaults "claude" | P1 | 1 char | Wrong identity |
| P1.5 | Only DEEPSEEK_API_KEY auto-detected | P1 | 10 lines | Missing backends |
| P1.6 | os.environ key injection | P1 | Design change | Credential leak |
| P1.7 | Memory path relative to CWD | P1 | 1 line | Memory per-directory |
| P2.1 | render_system takes string not Backend | P2 | Add parameter | Missing model/provider |
| P2.2 | Backend instances cache stale | P2 | 1 line | Memory leak |
| P2.3 | Version hardcoded | P2 | 1 line | Version drift |
| P2.4 | Session save silent failure | P2 | 1 line | Hidden errors |
| P2.5 | Workspace directories accumulate | P2 | Design change | Disk growth |
| P2.6 | /status accesses private attr | P2 | Add property | Encapsulation |

### Total fix estimate

- **P0:** 2 items, ~10 minutes each
- **P1:** 7 items, ~5-15 minutes each (~1 hour total)
- **P2:** 6 items, ~5-10 minutes each (~30 minutes total)

All fixes are minimal, localized, and don't change the architecture.
