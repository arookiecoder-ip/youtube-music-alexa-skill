"""Private controller for a persistent, owner-operated YT Music browser.

Only redacted lifecycle state crosses this API. Captured request headers are
returned once and are never written to logs or the Chromium profile volume.

The container runs one Chromium at a time, but each caller signs in to its *own*
profile directory: the owner keeps the base profile it has always used, while an
account gets ``<PROFILE_DIR>/<key>``. Without that split, whoever clicked "Connect
YouTube Music" last would hand Google's cookies to every other account.
"""
import concurrent.futures
import hmac
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlparse

from flask import Flask, jsonify, request

import google_signin

app = Flask(__name__)
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
CONTROL_TOKEN = os.environ.get("YT_BROWSER_CONTROL_TOKEN", "")
CAPTURE_TIMEOUT = float(os.environ.get("YT_BROWSER_CAPTURE_TIMEOUT", "120"))
LOGIN_TIMEOUT = float(os.environ.get("YT_BROWSER_LOGIN_TIMEOUT", "240"))
SUCCESS_DISPLAY_SECONDS = float(os.environ.get("YT_BROWSER_SUCCESS_DISPLAY", "12"))
PROFILE_DIR = os.environ.get("CHROME_USER_DATA_DIR", "/profile")
CHROMIUM = os.environ.get("CHROMIUM_EXECUTABLE", "/usr/bin/chromium")
CDP_URL = os.environ.get("CHROMIUM_CDP_URL", "http://127.0.0.1:9222")

# Google's own entry into YouTube: a top-level pass through the account makes
# YouTube mint its .youtube.com cookies, which is the session capture needs.
_YOUTUBE_SIGNIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube"
                       "&continue=https%3A%2F%2Fmusic.youtube.com%2F")
# Any one of these on .google.com means the browser holds a Google session.
_GOOGLE_SESSION_COOKIES = frozenset((
    "SID", "HSID", "SSID", "SAPISID",
    "__Secure-1PSID", "__Secure-1PAPISID", "__Secure-3PSID", "__Secure-3PAPISID",
))

# Profile keys arrive over the control API, so they are restricted to a plain
# filename segment: no separators, no traversal, no leading dot.
_PROFILE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_lock = threading.RLock()
_state = {
    "state": "idle", "interactive": False, "started_at": None,
    "updated_at": time.time(), "message": None,
}
_candidate = None
_worker = None
_manual_browser = None
_complete_requested = False
_viewport = {"width":480,"height":900}
_capture_requested = threading.Event()
_profile_key = ""
_auth_attempt = ""
_auth_attempt_deadline = 0.0


def _profile_dir_for(key):
    """Resolve a profile key to its directory, refusing anything unexpected."""
    if not key:
        return PROFILE_DIR
    if not _PROFILE_KEY_RE.match(key):
        raise ValueError("Invalid browser profile key.")
    return os.path.join(PROFILE_DIR, key)


def _clear_profile_singleton_artifacts(profile_dir):
    """Remove Chromium locks left behind after an unclean browser exit.

    This controller is the only process allowed to open a profile directory, and
    _start() prevents concurrent workers, so artifacts present immediately
    before a new launch are stale.
    """
    for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
        path = os.path.join(profile_dir, name)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _stop_manual_browser():
    global _manual_browser
    with _lock:
        process, _manual_browser = _manual_browser, None
    if process is None or process.poll() is not None:
        return
    # Ask the X11 window to close first. This delivers Chromium's normal
    # WM_DELETE_WINDOW path so its cookie DB and profile state are flushed.
    # SIGTERM remains a bounded fallback for startup failures/wedged windows.
    try:
        window_ids = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible", "--class", "chromium"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).splitlines()
        for window_id in window_ids:
            subprocess.run(
                ["xdotool", "windowclose", window_id.decode("ascii")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, check=False,
            )
        process.wait(timeout=8)
        return
    except (FileNotFoundError, subprocess.SubprocessError, UnicodeError):
        pass
    _signal_browser_group(process, "terminate")
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_browser_group(process, "kill")
        process.wait(timeout=5)


def _signal_browser_group(process, how):
    """Signal Chromium's whole session, not just the wrapper that spawned it.

    The browser is a process group leader (``start_new_session``), and the
    renderer/GPU children hold the profile lock: leaving them behind makes the
    next launch exit immediately on a profile singleton that nobody owns.
    """
    import signal

    sig = signal.SIGKILL if how == "kill" else signal.SIGTERM
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError):
        getattr(process, how)()


