# Official Backend Policy — v1.0

**Date**: 2026-07-17  
**Status**: Policy design, not implemented

---

## 1. v1.0 Freeze — Official Built-in Backends

Only **2 backends** ship as official built-in in v1.0:

| Backend | Provider | Status | Reason |
|---|---|---|---|
| `claude` | Anthropic | **Official** | Primary backend, full capability set (streaming, tools, system prompt, multi-turn) |
| `deepseek` | DeepSeek | **Official** | Cost-effective secondary, OpenAI-compatible API |

### What "Official" Means

- Ships inside the `zmai` package (`gateway/backends/`)
- Full test coverage in `tests/`
- Documented in `README.md`
- Maintained by core team
- Guaranteed API stability within the same major version
- Included in `zmai doctor` diagnostics

### What "Official" Does NOT Mean

- Does NOT imply endorsement over other providers
- Does NOT block users from adding other providers via plugin
- Does NOT mean feature parity between official backends

---

## 2. Plugin Backends — All Others

The following providers are **not built-in**. They are implemented as plugins by the community or by the core team but distributed separately.

| Provider | Why Plugin | Recommended Plugin Name |
|---|---|---|
| **OpenAI** | API format is already covered by DeepSeek (OpenAI-compatible); community plugin fills gaps (GPT-4o specifics, vision, strict structured output) | `openai` |
| **Gemini** | Different API semantics (no system prompt in same way, different tool format), needs separate testing | `gemini` |
| **Qwen** | Regional provider, maintained by community | `qwen` |
| **Moonshot** | Regional provider, maintained by community | `moonshot` |
| **Ollama** | Local inference, different deployment model (no API key, user-managed server) | `ollama` |

### Plugin Distribution Channels

| Channel | How |
|---|---|
| `~/.zmai/backends/<name>.py` | Single-file plugin (drop-in) |
| PyPI package (`zmai-backend-<name>`) | Package with entry points for auto-discovery |
| Project `.zmai/backends/` | Per-project plugin pinning |

### Plugin Lifecycle

```
User writes plugin
        │
        ▼
zmai plugin install provider.py
        │
        ▼
~/.zmai/backends/provider.py
        │
        ▼
zmai auth update provider --api-key ...
        │
        ▼
zmai run --backend provider "task"
```

---

## 3. Official vs Plugin — Decision Matrix

A backend qualifies as **official** (built-in) when ALL of these are true:

| Criterion | Required for Official | Required for Plugin |
|---|---|---|
| **API stability** | Provider API is stable (no breaking changes in 6+ months) | Provider API may be in preview |
| **Test coverage** | Unit tests + integration tests + live API tests | Unit tests recommended |
| **Capabilities** | Streaming + tool_use + system_prompt + multi_turn | At least invoke() |
| **Auth model** | Standard API key (env var or config file) | Any auth model |
| **CI passing** | All tests pass on main branch | N/a |
| **Docs** | Full docs in `docs/backends/<name>.md` | Readme in plugin file |
| **Maintainer** | Core team member | Community member |
| **Usage** | Used by core team in production | Any |

### Decision Table

| Backend | API Stable | Tests | Capabilities | Auth | Docs | Maintainer | Usable | Verdict |
|---|---|---|---|---|---|---|---|---|
| Claude | ✅ | ✅ | ✅ full | ✅ key | ✅ | ✅ core | ✅ | **Official** |
| DeepSeek | ✅ | ✅ | ✅ partial (no stream) | ✅ key | ✅ | ✅ core | ✅ | **Official** |
| OpenAI | ✅ | ❌ missing | ✅ full | ✅ key | ❌ | ❌ community | ✅ | Plugin |
| Gemini | ⚠️ preview | ❌ missing | ❌ different | ✅ key | ❌ | ❌ community | ✅ | Plugin |
| Qwen | ✅ | ❌ missing | ❌ unknown | ✅ key | ❌ | ❌ community | ✅ | Plugin |
| Moonshot | ⚠️ preview | ❌ missing | ❌ unknown | ✅ key | ❌ | ❌ community | ✅ | Plugin |
| Ollama | ✅ | ❌ missing | ❌ depends on model | ❌ no key | ❌ | ❌ community | ✅ | Plugin |

