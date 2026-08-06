# Backend Plugin API — Design

**Date**: 2026-07-17  
**Status**: Design only, not implemented

---

## 1. Current Problem

Adding a new backend today requires modifying **3 locations** in Runtime's source code:

| Step | File | Change |
|---|---|---|
| 1 | `gateway/backends/__init__.py` | Add `from .new_provider import NewBackend` |
| 2 | `gateway/backends/__init__.py` | Add entry to `BACKEND_METADATA` dict |
| 3 | `gateway/backends/__init__.py` | Add entry to `BACKEND_DEFAULT_CONFIG` dict |

A third-party developer **cannot add a provider without forking the project**.

---

## 2. Goal

Third-party developers should only need to:

```
1. Write one file:    my_provider.py
2. Drop it in:        ~/.zmai/backends/my_provider.py
3. Configure:         zmai auth update my_provider --api-key ...
                      (or set MY_PROVIDER_API_KEY env var)
```

No modification to Runtime source code. No forking.

---

## 3. Plugin Interface

### 3a. `BackendPlugin` Data Class

The contract between a plugin and the Gateway. Every plugin file exports exactly one instance of this.

```python
# zmai/gateway/plugin.py (new)

from dataclasses import dataclass, field
from typing import Any, ClassVar

from zmai.gateway.base import Backend


@dataclass
class BackendPlugin:
    """Plugin descriptor — one per backend provider.

    A plugin file (.py) placed in the plugin directory exports
    exactly one ``plugin`` module-level variable of this type.
    """

    # ── Required ────────────────────────────────────────────
    name: str                              # "my_provider"
    backend_class: type[Backend]           # MyProviderBackend class

    # ── Metadata (optional, with sensible defaults) ─────────
    label: str = ""                        # "My Provider Inc."
    description: str = ""                  # short description

    # ── Defaults ────────────────────────────────────────────
    default_model: str = ""
    default_base_url: str = ""
    default_timeout: int = 120
    default_max_tokens: int = 4096
    default_temperature: float = 0.7

    # ── Env var names ───────────────────────────────────────
    # Default: {NAME}_API_KEY, {NAME}_MODEL, {NAME}_BASE_URL
    env_api_key: str = ""
    env_model: str = ""
    env_base_url: str = ""

    # ── Verification (for CLI ``zmai doctor``) ──────────────
    verify_url: str = ""
    verify_method: str = "GET"
    verify_headers: dict[str, str] = field(default_factory=dict)

    # ── Capability overrides ────────────────────────────────
    # Backend.capabilities already declares these;
    # this lets the plugin advertise capabilities the Gateway
    # can query before instantiation.
    capabilities: set[str] = field(default_factory=lambda: {
        "streaming", "tool_use", "system_prompt",
    })

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name.title()
        if not self.env_api_key:
            self.env_api_key = f"{self.name.upper()}_API_KEY"
        if not self.env_model:
            self.env_model = f"{self.name.upper()}_MODEL"
        if not self.env_base_url:
            self.env_base_url = f"{self.name.upper()}_BASE_URL"

    @property
    def builtin(self) -> bool:
        """True if this plugin ships with ZMAI."""
        return False
```

### 3b. Plugin File Example

A third-party developer creates `~/.zmai/backends/openai.py`:

