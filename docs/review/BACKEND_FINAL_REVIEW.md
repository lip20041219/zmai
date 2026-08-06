# Backend Architecture Final Review

**Author**: Principal Engineer  
**Date**: 2026-07-17  
**Question**: When adding the 2nd, 5th, and 20th backend — does Runtime need modification?

---

## 1. Current Architecture — Layer Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                        Runtime                                │
│  ┌────────────────────────────────────────────────────┐      │
│  │ run() → gateway.get(backend) → invoke/stream       │      │
│  │                                                      │      │
│  │ _register_available_backends()                      │      │
│  │   for name in get_available_backends():             │      │
│  │     cfg = merge(BACKEND_DEFAULT_CONFIG[name],       │      │
│  │                 config, AuthStore, env)             │      │
│  │     registry.register(name, cls, config=cfg)        │      │
│  │                                                      │      │
│  │ _auto_select_default_backend()                      │      │
│  │   for name in get_available_backends():             │      │
│  │     if has_credentials(name): set_default(name)     │      │
│  └────────────────────────────────────────────────────┘      │
│                                                              │
│  depends on: Backend (ABC) only                              │
└──────────────────────────────────────────────────────────────┘
         │
         │  BackendRegistry.get(name)  ← only instantiation point
         ▼
┌──────────────────────────────────────────────────────────────┐
│                      Gateway                                  │
│  ┌──────────────┐  ┌────────────────────────────────────┐    │
│  │ Backend(ABC) │  │ BackendRegistry                     │    │
│  │ invoke()     │  │  register(name, cls, config)        │    │
│  │ stream()     │  │  get(name) → cls(config=config)     │    │
│  │ capabilities │  │  _instances: dict[name → instance]  │    │
│  └──────────────┘  └────────────────────────────────────┘    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐      │
│  │ gateway/backends/__init__.py   ← THE BOTTLENECK     │      │
│  │  ├── import ClaudeBackend                          │      │
│  │  ├── import DeepSeekBackend                        │      │
│  │  ├── import GeminiBackend                          │      │
│  │  ├── BACKEND_METADATA = { ... }                    │      │
│  │  └── BACKEND_DEFAULT_CONFIG = { ... }              │      │
│  └────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│                Backend Implementations                        │
│  claude.py │ deepseek.py │ gemini.py │ ...                    │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Impact Analysis — Adding a Backend

### 2nd Backend (DeepSeek — already done)

| Action | File | Required? |
|---|---|---|
| Create `deepseek.py` | `gateway/backends/` | ✅ Yes |
| Add import | `gateway/backends/__init__.py` | ✅ Yes |
| Add `BACKEND_METADATA` entry | `gateway/backends/__init__.py` | ✅ Yes |
| Add `BACKEND_DEFAULT_CONFIG` entry | `gateway/backends/__init__.py` | ✅ Yes |
| Add `zmai.json` example | `zmai.json` | 🟡 Optional |
| Modify `Runtime` | `runtime/` | ❌ **No** |
| Modify `Gateway` core (base/registry) | `gateway/base.py`, `gateway/registry.py` | ❌ **No** |
| Modify `CLI` | `cli/` | ❌ **No** |
| Modify `AuthStore` | `auth/` | ❌ **No** |

**Runtime change**: **Zero lines.** Runtime iterates `BACKEND_METADATA` dynamically. Adding a new entry is automatically picked up.

### 5th Backend (e.g., adding OpenAI + Mistral + Cohere)

| Action | File | Required? |
|---|---|---|
| Create `openai.py`, `mistral.py`, `cohere.py` | `gateway/backends/` | ✅ Yes |
| Add 3 imports | `gateway/backends/__init__.py` | ✅ Yes |
| Add 3 `BACKEND_METADATA` entries | `gateway/backends/__init__.py` | ✅ Yes |
| Add 3 `BACKEND_DEFAULT_CONFIG` entries | `gateway/backends/__init__.py` | ✅ Yes |
| Modify `Runtime` | `runtime/` | ❌ **No** |
| Modify `Gateway` core | `gateway/base.py`, `gateway/registry.py` | ❌ **No** |

**Runtime change**: **Still zero lines.** Same dynamic iteration. Nothing in Runtime references backend names.

