"""AuthStore — 凭据文件存储（legacy obfuscation）。

安全声明：
  ZMAI 当前使用 XOR + Base64 对凭据文件进行混淆（obfuscation），
  这不是加密（encryption）。

  此方案：
  - ❌ 不提供机密性保证（可被已知明文攻击破解）
  - ❌ 不提供完整性保证（无 HMAC，密文可被篡改）
  - ❌ 不提供认证保证（无 AEAD）
  - ❌ 不防止已获得文件系统访问权限的本地攻击者

  在 Windows 平台上，chmod(0o600) 不生效，
  凭据文件可被任何有文件读取权限的用户/进程访问。

  如果你需要真正的凭据安全存储：
  - Windows: 使用 Windows Credential Manager（推荐）
  - macOS: 使用 Keychain
  - Linux: 使用 Secret Service / libsecret

  ZMAI 的 CredentialResolver 会按以下顺序查找凭据：
    1. OS 原生凭据存储（如果已配置）
    2. 环境变量（最高优先级覆盖）
    3. 配置文件（zmai.json / ~/.zmai/config.json）
    4. legacy credentials 文件（XOR obfuscated）
"""

from __future__ import annotations

import json
import logging
import os
import sys
import hashlib
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.errors import CredentialError

logger = logging.getLogger("zmai.auth.store")

AUTH_DIR = Path.home() / ".zmai"
CREDENTIALS_FILE = AUTH_DIR / "credentials"
KEY_FILE = AUTH_DIR / "credentials.key"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 密钥管理（2026-07 升级：文件式稳定密钥） ──────────────


def _resolve_key() -> bytes:
    """获取稳定的混淆密钥。

    策略（2026-07 升级）:
      1. 优先使用 ~/.zmai/credentials.key 文件中的稳定密钥（存在则读取）
      2. 不存在则创建新密钥文件（os.urandom 32 字节）
      3. 永不自动切换回 MachineGuid / 硬编码 fallback

    稳定性保证:
      文件不删除 → 密钥恒定。不受 MachineGuid / 注册表 / machine-id 影响。
    """
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if KEY_FILE.exists():
        try:
            raw = KEY_FILE.read_text(encoding="utf-8").strip()
            key_bytes = base64.b64decode(raw)
            if len(key_bytes) != 32:
                raise ValueError(f"密钥长度错误: {len(key_bytes)}, 期望 32")
            return hashlib.sha256(key_bytes).digest()
        except Exception as e:
            raise CredentialError(
                f"凭据密钥文件损坏或格式错误: {e}\n"
                f"  文件: {KEY_FILE}\n"
                f"  请删除后重新配置（凭据将重新加密）。",
                reason="KEY_FILE_CORRUPTED",
            )

    # 首次运行：生成 32 字节随机密钥
    new_key = os.urandom(32)
    try:
        KEY_FILE.write_text(
            base64.b64encode(new_key).decode(), encoding="utf-8"
        )
        # 权限 600（Unix），Windows 下 ignored
        try:
            KEY_FILE.chmod(0o600)
        except Exception:
            pass
    except Exception as e:
        raise CredentialError(
            f"无法创建凭据密钥文件: {e}\n"
            f"  请检查 {AUTH_DIR} 目录的写入权限。",
            reason="KEY_FILE_CREATE_FAILED",
        )

    logger.info("已创建新的凭据密钥文件: %s", KEY_FILE)
    return hashlib.sha256(new_key).digest()


# ── 旧密钥（仅迁移兼容，不在新系统中作为主密钥使用） ──────


def _legacy_machine_keys() -> list[bytes]:
    """生成所有可能的旧机器密钥列表（迁移用）。

    返回有序列表，按可能性从高到低:
      1. Windows MachineGuid（如果可读）
      2. 硬编码 fallback（始终可用）

    注意:
      本函数永不作为主密钥策略使用。
      仅在迁移场景中尝试用旧密钥解密已有凭据。
    """
    keys: list[bytes] = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as k:
                guid, _ = winreg.QueryValueEx(k, "MachineGuid")
                keys.append(hashlib.sha256(guid.encode()).digest())
        except Exception:
            pass
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            keys.append(hashlib.sha256(Path(p).read_bytes().strip()).digest())
        except Exception:
            pass
    # 硬编码 fallback 始终是最后兜底
    keys.append(hashlib.sha256(b"zmai-fallback-key").digest())
    return keys


# ── 混淆/反混淆（legacy — 非加密，无安全保证） ──────────


def _encrypt(plain: str, key: bytes) -> str:
    """XOR + base64 混淆（非加密）。

    安全属性（全部缺失）:
      - 无机密性：已知明文攻击可恢复密钥
      - 无完整性：无 HMAC，密文可篡改
      - 无认证：无 AEAD
      - 确定性：无 IV，同一明文每次加密结果相同

    仅用于阻止偶然的文件读取。
    """
    data = plain.encode()
    encrypted = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    return base64.b64encode(encrypted).decode()


