# API Key Management Review

**Date**: 2026-07-17  
**Goal**: Verify each Backend reads only its own API key; no cross-provider key leakage.

---

## 1. API Key Read Points — Full Map

Every location in the codebase that reads an API key:

| # | Location | Reads env var | Reads AuthStore | Passes to |
|---|---|---|---|---|
| 1 | `ClaudeBackend.__init__` | `ANTHROPIC_API_KEY` | — | internal `self._api_key` |
| 2 | `DeepSeekBackend.__init__` | `DEEPSEEK_API_KEY` | — | internal `self._api_key` |
| 3 | `Runtime._register_available_backends()` | `BACKEND_METADATA[name]["env_api_key"]` per backend | `AuthStore().get_backend(name)` per backend | `BackendRegistry.register(name, cls, config=cfg)` |
| 4 | `AuthStore.get_backend(name)` | `{name.upper()}_API_KEY` | `self._data["backends"][name]` | caller |
| 5 | `AuthStore._detect_env_backend()` | `BACKEND_METADATA[name]["env_api_key"]` per backend | — | returns name string |
| 6 | `CLI._inject_auth_credentials()` | `BACKEND_METADATA[name]["env_api_key"]` per backend | `AuthStore().get_backend(name)` per backend | sets `os.environ[env_key]` |
| 7 | `CLI._run_init_wizard()` | user input | `AuthStore().set_backend(name, key, ...)` | credentials file |
| 8 | `CLI._should_show_init_wizard()` | `BACKEND_METADATA[name]["env_api_key"]` per backend | — | boolean |
| 9 | `Runtime._auto_select_default_backend()` | `BACKEND_METADATA[name]["env_api_key"]` per backend | — | `set_default(name)` |

---

## 2. Per-Backend Key Isolation

### 2a. Direct reads in Backend constructors — ✅ Correct

| Backend | Reads | Scope |
|---|---|---|
| `ClaudeBackend.__init__` line 50-52 | `config.get("api_key")` then `os.environ.get("ANTHROPIC_API_KEY")` | Only its own key |
| `DeepSeekBackend.__init__` line 43 | `config.get("api_key")` then `os.environ.get("DEEPSEEK_API_KEY")` | Only its own key |

Each backend hardcodes exactly one env var name matching its provider. **No backend reads another provider's env var.**

### 2b. Config dict path — ✅ Correct

Runtime passes keys to backends through `config` dict (`runtime.py:392`):
```python
self._gateway.register(name, cls, config=cfg)
```

The `cfg` dict is built per-backend in a loop. For each iteration of `_register_available_backends()`:
1. Start with `BACKEND_DEFAULT_CONFIG[name]` — only that backend's defaults
2. Read `self._config.get(f"backends.{name}")` — only that backend's config section
3. Read `self._config.get(f"backends.{name}.api_key")` — only that backend's key
4. Read `AuthStore().get_backend(name)` — only that backend's AuthStore entry

**Each backend's `cfg` dict is built independently.** There is no code path where the key from one backend leaks into another backend's config.

### 2c. AuthStore path — ✅ Correct (stored data)

When `AuthStore.get_backend(name)` falls through to the stored credentials:
```python
return self._data.get("backends", {}).get(name)
```

This returns `backends["claude"]` for `name="claude"`, `backends["deepseek"]` for `name="deepseek"`. **Keys are stored per-backend and retrieved per-backend. No cross-contamination.**

---

## 3. Issue Found: Env Var Naming Mismatch in `AuthStore.get_backend()`

**File**: `auth/store.py:134-135`

```python
prefix = name.upper()
env_key = os.environ.get(f"{prefix}_API_KEY")
```

For `name="claude"`, this reads `CLAUDE_API_KEY`. But:

| Backend | `name` | AuthStore checks | Backend actually reads |
|---|---|---|---|
| Claude | `"claude"` | `CLAUDE_API_KEY` | `ANTHROPIC_API_KEY` |
| DeepSeek | `"deepseek"` | `DEEPSEEK_API_KEY` | `DEEPSEEK_API_KEY` |

### Impact

This is an **env var naming inconsistency**, NOT a cross-provider leakage:

