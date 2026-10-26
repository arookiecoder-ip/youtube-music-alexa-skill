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
    positive = ("login required", "authentication required",
                 "requires authentication", "unauthorized",
                 "invalid authentication", "invalid sapisid", "session expired")

    negative = ("timeout", "timed out", "rate limit", "too many requests",
                "parse", "json", "decode")
    return any(x in message for x in positive) and not any(x in message for x in negative)


class BrowserController:
    def __init__(self, base_url, token, timeout=10):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def call(self, path, method="GET", payload=None):
        if not self.base_url or not self.token:
            raise RuntimeError("Browser sidecar is not configured")
        req = urllib.request.Request(self.base_url + path, method=method,
                                     headers={"X-Control-Token": self.token,
                                              "Content-Type": "application/json"},
                                     data=json.dumps(payload or {}).encode() if method != "GET" else None)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # 409 means an existing capture is already running; status remains usable.
            if exc.code == 409:
                return json.load(exc)
            raise


class _OwnerAuth:
    """One identity's view of the shared capture browser.

    The sidecar runs a single Chromium, so the interactive flow is one-at-a-time,
    but the *state* must not be: showing account A's "connected" verdict to account
    B would make B believe its own YouTube Music was linked."""
    __slots__ = ("lease", "state", "message", "last_success", "last_attempt",
                 "refreshing")

    def __init__(self):
        self.lease = None
        self.state = "idle"
        self.message = None
        self.last_success = None
        self.last_attempt = None
        self.refreshing = False


