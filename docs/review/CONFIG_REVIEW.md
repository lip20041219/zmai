# Backend Config Review

**Date**: 2026-07-17  
**Goal**: Unify all Backend configuration into a single, consistent model across Project Config and Global Config.

---

## 1. Current Config Sources — Fragmentation Map

There are **7 distinct locations** where Backend configuration lives, spread across code, encrypted files, JSON files, and environment variables:

```
┌─────────────────────────────────────────────────────────┐
│                   7 CONFIG LOCATIONS                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  CODE             BACKEND_METADATA (backends/__init__)  │
│                  ───────────────────────────────         │
│                   claude.py (hardcoded defaults)         │
│                   deepseek.py (hardcoded defaults)       │
│                                                         │
│  PROJECT FILE     zmai.json                             │
│                                                         │
│  GLOBAL FILE      ~/.zmai/config.json  ← WRITTEN BUT    │
│                                        ← NEVER READ     │
│                   ~/.zmai/credentials  (encrypted)       │
│                                                         │
│  ENV              ANTHROPIC_API_KEY / ANTHROPIC_MODEL   │
│                   DEEPSEEK_API_KEY / DEEPSEEK_MODEL     │
│                   ZMAI_* (generic Config prefix)         │
│                                                         │
│  CLI ARGS         --backend <name>                       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 1a. `BACKEND_METADATA` (in code)

**File**: `gateway/backends/__init__.py:17-39`

```python
BACKEND_METADATA: dict[str, dict] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "default_model": "claude-sonnet-4-6",
        "env_api_key": "ANTHROPIC_API_KEY",
        "env_model": "ANTHROPIC_MODEL",
        "module": "zmai.gateway.backends.claude",
        "class": "ClaudeBackend",
        "verify_url": "https://api.anthropic.com/v1/messages",
        "verify_method": "POST",
        "verify_headers": {"anthropic-version": "2023-06-01"},
    },
    "deepseek": { ... },
}
```

- **Serves as**: registry of available backends + default model + env var names + verify config
- **Problem**: Hardcoded in source code. Fields like `verify_url`, `verify_method`, `verify_headers` are only needed by CLI init wizard, but live in the metadata dict.

### 1b. Backend implementation hardcoded defaults

**`gateway/backends/claude.py:30-33`**:
```python
CLAUDE_DEFAULT_MODEL = "claude-sonnet-4-6"
CLAUDE_API_BASE = "https://api.anthropic.com/v1"
CLAUDE_DEFAULT_MAX_TOKENS = 4096
CLAUDE_API_VERSION = "2023-06-01"
```

**`gateway/backends/deepseek.py:25-26`**:
```python
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
```

Each backend also reads config inside `__init__`:

| Backend | Config dict keys read | Env var fallbacks | Hardcoded fallbacks |
|---|---|---|---|
| `ClaudeBackend` | `api_key`, `model`, `base_url`, `max_retries`, `timeout` | `ANTHROPIC_API_KEY` | `CLAUDE_DEFAULT_MODEL`, `CLAUDE_API_BASE`, `CLAUDE_DEFAULT_MAX_TOKENS=4096`, timeout=300, max_retries=3 |
| `DeepSeekBackend` | `api_key`, `model`, `base_url`, `max_tokens` | `DEEPSEEK_API_KEY` | `DEEPSEEK_DEFAULT_MODEL`, `DEEPSEEK_API_BASE`, max_tokens=4096 |

**Problem**: No `temperature` support in either backend's `__init__` config. Each backend has **different fallback logic** — inconsistent.

### 1c. `zmai.json` (project-level config)

**File**: `zmai.json`

```json
{
    "runtime": { "max_iterations": 100, ... },
    "gateway": { "default_backend": "auto", "timeout": 300 },
    "workspace": { ... },
    "tool": { ... }
}
```

- **Stores**: Only `gateway.default_backend` and `gateway.timeout`
- **Does NOT store**: Any per-backend config (`model`, `api_key`, `base_url`, etc.)
- **Loaded by**: `Config` class via `FileSource("zmai.json")`
- **Problem**: `gateway.timeout` is never passed to Backend instances — it's an orphan config key.

### 1d. `~/.zmai/config.json` (global-level config)

**Created by CLI init wizard** (`cli/main.py:182-185`):
```python
cfg_path.write_text(json.dumps({
    "version": 1, "cli": {"theme": "dark", "trust_mode": True},
    "gateway": {"default_backend": name, "timeout": 300},
}, indent=2))
```

- **Stores**: CLI theme, trust mode, default backend name, gateway timeout
- **Never loaded by `Config`**: The `Config` class's `FileSource` only points at `zmai.json` in CWD (project root). `~/.zmai/config.json` is **written but never read** by any code.
- **No per-backend config**: Same as `zmai.json`, it stores no per-backend fields.

### 1e. `~/.zmai/credentials` (encrypted credential store)

**File**: `auth/store.py`

Data structure stored:
```json
{
    "version": 1,
    "active_backend": "claude",
    "backends": {
        "claude": {
            "api_key": "sk-ant-...",
            "model": "claude-sonnet-4-6",
            "verified_at": "...",
            "created_at": "..."
        }
    }
}
```

- **Stores per-backend**: `api_key` and `model` only
- **Does NOT store**: `base_url`, `timeout`, `max_tokens`, `temperature`
- **Status**: Encrypted with machine key (XOR + base64)
- **Read by**: `AuthStore.get_backend()` → `Runtime._register_available_backends()`
- **Problem**: Only `api_key` and `model` are stored. Other backend parameters are lost.

### 1f. Environment variables

| Variable | Set by | Read by |
|---|---|---|
| `ANTHROPIC_API_KEY` | user / `_inject_auth_credentials()` | `ClaudeBackend.__init__`, `Runtime._register_available_backends()` |
| `ANTHROPIC_MODEL` | user / `_inject_auth_credentials()` | `Runtime._register_available_backends()` |
| `DEEPSEEK_API_KEY` | user / `_inject_auth_credentials()` | `DeepSeekBackend.__init__`, `Runtime._register_available_backends()` |
| `DEEPSEEK_MODEL` | user / `_inject_auth_credentials()` | `Runtime._register_available_backends()` |
| `ZMAI_*` | user | `Config` via `EnvSource` |

- **Problem**: per-provider env var names are inconsistent (`{NAME}_API_KEY` vs `{NAME}_MODEL`). No env var for `base_url`, `max_tokens`, `temperature`.

### 1g. CLI arguments

```
zmai --backend claude <task>
```

- Only supports `--backend <name>`. No `--model`, `--api-key` etc.
- Parsed by `CLISource` into generic `Config`, but only `--backend` is used by `_oneshot_run()`.

---

## 2. Config Flow: How Config Reaches a Backend

### 2a. Registration path (used by Runtime)

```
Runtime.__init__()
  │
  ├── _register_available_backends()
  │     │
  │     ├── reads BACKEND_METADATA["claude"]["default_model"]     → "claude-sonnet-4-6"
  │     ├── reads env ANTHROPIC_MODEL (or falls back to default)  → "claude-sonnet-4-6"
  │     ├── reads env ANTHROPIC_API_KEY  (or AuthStore)           → "sk-ant-..."
  │     └── calls registry.register("claude", ClaudeBackend, config={"model": "claude-sonnet-4-6"})
  │
  └── _auto_select_default_backend()
        │
        ├── reads zmai.json → gateway.default_backend  (or "auto")
        ├── or reads env ANTHROPIC_API_KEY  (first found)
        └── or picks first registered