def _plain_chromium_command(enable_capture=False, profile_dir=PROFILE_DIR):
    """Build Chromium command, keeping interactive login free of CDP."""
    os.makedirs(profile_dir, exist_ok=True)
    command = [
        CHROMIUM, f"--user-data-dir={profile_dir}",
        *(["--no-sandbox"] if os.environ.get("MUSICBOX_CHROMIUM_DISABLE_SANDBOX") == "1" else []),
        "--disable-dev-shm-usage", f"--window-size={_viewport['width']},{_viewport['height']}",
        "--window-position=0,0", "--no-first-run",
        "--hide-crash-restore-bubble", "--test-type",
        # Keep cookie encryption identical across every launch.
        "--password-store=basic", "--use-mock-keychain",
    ]
    proxy = os.environ.get("MUSICBOX_BROWSER_PROXY", "")
    if proxy:
        command.append("--proxy-server=" + proxy)
        command.append("--proxy-bypass-list=localhost;127.0.0.1;[::1]")
    if enable_capture:
        # This is enabled only after Google login has completed. Playwright
        # observes the resulting YT Music request over loopback; it never
        # controls the browser while credentials are entered.
        command.extend([
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            "--remote-allow-origins=http://127.0.0.1:9222",
        ])
    command.append("https://music.youtube.com")
    return command


