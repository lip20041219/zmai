"""Tests for zmai.auth.store — AuthStore (legacy obfuscation), credential management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zmai.auth.store import AuthStore, _decrypt, _encrypt

# ═══════════════════════════════════════════════════════════════
# XOR obfuscation helpers (legacy, not encryption)
# ═══════════════════════════════════════════════════════════════

class TestLegacyObfuscation:
    """XOR + Base64 obfuscation 往返测试。

    注意：这是 legacy obfuscation，不是加密。
    不提供机密性、完整性、认证保证。
    仅用于向后兼容读取旧 credentials 文件。
    """

    def test_roundtrip(self):
        key = b"test-key-16-bytes!"
        plain = "hello world"
        cipher = _encrypt(plain, key)
        assert cipher != plain
        assert _decrypt(cipher, key) == plain

    def test_roundtrip_unicode(self):
        key = b"another-key-abcd"
        plain = "你好 ZMAI 🔑"
        cipher = _encrypt(plain, key)
        assert _decrypt(cipher, key) == plain

    def test_different_key_produces_garbage(self):
        key1 = b"key-one-16-bytes"
        key2 = b"key-two-16-bytes"
        plain = "secret"
        cipher = _encrypt(plain, key1)
        decrypted = _decrypt(cipher, key2)
        assert decrypted != plain  # wrong key produces garbage

    def test_empty_string(self):
        key = b"1234567890123456"
        assert _decrypt(_encrypt("", key), key) == ""


# ═══════════════════════════════════════════════════════════════
# AuthStore helpers
# ═══════════════════════════════════════════════════════════════

FIXED_KEY = b"f" * 32  # fixed 32-byte key for testing


@pytest.fixture
def auth_store(tmp_path: Path) -> AuthStore:
    """创建指向 tmp_path 的 AuthStore，隔离文件系统。"""
    auth_dir = tmp_path / ".zmai"
    with (
        patch("zmai.auth.store.AUTH_DIR", auth_dir),
        patch("zmai.auth.store.KEY_FILE", auth_dir / "credentials.key"),
        patch("zmai.auth.store.CREDENTIALS_FILE", auth_dir / "credentials"),
        patch("zmai.auth.store._resolve_key", return_value=FIXED_KEY),
    ):
        yield AuthStore()


# ═══════════════════════════════════════════════════════════════
# AuthStore init
# ═══════════════════════════════════════════════════════════════

class TestAuthStoreInit:
    def test_init_creates_directory(self, tmp_path: Path):
        auth_dir = tmp_path / ".zmai"
        with (
            patch("zmai.auth.store.AUTH_DIR", auth_dir),
            patch("zmai.auth.store.KEY_FILE", auth_dir / "credentials.key"),
            patch("zmai.auth.store.CREDENTIALS_FILE", auth_dir / "credentials"),
            patch("zmai.auth.store._resolve_key", return_value=FIXED_KEY),
        ):
            AuthStore()
            assert auth_dir.is_dir()

    def test_init_without_credentials(self, auth_store: AuthStore):
        """没有凭证文件时初始化不报错。"""
        assert auth_store.list_backends() == []

    def test_init_with_malformed_file(self, tmp_path: Path):
        """损坏的凭证文件抛出 CredentialError（非崩溃）。"""
        from zmai.errors import CredentialError
        auth_dir = tmp_path / ".zmai"
        cred_file = auth_dir / "credentials"
        cred_file.parent.mkdir(parents=True)
        cred_file.write_text("not-valid-base64")
        with (
            patch("zmai.auth.store.AUTH_DIR", auth_dir),
            patch("zmai.auth.store.KEY_FILE", auth_dir / "credentials.key"),
            patch("zmai.auth.store.CREDENTIALS_FILE", cred_file),
            patch("zmai.auth.store._resolve_key", return_value=FIXED_KEY),
        ):
            with pytest.raises(CredentialError, match="损坏|corrupted"):
                AuthStore()


# ═══════════════════════════════════════════════════════════════
# AuthStore backend operations
# ═══════════════════════════════════════════════════════════════

class TestAuthStoreBackends:
    def test_set_and_get(self, auth_store: AuthStore):
        auth_store.set_backend("deepseek", "sk-test-key")
        info = auth_store.get_backend("deepseek")
        assert info is not None
        assert info["api_key"] == "sk-test-key"

    def test_set_with_model(self, auth_store: AuthStore):
        auth_store.set_backend("claude", "sk-claude", model="claude-sonnet-4-6")
        info = auth_store.get_backend("claude")
        assert info["model"] == "claude-sonnet-4-6"

    def test_list_backends(self, auth_store: AuthStore):
        auth_store.set_backend("deepseek", "key-d")
        auth_store.set_backend("claude", "key-c")
        backends = auth_store.list_backends()
        assert len(backends) == 2
        names = [b["name"] for b in backends]
        assert "deepseek" in names
        assert "claude" in names

    def test_list_backends_key_preview(self, auth_store: AuthStore):
        auth_store.set_backend("deepseek", "sk-abcdefghijklmn")
        backends = auth_store.list_backends()
        assert backends[0]["key_preview"] == "sk-abcd..."

    def test_remove_backend(self, auth_store: AuthStore):
        auth_store.set_backend("test", "key")
        assert auth_store.remove_backend("test") is True
        assert auth_store.get_backend("test") is None

    def test_remove_nonexistent(self, auth_store: AuthStore):
        assert auth_store.remove_backend("nobody") is False

    def test_has_backend(self, auth_store: AuthStore):
        auth_store.set_backend("exists", "key")
        assert auth_store.has_backend("exists") is True
        assert auth_store.has_backend("missing") is False

    def test_active_backend_default(self, auth_store: AuthStore):
        """set_backend 默认激活。"""
        auth_store.set_backend("deepseek", "key")
        assert auth_store.get_active_backend() == "deepseek"

    def test_active_backend_not_make_active(self, auth_store: AuthStore):
        auth_store.set_backend("deepseek", "key", make_active=False)
        assert auth_store.get_active_backend() == ""

    def test_set_active_backend(self, auth_store: AuthStore):
        auth_store.set_backend("a", "k1")
        auth_store.set_backend("b", "k2", make_active=False)
        assert auth_store.set_active_backend("b") is True
        assert auth_store.get_active_backend() == "b"

    def test_set_active_nonexistent(self, auth_store: AuthStore):
        assert auth_store.set_active_backend("ghost") is False

    def test_remove_active_backend_fallback(self, auth_store: AuthStore):
        """删除当前激活的 backend 时自动切换到其他 backend。"""
        auth_store.set_backend("a", "k1")
        auth_store.set_backend("b", "k2")
        auth_store.set_active_backend("a")
        auth_store.remove_backend("a")
        assert auth_store.get_active_backend() == "b"

    def test_remove_only_backend_clears_active(self, auth_store: AuthStore):
        auth_store.set_backend("only", "k")
        auth_store.remove_backend("only")
        assert auth_store.get_active_backend() == ""

    def test_persistence_across_instances(self, tmp_path: Path):
        """写入的数据可以在新 AuthStore 实例中读取。"""
        auth_dir = tmp_path / ".zmai"
        cred_file = auth_dir / "credentials"
        with (
            patch("zmai.auth.store.AUTH_DIR", auth_dir),
            patch("zmai.auth.store.KEY_FILE", auth_dir / "credentials.key"),
            patch("zmai.auth.store.CREDENTIALS_FILE", cred_file),
            patch("zmai.auth.store._resolve_key", return_value=FIXED_KEY),
        ):
            store1 = AuthStore()
            store1.set_backend("persist", "test-key", model="m1")

            store2 = AuthStore()
            info = store2.get_backend("persist")
            assert info is not None
            assert info["api_key"] == "test-key"
            assert info["model"] == "m1"