```

### 2b. Instantiation path

```
BackendRegistry.get("claude")
  │
  └── ClaudeBackend(config={"model": "claude-sonnet-4-6"})
        │
        ├── self._api_key    = config["api_key"]    ?? env ANTHROPIC_API_KEY    ?? ""
        ├── self._model      = config["model"]      ?? env ?? CLAUDE_DEFAULT_MODEL
        ├── self._base_url   = config["base_url"]   ?? CLAUDE_API_BASE
        ├── self._max_retries= config["max_retries"] ?? 3
        ├── self._timeout    = config["timeout"]     ?? 300
        │
        │   Note: config dict only ever contains {"model": "..."}
        │   All other fields fall through to env var or hardcoded constant.
        │
        └── backend instance ready
```

### 2c. Usage path

```
Runtime.run()
  │
  ├── backend_inst = self._gateway.get(backend)   ← instance resolved above
  │
  ├── ctx = AgentContext(backend=backend_inst)
  │
  └── SWEAgent.step()
        │
        └── context.backend.invoke(BackendRequest)
              │
              └── BackendRequest max_tokens=4096, temperature=0.7
                    (from BackendRequest dataclass defaults, NOT from backend config)
```

---

## 3. Gap Analysis

### 3a. What's stored vs what's needed

| Config field | `zmai.json` | `~/.zmai/config.json` | `~/.zmai/credentials` | Env var | Backend hardcoded | Passed to Backend via config dict |
|---|---|---|---|---|---|---|
| `backend` (name) | ❌ | ❌ | ✅ (active_backend) | ❌ | ✅ (class attr) | n/a (registry key) |
| `model` | ❌ | ❌ | ✅ | ✅ (per-provider) | ✅ | ✅ |
| `api_key` | ❌ | ❌ | ✅ | ✅ (per-provider) | ❌ | ❌ (read directly from env) |
| `base_url` | ❌ | ❌ | ❌ | ❌ | ✅ (per-provider constant) | ❌ |
| `timeout` | ✅ (gateway.timeout) | ✅ (gateway.timeout) | ❌ | ❌ | ✅ (claude:300) | ❌ |
| `max_tokens` | ❌ | ❌ | ❌ | ❌ | ✅ (claude:4096, deepseek:4096) | ❌ |
| `temperature` | ❌ | ❌ | ❌ | ❌ | ❌ (BackendRequest default:0.7) | ❌ |

### 3b. Config fragmentation by subsystem

| Subsystem | Config it uses | Config it ignores |
|---|---|---|
| `Config` | `zmai.json`, `ZMAI_*` env, CLI `--key=val` | `~/.zmai/config.json`, `~/.zmai/credentials`, per-provider env vars |
| `AuthStore` | `~/.zmai/credentials`, per-provider env vars | `zmai.json`, `~/.zmai/config.json` |
| `Runtime` | `BACKEND_METADATA` (code), per-provider env vars, `AuthStore`, `Config` | `~/.zmai/config.json` |
| `CLI` | per-provider env vars, `AuthStore`, `BACKEND_METADATA` | `zmai.json` (for backend config) |

---

## 4. Specific Violations

### Violation 1: `~/.zmai/config.json` written but never read

**File**: `cli/main.py:180-185`

The init wizard creates `~/.zmai/config.json` with:
```json
{"gateway": {"default_backend": "claude", "timeout": 300}}
```

But `Config` (`config/config.py:24`) only loads `FileSource("zmai.json")` in CWD. The global config file is **never consumed** by any subsystem.

### Violation 2: `gateway.timeout` in `zmai.json` is an orphan

**File**: `zmai.json:10`

```json
"gateway": {"default_backend": "auto", "timeout": 300}
```

This `timeout` is stored in `Config` but **never read and passed** to any Backend instance. Each backend has its own timeout default (`claude.py:61`: `self._timeout = int(self._config.get("timeout", 300))`), but nothing connects `zmai.json`'s `gateway.timeout` to the backend's config dict.

### Violation 3: `api_key` never passed through config dict

**File**: `runtime/runtime.py:364`

```python
self._gateway.register(name, cls, config={"model": model})
```

The config dict passed at registration contains **only** `"model"`. The `api_key` is read directly from env vars inside each backend's `__init__`. There is no mechanism to inject `api_key` via the config dict.

### Violation 4: No `base_url`, `max_tokens`, `temperature` in credential store

**File**: `auth/store.py:142-147`

```python
backends[name] = {
    "api_key": api_key,
    "model": model or existing.get("model", ""),
    "verified_at": ...,
    "created_at": ...,
}
```

The credential store only persists `api_key` and `model`. Every time a user wants a custom `base_url`, `max_tokens`, or `temperature`, they must set environment variables — these cannot be persisted.

### Violation 5: Per-backend env var naming is inconsistent

| Backend | API Key env var | Model env var |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_MODEL` |