def _manual_login(enable_capture=False, profile_dir=PROFILE_DIR):
    """Launch Chromium, optionally exposing loopback CDP after login."""
    global _manual_browser
    _clear_profile_singleton_artifacts(profile_dir)
    process = subprocess.Popen(
        _plain_chromium_command(enable_capture=enable_capture, profile_dir=profile_dir),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with _lock:
        _manual_browser = process
    return True


def _open_youtube_music(target="https://music.youtube.com"):
    """Navigate the visible plain Chromium window without a DevTools channel."""
    window_ids = subprocess.check_output(
        ["xdotool", "search", "--onlyvisible", "--class", "chromium"],
        stderr=subprocess.DEVNULL, timeout=2,
    ).splitlines()
    if not window_ids:
        raise RuntimeError("Chromium window unavailable")
    window_id = window_ids[-1].decode("ascii")
    commands = (
        ["xdotool", "windowactivate", "--sync", window_id],
        ["xdotool", "key", "--window", window_id, "ctrl+l"],
        ["xdotool", "type", "--window", window_id, "--delay", "1",
         target],
        ["xdotool", "key", "--window", window_id, "Return"],
    )
    for command in commands:
        subprocess.run(command, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3, check=True)


def authenticated_browse_headers(headers):
    """Accept only YouTube's signed-in SAPISID authorization schemes."""
    if not isinstance(headers, dict) or not headers.get("cookie"):
        return False
    scheme = headers.get("authorization", "").partition(" ")[0].upper()
    return scheme in {"SAPISIDHASH", "SAPISID1PHASH", "SAPISID3PHASH"}


def account_menu_is_signed_in(payload):
    """Require YouTube Music's active account, not merely Google cookies."""
    try:
        renderer = payload["actions"][0]["openPopupAction"]["popup"][
            "multiPageMenuRenderer"]
        active = renderer["header"]["activeAccountHeaderRenderer"]
        runs = active["accountName"]["runs"]
        return bool(runs and isinstance(runs[0].get("text"), str)
                    and runs[0]["text"].strip())
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def account_menu_reports_signed_out(payload):
    """Recognize a real account-menu response which lacks an active account."""
    try:
        renderer = payload["actions"][0]["openPopupAction"]["popup"][
            "multiPageMenuRenderer"]
    except (KeyError, IndexError, TypeError):
        return False
    return not account_menu_is_signed_in(payload) and isinstance(renderer, dict)


def accepted_browse_url(url):
    """Accept any signed-in YT Music Innertube request.

    The web app may call player, next, or account_menu after login instead of
    browse. All use the same signed browser headers required by ytmusicapi.
    """
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return (parsed.scheme == "https" and parsed.hostname == "music.youtube.com"
            and parsed.path.startswith("/youtubei/v1/"))


@app.before_request
def require_control_token():
    global _auth_attempt, _auth_attempt_deadline, _candidate
    if request.path == "/health":
        return None
    supplied = request.headers.get("X-Control-Token", "")
    if not CONTROL_TOKEN or not hmac.compare_digest(supplied, CONTROL_TOKEN):
        return jsonify({"error": "unauthorized"}), 401
    attempt = request.headers.get('X-Auth-Attempt', '')
    if request.path in ('/profile/delete',):
        return None
    with _lock:
        if request.path in ('/interactive/start', '/refresh', '/google/start'):
            if not attempt:
                return jsonify({'error': 'attempt_required'}), 403
            if (_auth_attempt and _auth_attempt != attempt and _auth_attempt_deadline > time.monotonic()
                    and not hmac.compare_digest(request.headers.get('X-Previous-Attempt', ''), _auth_attempt)):
                return jsonify({'error': 'browser_in_use'}), 409
            if _auth_attempt != attempt:
                _candidate = None
            _auth_attempt = attempt
            _auth_attempt_deadline = time.monotonic() + 300
        elif (not attempt or not hmac.compare_digest(attempt, _auth_attempt)
              or (time.monotonic() >= _auth_attempt_deadline and request.path not in ('/google/stop', '/interactive/close', '/interactive/complete'))):
            return jsonify({'error': 'attempt_expired'}), 403
        else:
            _auth_attempt_deadline = time.monotonic() + 300



@app.after_request
def retire_auth_attempt(response):
    global _auth_attempt, _auth_attempt_deadline, _candidate
    if request.path in ('/google/stop', '/interactive/close', '/interactive/complete') and response.status_code < 300:
        with _lock:
            if request.headers.get('X-Auth-Attempt') == _auth_attempt:
                _auth_attempt, _auth_attempt_deadline, _candidate = '', 0.0, None
    return response


def _public_state():
    with _lock:
        return dict(_state, candidate_ready=_candidate is not None)


def _set_state(name, **extra):
    with _lock:
        _state.update(state=name, updated_at=time.time(), **extra)


def _capture_from_running_browser(interactive, connection=None):
    """Attach briefly to the existing Chromium process and validate YT Music.

    ``connection`` is the automation's ``(context, page)`` when a scripted
    sign-in already owns this thread: Playwright's sync API allows only one
    instance per thread, so opening a second one here would fail outright.
    """
    if connection is not None:
        return _watch_for_account_headers(connection[0], connection[1], interactive)
    from playwright.sync_api import sync_playwright

    # Exiting sync_playwright disconnects its transport. Deliberately do not
    # call browser.close(): the owner keeps using this exact Chromium process.
    with sync_playwright() as pw:
        browser = None
        connect_deadline = time.time() + 10
        while browser is None and time.time() < connect_deadline:
            try:
                browser = pw.chromium.connect_over_cdp(CDP_URL)
            except Exception:
                time.sleep(0.2)
        if browser is None or not browser.contexts:
            raise RuntimeError("Chromium CDP endpoint unavailable")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        return _watch_for_account_headers(context, page, interactive)


def _google_session_present(context):
    """True when the browser jar carries a Google sign-in, not just visitor cookies."""
    try:
        names = {str(cookie.get("name") or "") for cookie in context.cookies()}
    except Exception:
        logger.warning("Could not read the browser cookie jar", exc_info=True)
        return False
    present = bool(names & _GOOGLE_SESSION_COOKIES)
    logger.info("Capture: Google session cookies present: %s", present)
    return present


def _watch_for_account_headers(context, page, interactive):
    """Navigate to YouTube Music and wait for the active-account menu response."""
    global _candidate
    expected_attempt = _auth_attempt

    captured = threading.Event()
    signed_out = threading.Event()
    signed_out_at = [0.0]

    def on_response(response):
        global _candidate
        if captured.is_set():
            return
        try:
            parsed = urlparse(response.url)
            if (parsed.scheme != "https"
                    or parsed.hostname != "music.youtube.com"
                    or parsed.path != "/youtubei/v1/account/account_menu"):
                return
            payload = response.json()
            if account_menu_reports_signed_out(payload):
                if not signed_out.is_set():
                    signed_out_at[0] = time.time()
                signed_out.set()
                return
            if not account_menu_is_signed_in(payload):
                return
            headers = response.request.all_headers()
        except Exception:
            return
        if not authenticated_browse_headers(headers):
            return
        with _lock:
            if expected_attempt != _auth_attempt:
                return
            _candidate = {"url": response.url, "headers": headers, "attempt_id": expected_attempt}
        captured.set()
        _set_state("captured", interactive=interactive, message=None)

    context.on("response", on_response)
    try:
        page.goto("https://music.youtube.com", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        # A consent wall or a half-painted page still leaves a page to inspect,
        # and the account-menu response is the only verdict that counts.
        logger.warning("Could not finish loading YouTube Music during capture", exc_info=True)
    deadline = time.time() + CAPTURE_TIMEOUT
    next_probe = 0.0
    reason = ""
    handshake_tried = False
    while not captured.is_set() and time.time() < deadline:
        if interactive and not _public_state()["interactive"]:
            break
        settle = 10.0 if handshake_tried else 4.0
        if signed_out.is_set() and time.time() - signed_out_at[0] >= settle:
            if handshake_tried:
                break
            handshake_tried = True
            if not _google_session_present(context):
                break
            # The Google account is signed in, but YouTube Music only ever trusts
            # its own cookies. Opening YouTube Music cold shows the anonymous page
            # and its account menu answers "signed out" before any handshake has
            # run; passing through Google's YouTube entry performs that handshake
            # as a top-level navigation, so it works without third-party cookies.
            logger.info("Signed out on YouTube Music with a live Google session; "
                        "following Google's YouTube entry")
            signed_out.clear()
            try:
                page.goto(_YOUTUBE_SIGNIN_URL, wait_until="domcontentloaded",
                          timeout=45000)
            except Exception:
                logger.warning("Could not follow Google's YouTube entry", exc_info=True)
            continue
        now = time.time()
        if not reason and now >= next_probe:
            next_probe = now + 2.0
            try:
                state = google_signin.read_page_state(page)
                verdict = google_signin.classify_page(state)
            except Exception:
                verdict = ""
            if verdict == google_signin.CONSENT:
                # The EU cookie wall sits between sign-in and the account menu.
                if google_signin.click_consent(page):
                    next_probe = 0.0
            elif verdict == google_signin.CAPTCHA:
                reason = "captcha"
            elif verdict in (google_signin.NEED_IDENTIFIER,
                             google_signin.NEED_PASSWORD,
                             google_signin.UNKNOWN_ACCOUNT,
                             google_signin.BLOCKED,
                             google_signin.LOCKED):
                reason = "signed_out"
        page.wait_for_timeout(250)
    context.remove_listener("response", on_response)
    if captured.is_set():
        return "captured"
    if reason:
        return reason
    if signed_out.is_set():
        return "signed_out"
    if interactive and not _public_state()["interactive"]:
        return "cancelled"
    return "timeout"


# --------------------------------------------------------------------------
# Automated sign-in as the server's own Google account.
#
# This is the deliberate exception to the rule at the top of the file. While a
# human is signing in, no client may attach to the browser, because a DevTools
# transport can watch what they type. Here nobody types: the credential arrives
# from the application server over the loopback control API one step at a time,
# so the trade being made is scripted reliability against a slightly more
# detectable browser -- and the whole ladder can be left switched off.
# --------------------------------------------------------------------------

_automation = None
_AUTOMATION_KINDS = ("start", "identifier", "password", "code", "choice", "consent",
                     "methods", "method")

# Playwright's sync API is bound to the thread that started it: a page created
# while answering one request raises "Cannot switch to a different thread" when
# the next request lands on another waitress worker. Every scripted step (and
# the capture and stop paths that touch the same page) therefore runs on this
# one long-lived thread, which is what makes the ladder survive across calls.
_google_worker = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="google-signin")


def _on_google_thread(work, timeout=180):
    from flask import has_request_context
    expected = request.headers.get('X-Auth-Attempt') if has_request_context() else None
    def guarded():
        if expected and expected != _auth_attempt:
            return {'outcome':'attempt_expired'}, 409
        result = work()
        if expected and expected != _auth_attempt:
            return {'outcome':'attempt_expired'}, 409
        return result
    return _google_worker.submit(guarded).result(timeout=timeout)


def _automation_usable():
    if not _automation:
        return False
    try:
        return not _automation["page"].is_closed()
    except Exception:
        return False


def _cdp_ready(deadline):
    """Wait for Chromium's DevTools port before asking Playwright to attach.

    ``connect_over_cdp`` against a window that has not opened the port yet fails
    with a driver-level error that can wedge the Playwright transport; probing
    the HTTP endpoint first keeps a slow cold start from looking like a crash.
    """
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(CDP_URL + "/json/version", timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _attach_automation(profile_dir):
    """Open Chromium with CDP enabled and attach the driver that types into it."""
    global _automation
    from playwright.sync_api import sync_playwright

    if _automation_usable() and _automation.get("profile_dir") == profile_dir:
        return _automation["page"]
    _detach_automation()
    _manual_login(enable_capture=True, profile_dir=profile_dir)
    try:
        if not _cdp_ready(time.time() + 45):
            raise RuntimeError("Chromium did not open its DevTools port")
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            if not browser.contexts:
                raise RuntimeError("Chromium exposed no browser context")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
        except Exception:
            try:
                playwright.stop()
            except Exception:
                pass
            raise
    except Exception:
        _stop_manual_browser()
        raise
    _automation = {"playwright": playwright, "browser": browser, "context": context,
                   "page": page, "profile_dir": profile_dir}
    return page


def _detach_automation():
    """Disconnect the driver and close the browser it was driving.

    Closing through :func:`_stop_manual_browser` rather than ``browser.close()``
    is what makes the next ``/refresh`` work: the window-close path is the one
    where Chromium writes its cookie database to the profile volume.
    """
    global _automation
    with _lock:
        auto, _automation = _automation, None
    if auto:
        try:
            auto["playwright"].stop()
        except Exception:
            pass
    _stop_manual_browser()


def _netscape_jar(cookies):
    """Render browser cookies as a Netscape jar, for yt-dlp and nothing else.

    Only YouTube's own domains are exported: the Google account cookies on the
    same browser can drive the account itself, and have no business sitting in a
    file the download path reads.
    """
    lines = ["# Netscape HTTP Cookie File",
             "# Generated by the Music Box browser service", ""]
    seen = set()
    horizon = int(time.time()) + 30 * 86400
    for cookie in cookies or ():
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not domain or not name or not value:
            continue
        host = domain[1:] if domain.startswith(".") else domain
        if host != "youtube.com" and not host.endswith(".youtube.com"):
            continue
        path = str(cookie.get("path") or "/")
        key = (host, path, name)
        if key in seen:
            continue
        seen.add(key)
        try:
            expires = float(cookie.get("expires") or 0)
        except (TypeError, ValueError):
            expires = 0.0
        expiry = int(expires) if expires > 0 else horizon
        # The HttpOnly marker is part of the file format, not decoration: a jar
        # without it reads back as if these were script-visible cookies.
        prefix = "#HttpOnly_" if cookie.get("httponly") else ""
        lines.append("\t".join([
            f"{prefix}.{host}", "TRUE", path,
            "TRUE" if cookie.get("secure") else "FALSE", str(expiry), name, value,
        ]))
    return "\n".join(lines) + "\n"


def _google_busy():
    with _lock:
        return bool(_worker and _worker.is_alive()) or bool(_state["interactive"])


def _excerpt(page, secrets=()):
    """A short, secret-free description of what the page actually says.

    "Google showed a page this server does not know how to answer" is true and
    useless. The operator reading the failure email needs the heading Google put
    on screen, which is stable copy -- never the credential that was typed, so the
    values we just handled are replaced before anything is kept.
    """
    text = ""
    for reader in (
        lambda: page.inner_text("body", timeout=1500),
        lambda: page.evaluate("() => document.body ? document.body.innerText : ''"),
        lambda: page.title(),
    ):
        try:
            text = reader() or ""
        except Exception as exc:
            logger.warning("Could not read the sign-in page text: %s: %s",
                           type(exc).__name__, str(exc)[:120])
            text = ""
        if text.strip():
            break
    text = re.sub(r"\s+", " ", text or "").strip()
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[redacted]")
    return text[:280]


def _google_step(kind, handler, profile_key="", secrets=()):
    """Run one driver step, and never let a broken page look like a login answer."""
    global _profile_key
    if kind not in _AUTOMATION_KINDS and kind != "capture":
        return jsonify({"error": "unknown_step"}), 400
    if _google_busy():
        return jsonify({"outcome": "browser_in_use"}), 409
    try:
        profile_dir = _profile_dir_for(profile_key)
    except ValueError:
        return jsonify({"error": "invalid_profile_key"}), 400

    def work():
        global _profile_key
        try:
            page = _attach_automation(profile_dir)
        except Exception:
            logger.warning("Could not attach the sign-in browser during %s", kind,
                           exc_info=True)
            return {"outcome": "crashed"}, 503
        _profile_key = profile_dir
        _set_state("signing_in", interactive=False, message=None)
        try:
            state, outcome = handler(page)
        except Exception:
            logger.warning("Sign-in step %s raised", kind, exc_info=True)
            _detach_automation()
            return {"outcome": "crashed"}, 503
        detail = ""
        if page.is_closed():
            detail = "The sign-in browser window closed before this page could be read."
        elif (outcome or "unsupported") in ("unsupported", "blocked", "captcha", "locked",
                                            "wrong_password", "unknown_account", "timeout"):
            detail = _excerpt(page, secrets)
        if outcome in ("crashed", "unsupported", "blocked", "locked",
                       "wrong_password", "unknown_account", "timeout"):
            # Nothing further can be learned from this browser: release it so the
            # profile is flushed and the next attempt starts from a clean window.
            # A captcha keeps the browser, because the person can still answer it,
            # and ``signed_in`` keeps it for the capture step.
            _detach_automation()
        answer = {"outcome": outcome or "unsupported",
                  "url": _redact_url((state or {}).get("url"))}
        if detail:
            answer["detail"] = detail
        if outcome == google_signin.NEED_PHONE:
            answer["phone"] = google_signin.phone_hint((state or {}).get("text") or "")
        if outcome == google_signin.NEED_PROMPT:
            answer["prompt"] = google_signin.prompt_hint((state or {}).get("text") or "")
            answer["devices"] = google_signin.device_hint((state or {}).get("text") or "")
        if outcome == google_signin.CAPTCHA:
            answer["captcha"] = {
                "text": bool(google_signin._present(page, google_signin._CAPTCHA_INPUTS)),
                "hint": google_signin.captcha_hint((state or {}).get("text") or ""),
            }
        logger.info("Sign-in step %s -> %s at %s", kind, answer["outcome"], answer["url"])
        return answer, 200

    answer, status = _on_google_thread(work)
    return jsonify(answer), status


def _redact_url(url):
    """Keep Google's path for the status panel and drop anything it carries."""
    try:
        parsed = urlparse(url or "")
    except (TypeError, ValueError):
        return ""
    return f"{parsed.hostname or ''}{parsed.path or ''}"[:160]


def _capture_and_export():
    """Capture the signed-in headers and the matching cookie jar, and hand them over once.

    This is ``/candidate/take`` for the automated ladder: the candidate is cleared
    as it is returned, so a captured session cannot be read twice by anything that
    learns the control token later.
    """
    global _candidate
    auto = _automation
    connection = (auto["context"], auto["page"]) if auto else None
    result = _capture_from_running_browser(False, connection=connection)
    if result != "captured":
        page = auto["page"] if auto else None
        answer = {"outcome": result or "timeout"}
        if page is not None and not page.is_closed():
            try:
                answer["url"] = _redact_url(page.url)
                answer["detail"] = _excerpt(page)
            except Exception:
                pass
        if result == "captcha":
            # The person can still clear the check, so the browser stays.
            try:
                _state, shot = google_signin.captcha_snapshot(page)
                answer.update(shot)
            except Exception:
                logger.warning("Could not photograph the security check", exc_info=True)
        else:
            _detach_automation()
        return answer, 200
    cookies = ""
    try:
        cookies = _netscape_jar(auto["context"].cookies("https://music.youtube.com"))
    except Exception:
        # A signed-in browser whose jar cannot be read is still a signed-in browser:
        # the headers alone re-authenticate the app, and the jar is a convenience.
        logger.warning("Signed in, but the cookie jar could not be read")
    with _lock:
        candidate, _candidate = _candidate, None
    _detach_automation()
    if candidate is None:
        # Something took the candidate mid-capture (a revoke, a stale lease).
        return {"outcome": "timeout"}, 200
    _set_state("candidate_taken", message=None)
    return {"outcome": "captured", "url": candidate.get("url"),
            "headers": candidate.get("headers") or {},
            "cookies": cookies}, 200


@app.post("/google/start")
def google_start():
    body = request.get_json(silent=True) or {}
    return _google_step("start", google_signin.open_signin,
                        str(body.get("profile") or ""),
                        secrets=(str(body.get("email") or ""),))


@app.post("/google/identifier")
def google_identifier():
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "")
    return _google_step("identifier",
                        lambda page: google_signin.submit_identifier(page, email),
                        str(body.get("profile") or ""),
                        secrets=(email,))


@app.post("/google/password")
def google_password():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password") or "")
    return _google_step("password",
                        lambda page: google_signin.submit_password(page, password),
                        str(body.get("profile") or ""),
                        secrets=(password,))


@app.post("/google/code")
def google_code():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "")
    if not re.fullmatch(r"[A-Za-z0-9-]{4,16}", code):
        return jsonify({"error": "invalid_code"}), 400
    return _google_step("code",
                        lambda page: google_signin.submit_code(page, code),
                        str(body.get("profile") or ""),
                        secrets=(code,))


