# Gateway Architecture Review

**Date**: 2026-07-17  
**Goal**: Verify that Gateway owns Backend creation; Runtime never directly instantiates a Backend.

---

## 1. Backend Instantiation — Where Does `new` Happen?

### 1a. All `cls()` calls (the actual `new`)

Every place in the codebase where a Backend subclass constructor is called:

| # | Location | Code | Who |
|---|---|---|---|
| 1 | `gateway/registry.py:90` | `self._instances[resolved] = cls(config=config)` | **Gateway** |
| 2 | `cli/doctor.py:179` | `reg.register(name, cls, default=True)` | CLI (doctor only) |
| — | `runtime/` | — | **None** |

**There is zero `ClaudeBackend(...)` or `DeepSeekBackend(...)` calls anywhere in Runtime.** ✅

### 1b. The only instantiation path

```
BackendRegistry.get(name)
  │
  │  if resolved not in self._instances:
  │
  ▼
cls(config=config)                    ← gateway/registry.py:90
  │
  ▼
ClaudeBackend or DeepSeekBackend      ← the actual constructor
```

The actual `__init__()` call happens **inside the Gateway** (`BackendRegistry.get()`), not in Runtime.

---

## 2. Gateway vs Runtime — Division of Labor

### Current State

```
Runtime.__init__()
  │
  ├── Config()                       ← Runtime owns config
  ├── BackendRegistry()              ← Gateway data structure
  │
  ├── _register_available_backends()
  │     │
  │     │  Reads BACKEND_METADATA     ← discovery (in Runtime)
  │     │  Reads Config + AuthStore   ← config assembly (in Runtime)
  │     │  importlib import_module   ← module loading (in Runtime)
  │     │  getattr(mod, "ClaudeBackend") ← class resolution (in Runtime)
  │     │
  │     └── registry.register(name, cls, config=cfg)
  │           │                         ← registration (in Gateway)
  │           │
  │           └── stores (name → cls, config)
  │
  └── _auto_select_default_backend()
        │
        │  registry.set_default(name) ← default selection (in Runtime)
        │
        ▼
        ready
```

Later, when an agent runs:

```
Runtime.run()
  │
  └── self._gateway.get(backend)
        │
        └── cls(config=config)     ← CREATION (in Gateway ✅)
```

### Ideal State (what the requirement asks for)

```
Runtime
  │
  └── gateway.create_backend(name)    ← single call
        │
        ├── discover backends          ← in Gateway
        ├── read config                ← in Gateway
        ├── resolve class              ← in Gateway
        ├── instantiate                ← in Gateway
        │
        └── return Backend instance
```

---

## 3. Runtime Responsibilities That Leak into Backend Creation

### Issue 1: `_register_available_backends()` lives in Runtime

**File**: `runtime/runtime.py:349-400`

This 50-line method in Runtime handles:
1. **Iterating `BACKEND_METADATA`** — deciding which backends exist
2. **Config assembly** — merging defaults, global config, project config, env vars, AuthStore
3. **Module loading** — `importlib.import_module(info["module"])`
4. **Class resolution** — `getattr(mod, info["class"])`
5. **Credential validation** — checking api_key before registering
6. **Calling `registry.register()`** — feeding the Gateway

This is **backend lifecycle logic** that belongs in the Gateway, not Runtime.

### Issue 2: `_auto_select_default_backend()` lives in Runtime

**File**: `runtime/runtime.py:402-416`

```python
def _auto_select_default_backend(self) -> None:
    cfg = self._config.get("gateway.default_backend", "auto")
    if cfg and cfg != "auto" and cfg in self._gateway.list():
        self._gateway.set_default(cfg)
        return
    for name, info in get_available_backends().items():
        import os as _os
        if _os.environ.get(info["env_api_key"]):
            if name in self._gateway.list():
                self._gateway.set_default(name)
                return
    if self._gateway.list():
        self._gateway.set_default(self._gateway.list()[0])
```

Default backend selection — deciding which backend to use when none is specified — is in Runtime. This is a policy decision that could live in either layer, but if Gateway owns backend lifecycle, it should own the default selection too.

### Issue 3: `_register_available_backends()` is called ONLY from Runtime

No other caller invokes `_register_available_backends()`. The Gateway has no mechanism to discover or register backends on its own.

---

## 4. What's Already in the Gateway

| Component | Location | Responsibility |
|---|---|---|
| `Backend` ABC | `gateway/base.py` | Interface definition |
| `BackendRegistry` | `gateway/registry.py` | Store classes, cache instances, create on `get()` |
| `BACKEND_METADATA` | `gateway/backends/__init__.py` | Registry of available backends |
| `BACKEND_DEFAULT_CONFIG` | `gateway/backends/__init__.py` | Default config values per backend |
| `get_available_backends()` | `gateway/backends/__init__.py` | Discovery helper |
| `resolve_backend_config()` | `gateway/backends/__init__.py` | Config merge helper |

The Gateway already has **all the data and helpers** needed for backend creation. What's missing is the orchestration method that ties them together.

---

## 5. Summary

| Requirement | Status | Evidence |
|---|---|---|
| Runtime never writes `new ClaudeBackend()` | ✅ Confirmed | Zero references to backend class names in `runtime/` |
| Runtime never writes `new DeepSeekBackend()` | ✅ Confirmed | Zero references to backend class names in `runtime/` |
| Actual `cls(config=config)` in Gateway | ✅ Confirmed | `gateway/registry.py:90` |
| Gateway owns `register()` | ✅ Confirmed | Only `BackendRegistry.register()` stores classes |

### Remaining Issue

| Issue | Location | Severity |
|---|---|---|
| **Backend creation orchestration is in Runtime** | `runtime/runtime.py:349-400` (`_register_available_backends`) | 🟡 Medium |
| **Default backend selection is in Runtime** | `runtime/runtime.py:402-416` (`_auto_select_default_backend`) | 🟢 Low |
| **Gateway has no self-contained `create()` method** | `gateway/` | 🟡 Medium |

### Conclusion

**✅ The Runtime never directly instantiates a Backend subclass.** Every Backend instance is created by `BackendRegistry.get()` inside the Gateway.

**🟡 However, the orchestration that decides *which* backends to create and *how to configure them* lives in Runtime.** The `_register_available_backends()` method in `runtime/runtime.py` handles discovery, config assembly, module loading, and class resolution — all of which are backend lifecycle concerns.

Moving `_register_available_backends()` and `_auto_select_default_backend()` into the Gateway (as `Gateway.create_backend(name)` or similar) would make the Gateway fully self-contained for backend creation. The Runtime would then only call `gateway.get_backend(name)` — a single-interface handoff.
