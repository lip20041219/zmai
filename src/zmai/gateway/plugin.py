"""Backend Plugin API — 插件发现、注册、生命周期管理。

第三方开发者只需创建一个 .py 文件并导出 ``plugin`` 变量，
Gateway 启动时自动发现并注册。

用法:
    # my_provider.py
    from zmai.gateway.plugin import BackendPlugin
    from zmai.gateway.base import Backend

    class MyBackend(Backend):
        name = "my_provider"
        ...

    plugin = BackendPlugin(
        name="my_provider",
        backend_class=MyBackend,
        default_model="my-model",
    )
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zmai.errors import BackendError, CredentialError
from zmai.gateway.base import Backend
from zmai.gateway.registry import BackendRegistry

logger = logging.getLogger("zmai.gateway.plugin")

# ── 插件发现目录 ─────────────────────────────────────────

PLUGIN_DIR_USER = Path.home() / ".zmai" / "backends"
PLUGIN_DIR_PROJECT = Path.cwd() / ".zmai" / "backends"


@dataclass
class BackendPlugin:
    """Backend 插件描述符。

    插件文件 (.py) 必须导出一个模块级 ``plugin`` 变量，
    类型为 ``BackendPlugin``。

    Attributes:
        name: Backend 唯一标识（如 "openai", "my_provider"）。
        backend_class: Backend 子类。
        label: 人类可读名称（如 "OpenAI"）。
        description: 简短描述。

        default_model: 默认模型名称。
        default_base_url: 默认 API 基础 URL。
        default_timeout: 默认请求超时（秒）。
        default_max_tokens: 默认最大 token 数。
        default_temperature: 默认采样温度。

        env_api_key: API Key 环境变量名（默认 {NAME}_API_KEY）。
        env_model: 模型环境变量名（默认 {NAME}_MODEL）。
        env_base_url: Base URL 环境变量名（默认 {NAME}_BASE_URL）。

        verify_url: CLI 验证 URL。
        verify_method: 验证请求方法。
        verify_headers: 验证请求头。
    """

    # ── Required ─────────────────────────────────────────
    name: str
    backend_class: type[Backend]

    # ── Metadata ─────────────────────────────────────────
    label: str = ""
    description: str = ""

    # ── Defaults ─────────────────────────────────────────
    default_model: str = ""
    default_base_url: str = ""
    default_timeout: int = 120
    default_max_tokens: int = 4096
    default_temperature: float = 0.7

    # ── Env var names ────────────────────────────────────
    env_api_key: str = ""
    env_model: str = ""
    env_base_url: str = ""

    # ── Verification ─────────────────────────────────────
    verify_url: str = ""
    verify_method: str = "GET"
    verify_headers: dict[str, str] = field(default_factory=dict)

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
        """True 表示此插件随 ZMAI 官方发布。"""
        return False

    def to_metadata(self) -> dict[str, Any]:
        """转换为 BACKEND_METADATA 格式（向后兼容）。"""
        return {
            "label": self.label,
            "default_model": self.default_model,
            "env_api_key": self.env_api_key,
            "env_model": self.env_model,
            "module": self.backend_class.__module__,
            "class": self.backend_class.__name__,
            "verify_url": self.verify_url,
            "verify_method": self.verify_method,
            "verify_headers": self.verify_headers,
        }

    def to_default_config(self) -> dict[str, Any]:
        """转换为 BACKEND_DEFAULT_CONFIG 格式（向后兼容）。"""
        return {
            "model": self.default_model,
            "base_url": self.default_base_url,
            "timeout": self.default_timeout,
            "max_tokens": self.default_max_tokens,
            "temperature": self.default_temperature,
        }


# ── 插件发现 ────────────────────────────────────────────────


def discover_plugins(
    extra_dirs: list[Path] | None = None,
) -> Iterator[BackendPlugin]:
    """发现所有 BackendPlugin。

    扫描顺序（低 → 高优先级，同名覆盖）:
      1. zmai.gateway.backends 包内的官方 Backend（通过 BACKEND_METADATA）
      2. 项目目录 .zmai/backends/
      3. 用户目录 ~/.zmai/backends/
      4. extra_dirs（测试注入）
    """
    seen: set[str] = set()

    # 1. Official built-in backends
    from zmai.gateway.backends import BACKEND_METADATA
    for name in BACKEND_METADATA:
        if name not in seen:
            seen.add(name)
            yield _builtin_plugin(name)

    # 2. Plugin directories
    dirs = list(extra_dirs or [])
    dirs.append(PLUGIN_DIR_PROJECT)
    dirs.append(PLUGIN_DIR_USER)

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
    """加载 .py 文件并提取模块级 ``plugin`` 变量。"""
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        plugin = getattr(mod, "plugin", None)
        if isinstance(plugin, BackendPlugin):
            logger.info("插件已发现: %s (%s)", plugin.name, path)
            return plugin
        elif plugin is not None:
            logger.warning("插件文件 %s 的 plugin 变量类型错误: %s", path, type(plugin))
    except Exception as e:
        logger.warning("插件加载失败 %s: %s", path, e)
    return None


def _builtin_plugin(name: str) -> BackendPlugin:
    """从 BACKEND_METADATA 构造内置 Backend 的插件描述符。"""
    from zmai.gateway.backends import BACKEND_DEFAULT_CONFIG, BACKEND_METADATA
    info = BACKEND_METADATA[name]
    cfg = BACKEND_DEFAULT_CONFIG.get(name, {})

    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])

    return BackendPlugin(
        name=name,
        backend_class=cls,
        label=info.get("label", name.title()),
        default_model=cfg.get("model", info.get("default_model", "")),
        default_base_url=cfg.get("base_url", ""),
        default_timeout=cfg.get("timeout", 120),
        default_max_tokens=cfg.get("max_tokens", 4096),
        default_temperature=cfg.get("temperature", 0.7),
        env_api_key=info.get("env_api_key", f"{name.upper()}_API_KEY"),
        env_model=info.get("env_model", f"{name.upper()}_MODEL"),
        verify_url=info.get("verify_url", ""),
        verify_method=info.get("verify_method", "GET"),
        verify_headers=info.get("verify_headers", {}),
    )


# ── 插件注册表 ──────────────────────────────────────────────


class PluginRegistry(BackendRegistry):
    """BackendRegistry 扩展 — 支持插件自动发现和生命周期管理。

    用法:
        registry = PluginRegistry(config)
        # 自动发现并注册所有插件
        backend = registry.get("claude")    # 自动装配配置
    """

    def __init__(
        self,
        config: Any = None,
        plugin_dirs: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._plugins: dict[str, BackendPlugin] = {}
        self._discover(plugin_dirs)
        self._auto_select_default()

    # ── 发现与注册 ──────────────────────────────────────

    def _discover(self, extra_dirs: list[Path] | None = None) -> None:
        """发现所有插件并注册到 Registry。"""
        for plugin in discover_plugins(extra_dirs):
            self._plugins[plugin.name] = plugin
            self.register(plugin.name, plugin.backend_class)
            logger.info("Backend 已注册: %s (plugin)", plugin.name)

    # ── 插件查询 ────────────────────────────────────────

    def get_plugin(self, name: str) -> BackendPlugin | None:
        """获取指定名称的插件描述符。"""
        return self._plugins.get(name)

    def list_plugins(self) -> list[BackendPlugin]:
        """列出所有已发现的插件。"""
        return list(self._plugins.values())

    def list_user_plugins(self) -> list[BackendPlugin]:
        """列出所有非官方插件。"""
        return [p for p in self._plugins.values() if not p.builtin]

    # ── 实例化（覆写父类，加入自动装配 + Fallback） ────

    def get(self, name: str | None = None) -> Backend:
        """获取 Backend 实例，按需自动装配配置。

        如果请求的 Backend 没有 API Key，自动寻找第一个有 Key 的
        可用 Backend 作为 Fallback，并记录日志。

        Args:
            name: Backend 名称。None 表示使用默认 Backend。

        Returns:
            Backend 实例。

        Raises:
            BackendError: 所有 Backend 均不可用时抛出。
        """
        resolved = name or self._default
        if resolved is None:
            raise BackendError("未设置默认 Backend，且未指定 Backend 名称")
        if resolved not in self._backends:
            raise BackendError(f"Backend 未注册: {resolved}")

        # 如果实例已缓存，直接返回
        if resolved in self._instances:
            return self._instances[resolved]

        # 构建配置并检查 API Key
        plugin = self._plugins.get(resolved)
        if plugin:
            cfg = self._build_config(plugin)
            if not cfg.get("api_key"):
                # 没有 API Key → Fallback
                return self._fallback(failed=resolved)
            self._configs[resolved] = cfg
        elif self._config:
            bc = self._config.get(f"backends.{resolved}")
            if isinstance(bc, dict):
                self._configs[resolved] = dict(bc)

        return super().get(name)

    def _fallback(self, failed: str) -> Backend:
        """当前 Backend 不可用时，自动寻找第一个可用 Backend。

        Args:
            failed: 失败的 Backend 名称（用于日志）。

        Returns:
            第一个有 API Key 的 Backend 实例。

        Raises:
            BackendError: 没有任何 Backend 有 API Key 时抛出。
        """
        logger.warning("Backend fallback: %s 无 API Key，尝试其他 Backend", failed)

        for plugin_name, plugin in self._plugins.items():
            if plugin_name == failed:
                continue
            if plugin_name in self._instances:
                # 已缓存的实例说明之前可用，直接使用
                logger.info("Backend fallback: %s → %s (cached)", failed, plugin_name)
                self._default = plugin_name
                return self._instances[plugin_name]
            cfg = self._build_config(plugin)
            if cfg.get("api_key"):
                self._configs[plugin_name] = cfg
                self._default = plugin_name
                logger.info("Backend fallback: %s → %s", failed, plugin_name)
                return super().get(plugin_name)

        raise BackendError("未发现可用模型。请配置 API Key。")

    # ── 配置装配 ────────────────────────────────────────

    def _build_config(self, plugin: BackendPlugin) -> dict[str, Any]:
        """按优先级合并配置。

        优先级（低 → 高）:
          plugin defaults < credentials < config file < env vars < CLI

        使用 CredentialResolver 统一解析，不再在此处自行拼装 env/file 逻辑。
        """
        cfg: dict[str, Any] = {
            "model": plugin.default_model,
            "base_url": plugin.default_base_url,
            "timeout": plugin.default_timeout,
            "max_tokens": plugin.default_max_tokens,
            "temperature": plugin.default_temperature,
        }

        # 使用统一的 CredentialResolver 读取最终凭据
        from zmai.auth.resolver import CredentialResolver
        status = CredentialResolver().get_status(plugin.name)
        if status.configured:
            cfg["api_key"] = status.api_key
            cfg["model"] = status.model or cfg["model"]
            cfg["base_url"] = status.base_url or cfg["base_url"]
            cfg["timeout"] = status.timeout or cfg["timeout"]
            cfg["max_tokens"] = status.max_tokens or cfg["max_tokens"]
            cfg["temperature"] = status.temperature or cfg["temperature"]

        cfg["backend"] = plugin.name

        # 记录诊断信息
        if status.conflict:
            logger.warning(
                "Backend %s: credential conflict detected, using %s",
                plugin.name, status.source,
            )

        return cfg

    # ── 默认 Backend 选择 ──────────────────────────────

    def _auto_select_default(self) -> None:
        """自动选择默认 Backend。

        优先级:
          1. AuthStore.active_backend（~/.zmai/credentials 中的活跃标记）
          2. Config gateway.default_backend（非 "auto"）
          3. 第一个有环境变量凭据的 Backend
          4. 第一个已注册的 Backend
        """
        # 1. AuthStore 文件中记录的活跃 Backend
        try:
            from zmai.auth import AuthStore
            store_active = AuthStore().get_active_backend()
            if store_active and store_active in self._backends:
                self._default = store_active
                return
        except CredentialError:
            # 凭据文件存在但无法解密，跳过此步，由 preflight 处理
            pass
        except Exception:
            pass

        # 2. Config 指定
        if self._config:
            cfg_default = self._config.get("gateway.default_backend", "auto")
            if cfg_default and cfg_default != "auto" and cfg_default in self._backends:
                self._default = cfg_default
                return

        # 3. 第一个有环境变量凭据的 Backend
        for name, plugin in self._plugins.items():
            if os.environ.get(plugin.env_api_key):
                self._default = name
                return

        # 4. 第一个已注册
        if self._backends:
            self._default = next(iter(self._backends))
