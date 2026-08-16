"""Tests for CredentialStore abstraction, Windows Credential Manager, and keyring."""

from __future__ import annotations

import sys

import pytest

from zmai.auth.store_base import (
    CredentialStore,
    CredentialStoreUnavailableError,
    NullCredentialStore,
    StoredCredential,
    get_default_credential_store,
)

# ═══════════════════════════════════════════════════════════════
# StoredCredential
# ═══════════════════════════════════════════════════════════════


class TestStoredCredential:
    def test_creates_with_minimal_fields(self):
        cred = StoredCredential(provider="deepseek", api_key="sk-test")
        assert cred.provider == "deepseek"
        assert cred.api_key == "sk-test"

    def test_creates_with_all_fields(self):
        cred = StoredCredential(
            provider="claude", api_key="sk-ant-test",
            model="claude-opus-4-8", base_url="https://custom.example.com",
            timeout=60, max_tokens=4096, temperature=0.5,
        )
        assert cred.model == "claude-opus-4-8"
        assert cred.timeout == 60

    def test_to_dict_excludes_api_key(self):
        """to_dict() 不应包含 api_key。"""
        cred = StoredCredential(provider="d", api_key="secret-key")
        d = cred.to_dict()
        assert "api_key" not in d
        assert "provider" not in d  # 由外部关联
        assert d["model"] == ""


# ═══════════════════════════════════════════════════════════════
# NullCredentialStore
# ═══════════════════════════════════════════════════════════════


class TestNullCredentialStore:
    @pytest.fixture
    def store(self) -> NullCredentialStore:
        return NullCredentialStore()

    def test_is_available_false(self, store: NullCredentialStore):
        assert store.is_available() is False

    def test_get_returns_none(self, store: NullCredentialStore):
        assert store.get("deepseek") is None

    def test_set_raises_unavailable(self, store: NullCredentialStore):
        with pytest.raises(CredentialStoreUnavailableError, match="无可用的"):
            store.set("deepseek", StoredCredential(provider="deepseek", api_key="key"))

    def test_delete_returns_false(self, store: NullCredentialStore):
        assert store.delete("deepseek") is False

    def test_exists_returns_false(self, store: NullCredentialStore):
        assert store.exists("deepseek") is False

    def test_list_providers_empty(self, store: NullCredentialStore):
        assert store.list_providers() == []


# ═══════════════════════════════════════════════════════════════
# get_default_credential_store
# ═══════════════════════════════════════════════════════════════


class TestDefaultCredentialStore:
    def test_returns_concrete_store(self):
        """返回非 None 的 CredentialStore 实例。"""
        store = get_default_credential_store()
        assert isinstance(store, CredentialStore)

    def test_windows_returns_wincred(self):
        """Windows 上应返回 WindowsCredentialStore。"""
        store = get_default_credential_store()
        if sys.platform == "win32":
            from zmai.auth.store_wincred import WindowsCredentialStore
            assert isinstance(store, WindowsCredentialStore)
        # 不测试其他平台的返回值（取决于 keyring 是否安装）


# ═══════════════════════════════════════════════════════════════
# KeyringCredentialStore
# ═══════════════════════════════════════════════════════════════


class TestKeyringCredentialStore:
    @pytest.fixture
    def store(self):
        from zmai.auth.store_keyring import KeyringCredentialStore
        s = KeyringCredentialStore()
        return s

    def test_available_or_not(self, store):
        """验证 is_available 返回 bool 且不抛出异常。"""
        assert isinstance(store.is_available(), bool)

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("__ktest_nonexistent") is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("__ktest_nonexistent") is False


# ═══════════════════════════════════════════════════════════════
# WindowsCredentialStore
# ═══════════════════════════════════════════════════════════════

