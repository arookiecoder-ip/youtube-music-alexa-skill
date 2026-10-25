"""Password, token, TOTP and rate-limiting helpers.

Kept dependency-light: Werkzeug ships with Flask and is already a dependency.
Argon2id migration is tracked in the roadmap; PBKDF2-SHA256 with a high
iteration count is the safe current baseline.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from typing import Deque

from werkzeug.security import check_password_hash, generate_password_hash

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 200
PASSWORD_METHOD = "pbkdf2:sha256:600000"

_PASSWORD_CLASSES = (
    ("lower", lambda c: c.islower()),
    ("upper", lambda c: c.isupper()),
    ("digit", lambda c: c.isdigit()),
    ("symbol", lambda c: not c.isalnum()),
)


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PASSWORD_METHOD)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash or password is None:
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def validate_password(password: str, username: str = "") -> str | None:
    """Return an error message, or None when the password is acceptable."""
    if not isinstance(password, str) or password == "":
        return "Password is required."
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    if username and password.strip().lower() == username.strip().lower():
        return "Password must not be the same as the username."
    if password.strip().lower() in {"password", "password123", "1234567890", "qwerty123456"}:
        return "That password is too common. Choose a different one."
    classes = sum(1 for _, predicate in _PASSWORD_CLASSES if any(predicate(c) for c in password))
    if classes < 2:
        return "Use at least two of: lowercase, uppercase, digits, symbols."
    return None


def generate_password(length: int = 18) -> str:
    """Generate a password that satisfies validate_password."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(max(length, PASSWORD_MIN_LENGTH)))
        if validate_password(value) is None:
            return value


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_equal(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left or "", right or "")
    except TypeError:
        return False


# ---- TOTP (RFC 6238) -------------------------------------------------------
def totp_at(secret_b32: str, counter: int) -> str:
    padded = secret_b32 + "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    if not secret_b32:
        return False
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return False
    counter = int(time.time()) // 30
    for step in range(-window, window + 1):
        try:
            candidate = totp_at(secret_b32, counter + step)
        except (ValueError, TypeError):
            return False
        if hmac.compare_digest(candidate, code):
            return True
    return False


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


class RateLimiter:
    """Small in-process sliding-window limiter.

    Correct for the current single-authoritative-process deployment.  When the
    service is split across replicas this moves to Redis (roadmap Phase 5).
    """

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], cutoff: float) -> Deque[float]:
        bucket = self._events[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return bucket

    def allow(self, scope: str, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._prune((scope, key), cutoff)
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            # Bound memory if many one-off keys are used.
            if len(self._events) > 5000:
                for stale_key in [k for k, v in self._events.items() if not v or v[-1] < cutoff][:1000]:
                    self._events.pop(stale_key, None)
            return True, 0

    def reset(self, scope: str, key: str) -> None:
        with self._lock:
            self._events.pop((scope, key), None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