@app.post("/google/choice")
def google_choice():
    body = request.get_json(silent=True) or {}
    return _google_step("choice", google_signin.choose_two_step,
                        str(body.get("profile") or ""))


@app.post("/google/consent")
def google_consent():
    body = request.get_json(silent=True) or {}
    return _google_step("consent", google_signin.accept_consent,
                        str(body.get("profile") or ""))


@app.post("/google/methods")
def google_methods():
    body = request.get_json(silent=True) or {}
    return _google_step("methods", google_signin.show_methods,
                        str(body.get("profile") or ""))


@app.post("/google/method")
def google_method():
    body = request.get_json(silent=True) or {}
    method = str(body.get("method") or "")
    if method not in google_signin.METHODS:
        return jsonify({"error": "unknown_method"}), 400
    return _google_step("method",
                        lambda page: google_signin.choose_method(page, method),
                        str(body.get("profile") or ""))


def _captcha_answer(state, outcome, page):
    """The step answer for a captcha page, with a fresh picture while it lasts."""
    answer = {"outcome": outcome or google_signin.CAPTCHA,
              "url": _redact_url(state.get("url"))}
    if outcome == google_signin.NEED_PHONE:
        answer["phone"] = google_signin.phone_hint(state.get("text") or "")
    if outcome == google_signin.NEED_PROMPT:
        answer["prompt"] = google_signin.prompt_hint(state.get("text") or "")
        answer["devices"] = google_signin.device_hint(state.get("text") or "")
    if outcome == google_signin.CAPTCHA:
        try:
            _state, shot = google_signin.captcha_snapshot(page)
            answer.update(shot)
        except Exception:
            logger.warning("Could not photograph the security check", exc_info=True)
    return answer