```python
"""OpenAI Backend Plugin — third-party example."""

from zmai.gateway.base import (
    Backend, BackendCapability, BackendEvent,
    BackendRequest, BackendResponse, TokenUsage,
)
from zmai.gateway.plugin import BackendPlugin
from zmai.tool import ToolCall


class OpenAIBackend(Backend):
    """OpenAI-compatible API backend."""

    name: str = "openai"
    provider: str = "openai"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._api_key = self._config.get(
            "api_key", os.environ.get("OPENAI_API_KEY", "")
        )
        self._model = self._config.get("model", "gpt-4o")
        self._base_url = self._config.get("base_url",
            "https://api.openai.com/v1").rstrip("/")
        self._timeout = int(self._config.get("timeout", 120))
        self._max_tokens = int(self._config.get("max_tokens", 4096))
        self._temperature = float(self._config.get("temperature", 0.7))

    @property
    def model(self) -> str:
        return self._model

    def invoke(self, request: BackendRequest) -> BackendResponse:
        ...  # OpenAI API call

    def stream(self, request: BackendRequest) -> BackendEvent:
        ...  # OpenAI streaming

    @property
    def capabilities(self) -> set[BackendCapability]:
        return {
            BackendCapability.STREAMING,
            BackendCapability.TOOL_USE,
            BackendCapability.SYSTEM_PROMPT,
        }


# ── Plugin descriptor ────────────────────────────────────
# This is the only line the Gateway needs to discover.

plugin = BackendPlugin(
    name="openai",
    backend_class=OpenAIBackend,
    label="OpenAI",
    default_model="gpt-4o",
    default_base_url="https://api.openai.com/v1",
    verify_url="https://api.openai.com/v1/models",
)
```

---

## 4. Discovery Mechanism

### 4a. Plugin Directories (scanned at startup)

| Directory | Purpose |
|---|---|
| `~/.zmai/backends/` | User-installed plugins |
| `<project_root>/.zmai/backends/` | Project-local plugins |
| `zmai/gateway/backends/` | Built-in backends |

Scan order: built-in → project → user. Later directories override earlier ones (user plugins take highest priority).

### 4b. Discovery Algorithm

```python
# zmai/gateway/plugin.py

import importlib.util
import inspect
from pathlib import Path
from typing import Iterator


def discover_plugins(
    extra_dirs: list[Path] | None = None,
) -> Iterator[BackendPlugin]:
    """Discover all BackendPlugin instances from plugin directories.

    Scans:
      1. zmai/gateway/backends/  (built-in, declared in BACKEND_METADATA)
      2. <project>/.zmai/backends/
      3. ~/.zmai/backends/
      4. extra_dirs (test, CLI --plugin-dir)
    """
    dirs = [
        _builtin_dir(),              # built-in
        *(extra_dirs or []),
        Path.cwd() / ".zmai" / "backends",
        Path.home() / ".zmai" / "backends",
    ]

    seen: set[str] = set()

    # Built-in: from BACKEND_METADATA (no files to scan)
    for name in _builtin_backends():
        if name not in seen:
            seen.add(name)
            yield _builtin_plugin(name)

    # Plugin directories: scan .py files
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.stem.startswith("_"):
                continue
            if f.stem in seen:
                continue
            seen.add(f.stem)
            plugin = _load_plugin_file(f)
            if plugin is not None:
                yield plugin


def _load_plugin_file(path: Path) -> BackendPlugin | None:
    """Load a .py file and extract the ``plugin`` variable."""
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        plugin = getattr(mod, "plugin", None)
        if isinstance(plugin, BackendPlugin):
            return plugin
    except Exception:
        pass
    return None
```

### 4c. Built-in Backends as Plugins

Built-in backends (Claude, DeepSeek) are internally represented as `BackendPlugin` instances too, constructed from `BACKEND_METADATA` + `BACKEND_DEFAULT_CONFIG`. This unifies the code path:

```python
# zmai/gateway/plugin.py

def _builtin_plugin(name: str) -> BackendPlugin:
    from zmai.gateway.backends import BACKEND_METADATA, BACKEND_DEFAULT_CONFIG
    info = BACKEND_METADATA[name]
    cfg = BACKEND_DEFAULT_CONFIG.get(name, {})

    # Import the backend class
    import importlib
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])

    return BackendPlugin(
        name=name,
        backend_class=cls,
        label=info["label"],
        default_model=cfg.get("model", info["default_model"]),
        default_base_url=cfg.get("base_url", ""),
        default_timeout=cfg.get("timeout", 120),
        default_max_tokens=cfg.get("max_tokens", 4096),
        default_temperature=cfg.get("temperature", 0.7),
        env_api_key=info["env_api_key"],
        env_model=info["env_model"],
        verify_url=info.get("verify_url", ""),
        verify_method=info.get("verify_method", "GET"),
        verify_headers=info.get("verify_headers", {}),
        capabilities=set(),    # queried from class at runtime
    )
```