def _decrypt(cipher: str, key: bytes) -> str:
    """XOR + base64 反混淆。"""
    encrypted = base64.b64decode(cipher.encode())
    decrypted = bytes(
        encrypted[i] ^ key[i % len(key)] for i in range(len(encrypted))
    )
    return decrypted.decode()


def _try_decode(cipher: str, key: bytes) -> dict[str, Any] | None:
    """尝试用指定密钥解密凭据文件。

    Args:
        cipher: base64 编码的密文。
        key: 解密密钥（32 字节）。

    Returns:
        解密成功的 dict，或 None（任何失败均返回 None 而非异常）。
    """
    try:
        encrypted = base64.b64decode(cipher.encode())
    except Exception:
        return None  # base64 损坏

    try:
        decrypted = bytes(
            encrypted[i] ^ key[i % len(key)] for i in range(len(encrypted))
        )
        plain = decrypted.decode("utf-8")
    except UnicodeDecodeError:
        return None  # 密钥不匹配
    except Exception:
        return None

    try:
        return json.loads(plain)
    except json.JSONDecodeError:
        return None  # 解密后内容非 JSON


# ── 存储类 ─────────────────────────────────────────────────


class AuthStore:
    """凭据文件存储（legacy obfuscation — 非加密）。

    警告：
      本模块使用 XOR + Base64 混淆凭据，不是真正的加密。
      仅适用于阻止偶然的文件读取，不提供安全保护。
      新代码应使用 CredentialStore 抽象（Windows Credential Manager 等）。

    职责：混淆文件 ~/.zmai/credentials 的读写。
    仅做文件 I/O，不查询环境变量。环境变量覆盖由 CredentialResolver 处理。

    混淆密钥：
      使用 ~/.zmai/credentials.key 文件中的稳定密钥。
      旧 MachineGuid / fallback 密钥仅用于迁移兼容。

    文件结构:
        {"version": 1, "active_backend": "deepseek",
         "backends": {"deepseek": {"api_key": "...", "model": "...", ...}}}
    """

    _data: dict[str, Any]

    def __init__(self) -> None:
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        self._key = _resolve_key()
        self._data = self._load()

    # ── 内部加载/保存 ─────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """加载并解密凭据文件。

        解密策略（按顺序尝试）:
          1. 稳定密钥（credentials.key）
          2. 旧 MachineGuid 密钥（迁移兼容）
          3. 旧硬编码 fallback 密钥（迁移兼容）

        Returns:
            解密成功后的凭据 dict。

        Raises:
            CredentialError: 所有密钥均无法解密，且不是因为文件不存在。
        """
        # ── 文件不存在：首次运行，正常返回空 ────────────
        if not CREDENTIALS_FILE.exists():
            return {"version": 1, "active_backend": "", "backends": {}}

        cipher = CREDENTIALS_FILE.read_text(encoding="utf-8").strip()
        if not cipher:
            raise CredentialError(
                "凭据文件为空，可能已损坏。\n"
                f"  文件: {CREDENTIALS_FILE}\n"
                "  请删除后运行 `zmai auth` 重新配置。",
                reason="FILE_EMPTY",
            )

        # ── 1. 用稳定密钥尝试 ──────────────────────────
        result = _try_decode(cipher, self._key)
        if result is not None:
            return result

        # ── 2. 迁移兼容：用旧密钥尝试 ──────────────────
        for legacy_key in _legacy_machine_keys():
            # 如果 legacy_key 恰好与稳定密钥相同，跳过（已尝试过）
            if legacy_key == self._key:
                continue
            result = _try_decode(cipher, legacy_key)
            if result is not None:
                # 旧密钥解密成功 → 立即用稳定密钥重加密保存
                logger.info(
                    "凭据使用旧密钥解密成功，正在迁移到新密钥: %s",
                    CREDENTIALS_FILE,
                )
                self._data = result
                self._save()
                return result

        # ── 3. 所有密钥均失败 → 明确错误 ──────────────

        # 区分"损坏"和"密钥不匹配"
        # 如果 base64 解码失败 → 文件损坏
        try:
            base64.b64decode(cipher.encode())
        except Exception:
            raise CredentialError(
                "凭据文件损坏：base64 解码失败。\n"
                f"  文件: {CREDENTIALS_FILE}\n"
                "  请删除后重新配置。",
                reason="FILE_CORRUPTED",
            )

        # base64 有效但解密不出有效内容 → 密钥不匹配
        # 尝试用 UTF-8 直接读一下看是不是明文 JSON（旧版兼容）
        try:
            plaintext = json.loads(cipher)
            if isinstance(plaintext, dict):
                # 这是明文 JSON → 用稳定密钥重加密保存
                logger.info(
                    "发现明文凭据文件，正在加密保存: %s", CREDENTIALS_FILE
                )
                self._data = plaintext
                self._save()
                return plaintext
        except Exception:
            pass

        raise CredentialError(
            "凭据解密失败：加密密钥不匹配。\n"
            f"  文件: {CREDENTIALS_FILE}\n"
            "  此凭据文件是由另一台机器或另一个用户加密的。\n"
            "  请运行 `zmai auth update <backend>` 重新配置。",
            reason="KEY_MISMATCH",
        )

    def _save(self) -> None:
        """加密保存凭据到文件。"""
        plain = json.dumps(self._data, ensure_ascii=False)
        cipher = _encrypt(plain, self._key)
        CREDENTIALS_FILE.write_text(cipher, encoding="utf-8")
        # 权限 600（Unix），Windows 下 ignored
        try:
            CREDENTIALS_FILE.chmod(0o600)
        except Exception:
            pass

    def rotate_key(self) -> None:
        """重新生成加密密钥并重加密凭据文件。

        用于密钥轮换或修复密钥文件损坏。
        注意：删除旧密钥文件后，用旧密钥加密的数据将不可恢复。
        """
        old_key = self._key
        # 删除旧密钥文件
        if KEY_FILE.exists():
            KEY_FILE.unlink()
        # 重新生成密钥
        self._key = _resolve_key()
        # 用新密钥重加密
        if CREDENTIALS_FILE.exists():
            cipher = CREDENTIALS_FILE.read_text(encoding="utf-8").strip()
            data = _try_decode(cipher, old_key)
            if data is not None:
                self._data = data
                self._save()
                logger.info("凭据已使用新密钥重新加密: %s", CREDENTIALS_FILE)

    # ── 公开 API ───────────────────────────────────────

    def list_backends(self) -> list[dict[str, Any]]:
        """列出所有已配置的 Backend。"""
        backends = self._data.get("backends", {})
        active = self._data.get("active_backend", "")
        result = []
        for name, info in backends.items():
            result.append({
                "name": name,
                "active": name == active,
                "model": info.get("model", ""),
                "base_url": info.get("base_url", ""),
                "timeout": info.get("timeout", 0),
                "max_tokens": info.get("max_tokens", 0),
                "temperature": info.get("temperature", 0.0),
                "verified": bool(info.get("verified_at")),
                "verified_at": info.get("verified_at", ""),
                "created_at": info.get("created_at", ""),
                "key_preview": info.get("api_key", "")[:7] + "..."
                if info.get("api_key")
                else "",
            })
        return result

    def get_backend(self, name: str) -> dict[str, Any] | None:
        """从加密文件读取指定 Backend 凭据。

        纯文件 I/O，不检查环境变量。要获取包含环境变量覆盖的最终值，
        请使用 CredentialResolver.resolve(name)。
        """
        return self._data.get("backends", {}).get(name)

    read = get_backend  # 语义更清晰的别名

    def set_backend(
        self,
        name: str,
        api_key: str,
        model: str = "",
        base_url: str = "",
        timeout: int = 0,
        max_tokens: int = 0,
        temperature: float = 0.0,
        make_active: bool = True,
    ) -> None:
        """存储 Backend 凭证。

        Args:
            name: Backend 名称。
            api_key: API Key。
            model: 模型名称。
            base_url: API 基础 URL 覆盖。
            timeout: 请求超时秒数（0 表示保留现有值）。
            max_tokens: 最大生成 token 数（0 表示保留现有值）。
            temperature: 采样温度（0.0 表示保留现有值）。
            make_active: 是否设为默认 Backend。
        """
        backends = self._data.setdefault("backends", {})
        existing = backends.get(name, {})
        backends[name] = {
            "api_key": api_key,
            "model": model or existing.get("model", ""),
            "base_url": base_url or existing.get("base_url", ""),
            "timeout": timeout or existing.get("timeout", 0),
            "max_tokens": max_tokens or existing.get("max_tokens", 0),
            "temperature": temperature or existing.get("temperature", 0.0),
            "verified_at": existing.get("verified_at", ""),
            "created_at": existing.get("created_at", _now()),
        }
        if make_active:
            self._data["active_backend"] = name
        self._save()

    def remove_backend(self, name: str) -> bool:
        """删除 Backend 凭证。"""
        backends = self._data.get("backends", {})
        if name not in backends:
            return False
        del backends[name]
        if self._data.get("active_backend") == name:
            self._data["active_backend"] = (
                next(iter(backends)) if backends else ""
            )
        self._save()
        return True

    def get_active_backend(self) -> str:
        """获取文件中记录的默认 Backend。"""
        return self._data.get("active_backend", "")

    def set_active_backend(self, name: str) -> bool:
        """切换默认 Backend。"""
        backends = self._data.get("backends", {})
        if name not in backends:
            return False
        self._data["active_backend"] = name
        self._save()
        return True

    def has_backend(self, name: str) -> bool:
        return name in self._data.get("backends", {})

    @property
    def active_backend_info(self) -> dict[str, Any] | None:
        return self.read(self.get_active_backend())