pytestmark_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows Credential Manager 仅在 Windows 上可用",
)


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")
class TestWindowsCredentialStore:
    @pytest.fixture
    def store(self):
        from zmai.auth.store_wincred import WindowsCredentialStore
        s = WindowsCredentialStore()
        if not s.is_available():
            pytest.skip("Windows Credential Manager 不可用")
        # 清除测试可能遗留的凭据
        for p in s.list_providers():
            if p.startswith("__test_"):
                s.delete(p)
        yield s
        # 清理
        for p in s.list_providers():
            if p.startswith("__test_"):
                s.delete(p)

    def test_is_available(self, store):
        assert store.is_available() is True

    def test_set_and_get(self, store):
        cred = StoredCredential(provider="__test_d", api_key="sk-test-key")
        store.set("__test_d", cred)
        retrieved = store.get("__test_d")
        assert retrieved is not None
        assert retrieved.api_key == "sk-test-key"
        assert retrieved.provider == "__test_d"

    def test_get_nonexistent(self, store):
        assert store.get("__test_nonexistent") is None

    def test_set_with_all_fields(self, store):
        cred = StoredCredential(
            provider="__test_full", api_key="sk-full",
            model="m1", base_url="https://example.com",
            timeout=30, max_tokens=2048, temperature=0.3,
        )
        store.set("__test_full", cred)
        retrieved = store.get("__test_full")
        assert retrieved is not None
        assert retrieved.model == "m1"
        assert retrieved.base_url == "https://example.com"
        assert retrieved.timeout == 30
        assert retrieved.max_tokens == 2048
        assert retrieved.temperature == 0.3

    def test_delete_existing(self, store):
        store.set("__test_del", StoredCredential(provider="__test_del", api_key="k"))
        assert store.delete("__test_del") is True
        assert store.get("__test_del") is None

    def test_delete_nonexistent(self, store):
        assert store.delete("__test_nonexistent_del") is False

    def test_exists(self, store):
        store.set("__test_exists", StoredCredential(provider="__test_exists", api_key="k"))
        assert store.exists("__test_exists") is True
        store.delete("__test_exists")
        assert store.exists("__test_exists") is False

    def test_list_providers(self, store):
        store.set("__test_list_a", StoredCredential(provider="__test_list_a", api_key="k1"))
        store.set("__test_list_b", StoredCredential(provider="__test_list_b", api_key="k2"))
        providers = store.list_providers()
        assert "__test_list_a" in providers
        assert "__test_list_b" in providers

    def test_provider_isolation(self, store):
        """不同 provider 的凭据互不干扰。"""
        store.set("__test_iso_a", StoredCredential(provider="__test_iso_a", api_key="key_a"))
        store.set("__test_iso_b", StoredCredential(provider="__test_iso_b", api_key="key_b"))
        a = store.get("__test_iso_a")
        b = store.get("__test_iso_b")
        assert a is not None and b is not None
        assert a.api_key == "key_a"
        assert b.api_key == "key_b"

    def test_api_key_not_in_log(self, store, caplog):
        """API Key 不应出现在日志中。"""
        import logging
        caplog.set_level(logging.DEBUG)
        store.set("__test_log", StoredCredential(provider="__test_log", api_key="sk-super-secret-key"))  # noqa: E501
        log_text = caplog.text
        assert "sk-super-secret-key" not in log_text

    def test_to_dict_no_api_key_leak(self, store):
        """to_dict() 不应包含 api_key。"""
        store.set("__test_dict", StoredCredential(provider="__test_dict", api_key="sk-leak-test"))
        retrieved = store.get("__test_dict")
        assert retrieved is not None
        d = retrieved.to_dict()
        assert "api_key" not in d

    def test_set_twice_updates(self, store):
        """重复 set 应更新已有凭据。"""
        store.set("__test_upd", StoredCredential(provider="__test_upd", api_key="old_key"))
        store.set("__test_upd", StoredCredential(provider="__test_upd", api_key="new_key"))
        retrieved = store.get("__test_upd")
        assert retrieved is not None
        assert retrieved.api_key == "new_key"