@app.route("/google/captcha", methods=["GET", "POST"])
def google_captcha():
    """Show the security check, then take the answer it is waiting for."""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        text = str(body.get("text") or "").strip()
        points = body.get("points") or []
        if text and len(text) > 120:
            return jsonify({"error": "invalid_answer"}), 400
        if points and (not isinstance(points, list) or len(points) > 20):
            return jsonify({"error": "invalid_answer"}), 400
        for point in points:
            if (not isinstance(point, list) or len(point) != 2
                    or not all(isinstance(value, (int, float)) and 0 <= value <= 1
                               for value in point)):
                return jsonify({"error": "invalid_answer"}), 400
        if not text and not points:
            return jsonify({"error": "answer_required"}), 400
        profile = str(body.get("profile") or "")
    else:
        text, points = "", []
        profile = request.args.get("profile") or ""
    try:
        _profile_dir_for(profile)
    except ValueError:
        return jsonify({"error": "invalid_profile_key"}), 400
    is_get = request.method == "GET"

    def work():
        if not _automation_usable():
            return {"outcome": "not_signed_in"}, 409
        page = _automation["page"]
        if is_get:
            try:
                state, shot = google_signin.captcha_snapshot(page)
            except Exception:
                logger.warning("Could not photograph the security check", exc_info=True)
                return {"outcome": "crashed"}, 503
            return {"outcome": "captcha", **shot}, 200
        if page.is_closed():
            return {"outcome": "not_signed_in"}, 409
        try:
            state, outcome = google_signin.answer_captcha(page, text=text, points=points)
        except Exception:
            logger.warning("Security check answer raised", exc_info=True)
            _detach_automation()
            return {"outcome": "crashed"}, 503
        if outcome in ("crashed", "unsupported", "blocked", "locked",
                       "wrong_password", "unknown_account", "timeout",
                       "captcha_needs_taps"):
            _detach_automation()
        return _captcha_answer(state, outcome, page), 200

    answer, status = _on_google_thread(work)
    return jsonify(answer), status


