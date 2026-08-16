"""WindowsCredentialStore — Windows Credential Manager 凭据存储。

使用 ctypes 直接调用 Windows Credential Manager API（advapi32.dll），
不依赖 pywin32 / keyring 等第三方库，保持 ZMAI 零运行依赖。

凭据由 Windows DPAPI 加密保护，绑定当前用户。

不依赖：
  - credentials.key 文件
  - XOR obfuscation
  - chmod(0o600)

凭据标识格式：
  Target: ZMAI_<provider> （如 ZMAI_deepseek）
  Type: CRED_TYPE_GENERIC
  Persist: CRED_PERSIST_LOCAL_MACHINE
"""

from __future__ import annotations

import ctypes
import json
import logging
import sys
from ctypes import wintypes

from zmai.auth.store_base import (
    CredentialStore,
    CredentialStoreError,
    StoredCredential,
)

logger = logging.getLogger("zmai.auth.store_wincred")

CREDENTIAL_TARGET_PREFIX = "ZMAI_"

# ── Windows Credential Manager 常量 ─────────────────────────
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168  # WinError: 指定的目标不存在。


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _CREDENTIALW(ctypes.Structure):
    pass


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)

_CREDENTIALW._fields_ = [
    ("Flags", wintypes.DWORD),
    ("Type", wintypes.DWORD),
    ("TargetName", wintypes.LPWSTR),
    ("Comment", wintypes.LPWSTR),
    ("LastWritten", _FILETIME),
    ("CredentialBlobSize", wintypes.DWORD),
    ("CredentialBlob", ctypes.c_void_p),
    ("Persist", wintypes.DWORD),
    ("AttributeCount", wintypes.DWORD),
    ("Attributes", ctypes.c_void_p),
    ("TargetAlias", wintypes.LPWSTR),
    ("UserName", wintypes.LPWSTR),
]


class _WinCred:
    """advapi32.dll Credential Manager 的 ctypes 封装（零第三方依赖）。"""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows Credential Manager 仅在 Windows 上可用")
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._bind()

    def _bind(self) -> None:
        a = self._advapi32
        a.CredReadW.restype = wintypes.BOOL
        a.CredReadW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(_PCREDENTIALW),
        ]
        a.CredWriteW.restype = wintypes.BOOL
        a.CredWriteW.argtypes = [_PCREDENTIALW, wintypes.DWORD]
        a.CredDeleteW.restype = wintypes.BOOL
        a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        a.CredEnumerateW.restype = wintypes.BOOL
        a.CredEnumerateW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(ctypes.POINTER(_PCREDENTIALW)),
        ]
        a.CredFree.restype = None
        a.CredFree.argtypes = [ctypes.c_void_p]

    # ── 底层操作 ──────────────────────────────────────────

    def read(self, target: str) -> bytes | None:
        """按目标名读取凭据 blob；不存在返回 None。"""
        cred_ptr = _PCREDENTIALW()
        ok = self._advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
        if not ok:
            err = ctypes.get_last_error()
            if err == ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError(err)
        try:
            if not cred_ptr:
                return None
            cred = cred_ptr.contents
            blob_size = int(cred.CredentialBlobSize)
            if blob_size <= 0 or not cred.CredentialBlob:
                return None
            return ctypes.string_at(cred.CredentialBlob, blob_size)
        finally:
            self._advapi32.CredFree(cred_ptr)

    def write(self, target: str, blob: bytes) -> None:
        cred = _CREDENTIALW()
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.UserName = "zmai"
        cred.CredentialBlob = ctypes.cast(
            ctypes.create_string_buffer(blob), ctypes.c_void_p
        )
        cred.CredentialBlobSize = len(blob)
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        ok = self._advapi32.CredWriteW(ctypes.byref(cred), 0)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def delete(self, target: str) -> bool:
        ok = self._advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0)
        if ok:
            return True
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return False
        raise ctypes.WinError(err)

    def enumerate(self, filter_str: str) -> list[str]:
        count = wintypes.DWORD(0)
        creds_ptr = ctypes.POINTER(_PCREDENTIALW)()
        ok = self._advapi32.CredEnumerateW(
            filter_str, 0, ctypes.byref(count), ctypes.byref(creds_ptr)
        )
        if not ok:
            err = ctypes.get_last_error()
            if err == ERROR_NOT_FOUND:
                return []
            raise ctypes.WinError(err)
        try:
            targets: list[str] = []
            for i in range(int(count.value)):
                item = creds_ptr[i]
                if item:
                    name = item.contents.TargetName
                    if name:
                        targets.append(name)
            return targets
        finally:
            self._advapi32.CredFree(creds_ptr)