**Result**: The v1.0 boundary is clear — Claude and DeepSeek are the only backends that meet all official criteria.

---

## 4. API Compatibility Matrix

Backends fall into two API families:

### Anthropic API Family
```
Claude                   ← native Anthropic API
```

### OpenAI-Compatible API Family
```
DeepSeek                 ← /v1/chat/completions
OpenAI (plugin)          ← /v1/chat/completions
Qwen    (plugin)         ← /v1/chat/completions (DashScope)
Moonshot (plugin)        ← /v1/chat/completions
Ollama  (plugin)         ← /v1/chat/completions (local)
```

### Shared Base Class for OpenAI-Compat

A shared `OpenAICompatBackend` mixin could be provided as a plugin helper:

```python
# zmai/gateway/backends/compat.py (new, shipped with zmai)

class OpenAICompatBackend(Backend):
    """Base for OpenAI-compatible API backends.

    Plugin authors can subclass this instead of Backend directly
    to reuse request building, response parsing, and streaming.
    """
    ...
```

This is not a built-in backend — it's a **reusable base class** for plugin authors implementing OpenAI-compatible providers.

---

## 5. Quality Gates for Plugin Backends

### Required for distribution as `zmai-backend-<name>` on PyPI

| Gate | Detail |
|---|---|
| Implements `Backend` ABC | `invoke()`, `stream()`, `capabilities` |
| Exports `plugin: BackendPlugin` | Module-level variable |
| Tested with `zmai doctor` | Plugin shows in diagnostic |
| Uses `zmai.auth.AuthStore` | Standard credential storage |
| Versioned | Follows semantic versioning |
| Licensed | Apache 2.0 or MIT |

### Recommended (but not required)

- Integration test with a real API key
- `pyproject.toml` with `[project.entry-points."zmai.backends"]`
- Published on PyPI with `zmai-backend-` prefix

---

## 6. Plugin → Official Migration Path

If a plugin backend gains enough traction, it can be promoted to official:

### Criteria for Promotion

1. Used by > 5% of the user base (measured via telemetry or surveys)
2. Plugin has been stable for > 3 months without breaking changes
3. Core team has capacity to maintain it
4. All official criteria from §3 are met

### Promotion Process

```
1. Plugin author opens RFC issue
2. Core team reviews against promotion criteria
3. If approved:
   a. Move plugin file to zmai/gateway/backends/
   b. Add to BACKEND_METADATA and BACKEND_DEFAULT_CONFIG
   c. Add test suite to tests/
   d. Add docs to docs/backends/
   e. Add live API test (with CI key)
   f. Include in zmai doctor
4. Released in next minor version
```

---

## 7. Version Compatibility

| zmai version | Official backends | Plugin API |
|---|---|---|
| `< 1.0` (pre-release) | Claude, DeepSeek (hardcoded) | Not available |
| `1.0.0` | Claude, DeepSeek | `BackendPlugin` API stable |
| `1.x.x` | Claude, DeepSeek | Backward-compatible plugin API |
| `2.0.0` (future) | TBD | May remove built-in backends if plugin ecosystem matures |

### Plugin API Stability Guarantee

The `BackendPlugin` dataclass and `PluginRegistry` API are part of the public `zmai.gateway` interface from v1.0 onward. Any breaking change will:
1. Be announced in the changelog
2. Be accompanied by a migration guide
3. Ship with a deprecation period of at least one minor version

---

## 8. Summary

```
v1.0 Official (built-in):       Plugin (external):
┌──────────────┐               ┌──────────────┐
│   Claude     │               │   OpenAI     │
│   DeepSeek   │               │   Gemini     │
└──────────────┘               │   Qwen       │
                               │   Moonshot   │
Core team maintains.           │   Ollama     │
Guaranteed stable.             └──────────────┘
Full test suite.               Community maintained.
                               Distributed separately.
                               No stability guarantee.
```