@app.post("/google/capture")
def google_capture():
    body = request.get_json(silent=True) or {}
    try:
        _profile_dir_for(str(body.get("profile") or ""))
    except ValueError:
        return jsonify({"error": "invalid_profile_key"}), 400

    def work():
        if not _automation_usable():
            return {"outcome": "not_signed_in"}, 409
        return _capture_and_export()

    answer, status = _on_google_thread(work)
    return jsonify(answer), status


@app.post("/google/stop")
def google_stop():
    _on_google_thread(_detach_automation)
    _set_state("idle", interactive=False, message=None)
    return jsonify({"outcome": "stopped"})


@app.get("/google/state")
def google_state():
    def work():
        if not _automation_usable():
            return {"outcome": "idle"}
        try:
            state = google_signin.read_page_state(_automation["page"])
        except Exception:
            return {"outcome": "crashed"}
        outcome = google_signin.classify_page(state)
        answer = {"outcome": outcome, "url": _redact_url(state.get("url"))}
        text = state.get("text") or ""
        if outcome == google_signin.NEED_PHONE:
            answer["phone"] = google_signin.phone_hint(text)
        if outcome == google_signin.NEED_PROMPT:
            answer["prompt"] = google_signin.prompt_hint(text)
            answer["devices"] = google_signin.device_hint(text)
        if outcome == google_signin.CAPTCHA:
            answer["captcha"] = {
                "text": bool(google_signin._present(
                    _automation["page"], google_signin._CAPTCHA_INPUTS)),
                "hint": google_signin.captcha_hint(text),
            }
        if outcome in (google_signin.UNSUPPORTED, google_signin.TIMEOUT):
            answer["detail"] = _excerpt(_automation["page"])
        return answer

    return jsonify(_on_google_thread(work, timeout=30))