---

## 5. Plugin-Aware Gateway

### 5a. `PluginRegistry` (extends `BackendRegistry`)

```python
# zmai/gateway/plugin.py

class PluginRegistry(BackendRegistry):
    """BackendRegistry with plugin discovery and lifecycle."""

    def __init__(
        self,
        plugin_dirs: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self._plugins: dict[str, BackendPlugin] = {}
        self._discover(plugin_dirs)

    def _discover(self, extra_dirs: list[Path] | None = None) -> None:
        """Discover and register all plugins."""
        for plugin in discover_plugins(extra_dirs):
            self._plugins[plugin.name] = plugin
            # Register class; config is assembled lazily on get()
            self.register(plugin.name, plugin.backend_class)

    def get_plugin(self, name: str) -> BackendPlugin | None:
        return self._plugins.get(name)

    def list_plugins(self) -> list[BackendPlugin]:
        return list(self._plugins.values())

    def get(self, name: str | None = None) -> Backend:
        """Get backend instance, auto-assembling config if needed."""
        resolved = name or self._default
        # If instance not yet created, build config from plugin metadata
        if resolved and resolved not in self._instances:
            plugin = self._plugins.get(resolved)
            if plugin:
                cfg = self._build_config(plugin)
                self._configs[resolved] = cfg
        return super().get(name)

    def _build_config(self, plugin: BackendPlugin) -> dict:
        """Assemble full config for a plugin backend.

        Priority: plugin defaults < credentials < project config
                  < env vars < CLI args
        """
        cfg = {
            "model": plugin.default_model,
            "base_url": plugin.default_base_url,
            "timeout": plugin.default_timeout,
            "max_tokens": plugin.default_max_tokens,
            "temperature": plugin.default_temperature,
        }

        # Overlay from AuthStore credentials
        try:
            from zmai.auth import AuthStore
            auth = AuthStore().get_backend(plugin.name)
            if auth:
                for k in ("api_key", "model", "base_url",
                          "timeout", "max_tokens", "temperature"):
                    v = auth.get(k)
                    if v:
                        cfg[k] = v
        except Exception:
            pass

        # Env var overrides (from plugin metadata)
        if plugin.env_api_key:
            import os
            cfg.setdefault("api_key", os.environ.get(plugin.env_api_key, ""))
        if plugin.env_model:
            cfg.setdefault("model", os.environ.get(plugin.env_model, cfg["model"]))
        if plugin.env_base_url:
            cfg.setdefault("base_url", os.environ.get(plugin.env_base_url, cfg["base_url"]))

        cfg["backend"] = plugin.name
        return cfg
```

### 5b. Runtime Integration

```python
# runtime/runtime.py

class Runtime:
    def __init__(self, config=None):
        ...
        # Old: self._gateway = BackendRegistry()
        #      self._register_available_backends()
        # New:
        self._gateway = PluginRegistry()
        #   ↑  discovery + registration happens automatically
        self._auto_select_default_backend()
        ...
```

The Runtime no longer calls `_register_available_backends()`. All discovery is handled by `PluginRegistry.__init__()`.

---

## 6. Configuration

### 6a. Per-Plugin Config in `zmai.json`

```json
{
    "gateway": {
        "default_backend": "auto"
    },
    "backends": {
        "openai": {
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "timeout": 120
        },
        "my_provider": {
            "model": "my-model-v2",
            "temperature": 0.3
        }
    }
}
```

Plugin backends are configured identically to built-in backends — the `backends.{name}` key pattern works for both.

### 6b. Environment Variables

