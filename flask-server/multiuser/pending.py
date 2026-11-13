"""The few seconds between "password accepted" and "second factor proven".

Step one of sign-in must not leave anything in the cookie that reads as *already
past the password*, so the proof lives here instead: a single-use random token is
handed to the browser and the record behind it stays on the server.

Two properties the doors depend on:

* **Bound.** Each record carries a fingerprint of the address bucket and user
  agent that earned it, re-derived and compared at step two. A token that leaves
  the browser -- a proxy log, a shoulder-surfed URL -- cannot be spent elsewhere.
* **Consumed on read.** ``consume`` pops, so a replay and a two-tab race both
  land on ``None`` and are told to start again. A wrong code therefore destroys
  the pending state rather than offering a fresh guess every thirty seconds.

The store is capped and self-pruning: an attacker who can solve captchas faster
than the login limiter allows should be able to pile up pending records, so the
oldest are evicted once ``capacity`` is reached instead of the heap growing.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time

BIND_KEY = b"musicbox-pending-second-factor"


def bind_value(ip_bucket: str, user_agent: str) -> str:
    """Fingerprint of the browser standing at the door right now."""
    material = f"{(ip_bucket or '')[:64]}|{(user_agent or '')[:120]}"
    return hmac.new(BIND_KEY, material.encode("utf-8", "replace"), "sha256").hexdigest()[:32]


class PendingSecondFactor:
    def __init__(self, ttl_seconds: int = 300, capacity: int = 256) -> None:
        self.ttl = int(ttl_seconds)
        self.capacity = max(int(capacity), 1)
        self._records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def issue(self, record: dict, bind: str) -> str:
        """Store ``record`` and return the single-use token for it."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        stored = dict(record)
        stored["bind"] = bind
        stored["at"] = now
        with self._lock:
            self._prune(now)
            while len(self._records) >= self.capacity:
                oldest = min(self._records.items(), key=lambda item: item[1].get("at", 0))[0]
                self._records.pop(oldest, None)
            self._records[token] = stored
        return token

    def consume(self, token: str, bind: str) -> dict | None:
        """Destroy and return the record for ``token``; ``None`` means start over."""
        with self._lock:
            record = self._records.pop(str(token or ""), None)
        if not record:
            return None
        if (time.time() - float(record.get("at", 0))) > self.ttl:
            return None
        if not hmac.compare_digest(str(record.get("bind", "")), str(bind or "")):
            return None
        return record

    def discard(self, token: str) -> None:
        with self._lock:
            self._records.pop(str(token or ""), None)

    def waiting(self) -> int:
        with self._lock:
            return len(self._records)

    def _prune(self, now: float) -> None:
        expired = [
            token for token, record in self._records.items()
            if now - float(record.get("at", 0)) > self.ttl
        ]
        for token in expired:
            self._records.pop(token, None)