| Scenario | AuthStore behavior | Actual effect |
|---|---|---|
| User sets `ANTHROPIC_API_KEY` only | `get_backend("claude")` → no `CLAUDE_API_KEY` → falls through to stored credentials | `from_env=False`, still correct key |
| User sets `CLAUDE_API_KEY` only | `get_backend("claude")` → finds `CLAUDE_API_KEY` → returns it with `from_env=True` | Key is correct (Claude's key), but `ClaudeBackend.__init__` won't find it because it reads `ANTHROPIC_API_KEY` |
| User sets both `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` | `get_backend("claude")` → finds `CLAUDE_API_KEY` → returns it | Both work independently |
| User sets `DEEPSEEK_API_KEY` | `get_backend("deepseek")` → finds `DEEPSEEK_API_KEY` → returns it | ✅ Correct, both naming conventions agree |

### Root Cause

`AuthStore.get_backend()` uses `{NAME}_API_KEY` convention (dynamically computed from the `name` parameter), while `ClaudeBackend` directly reads `ANTHROPIC_API_KEY` (hardcoded). The two naming conventions diverge for Claude because the provider name ("anthropic") differs from the backend name ("claude").

The `BACKEND_METADATA` dict correctly maps `"claude" → "env_api_key": "ANTHROPIC_API_KEY"`, but `AuthStore.get_backend()` doesn't consult `BACKEND_METADATA` for the env var name — it derives it algorithmically.

### All Callers of `AuthStore.get_backend()`

| Caller | Passes `name` | Gets correct key? |
|---|---|---|
| `Runtime._register_available_backends()` line 379 | `name` from `BACKEND_METADATA` iteration | ✅ Falls through to stored credentials correctly |
| `CLI._inject_auth_credentials()` line 234 | `name` from `BACKEND_METADATA` iteration | ✅ Falls through to stored credentials (checks `from_env` and skips if True anyway) |
| `AuthStore.get_active_backend()` → `get_backend(active)` | `active` from stored `active_backend` | ✅ Falls through to stored credentials |

No caller of `AuthStore.get_backend()` **depends** on the `{NAME}_API_KEY` env var check for correctness. All callers treat the env var result as an override and gracefully fall through to the stored credentials.

---

## 4. Backend Registration — Key Routing Diagram

```
BACKEND_METADATA
  ├── "claude" → env_api_key="ANTHROPIC_API_KEY"
  ├── "deepseek" → env_api_key="DEEPSEEK_API_KEY"
  │
  ▼
Runtime._register_available_backends()
  │
  ├── for "claude":
  │     ├── os.environ.get("ANTHROPIC_API_KEY")    ← ONLY Claude's env var
  │     ├── AuthStore().get_backend("claude")        ← ONLY Claude's store entry
  │     └── cfg["api_key"] → BackendRegistry("claude")
  │
  └── for "deepseek":
        ├── os.environ.get("DEEPSEEK_API_KEY")      ← ONLY DeepSeek's env var
        ├── AuthStore().get_backend("deepseek")      ← ONLY DeepSeek's store entry
        └── cfg["api_key"] → BackendRegistry("deepseek")
  │
  ▼
BackendRegistry.get("claude")
  │
  └── ClaudeBackend(config=cfg)
        └── cfg.get("api_key") ?? os.environ.get("ANTHROPIC_API_KEY")
              ↑ reads config first, then its own env var as safety net
```

**Each step in the chain is keyed by backend name. No step mixes keys across names.**

---

## 5. Cross-Provider Contamination Scenarios

### Scenario A: ClaudeBackend reads DeepSeek's key
**Path**: None. `ClaudeBackend.__init__` only reads `ANTHROPIC_API_KEY` or `config["api_key"]` (which was built for "claude"). It never touches `DEEPSEEK_API_KEY`.
**Verdict**: ❌ Impossible

### Scenario B: DeepSeekBackend reads Claude's key
**Path**: None. `DeepSeekBackend.__init__` only reads `DEEPSEEK_API_KEY` or `config["api_key"]` (which was built for "deepseek").
**Verdict**: ❌ Impossible

### Scenario C: Runtime passes Claude's key to DeepSeekBackend
**Path**: Impossible. The `_register_available_backends()` loop builds a fresh `cfg` dict per backend name. The `cfg` for "deepseek" is populated from `BACKEND_DEFAULT_CONFIG["deepseek"]` and `AuthStore().get_backend("deepseek")`.
**Verdict**: ❌ Impossible

### Scenario D: AuthStore returns wrong key for a backend name
**Path**: `get_backend("claude")` → returns `backends["claude"]["api_key"]` from stored credentials, or `CLAUDE_API_KEY` env var. Neither comes from DeepSeek's entry.
**Verdict**: ❌ Impossible

### Scenario E: Shared env var between providers
**Path**: No env var name is shared between `ANTHROPIC_API_KEY` and `DEEPSEEK_API_KEY`. They are distinct strings.
**Verdict**: ❌ Impossible

---

## 6. Summary

| Requirement | Status | Evidence |
|---|---|---|
| Claude reads `ANTHROPIC_API_KEY` | ✅ | `claude.py:51` — `os.environ.get("ANTHROPIC_API_KEY", "")` |
| DeepSeek reads `DEEPSEEK_API_KEY` | ✅ | `deepseek.py:43` — `os.environ.get("DEEPSEEK_API_KEY", "")` |
| No shared key env var | ✅ | Each backend hardcodes a distinct env var name |
| Runtime doesn't mix keys | ✅ | Per-backend config dict built independently per loop iteration |
| AuthStore doesn't mix keys | ✅ | Keyed by `name` in `self._data["backends"][name]` |

### One Issue

| Issue | Severity | File | Line |
|---|---|---|---|
| `AuthStore.get_backend("claude")` checks `CLAUDE_API_KEY` instead of `ANTHROPIC_API_KEY` | 🟡 Low | `auth/store.py` | 134-135 |

This is an env var **naming mismatch**, not a cross-provider leakage:
- User sets `ANTHROPIC_API_KEY` → `AuthStore.get_backend("claude")` won't detect it via env var → falls through to credentials → still correct
- User sets `CLAUDE_API_KEY` → `AuthStore.get_backend("claude")` detects it → but `ClaudeBackend.__init__` won't find it (reads `ANTHROPIC_API_KEY`) → config flow from Runtime still works because Runtime reads `ANTHROPIC_API_KEY` directly

**The mismatch has no practical impact on correct operation** because:
1. Runtime's `_register_available_backends()` reads `ANTHROPIC_API_KEY` directly (via `BACKEND_METADATA`), not through `AuthStore.get_backend()`'s env var check
2. AuthStore's stored credentials path (`backends["claude"]["api_key"]`) is always correct regardless of env var names
3. No cross-provider contamination is possible through this path

### Conclusion

**✅ All backends read only their own API keys. No cross-provider leakage exists.**

The env var naming mismatch in `AuthStore.get_backend()` (using `{NAME}_API_KEY` convention) is a **cosmetic inconsistency** that doesn't cause key leakage. Fixing it would mean making `AuthStore.get_backend()` consult `BACKEND_METADATA` for the correct env var name per backend, rather than computing it algorithmically.
