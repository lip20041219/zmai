"""ZMAI Auth — 统一凭证管理。"""

from zmai.auth.bundle import CredentialBundle
from zmai.auth.resolver import CredentialResolver
from zmai.auth.store import AuthStore
from zmai.auth.store_base import (
    CredentialStore,
    CredentialStoreError,
    CredentialStoreUnavailableError,
    NullCredentialStore,
    StoredCredential,
    get_default_credential_store,
)
from zmai.auth.store_keyring import KeyringCredentialStore
from zmai.auth.store_wincred import WindowsCredentialStore
from zmai.errors import CredentialError

__all__ = [
    "AuthStore", "CredentialBundle", "CredentialResolver",
    "CredentialStatus", "ConflictDetail", "CredentialStore",
    "CredentialStoreError", "CredentialStoreUnavailableError",
    "KeyringCredentialStore", "NullCredentialStore",
    "StoredCredential", "WindowsCredentialStore",
    "get_default_credential_store", "source_label",
    "CredentialError",
]