class YouTubeBrowserSessionManager:
    COOKIE_NAME = "yt_browser_lease"

    def __init__(self, controller, promote, invalidate=None, lease_ttl=300,
                 refresh_timeout=45, refresh_cooldown=300, clock=time.time):
        self.controller = controller
        self.promote = promote
        self.invalidate = invalidate or (lambda owner_id=None: None)
        self.lease_ttl = float(lease_ttl)
        self.refresh_timeout = float(refresh_timeout)
        self.refresh_cooldown = float(refresh_cooldown)
        self.clock = clock
        self._lock = threading.RLock()
        self._refresh_condition = threading.Condition(self._lock)
        self._auth = {}
        # The sidecar's single Chromium: which identity is using it right now.
        self._busy = None

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode()).digest()

    def _auth_for(self, owner_id):
        # Callers hold self._lock.
        key = str(owner_id or "")
        entry = self._auth.get(key)
        if entry is None:
            entry = self._auth[key] = _OwnerAuth()
        return entry

    def _expire_locked(self, entry):
        if entry.lease and self.clock() >= entry.lease[2]:
            entry.lease = None
            if entry.state not in ("connected", "reconnect_required"):
                entry.state = "idle"
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

    def start(self, owner_id, profile_key="", viewport=None):
        if viewport is not None:
            if (not isinstance(viewport, dict) or type(viewport.get('width')) is not int
                    or type(viewport.get('height')) is not int
                    or not 320 <= viewport['width'] <= 1920 or not 480 <= viewport['height'] <= 1080):
                raise ValueError('Invalid browser viewport')
            viewport = {key: viewport[key] for key in ('width', 'height')}
        token = secrets.token_urlsafe(32)
        with self._lock:
            if self._busy not in (None, str(owner_id or "")):
                return None, dict(self.status(owner_id),
                                  message="Another account is connecting YouTube Music "
                                          "right now. Try again in a minute.")
            entry = self._auth_for(owner_id)
            self._expire_locked(entry)
            entry.lease = (str(owner_id or ""), self._digest(token), self.clock() + self.lease_ttl)
            entry.state, entry.message = "connecting", None
            self._busy = str(owner_id or "")
        try:
            sidecar = self.controller.call("/interactive/start", "POST",
                                           dict({"profile": profile_key or ""}, **(viewport or {})))
            with self._lock:
                entry.state = sidecar.get("state", "waiting_for_login")
        except Exception:
            with self._lock:
                entry.state, entry.message = "unavailable", "Browser service is unavailable"
                # Nothing is pending for this identity, so it must not keep the
                # Chromium claimed and lock everyone else out until the lease dies.
                entry.lease = None
                self._release_busy_locked()
        return token, self.status(owner_id)

    def _release_busy_locked(self):
        """Hand the sidecar back if no identity holds a live lease on it."""
        for entry in self._auth.values():
            if entry.lease or entry.refreshing:
                return
        self._busy = None

    def authorize(self, owner_id, token):
        with self._lock:
            entry = self._auth_for(owner_id)
            self._expire_locked(entry)
            if not entry.lease or not token:
                return False
            return (hmac.compare_digest(entry.lease[0], str(owner_id)) and
                    hmac.compare_digest(entry.lease[1], self._digest(token)))

    def revoke(self, owner_id=None, close=True):
        with self._lock:
            entry = self._auth_for(owner_id)
            entry.lease = None
            if entry.state != "connected":
                entry.state = "idle"
            self._release_busy_locked()
        if close:
            self._close_sidecar()

    def forget(self, owner_id=None):
        """Drop one identity's browser record without leaving a new one behind.

        ``revoke`` goes through ``_auth_for``, which *creates* the entry it is
        about to clear, and then closes the sidecar over the network. A purge has
        no use for either: the identity is gone, and the entry must be gone with it."""
        with self._lock:
            existed = self._auth.pop(str(owner_id or ""), None) is not None
            self._release_busy_locked()
        return existed

    def open_youtube(self, owner_id=None):
        """Ask the visible plain browser to navigate to YouTube Music."""
        sidecar = self.controller.call("/interactive/open-youtube", "POST")
        with self._lock:
            entry = self._auth_for(owner_id)
            entry.state = sidecar.get("state", entry.state)
            entry.message = sidecar.get("message")
        return self.status(owner_id)

    def request_capture(self, owner_id=None):
        """Begin the explicit plain-browser to validation-browser handoff."""
        sidecar = self.controller.call("/interactive/capture", "POST")
        with self._lock:
            entry = self._auth_for(owner_id)
            entry.state = sidecar.get("state", "capture_requested")
            entry.message = sidecar.get("message")
        return self.status(owner_id)

    def _take_and_promote(self, owner_id):
        candidate = self.controller.call("/candidate/take", "POST")
        if not accepted_browse_url(candidate.get("url")):
            raise ValueError("Captured request was not a YouTube Music browse request")
        self.promote(candidate.get("headers"), owner_id)
        self.invalidate(owner_id)
        with self._lock:
            entry = self._auth_for(owner_id)
            entry.state, entry.message = "connected", None
            entry.last_success = self.clock()
            entry.lease = None
            self._release_busy_locked()
        self._complete_sidecar()

    def poll(self, owner_id=None):
        with self._lock:
            entry = self._auth_for(owner_id)
            self._expire_locked(entry)
            # Only the identity that owns the Chromium can read meaningful state
            # from it; the sidecar has no per-user view.
            if self._busy not in (None, str(owner_id or "")):
                return self._status_locked(entry)
        try:
            sidecar = self.controller.call("/status")
            state = sidecar.get("state", "unavailable")
            if state in ("candidate_taken", "complete"):
                # Promotion owns the one-time candidate, then asks the
                # sidecar to complete. Keep the manager's authoritative
                # connected state in both cases: replacing it with the
                # sidecar's transient "complete" status prevents the remote
                # UI from running its successful-auth refresh path.
                return self.status(owner_id)
            with self._lock:
                self._expire_locked(entry)
                entry.state = "validating" if state == "captured" else state
                entry.message = sidecar.get("message")
            if state == "captured":
                self._take_and_promote(owner_id)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                with self._lock:
                    entry.state, entry.message = "unavailable", "Browser service is unavailable"
        except urllib.error.URLError:
            with self._lock:
                entry.state, entry.message = "unavailable", "Browser service is unavailable"
        except Exception:
            with self._lock:
                entry.state, entry.message = "reconnect_required", "Captured session was not accepted"
        return self.status(owner_id)

    def _status_locked(self, entry):
        return {"state": entry.state, "available": entry.state != "unavailable",
                "refreshing": entry.refreshing,
                "reconnect_required": entry.state == "reconnect_required",
                "last_successful_renewal": entry.last_success, "message": entry.message,
                "lease_expires_at": entry.lease[2] if entry.lease else None}

    def status(self, owner_id=None):
        with self._lock:
            entry = self._auth_for(owner_id)
            self._expire_locked(entry)
            return self._status_locked(entry)

    def refresh(self, owner_id=None, wait=True):
        """Single-flight saved-profile refresh for one identity's own profile."""
        profile_key = str(owner_id or "")
        with self._refresh_condition:
            entry = self._auth_for(owner_id)
            now = self.clock()
            if entry.refreshing:
                if wait:
                    self._refresh_condition.wait(timeout=self.refresh_timeout)
                return entry.state == "connected"
            if entry.last_attempt and now - entry.last_attempt < self.refresh_cooldown:
                return entry.state == "connected"
            if self._busy not in (None, profile_key):
                # The one Chromium is busy with another account's login.
                return entry.state == "connected"
            entry.refreshing, entry.last_attempt = True, now
            entry.state, entry.message = "refreshing", None
            self._busy = profile_key
        ok = False
        try:
            self.controller.call("/refresh", "POST", {"profile": profile_key})
            deadline = self.clock() + self.refresh_timeout
            while self.clock() < deadline:
                sidecar = self.controller.call("/status")
                state = sidecar.get("state")
                if state == "captured":
                    self._take_and_promote(owner_id); ok = True; break
                if state in ("reconnect_required", "unavailable"):
                    with self._lock:
                        entry.state = state
                        entry.message = sidecar.get("message")
                    break
                time.sleep(0.25)
            if not ok and entry.state == "refreshing":
                with self._lock:
                    entry.state, entry.message = "reconnect_required", "Google interaction is required"
        except Exception:
            with self._lock:
                entry.state, entry.message = "reconnect_required", "Saved browser session could not renew authentication"
        finally:
            with self._refresh_condition:
                entry.refreshing = False
                self._release_busy_locked()
                self._refresh_condition.notify_all()
        return ok

    def call_with_one_refresh(self, operation, owner_id=None):
        try:
            return operation()
        except Exception as exc:
            if not is_authentication_error(exc) or not self.refresh(owner_id):
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
