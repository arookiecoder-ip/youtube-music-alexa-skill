"""Lease and credential-promotion boundary for server-side Chromium auth."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shlex
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


def accepted_browse_url(url):
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return (parsed.scheme == "https" and parsed.hostname == "music.youtube.com"
            and parsed.path.startswith("/youtubei/v1/"))


def normalize_browser_headers(value):
    """Accept raw headers, copied cURL, or the controller's header mapping."""
    if isinstance(value, dict):
        lines = [f"{key}: {val}" for key, val in value.items()
                 if isinstance(key, str) and isinstance(val, str)]
        normalized = "\n".join(lines)
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.lower().startswith("curl "):
            tokens = shlex.split(normalized.replace("\\\n", " "), posix=True)
            lines, i = [], 1
            while i < len(tokens):
                token = tokens[i]
                if token in ("-H", "--header") and i + 1 < len(tokens):
                    lines.append(tokens[i + 1]); i += 2; continue
                if token.startswith("--header="):
                    lines.append(token.split("=", 1)[1])
                elif token in ("-b", "--cookie") and i + 1 < len(tokens):
                    lines.append("cookie: " + tokens[i + 1]); i += 2; continue
                elif token.startswith("--cookie="):
                    lines.append("cookie: " + token.split("=", 1)[1])
                i += 1
            normalized = "\n".join(lines)
    else:
        raise ValueError("Browser headers must be text or an object")
    lowered = normalized.lower()
    if "cookie:" not in lowered:
        raise ValueError("Captured request did not include browser cookies")
    if "x-goog-authuser:" not in lowered:
        normalized += "\nx-goog-authuser: 0"
    return normalized


