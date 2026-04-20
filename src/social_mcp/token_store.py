"""Encrypted token storage.

Tokens are serialized as JSON, encrypted with Fernet (AES-128-CBC + HMAC-SHA256),
and written to a single file. The encryption key lives in the OS keyring by
default (Keychain on macOS, Credential Manager on Windows, Secret Service on
Linux). For headless servers where no keyring is available, set
``SOCIAL_MCP_FERNET_KEY`` in the environment.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import keyring
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "social-mcp"
_KEYRING_USER = "fernet-key"


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


@dataclass
class Credential:
    """One set of OAuth credentials for one provider.

    Attributes:
        provider: "twitter" or "facebook".
        access_token: The bearer token used for API calls.
        refresh_token: Optional refresh token (Twitter issues these; Facebook
            long-lived tokens are refreshed differently and have no refresh token).
        expires_at: Absolute Unix timestamp when the access token expires.
            ``None`` means "non-expiring" (Facebook long-lived page tokens
            are effectively permanent).
        scope: Granted OAuth scopes, space-separated.
        extra: Provider-specific bag (user id, page tokens, etc.).
    """

    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, skew_seconds: int = 60) -> bool:
        """Return True if the token is expired or will expire within ``skew_seconds``."""
        if self.expires_at is None:
            return False
        return time.time() + skew_seconds >= self.expires_at


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


def _load_or_create_key() -> bytes:
    """Return a Fernet key, creating + persisting one if it doesn't exist."""
    settings = get_settings()

    # 1. Environment override (best for headless).
    if settings.social_mcp_fernet_key:
        return settings.social_mcp_fernet_key.encode("utf-8")

    # 2. OS keyring.
    try:
        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if stored:
            return stored.encode("utf-8")
        new_key = Fernet.generate_key()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, new_key.decode("utf-8"))
        return new_key
    except keyring.errors.KeyringError as e:
        raise RuntimeError(
            "No keyring backend is available and SOCIAL_MCP_FERNET_KEY is not set. "
            "Generate a key with `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and export it as "
            "SOCIAL_MCP_FERNET_KEY."
        ) from e


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TokenStore:
    """Thread-unsafe but async-safe-within-event-loop credential vault."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or get_settings().store_path
        self._fernet = Fernet(_load_or_create_key())
        self._cache: dict[str, Credential] | None = None

    # -- IO -----------------------------------------------------------------

    def _read_all(self) -> dict[str, Credential]:
        if self._cache is not None:
            return self._cache

        if not self._path.exists():
            self._cache = {}
            return self._cache

        try:
            blob = self._path.read_bytes()
            plaintext = self._fernet.decrypt(blob)
            raw: dict[str, dict[str, Any]] = json.loads(plaintext)
            self._cache = {k: Credential(**v) for k, v in raw.items()}
        except InvalidToken as e:
            raise RuntimeError(
                f"Token store at {self._path} cannot be decrypted with the current "
                "key. Either the key rotated or the file is corrupt. Delete the file "
                "to start fresh (you will need to re-authenticate)."
            ) from e
        except (json.JSONDecodeError, TypeError) as e:
            raise RuntimeError(f"Token store at {self._path} is malformed: {e}") from e

        return self._cache

    def _write_all(self, data: dict[str, Credential]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: asdict(v) for k, v in data.items()}
        payload = json.dumps(serializable, separators=(",", ":")).encode("utf-8")
        ciphertext = self._fernet.encrypt(payload)
        # Atomic write: write to tmp then rename.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(ciphertext)
        os.replace(tmp, self._path)
        # Tighten perms (POSIX only; on Windows this is a no-op).
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        self._cache = data

    # -- Public API ---------------------------------------------------------

    def get(self, provider: str) -> Credential | None:
        return self._read_all().get(provider)

    def put(self, cred: Credential) -> None:
        data = dict(self._read_all())
        data[cred.provider] = cred
        self._write_all(data)

    def delete(self, provider: str) -> bool:
        data = dict(self._read_all())
        if provider in data:
            del data[provider]
            self._write_all(data)
            return True
        return False

    def providers(self) -> list[str]:
        return list(self._read_all().keys())


_store: TokenStore | None = None


def get_store() -> TokenStore:
    """Process-wide singleton store."""
    global _store
    if _store is None:
        _store = TokenStore()
    return _store