def _capture(interactive, profile_dir):
    phase = "startup"
    _set_state("waiting_for_login" if interactive else "refreshing",
               interactive=interactive, started_at=time.time(), message=None)
    try:
        # A fixed nonzero loopback CDP port does not set Chromium's webdriver
        # flag. Crucially, no client attaches while Google credentials are
        # entered. Explicit Capture attaches to this same process only after
        # the owner has visibly confirmed the YouTube Music account.
        phase = "Chromium startup"
        _manual_login(enable_capture=True, profile_dir=profile_dir)

        while interactive and _public_state()["interactive"]:
            if not _capture_requested.wait(timeout=0.25):
                continue
            _capture_requested.clear()
            _set_state("validating_login", interactive=True, message=None)
            phase = "same-process CDP validation"
            result = _capture_from_running_browser(True)
            if result == "captured":
                break
            if result == "cancelled":
                _set_state("idle", interactive=False, message=None)
                return
            message = ("YouTube Music is still signed out. Sign in, then capture again"
                       if result == "signed_out" else
                       "Could not validate YouTube Music yet. Try capture again")
            _set_state("waiting_for_login", interactive=True, message=message)

        if not interactive:
            phase = "saved-profile CDP validation"
            result = _capture_from_running_browser(False)
            if result != "captured":
                _set_state("reconnect_required", interactive=False,
                           message="Saved browser profile is signed out")
                return

        if _public_state()["state"] != "captured":
            return
        # Flask must promote the one-time candidate before Chromium is closed.
        # A rejected promotion also leaves this process alive for inspection.
        while _public_state()["interactive"] and not _complete_requested:
            time.sleep(0.25)
        if _complete_requested:
            _set_state("complete", interactive=False, message="Session saved")
            time.sleep(max(0, SUCCESS_DISPLAY_SECONDS))
    except Exception as exc:
        logger.warning("Browser workflow failed during %s (%s)", phase, type(exc).__name__)
        _set_state("unavailable", interactive=False,
                   message=f"Browser workflow failed during {phase}")
    finally:
        # Only explicit Close, lease expiry, or manager completion reaches this
        # boundary. Capture attempts themselves never replace Chromium.
        _stop_manual_browser()


def _start(interactive, profile_key=""):
    """Launch a capture run in one identity's profile. One run at a time."""
    global _worker, _candidate, _complete_requested, _profile_key
    try:
        profile_dir = _profile_dir_for(profile_key)
    except ValueError:
        return False
    with _lock:
        if _worker and _worker.is_alive():
            return False
        if _automation is not None:
            # One Chromium. A scripted sign-in in progress owns it, and starting a
            # second workflow would drive the same window from two places.
            return False
        _candidate = None
        _complete_requested = False
        _capture_requested.clear()
        _profile_key = profile_dir
        _state["interactive"] = interactive
        _worker = threading.Thread(target=_capture, args=(interactive, profile_dir),
                                   daemon=True)
        _worker.start()
        return True


def _requested_profile():
    body = request.get_json(silent=True)
    return str((body or {}).get("profile") or "")


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/status")
def status():
    return jsonify(_public_state())


def _resize_signin_browser():
    # A second controller may open desktop sign-in while the mobile browser is
    # still alive. Resize the same browser without discarding its login state.
    for _ in range(40):
        if not _public_state()['interactive']:
            return
        try:
            from touch_browser import geometry,run
            window,current=geometry()
            with _lock:
                width,height=max(508,_viewport['width']),_viewport['height']
            if current['WIDTH']==width and current['HEIGHT']==height:
                return
            run('windowsize','--sync',window,width,height)
            return
        except Exception:
            time.sleep(.25)


