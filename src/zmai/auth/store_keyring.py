"""KeyringCredentialStore — 跨平台 Keychain 凭据存储（keyring 库）。

使用 keyring 库对接操作系统原生凭据存储：
  - macOS: Keychain
  - Linux: Secret Service (GNOME Keyring / KDE Wallet)
  - Windows: Windows Credential Manager（但推荐使用 WindowsCredentialStore）

依赖:

安全属性:
  - 凭据由操作系统原生加密保护
  - 不落盘明文文件
  - 不自行实现加密算法
"""

from __future__ import annotations

import json
import logging

from zmai.auth.store_base import (
    CredentialStore,
    CredentialStoreError,
    CredentialStoreUnavailableError,
    StoredCredential,
)

logger = logging.getLogger("zmai.auth.store_keyring")

KEYRING_SERVICE = "zmai"


class KeyringCredentialStore(CredentialStore):
    """keyring 库凭据存储后端。

    所有平台通用，通过 keyring 库对接操作系统原生凭据存储。
    keyring 会自动选择当前平台的最佳后端。
    """

    def __init__(self) -> None:
        self._available = self._check_available()
        self._keyring = None

    # ── 可用性检测 ──────────────────────────────────────

    @staticmethod
    def _check_available() -> bool:
        """检测 keyring 库及其后端是否可用。"""
        try:
            import keyring
            # 验证后端可用（不抛出异常）
            bk = keyring.get_keyring()
            name = type(bk).__name__
            # 某些后端名称包含 "fail"、"null"、"plain" 表示不可用
            unavailable = ("fail", "null", "plain", "dummy", "test")
            if any(x in name.lower() for x in unavailable):
                logger.debug("keyring 后端不可用: %s", name)
                return False
            logger.debug("keyring 后端可用: %s", name)
            return True
        except ImportError:
            logger.debug("keyring 库未安装")
            return False
        except Exception as e:
            logger.debug("keyring 检测失败: %s", e)
            return False

    def is_available(self) -> bool:
        return self._available

    # ── 内部 helpers ─────────────────────────────────────

    def _get_keyring(self):
        """延迟加载 keyring 模块。"""
        if self._keyring is None:
            import keyring
            self._keyring = keyring
        return self._keyring

    def _to_blob(self, credential: StoredCredential) -> str:
        """将凭据序列化为 JSON 字符串。"""
        data = {
            "api_key": credential.api_key,
            "model": credential.model,
            "base_url": credential.base_url,
            "timeout": credential.timeout,
            "max_tokens": credential.max_tokens,
            "temperature": credential.temperature,
        }
        return json.dumps(data)

    def _from_blob(self, provider: str, blob: str) -> StoredCredential:
        """从 JSON 字符串反序列化凭据。"""
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as e:
            raise CredentialStoreError(f"凭据数据损坏: {e}") from e
        return StoredCredential(
            provider=provider,
            api_key=data.get("api_key", ""),
            model=data.get("model", ""),
            base_url=data.get("base_url", ""),
            timeout=data.get("timeout", 0),
            max_tokens=data.get("max_tokens", 0),
            temperature=data.get("temperature", 0.0),
        )

    # ── CredentialStore 接口实现 ─────────────────────────

    def get(self, provider: str) -> StoredCredential | None:
        """读取凭据。

        Args:
            provider: Backend 名称。

        Returns:
            StoredCredential 或 None。
        """
        if not self._available:
            # keyring 不可用时返回 None（不抛异常），与 get() 的语义一致
            return None

        kr = self._get_keyring()
        try:
            blob = kr.get_password(KEYRING_SERVICE, provider)
        except Exception as e:
            raise CredentialStoreError(f"读取凭据失败: {e}") from e

        if blob is None:
            return None
        return self._from_blob(provider, blob)

    def set(self, provider: str, credential: StoredCredential) -> None:
        """保存凭据。

        Args:
            provider: Backend 名称。
            credential: 凭据数据。
        """
        if not self._available:
            raise CredentialStoreUnavailableError(
                "keyring 凭据存储不可用。请安装 keyring 库：pip install keyring"
            )

        kr = self._get_keyring()
        blob = self._to_blob(credential)
        try:
            kr.set_password(KEYRING_SERVICE, provider, blob)
        except Exception as e:
            raise CredentialStoreError(f"保存凭据失败: {e}") from e

        logger.debug("凭据已保存到系统 keychain: %s", provider)

    def delete(self, provider: str) -> bool:
        """删除凭据。"""
        if not self._available:
            # keyring 不可用时返回 False（不抛异常），与 delete() 的语义一致
            return False

        kr = self._get_keyring()
        try:
            kr.delete_password(KEYRING_SERVICE, provider)
            return True
        except kr.errors.PasswordDeleteError:
            return False
        except Exception as e:
            raise CredentialStoreError(f"删除凭据失败: {e}") from e

    def exists(self, provider: str) -> bool:
        """检查凭据是否存在。"""
        return self.get(provider) is not None

        """列出所有 provider。

        注意: keyring 库不直接支持列出所有条目。
        此方法返回空列表，实际凭据通过 credential_resolver 统一管理。
        """
        return []