| Convention | Example |
|---|---|
| `{NAME}_API_KEY` | `OPENAI_API_KEY`, `MY_PROVIDER_API_KEY` |
| `{NAME}_MODEL` | `OPENAI_MODEL`, `MY_PROVIDER_MODEL` |
| `{NAME}_BASE_URL` | `OPENAI_BASE_URL`, `MY_PROVIDER_BASE_URL` |

The `BackendPlugin` auto-derives these from `name` (set `env_api_key` explicitly to override).

---

## 7. CLI Integration

### 7a. Plugin Discovery Status

```bash
zmai doctor
  ...
  Backend
    PASS Backend plugins  openai, my_provider (2 user plugins)
    PASS Default backend  deepseek
```

### 7b. Auth for Plugin Backends

```bash
# Plugin backends work with existing auth commands:
zmai auth update openai
zmai auth switch openai

# Plugin directories:
zmai plugin list
zmai plugin install /path/to/my_provider.py
zmai plugin remove my_provider
```

### 7c. `zmai plugin` Subcommand (new)

```python
# cli/main.py

def _run_plugin(argv: list[str]) -> None:
    """Manage plugin backends."""
    plugins_home = Path.home() / ".zmai" / "backends"
    plugins_home.mkdir(parents=True, exist_ok=True)

    sub = argv[0] if argv else "list"
    if sub == "list":
        for f in sorted(plugins_home.glob("*.py")):
            print(f"  {f.stem}")
    elif sub == "install":
        src = Path(argv[1])
        dst = plugins_home / src.name
        shutil.copy2(src, dst)
        print(f"installed: {dst}")
    elif sub == "remove":
        (plugins_home / f"{argv[1]}.py").unlink(missing_ok=True)
        print(f"removed: {argv[1]}")
```

---

## 8. Migration Path

### Phase 1 (current)
`BACKEND_METADATA` + `BACKEND_DEFAULT_CONFIG` hardcoded in `gateway/backends/__init__.py`.

### Phase 2 (this design)
1. Add `BackendPlugin` and `PluginRegistry` to `gateway/plugin.py`
2. Wrap built-in backends as `BackendPlugin` instances internally
3. Add directory scanning for `~/.zmai/backends/`
4. Replace `BackendRegistry` with `PluginRegistry` in Runtime
5. Remove `_register_available_backends()` from Runtime
6. Add `zmai plugin` CLI subcommand
7. Keep `BACKEND_METADATA` as the source for built-in plugins only

### Backward Compatibility
- Existing `BACKEND_METADATA` remains the source for built-in backends
- Existing env vars (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`) continue to work
- Existing `zmai.json` config continues to work
- Plugin backends are additive — no breaking changes

---

## 9. Security Considerations

| Concern | Mitigation |
|---|---|
| Arbitrary code execution from plugins | User must explicitly place files in `~/.zmai/backends/` or run `zmai plugin install` |
| Malicious plugin reads other plugin's keys | Plugin runs in same process; keys are per-plugin in AuthStore; no cross-plugin data sharing |
| Plugin namespace collision | User plugins override built-in; first-come-first-serve within same directory |
| Unsafe imports in plugin | Plugin loading is wrapped in try/except; failure doesn't crash Runtime |

---

## 10. File Map

| File | Status | Purpose |
|---|---|---|
| `src/zmai/gateway/plugin.py` | **New** | `BackendPlugin`, `PluginRegistry`, `discover_plugins()` |
| `src/zmai/gateway/__init__.py` | Edit | Export `BackendPlugin`, `PluginRegistry` |
| `src/zmai/gateway/registry.py` | Unchanged | `BackendRegistry` base class (unchanged) |
| `src/zmai/gateway/backends/__init__.py` | Unchanged | Built-in metadata (kept for backward compat) |
| `src/zmai/runtime/runtime.py` | Edit | Replace `BackendRegistry` + `_register_available_backends()` with `PluginRegistry()` |
| `src/zmai/cli/main.py` | Edit | Add `plugin` subcommand |
| `src/zmai/cli/doctor.py` | Edit | Add plugin discovery diagnostic |