But now `gateway/backends/__init__.py` has grown to ~200 lines of metadata + imports. The single-file bottleneck is becoming visible.

### 20th Backend

| Action | File | Required? |
|---|---|---|
| Create 15 new backend `.py` files | `gateway/backends/` | ✅ Yes |
| Add 15 imports | `gateway/backends/__init__.py` | ✅ Yes |
| Add 15 `BACKEND_METADATA` entries (~45 lines each) | `gateway/backends/__init__.py` | ✅ Yes |
| Add 15 `BACKEND_DEFAULT_CONFIG` entries (~6 lines each) | `gateway/backends/__init__.py` | ✅ Yes |
| Modify `Runtime` | `runtime/` | ❌ **No** |
| Modify `Gateway` core | `gateway/base.py`, `gateway/registry.py` | ❌ **No** |
| Modify `CLI` | `cli/` | ❌ **No** |
| Modify `AuthStore` | `auth/` | ❌ **No** |

**Runtime change**: **Still zero lines.**

**`__init__.py` size**: ~900+ lines of metadata, ~30 import lines. A single file that every backend contributor must touch, creating merge conflicts.

---

## 3. Why Runtime Never Needs Modification

The Runtime uses only **dynamic dispatch** for backend operations:

### 3a. Backend resolution

```python
# runtime/runtime.py:121
backend_inst = self._gateway.get(backend)
```

`BackendRegistry.get()` accepts a string name and returns a `Backend` instance. **It never references specific backend classes.** Adding a new name to the registry changes nothing in this call.

### 3b. Backend invocation

```python
# swe/agent.py:203
response = context.backend.invoke(request)
```

Every backend implements the same `Backend` ABC. The Runtime calls `invoke()` / `stream()` — **it never branches on backend type.**

### 3c. Discovery loop

```python
# runtime/runtime.py:361
for name, info in get_available_backends().items():
```

The loop iterates whatever `BACKEND_METADATA` contains. **Adding entries to the dict changes nothing in the loop logic.**

### 3d. Config assembly

```python
# runtime/runtime.py:363
cfg = dict(BACKEND_DEFAULT_CONFIG.get(name, {}))
```

Config merging is keyed by backend `name`. **The merge logic is identical for every backend.**

### 3e. Default selection

```python
# runtime/runtime.py:408
for name, info in get_available_backends().items():
    if has_credentials(name):
        set_default(name)
```

**Same dynamic iteration.** No backend-specific logic.

---

## 4. The Real Bottleneck — Not Runtime

### 4a. Registration file (`gateway/backends/__init__.py`)

The only file that must be changed for every new backend. Current cost per backend:

| Item | Lines |
|---|---|
| `import` line | 1 |
| `BACKEND_METADATA` entry | ~12 |
| `BACKEND_DEFAULT_CONFIG` entry | ~6 |
| **Total per backend** | **~19 lines** |

For 20 backends: **380 lines** of registration boilerplate in one file.

### 4b. Merge conflict risk

Every contributor adding a backend touches the same lines in the same file:
```python
BACKEND_METADATA: dict[str, dict] = {
    "claude": {...},      # ← existing
    "deepseek": {...},    # ← existing
    "gemini": {...},      # ← existing
    "openai": {...},      # ← contributor A adds here
    "mistral": {...},     # ← contributor B adds here
}
```

Two PRs adding different backends conflict on adjacent lines.

### 4c. Tests

Each backend needs its own test file. This is a one-time cost per backend, not a scaling issue. Tests are independent — no shared file contention.

### 4d. Configuration (`zmai.json`)

Adding an example section is optional. Users don't need to edit `zmai.json` to use a new backend — env vars suffice.

---

## 5. Scaling Projections

