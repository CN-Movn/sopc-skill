"""Separate fixed upstream-key and localhost bridge-token authentication."""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from .credentials import CredentialError, discover_credential, key_file_path


class AuthError(RuntimeError):
    def __init__(self, code: str, message: str, path: Path | None = None):
        super().__init__(message)
        self.code = code
        self.path = path


class BridgeAuth:
    def __init__(self, local_token: str | None = None, key_file: str | None = None):
        self._key: str | None = None
        self._source: str | None = None
        self._key_file = key_file
        self._secrets: set[str] = set()
        self.local_token: str = local_token or secrets.token_urlsafe(32)

    def load(self) -> None:
        target = Path(self._key_file).expanduser().resolve() if self._key_file else key_file_path()
        try:
            credential = discover_credential(target)
        except CredentialError as exc:
            raise AuthError(exc.code, str(exc), exc.path) from exc
        if credential is None:
            raise AuthError(
                "upstream_key_missing",
                f"OpenCode Go key file is missing: {target}",
                target,
            )
        self._key = credential.key
        self._source = credential.source
        self._secrets.add(credential.key)

    @property
    def credential_source(self) -> str | None:
        return self._source

    def has_key(self) -> bool:
        return bool(self._key)

    def bearer(self) -> str:
        if not self._key:
            raise AuthError("upstream_key_not_loaded", "OpenCode Go key is not loaded.")
        return self._key

    def check_local(self, token: str) -> bool:
        return hmac.compare_digest(token, self.local_token)

    def redact(self, text: str) -> str:
        redacted = text
        for secret in self._secrets | ({self._key} if self._key else set()):
            redacted = redacted.replace(secret, "***")
        return redacted

    def clear(self) -> None:
        self._key = None
        self._source = None
        self._secrets.clear()