class WindowsCredentialStore(CredentialStore):
    """Windows Credential Manager 凭据存储后端。

    要求:
      - Windows 操作系统

    凭据以 JSON 格式存储在 Windows Credential Manager 中，
    包含 provider、api_key 及其他可选字段。
    """

    def __init__(self) -> None:
        self._wincred: _WinCred | None = None
        self._available = self._check_available()

    # ── 可用性检测 ──────────────────────────────────────

    def _check_available(self) -> bool:
        """检测 advapi32 Credential Manager API 是否可用。"""
        if sys.platform != "win32":
            return False
        try:
            self._wincred = _WinCred()
            return True
        except Exception as e:
            logger.debug("Windows Credential Manager 不可用: %s", e)
            return False

    def is_available(self) -> bool:
        return self._available

    def _require(self) -> _WinCred:
        """返回底层封装；不可用时抛出明确错误。"""
        if not self._available or self._wincred is None:
            raise CredentialStoreError("Windows Credential Manager 不可用")
        return self._wincred

    # ── 内部 helpers ─────────────────────────────────────

    @staticmethod
    def _target_name(provider: str) -> str:
        """生成 Windows Credential Manager 中的目标名称。"""
        return f"{CREDENTIAL_TARGET_PREFIX}{provider}"

    @staticmethod
    def _provider_from_target(target: str) -> str:
        """从目标名称反推 provider。"""
        if target.startswith(CREDENTIAL_TARGET_PREFIX):
            return target[len(CREDENTIAL_TARGET_PREFIX):]
        return target

    def _to_blob(self, credential: StoredCredential) -> str:
        """将凭据序列化为 JSON 字符串（不含 provider 字段）。"""
        data = {
            "api_key": credential.api_key,
            "model": credential.model,
            "base_url": credential.base_url,
            "timeout": credential.timeout,
            "max_tokens": credential.max_tokens,
            "temperature": credential.temperature,
        }
        return json.dumps(data)

    def _from_blob(self, provider: str, blob: str | bytes) -> StoredCredential:
        """从 JSON 反序列化凭据。

        Credential Manager 将字符串以 UTF-16-LE 编码存储，
        读取时返回 bytes。
        """
        if isinstance(blob, bytes):
            try:
                blob = blob.decode("utf-16-le").rstrip("\x00").strip()
            except UnicodeDecodeError:
                blob = blob.decode("utf-8", errors="replace")
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise CredentialStoreError(
                f"凭据数据损坏: {e}"
            ) from e
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
        """从 Windows Credential Manager 读取凭据。

        Args:
            provider: Backend 名称。

        Returns:
            StoredCredential 或 None（不存在时）。
        """
        wc = self._require()
        target = self._target_name(provider)
        try:
            blob = wc.read(target)
        except CredentialStoreError:
            raise
        except OSError as e:
            raise CredentialStoreError(f"读取凭据失败: {e}") from e

        if blob is None:
            return None
        return self._from_blob(provider, blob)

    def set(self, provider: str, credential: StoredCredential) -> None:
        """保存凭据到 Windows Credential Manager。

        Args:
            provider: Backend 名称。
            credential: 凭据数据。

        Raises:
            CredentialStoreError: 保存失败时抛出。
        """
        wc = self._require()
        blob_text = self._to_blob(credential)
        blob = blob_text.encode("utf-16-le")
        target = self._target_name(provider)
        try:
            wc.write(target, blob)
        except OSError as e:
            raise CredentialStoreError(f"保存凭据失败: {e}") from e

        logger.debug("凭据已保存到 Windows Credential Manager: %s", target)

    def delete(self, provider: str) -> bool:
        """从 Windows Credential Manager 删除凭据。

        Returns:
            True 已删除，False 不存在。
        """
        wc = self._require()
        target = self._target_name(provider)
        try:
            return wc.delete(target)
        except OSError as e:
            raise CredentialStoreError(f"删除凭据失败: {e}") from e

    def exists(self, provider: str) -> bool:
        """检查凭据是否存在。"""
        return self.get(provider) is not None

    def list_providers(self) -> list[str]:
        """列出所有 ZMAI 凭据的 provider 名称。"""
        wc = self._require()
        try:
            targets = wc.enumerate(f"{CREDENTIAL_TARGET_PREFIX}*")
        except OSError:
            return []

        providers = []
        for target in targets or []:
            provider = self._provider_from_target(target)
            if provider:
                providers.append(provider)
        return providers