@app.post("/interactive/start")
def interactive_start():
    data = request.get_json(silent=True) or {}
    width, height = data.get('width', 480), data.get('height', 900)
    if type(width) is not int or type(height) is not int or not 320 <= width <= 1920 or not 480 <= height <= 1080:
        return jsonify({'error': 'invalid_viewport'}), 400
    profile = _requested_profile()
    with _lock:
        previous = _worker if _worker and _worker.is_alive() and not _state['interactive'] else None
    if previous:
        previous.join(timeout=10)
        if previous.is_alive():
            return jsonify({'error': 'previous_signin_closing'}), 503
    with _lock:
        _viewport.update(width=width, height=height)
        started = _start(True, profile)
    threading.Thread(target=_resize_signin_browser, daemon=True).start()
    return jsonify(_public_state()), (202 if started else 409)


@app.post("/refresh")
def refresh():
    started = _start(False, _requested_profile())
    return jsonify(_public_state()), (202 if started else 409)


@app.post("/interactive/close")
def close():
    with _lock:
        _state["interactive"] = False
        _state["updated_at"] = time.time()
    return jsonify(_public_state())


@app.post("/profile/delete")
def profile_delete():
    """Remove one account's Chromium profile directory for good.

    The path checks are the whole endpoint: an empty key is the *owner's* base
    profile, a symlink or a directory that is not a direct child of ``PROFILE_DIR``
    is not a profile this service is allowed to remove, and a running Chromium
    would simply re-create the tree behind the deletion.
    """
    global _profile_key
    key = _requested_profile()
    if not key:
        return jsonify({"error": "profile_key_required"}), 400
    try:
        _profile_dir_for(key)  # Validate the opaque key before looking at disk.
    except ValueError:
        return jsonify({"error": "invalid_profile_key"}), 400
    root = os.path.realpath(PROFILE_DIR)
    try:
        with os.scandir(root) as entries:
            path = next((entry.path for entry in entries if entry.name == key), None)
    except FileNotFoundError:
        path = None
    if path is None:
        return jsonify({"deleted": False})
    with _lock:
        in_flight = bool(_worker and _worker.is_alive()) or bool(_state["interactive"]) \
            or _manual_browser is not None
    if in_flight:
        return jsonify({"error": "browser_in_use"}), 409
    if os.path.islink(path):
        return jsonify({"error": "profile_not_deletable"}), 409
    real = os.path.realpath(path)
    root = os.path.realpath(PROFILE_DIR)
    if real == root or os.path.dirname(real) != root:
        return jsonify({"error": "profile_not_deletable"}), 409
    if not os.path.isdir(real):
        return jsonify({"deleted": False})
    shutil.rmtree(real)
    with _lock:
        if _profile_key == real:
            _profile_key = ""
    logger.info("Removed browser profile directory for a deleted account")
    return jsonify({"deleted": True})


@app.post("/interactive/open-youtube")
def open_youtube():
    if not _public_state()["interactive"]:
        return jsonify({"error": "interactive_browser_not_running"}), 409
    try:
        _open_youtube_music()
    except Exception:
        return jsonify({"error": "browser_window_unavailable"}), 503
    return jsonify(_public_state())


@app.post("/interactive/capture")
def capture_interactive():
    if not _public_state()["interactive"]:
        return jsonify({"error": "interactive_browser_not_running"}), 409
    _capture_requested.set()
    _set_state("capture_requested", interactive=True,
               message="Preparing signed-in session")
    return jsonify(_public_state()), 202


@app.post("/interactive/complete")
def complete():
    global _complete_requested
    with _lock:
        _complete_requested = True
        _state["state"] = "complete"
        _state["updated_at"] = time.time()
        _state["message"] = "Session saved"
    return jsonify(_public_state())


@app.post("/candidate/take")
def candidate_take():
    global _candidate
    with _lock:
        candidate, _candidate = _candidate, None
    if candidate is None:
        return jsonify({"error": "candidate_not_ready"}), 404
    _set_state("candidate_taken", message=None)
    return jsonify(candidate)


@app.get("/interactive/frame")
def interactive_frame():
    if not _public_state()["interactive"]:
        return jsonify({"error":"session_expired"}), 409
    try:
        from touch_browser import frame
        response=jsonify(frame());response.headers['Cache-Control']='no-store';return response
    except Exception:
        return jsonify({"error":"browser_starting"}), 503


@app.post("/interactive/input")
def interactive_input():
    if not _public_state()["interactive"]:
        return jsonify({"error":"session_expired"}), 409
    try:
        from touch_browser import input_event
        input_event(request.get_json(silent=True) or {})
        return jsonify({"ok":True})
    except ValueError:
        return jsonify({"error":"invalid_input"}), 400
    except Exception:
        return jsonify({"error":"browser_unavailable"}), 503


@app.post("/interactive/choose-account")
def choose_account():
    if not _public_state()["interactive"]:
        return jsonify({"error":"session_expired"}),409
    try:
        _open_youtube_music("https://accounts.google.com/AccountChooser?continue=https%3A%2F%2Fmusic.youtube.com%2F&service=youtube")
        return jsonify({"ok":True})
    except Exception:
        return jsonify({"error":"browser_unavailable"}),503