def is_authentication_error(error):
    """Conservative classifier: do not refresh on rate limits/parser/network errors."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if status in (401, 403):
        return True
    if status == 429 or isinstance(error, (TimeoutError, urllib.error.URLError)):
        return False
    message = str(error).lower()
    positive = ("login required", "authentication required", "unauthorized",
                "invalid authentication", "invalid sapisid", "session expired")
    negative = ("timeout", "timed out", "rate limit", "too many requests",
                "parse", "json", "decode")
    return any(x in message for x in positive) and not any(x in message for x in negative)


class BrowserController:
    def __init__(self, base_url, token, timeout=10):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def call(self, path, method="GET"):
        if not self.base_url or not self.token:
            raise RuntimeError("Browser sidecar is not configured")
        req = urllib.request.Request(self.base_url + path, method=method,
                                     headers={"X-Control-Token": self.token,
                                              "Content-Type": "application/json"},
                                     data=b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # 409 means an existing capture is already running; status remains usable.
            if exc.code == 409:
                return json.load(exc)
            raise


class YouTubeBrowserSessionManager:
    COOKIE_NAME = "yt_browser_lease"

    def __init__(self, controller, promote, invalidate=None, lease_ttl=300,
                 refresh_timeout=45, refresh_cooldown=300, clock=time.time):
        self.controller = controller
        self.promote = promote
        self.invalidate = invalidate or (lambda: None)
        self.lease_ttl = float(lease_ttl)
        self.refresh_timeout = float(refresh_timeout)
        self.refresh_cooldown = float(refresh_cooldown)
        self.clock = clock
        self._lock = threading.RLock()
        self._refresh_condition = threading.Condition(self._lock)
        self._lease = None
        self._refreshing = False
        self._state = "idle"
        self._message = None
        self._last_success = None
        self._last_attempt = None

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode()).digest()

    def _expire_locked(self):
        if self._lease and self.clock() >= self._lease[2]:
            self._lease = None
            if self._state not in ("connected", "reconnect_required"):
                self._state = "idle"
            # Do not perform network I/O under the manager lock. The lease is
            # already invalid, and this bounded best-effort close releases the
            # otherwise inaccessible Chromium window/resources.
            threading.Thread(target=self._close_sidecar, daemon=True).start()

    def _close_sidecar(self):
        try:
            self.controller.call("/interactive/close", "POST")
        except Exception:
            pass

    def _complete_sidecar(self):
        try:
            self.controller.call("/interactive/complete", "POST")
        except Exception:
            # Older or unavailable sidecars still need resources released.
            self._close_sidecar()

    def start(self, owner_id):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._lease = (str(owner_id), self._digest(token), self.clock() + self.lease_ttl)
            self._state, self._message = "connecting", None
        try:
            sidecar = self.controller.call("/interactive/start", "POST")
            with self._lock:
                self._state = sidecar.get("state", "waiting_for_login")
        except Exception:
            with self._lock:
                self._state, self._message = "unavailable", "Browser service is unavailable"
        return token, self.status()

    def authorize(self, owner_id, token):
        with self._lock:
            self._expire_locked()
            if not self._lease or not token:
                return False
            return (hmac.compare_digest(self._lease[0], str(owner_id)) and
                    hmac.compare_digest(self._lease[1], self._digest(token)))

    def revoke(self, close=True):
        with self._lock:
            self._lease = None
            if self._state != "connected":
                self._state = "idle"
        if close:
            self._close_sidecar()

    def open_youtube(self):
        """Ask the visible plain browser to navigate to YouTube Music."""
        sidecar = self.controller.call("/interactive/open-youtube", "POST")
        with self._lock:
            self._state = sidecar.get("state", self._state)
            self._message = sidecar.get("message")
        return self.status()

    def request_capture(self):
        """Begin the explicit plain-browser to validation-browser handoff."""
        sidecar = self.controller.call("/interactive/capture", "POST")
        with self._lock:
            self._state = sidecar.get("state", "capture_requested")
            self._message = sidecar.get("message")
        return self.status()

    def _take_and_promote(self):
        candidate = self.controller.call("/candidate/take", "POST")
        if not accepted_browse_url(candidate.get("url")):
            raise ValueError("Captured request was not a YouTube Music browse request")
        self.promote(candidate.get("headers"))
        self.invalidate()
        with self._lock:
            self._state, self._message = "connected", None
            self._last_success = self.clock()
            self._lease = None
        self._complete_sidecar()

    def poll(self):
        try:
            sidecar = self.controller.call("/status")
            state = sidecar.get("state", "unavailable")
            if state == "candidate_taken":
                # Promotion owns the one-time candidate. Keep the manager's
                # current validating/connected/error state instead of trying
                # to consume it again on concurrent or subsequent polls.
                return self.status()
            with self._lock:
                self._expire_locked()
                self._state = "validating" if state == "captured" else state
                self._message = sidecar.get("message")
            if state == "captured":
                self._take_and_promote()
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                with self._lock:
                    self._state, self._message = "unavailable", "Browser service is unavailable"
        except urllib.error.URLError:
            with self._lock:
                self._state, self._message = "unavailable", "Browser service is unavailable"
        except Exception:
            with self._lock:
                self._state, self._message = "reconnect_required", "Captured session was not accepted"
        return self.status()

    def status(self):
        with self._lock:
            self._expire_locked()
            return {"state": self._state, "available": self._state != "unavailable",
                    "refreshing": self._refreshing, "reconnect_required": self._state == "reconnect_required",
                    "last_successful_renewal": self._last_success, "message": self._message,
                    "lease_expires_at": self._lease[2] if self._lease else None}

    def refresh(self, wait=True):
        """Single-flight saved-profile refresh. Waiting callers share its result."""
        with self._refresh_condition:
            now = self.clock()
            if self._refreshing:
                if wait:
                    self._refresh_condition.wait(timeout=self.refresh_timeout)
                return self._state == "connected"
            if self._last_attempt and now - self._last_attempt < self.refresh_cooldown:
                return self._state == "connected"
            self._refreshing, self._last_attempt = True, now
            self._state, self._message = "refreshing", None
        ok = False
        try:
            self.controller.call("/refresh", "POST")
            deadline = self.clock() + self.refresh_timeout
            while self.clock() < deadline:
                sidecar = self.controller.call("/status")
                state = sidecar.get("state")
                if state == "captured":
                    self._take_and_promote(); ok = True; break
                if state in ("reconnect_required", "unavailable"):
                    with self._lock:
                        self._state = state
                        self._message = sidecar.get("message")
                    break
                time.sleep(0.25)
            if not ok and self._state == "refreshing":
                with self._lock:
                    self._state, self._message = "reconnect_required", "Google interaction is required"
        except Exception:
            with self._lock:
                self._state, self._message = "reconnect_required", "Saved browser session could not renew authentication"
        finally:
            with self._refresh_condition:
                self._refreshing = False
                self._refresh_condition.notify_all()
        return ok

    def call_with_one_refresh(self, operation):
        try:
            return operation()
        except Exception as exc:
            if not is_authentication_error(exc) or not self.refresh():
                raise
        return operation()  # exactly one retry


def account_menu_is_signed_in(payload):
    """Return True only when YouTube Music exposes an active account."""
    try:
        renderer = payload["actions"][0]["openPopupAction"]["popup"][
            "multiPageMenuRenderer"]
        active = renderer["header"]["activeAccountHeaderRenderer"]
        runs = active["accountName"]["runs"]
        return bool(runs and isinstance(runs[0].get("text"), str)
                    and runs[0]["text"].strip())
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def browser_client_is_signed_in(client):
    """Validate browser auth against YT Music's authoritative account menu."""
    try:
        payload = client._send_request("account/account_menu", {})
    except Exception:
        return False
    return account_menu_is_signed_in(payload)


def promote_browser_headers(headers, auth_file, setup_browser, ytmusic_factory):
    """Validate beside the destination and atomically replace only on success."""
    normalized = normalize_browser_headers(headers)
    if os.path.isdir(auth_file):
        raise ValueError("YTMUSIC_AUTH_FILE must be a writable file path")
    parent = os.path.dirname(os.path.abspath(auth_file))
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="ytmusic-browser-", suffix=".json", dir=parent)
    os.close(fd)
    try:
        setup_browser(filepath=temp_path, headers_raw=normalized)
        # Signed Google cookies and even an empty library response are possible
        # while YouTube Music itself remains signed out. Only its active-account
        # menu proves this browser profile is usable for personalized content.
        if not browser_client_is_signed_in(ytmusic_factory(temp_path)):
            raise ValueError("YouTube Music is still signed out")
        os.replace(temp_path, auth_file)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