```
Metric             │ 2 backends │ 5 backends │ 20 backends │ Scales?
───────────────────┼────────────┼────────────┼─────────────┼─────────
Runtime code       │ 416 lines  │ 416 lines  │ 416 lines   │ ✅ Flat
Gateway core       │ 312 lines  │ 312 lines  │ 312 lines   │ ✅ Flat
Backend .py files  │ 3 files    │ 6 files    │ 21 files    │ ✅ Linear
CLI code           │ 0 refs     │ 0 refs     │ 0 refs      │ ✅ Flat
AuthStore code     │ 0 refs     │ 0 refs     │ 0 refs      │ ✅ Flat
Doctor code        │ 0 refs     │ 0 refs     │ 0 refs      │ ✅ Flat
Config system      │ 0 refs     │ 0 refs     │ 0 refs      │ ✅ Flat
───────────────────┼────────────┼────────────┼─────────────┼─────────
__init__.py         │ 125 lines  │ ~200 lines │ ~900 lines  │ ❌ Linear
                    │            │            │             │    (shared)
Merge conflicts     │ 0          │ low risk   │ high risk   │ ❌ O(n)
Third-party add     │ impossible │ impossible │ impossible  │ ❌ Blocked
```

---

## 6. Verdict

### Does adding a new backend require modifying Runtime?

**No. For the 2nd, 5th, and 20th backend — Runtime requires zero modifications.**

The Runtime has no hardcoded backend names, no `if backend == "xxx"` branches, and no backend-specific logic. Every backend operation goes through `Backend` (ABC) or `BackendRegistry` (string key). Adding a new backend is additive to a data structure (`BACKEND_METADATA`) that Runtime already iterates generically.

### So where is the problem?

The problem is **not Runtime** — it's **registration**.

```python
# gateway/backends/__init__.py — the single bottleneck

# Every new backend requires editing this file:
# 1. Add import line
# 2. Add BACKEND_METADATA entry
# 3. Add BACKEND_DEFAULT_CONFIG entry
```

For the **2nd backend**: this is fine — the file exists, edit it.

For the **5th backend**: the file is getting crowded, but still manageable.

For the **20th backend**: the file is a maintenance hazard. Merge conflicts are likely. Third-party contributors must fork the entire repo to add a backend. The file's length (~900+ lines) discourages adding more.

### The root cause

The registration mechanism couples **discovery** with **code**. `BACKEND_METADATA` is a Python dict in a Python file — it cannot be extended without editing that file. A plugin/discovery mechanism (directory scanning, entry points) would decouple discovery from code entirely.

### Recommendation

Adopt the **Plugin API** design (`../design/BACKEND_PLUGIN_DESIGN.md`):

```
Current:                              Plugin API:
gateway/backends/__init__.py          ~/.zmai/backends/openai.py
  ├── import openai                     ├── class OpenAIBackend
  ├── BACKEND_METADATA["openai"]        └── plugin = BackendPlugin(...)
  └── BACKEND_DEFAULT_CONFIG["openai"]
```

This moves registration from a shared file into individual plugin files. Runtime, Gateway core, CLI, AuthStore are already compatible — they all use dynamic dispatch.

---

## 7. Appendix — All Call Sites That Would Change

Every location in the current codebase that touches backend registration:

| File | Line | Accesses | Changes when adding backend? |
|---|---|---|---|
| `gateway/backends/__init__.py` | 8-10 | `import` | ✅ **Yes** — add import |
| `gateway/backends/__init__.py` | 23-55 | `BACKEND_METADATA` | ✅ **Yes** — add entry |
| `gateway/backends/__init__.py` | 65-84 | `BACKEND_DEFAULT_CONFIG` | ✅ **Yes** — add entry |
| `runtime/runtime.py` | 361 | `for name in get_available_backends()` | ❌ No |
| `runtime/runtime.py` | 363 | `BACKEND_DEFAULT_CONFIG.get(name)` | ❌ No |
| `runtime/runtime.py` | 408 | `for name in get_available_backends()` | ❌ No |
| `cli/main.py` | 94 | `for name in get_available_backends()` | ❌ No |
| `cli/main.py` | 125 | `all_backends = get_available_backends()` | ❌ No |
| `cli/main.py` | 150 | `BACKEND_DEFAULT_CONFIG.get(name)` | ❌ No |
| `cli/main.py` | 227 | `for name in get_available_backends()` | ❌ No |
| `auth/store.py` | 218 | `for name in get_available_backends()` | ❌ No |
| `cli/doctor.py` | 175 | `for name in BACKEND_METADATA` | ❌ No |
| `cli/doctor.py` | 347 | `for name in BACKEND_METADATA` | ❌ No |

**13 call sites total. 3 require changes. 0 are in Runtime.**