No `BASE_URL`, `TIMEOUT`, `MAX_TOKENS`, `TEMPERATURE` env vars exist. The naming pattern is `{NAME}_API_KEY` / `{NAME}_MODEL`, but the prefix (`ANTHROPIC` vs `DEEPSEEK`) is backend-specific and hardcoded in `BACKEND_METADATA`.

### Violation 6: `temperature` nowhere in Backend config

The `BackendRequest` dataclass (`gateway/base.py:81`) has `temperature: float = 0.7`, but:
- No backend's `__init__` config reads `temperature`
- No credential/config store persists `temperature`
- The default `0.7` is always used, unchangeable

---

## 5. Current Config Hierarchy (as-implemented)

```
Priority (higher overrides lower):
  ┌────────────────────────────────┐
  │  1. CLI args (--backend)       │  ← only backend name
  ├────────────────────────────────┤
  │  2. Per-provider env vars      │  ← ANTHROPIC_API_KEY, etc.
  │     (ANTHROPIC_*, DEEPSEEK_*)  │
  ├────────────────────────────────┤
  │  3. ~/.zmai/credentials        │  ← only api_key + model
  ├────────────────────────────────┤
  │  4. zmai.json                  │  ← only gateway.default_backend + timeout
  ├────────────────────────────────┤
  │  5. BACKEND_METADATA (code)    │  ← default_model + module paths
  ├────────────────────────────────┤
  │  6. Backend hardcoded defaults │  ← base_url, max_tokens, etc.
  └────────────────────────────────┘

Note: ~/.zmai/config.json is NOT in this hierarchy — it's written but unused.
```

