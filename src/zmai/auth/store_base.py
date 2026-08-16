"""CredentialStore — 凭据存储抽象基类。

定义统一的凭据存储接口。
所有平台后端（Windows Credential Manager、macOS Keychain、Linux Secret Service）
必须实现此接口。

优先级（由 CredentialResolver 协调）:
  1. OS 原生凭据存储（CredentialStore 实现）
  2. 环境变量（最高优先级覆盖）
  3. 配置文件（zmai.json / ~/.zmai/config.json）
  4. legacy credentials 文件（XOR obfuscated，AuthStore）

安全边界:
  - API Key 不得出现在日志中
  - API Key 不得出现在异常信息中
  - API Key 不得出现在测试输出中
  - API Key 不得写入 Git
  - get() 返回的凭据仅存在于进程内存中
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("zmai.auth.store_base")


class CredentialStoreError(Exception):
    """凭据存储操作错误。"""


class CredentialStoreUnavailableError(CredentialStoreError):
    """凭据存储后端不可用。

    当系统没有可用的安全凭据存储时抛出。
    不会静默降级到不安全的存储。

    用户应:
      1. 安装/启用系统凭据后端（Windows Credential Manager / macOS Keychain / Linux Secret Service）
      2. 使用显式环境变量
      3. 明确启用 legacy fallback（仅迁移用）
    """


@dataclass
class StoredCredential:
    """凭据数据。

    安全的凭据容器，to_dict() 不输出 api_key。
    """

    provider: str
    api_key: str
    model: str = ""
    base_url: str = ""
    timeout: int = 0
    max_tokens: int = 0
    temperature: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为 dict（不含 api_key，不含 provider）。"""
        return {
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }


class CredentialStore(ABC):
    """凭据存储抽象基类。

    所有平台后端必须实现此接口。

    使用方式:
        store = WindowsCredentialStore()
        cred = store.get("deepseek")
        store.delete("deepseek")
    """

    @abstractmethod
    def get(self, provider: str) -> StoredCredential | None:
        """获取指定 provider 的凭据。

        Args:
            provider: Backend 名称（如 "deepseek", "claude"）。

        Returns:
            StoredCredential 或 None（不存在时）。
        """
        ...

    def set(self, provider: str, credential: StoredCredential) -> None:
        """存储凭据。

        Args:
            provider: Backend 名称。
            credential: 凭据数据。

        Raises:
            CredentialStoreError: 存储失败时抛出。
        """
        raise CredentialStoreError("此存储后端不支持 set 操作")

    @abstractmethod
    def delete(self, provider: str) -> bool:
        """删除指定 provider 的凭据。

        Args:
            provider: Backend 名称。

        Returns:
            True 存在且已删除，False 不存在。
        """
        ...

    @abstractmethod
    def exists(self, provider: str) -> bool:
        """检查指定 provider 的凭据是否存在。"""
        ...

    def list_providers(self) -> list[str]:
        """列出所有已存储凭据的 provider 名称。

        子类可覆盖返回实际存储的 provider 列表。
        """
        return []

    @abstractmethod
    def is_available(self) -> bool:
        """检查当前平台是否支持此凭据存储后端。

        Returns:
            True 可用，False 不可用（此时 get/set 应抛出 CredentialStoreError）。
        """
        ...


class NullCredentialStore(CredentialStore):
    """空实现 — 所有操作返回空/False。

    当没有可用的平台后端时使用。
    调用 set() 时抛出 CredentialStoreUnavailableError。
    """

    def get(self, provider: str) -> StoredCredential | None:
        return None

    def set(self, provider: str, credential: StoredCredential) -> None:
        raise CredentialStoreUnavailableError(
            f"无可用的凭据存储后端，无法保存 {provider} 的凭据。\n"
            "请安装系统凭据后端：\n"
            "  Windows: 内置 Windows Credential Manager（自动使用）\n"
            "  Linux:   pip install keyring secretstorage\n"
            "或使用环境变量配置 API Key。"
        )

    def delete(self, provider: str) -> bool:
        return False

    def exists(self, provider: str) -> bool:
        return False

    def list_providers(self) -> list[str]:
        return []

    def is_available(self) -> bool:
        return False


def get_default_credential_store() -> CredentialStore:
    """获取当前平台默认的安全凭据存储后端。

    优先级:
      1. Windows → WindowsCredentialStore（win32cred，零依赖）
      2. macOS/Linux → KeyringCredentialStore（keyring 库，需 pip install）
      3. 以上都不可用 → NullCredentialStore（显式不可用，抛出异常）

    Returns:
        可用的 CredentialStore 实例。
    """
    # 1. Windows Credential Manager（零额外依赖，已内置）
    try:
        from zmai.auth.store_wincred import WindowsCredentialStore
        store = WindowsCredentialStore()
        if store.is_available():
            return store
    except Exception:
        pass

    # 2. keyring 库（跨平台，需 pip install keyring）
    try:
        from zmai.auth.store_keyring import KeyringCredentialStore
        store = KeyringCredentialStore()
        if store.is_available():
            return store
    except Exception:
        pass

    # 3. 无可用安全后端 → 显式不可用
    return NullCredentialStore()
