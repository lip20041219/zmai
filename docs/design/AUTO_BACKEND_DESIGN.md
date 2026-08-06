# Auto Backend Selection — Design

**Date**: 2026-07-17  
**Status**: Design only, not implemented

---

## 1. Overview

`backend=auto` analyzes the task description and selects the optimal LLM Backend based on task type, capability requirements, and cost.

```
Runtime.run(backend="auto")
  │
  ▼
AutoSelector.select(task)
  │
  ├── classify(task) → task_type
  │
  └── route(task_type) → backend name
        │
        ▼
Gateway.get(backend_name)
```

---

## 2. Task Classification

Tasks are classified into types. Each type maps to a preferred backend.

### Type Definitions

| Type | Pattern | Example Tasks |
|---|---|---|
| `code_complex` | Multi-file changes, architecture design, refactoring | "Implement a new module with tests", "Refactor the auth system" |
| `code_simple` | Single-file edit, bug fix, small feature | "Fix typo in README", "Add input validation" |
| `chat` | General Q&A, explanation, brainstorming | "How does async work?", "Explain this code" |
| `write` | Document, email, report generation | "Write release notes", "Create a design doc" |
| `analysis` | Data analysis, debugging, investigation | "Why is this test failing?", "Profile this function" |

### Detection Mechanism

Rule-based keyword matching (no ML model needed):

```python
_CODE_COMPLEX = {
    "refactor", "architect", "design pattern", "multi-file",
    "implement.*module", "test suite", "migration", "middleware",
    "decouple", "abstraction", "dependency injection",
    "code review", "performance optimization",
}

_CODE_SIMPLE = {
    "fix", "bug", "typo", "add.*test", "rename", "delete",
    "update.*doc", "bump version",
}

_ANALYSIS = {
    "why", "investigate", "profile", "debug", "root cause",
    "stack trace", "error log", "crash",
}

_WRITE = {
    "write.*doc", "release notes", "changelog", "readme",
    "design doc", "email", "report",
}
```

If no keyword matches, default to `chat`.

---

## 3. Routing Rules

### Primary Routing

| Task Type | Backend | Rationale |
|---|---|---|
| `code_complex` | `claude` | Strongest at multi-step reasoning, tool use, long context |
| `code_simple` | `deepseek` | Cost-effective for simple code changes |
| `chat` | `deepseek` | Cost-effective for general conversation |
| `write` | `claude` | Better at structured, long-form output |
| `analysis` | `claude` | Better at deep reasoning and investigation |

### Fallback Chain

If the preferred backend is unavailable (no credentials, API error):

```
preferred → fallback_1 → fallback_2 → error
```

| Preferred | Fallback 1 | Fallback 2 |
|---|---|---|
| `claude` | `deepseek` | error |
| `deepseek` | `claude` | error |

---

## 4. Interface Design

### AutoSelector Class

```python
class AutoSelector:
    """Task-based backend auto-selector."""

    def __init__(self, registry: BackendRegistry) -> None:
        self._registry = registry

    def select(self, task: str) -> str:
        """Analyze task and return the best backend name.

        Args:
            task: The user's task description.

        Returns:
            Backend name (e.g. "claude", "deepseek").

        Raises:
            BackendError: No registered backends available.
        """
        task_type = self._classify(task)
        return self._route(task_type)

    def _classify(self, task: str) -> str:
        """Classify task into a type using keyword matching."""
        task_lower = task.lower()

        # Priority order: code_complex > analysis > write > code_simple > chat
        for pattern_list, type_name in [
            (_CODE_COMPLEX, "code_complex"),
            (_ANALYSIS, "analysis"),
            (_WRITE, "write"),
            (_CODE_SIMPLE, "code_simple"),
        ]:
            if any(kw in task_lower for kw in pattern_list):
                return type_name

        return "chat"

    def _route(self, task_type: str) -> str:
        """Map task type to backend name with fallback."""
        preferred = _ROUTING.get(task_type, "chat")
        fallbacks = _FALLBACK.get(preferred, [])

        for name in [preferred] + fallbacks:
            if name in self._registry.list():
                return name

        raise BackendError("Auto-selection failed: no registered backends available")
```

### Config

No additional config needed. `BACKEND_DEFAULT_CONFIG` in `gateway/backends/__init__.py` already defines all available backends. The `AutoSelector` checks `registry.list()` for available backends.

---

## 5. Integration Points

### 5a. Runtime Integration

**File**: `runtime/runtime.py`

In `Runtime.run()`, when `backend="auto"`:

```python
async def run(self, agent_id, task, backend=None, ...):
    if backend == "auto":
        selector = AutoSelector(self._gateway)
        backend = selector.select(task)

    backend_inst = self._gateway.get(backend)
    ...
```

### 5b. CLI Integration

**File**: `cli/main.py`

Default value for `--backend` flag should be `"auto"` instead of `None`:

```python
p.add_argument("--backend", default="auto", help="backend name or 'auto'")
```

In `_oneshot_run()` and `_repl_run()`:

```python
result = asyncio.run(runtime.run(
    ...
    backend=args.backend,  # "auto" by default
    ...
))
```

### 5c. zmai.json

```json
{
    "gateway": {
        "default_backend": "auto"
    }
}
```

Already is `"auto"` in the current `zmai.json`.

---

## 6. Category Weights and Thresholds

| Factor | Weight | Values |
|---|---|---|
| Task length | `1x` | `< 50 chars → chat`; `> 500 chars → code_complex` |
| Code keywords | `3x` | `refactor`, `implement`, `test` → `code_complex` |
| Analysis keywords | `2x` | `why`, `debug`, `root cause` → `analysis` |
| Writing keywords | `2x` | `write`, `document`, `report` → `write` |
| Simple keywords | `1x` | `fix`, `typo`, `rename` → `code_simple` |

### Scoring-based variant (alternative to fixed rules)

```python
def _score(self, task: str) -> dict[str, float]:
    scores = {"code_complex": 0, "code_simple": 0, "chat": 1,
              "write": 0, "analysis": 0}
    lower = task.lower()

    # Task length heuristic
    if len(task) > 500:
        scores["code_complex"] += 1.5
    elif len(task) < 50:
        scores["chat"] += 0.5

    # Keyword scoring
    for kw in _CODE_COMPLEX:
        if kw in lower:
            scores["code_complex"] += 3
    for kw in _CODE_SIMPLE:
        if kw in lower:
            scores["code_simple"] += 1
    for kw in _ANALYSIS:
        if kw in lower:
            scores["analysis"] += 2
    for kw in _WRITE:
        if kw in lower:
            scores["write"] += 2

    return scores
```

---

## 7. File Map

| File | Change |
|---|---|
| `src/zmai/gateway/auto.py` | **New** — `AutoSelector` class |
| `src/zmai/gateway/__init__.py` | Add `AutoSelector` to exports |
| `src/zmai/runtime/runtime.py` | Handle `backend="auto"` in `run()` |
| `src/zmai/cli/main.py` | Change `--backend` default to `"auto"` |

---

## 8. Edge Cases

| Scenario | Behavior |
|---|---|
| Only one backend registered | AutoSelector picks the only available one |
| No backends registered | `BackendError: no registered backends` |
| Task matches multiple types | Highest-priority type wins (code_complex > analysis > write > code_simple > chat) |
| Empty task string | Defaults to `chat` → first available backend |
| Preferred backend API failure | Runtime already handles this — returns `"failed"` status, no auto-retry |
