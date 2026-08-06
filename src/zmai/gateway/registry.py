"""Backend registry — backend registration, discovery, and instance management."""

from __future__ import annotations

import logging
from typing import Any

from zmai.errors import BackendError
from zmai.gateway.base import Backend

logger = logging.getLogger("zmai.gateway.registry")


class BackendRegistry:
    """Backend 注册表。

    管理 Backend 类的注册、实例创建、默认 Backend 设置。

    使用方式:
        registry = BackendRegistry()
        registry.register("claude", ClaudeBackend, default=True)
        backend = registry.get("claude")
        backend = registry.get()  # 获取默认 Backend
    """

    def __init__(self) -> None:
        self._backends: dict[str, type[Backend]] = {}
        self._instances: dict[str, Backend] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._default: str | None = None

    def register(
        self,
        name: str,
        backend_cls: type[Backend],
        *,
        default: bool = False,
        config: dict[str, Any] | None = None,
    ) -> None:
        """注册 Backend 类。

        Args:
            name: Backend 名称，唯一标识。
            backend_cls: Backend 子类。
            default: 是否设为默认 Backend。
            config: Backend 配置字典，创建实例时传入。

        Raises:
            BackendError: backend_cls 不是 Backend 子类时抛出。
        """
        if not (isinstance(backend_cls, type) and issubclass(backend_cls, Backend)):
            raise BackendError(f"{backend_cls} 不是 Backend 子类")

        self._backends[name] = backend_cls
        self._configs[name] = config or {}

        # 清理旧实例（配置可能已变更）
        if name in self._instances:
            del self._instances[name]

        if default or self._default is None:
            self._default = name

        logger.info("Backend 已注册: %s (class=%s)", name, backend_cls.__name__)

    def get(self, name: str | None = None) -> Backend:
        """获取 Backend 实例。

        实例按需创建并缓存。

        Args:
            name: Backend 名称。None 时返回默认 Backend。

        Returns:
            Backend 实例。

        Raises:
            BackendError: Backend 未注册时抛出。
        """
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

    def list(self) -> list[str]:
        """列出所有已注册的 Backend 名称。"""
        return list(self._backends.keys())

    def set_default(self, name: str) -> None:
        """设置默认 Backend。

        Args:
            name: Backend 名称。

        Raises:
            BackendError: Backend 未注册时抛出。
        """
        if name not in self._backends:
            raise BackendError(f"Backend 未注册: {name}")
        self._default = name
        logger.info("默认 Backend 已设为: %s", name)

    @property
    def default_name(self) -> str | None:
        """当前默认 Backend 的名称。"""
        return self._default