---

## 6. Recommended Unified Config Model

### 6a. Per-backend config shape (one schema for all backends)

```json
{
    "backend": "<name>",
    "model": "<model-name>",
    "api_key": "<key>",
    "base_url": "https://api.example.com/v1",
    "timeout": 300,
    "max_tokens": 4096,
    "temperature": 0.7
}
```

Every backend should accept **exactly this set** of config keys. No backend-specific key names.

### 6b. Global Config (`~/.zmai/config.json`) — unified storage

```json
{
    "version": 1,
    "cli": {
        "theme": "dark",
        "trust_mode": true
    },
    "gateway": {
        "default_backend": "claude",
        "backends": {
            "claude": {
                "model": "claude-sonnet-4-6",
                "api_key": "sk-ant-...",
                "base_url": "https://api.anthropic.com/v1",
                "timeout": 300,
                "max_tokens": 4096,
                "temperature": 0.7
            },
            "deepseek": {
                "model": "deepseek-chat",
                "api_key": "sk-...",
                "timeout": 300,
                "max_tokens": 4096,
                "temperature": 0.7
            }
        }
    }
}
```

`~/.zmai/credentials` should be merged into this single file, or the encrypted credential file should store the full backend config object (not just `api_key` + `model`).

### 6c. Project Config (`zmai.json`) — override per-project

```json
{
    "gateway": {
        "default_backend": "deepseek",
        "backends": {
            "claude": {
                "model": "claude-sonnet-4-6",
                "timeout": 600
            },
            "deepseek": {
                "model": "deepseek-chat",
                "base_url": "https://internal.deepseek.example.com/v1"
            }
        }
    }
}
```

Project config **overrides** Global config for the same fields. `api_key` should remain in Global (encrypted) — project config only overrides non-secret fields.

### 6d. Config hierarchy (recommended)

```
Priority (higher overrides lower):
  ┌─────────────────────────────────┐
  │  1. CLI args                     │  ← --backend, --model, etc.
  ├─────────────────────────────────┤
  │  2. Per-provider env vars        │  ← override everything
  ├─────────────────────────────────┤
  │  3. Project Config (zmai.json)   │  ← per-project overrides
  ├─────────────────────────────────┤
  │  4. Global Config                │  ← ~/.zmai/config.json (primary storage)
  │     (~/.zmai/config.json)        │
  ├─────────────────────────────────┤
  │  5. Backend hardcoded defaults   │  ← module-level constants
  └─────────────────────────────────┘
```

### 6e. Backend `__init__` interface (recommended)

```python
class Backend(ABC):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        c = config or {}
        self._model: str       = c.get("model", DEFAULT_MODEL)
        self._api_key: str     = c.get("api_key", os.environ.get("...", ""))
        self._base_url: str    = c.get("base_url", DEFAULT_BASE_URL)
        self._timeout: int     = int(c.get("timeout", 300))
        self._max_tokens: int  = int(c.get("max_tokens", 4096))
        self._temperature: float = float(c.get("temperature", 0.7))
```

Every backend reads the **same 6 keys** from its config dict. The `Runtime` passes the full merged config (global + project + env) for the chosen backend.

---

## 7. Summary

| Issue | Severity | Description |
|---|---|---|
| `~/.zmai/config.json` written but never read | 🔴 High | Dangling file, config lost |
| `gateway.timeout` orphaned in `zmai.json` | 🟡 Medium | Never passed to Backend |
| `api_key` bypasses config dict | 🟡 Medium | Read directly from env, not injectable |
| Only `api_key` + `model` in credential store | 🟡 Medium | `base_url`, `timeout`, `max_tokens`, `temperature` not persistable |
| `temperature` nowhere configurable | 🟡 Medium | Always defaults to 0.7 |
| Per-backend env var names differ | 🟢 Low | Hardcoded in `BACKEND_METADATA` |
| No real global config loading | 🔴 High | `~/.zmai/config.json` not in `Config` source chain |
| 7 separate storage locations | 🔴 High | Fragmentation across code, encrypted file, JSON files, env vars |
