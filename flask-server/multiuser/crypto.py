"""Reversible encryption for per-user provider credentials.

YouTube headers and Amazon session cookies are bearer credentials: anyone who
reads them can act as the user.  They are therefore encrypted at rest with
Fernet (AES-128-CBC + HMAC-SHA256) and never written to logs or rendered in the
admin UI.
"""
from __future__ import annotations

import base64
import json
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError as exc:  # pragma: no cover - deployment guard
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class SecretBoxError(RuntimeError):
    pass


def generate_encryption_key() -> str:
    if Fernet is None:
        raise SecretBoxError("cryptography is not installed")
    return Fernet.generate_key().decode("ascii")


def _fernet_key(configured_key: str, fallback_secret: str) -> bytes:
    if Fernet is None:
        raise SecretBoxError(
            "The multi-user layer requires the 'cryptography' package. "
            "Install requirements.txt and restart."
        ) from _IMPORT_ERROR
    if configured_key:
        try:
            key = configured_key.encode("ascii")
            Fernet(key)  # validate
            return key
        except (ValueError, TypeError) as exc:
            raise SecretBoxError(
                "MULTIUSER_ENCRYPTION_KEY must be a 32-byte url-safe base64 "
                "Fernet key (generate one with scripts/generate-multiuser-key.py)."
            ) from exc
    if not fallback_secret:
        raise SecretBoxError(
            "No encryption key available. Set MULTIUSER_ENCRYPTION_KEY (preferred) "
            "or SECRET_KEY."
        )
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"musicbox-multiuser-credentials-v1",
        info=b"provider-credentials",
    ).derive(fallback_secret.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


class SecretBox:
    """Encrypt/decrypt JSON provider payloads."""

    def __init__(self, configured_key: str = "", fallback_secret: str = ""):
        self._fernet = Fernet(_fernet_key(configured_key, fallback_secret))
        self.using_derived_key = not bool(configured_key)

    def encrypt_json(self, payload: Any) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt_json(self, token: str) -> Any:
        try:
            raw = self._fernet.decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise SecretBoxError("Stored credential could not be decrypted") from exc
        return json.loads(raw.decode("utf-8"))
