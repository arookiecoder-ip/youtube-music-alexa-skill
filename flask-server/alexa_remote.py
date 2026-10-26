"""Bridge to Amazon's (unofficial) Alexa app API via AlexaPy.

Lets the web remote (/remote/) start music through the skill and control
playback on Echo devices, the same way the Alexa phone app does.

Login is interactive and browser-driven: the user enters their Amazon
credentials in the /remote/ login form, the server drives one AlexaProxy
session with them (so captcha / 2-step happen in the user's own browser),
and only the resulting session cookie is stored. No Amazon credentials are
kept on the server.

Configuration (environment variables):
  AMAZON_DOMAIN      Amazon site the account lives on, e.g. "amazon.in"
                     or "amazon.com" (default: amazon.in)
  ALEXA_PROXY_BASE_URL  Public https origin the browser reaches this server
                     at (falls back to PUBLIC_BASE_URL). Required for login.
  ALEXA_COOKIE_DIR   Where to persist the session cookie (default:
                     ./alexa_cookies next to this file). Every signed-in
                     account gets its own subdirectory of it, so one account's
                     Amazon session can never be read or deleted by another.
  SKILL_INVOCATION_NAME  Invocation name of the music skill
                     (default: "music box")

AlexaPy's aiohttp session is bound to the event loop it was created in,
but Flask async views each run in a throwaway loop — so all AlexaPy work
runs on one dedicated background loop owned by this module.
"""
import asyncio, logging, os, shutil, secrets, tempfile, threading, time
from concurrent.futures import TimeoutError as FuturesTimeoutError

import httpx
from aiohttp import web
from alexapy import AlexaAPI, AlexaProxy
from network_sessions import ProxyAwareAlexaLogin as AlexaLogin
from amazon_login_transport import AmazonLoginTransport
from alexapy.helpers import get_json_value
from player_state import OWNER, PlayerStateProxy, PlayerStateRegistry

logger = logging.getLogger(__name__)


def _load_local_env():
    """Load a sibling .env file if present.

    The app is often started directly with `python server.py`, so relying on
    the shell to export every variable is brittle. We keep this tiny loader
    dependency-free so it works the same in Docker and local runs.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('\'"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        logger.exception("failed to load local .env file")


_load_local_env()
logger.info(
    "Alexa env: AMAZON_DOMAIN=%s ALEXA_PROXY_BASE_URL=%s PUBLIC_BASE_URL=%s PROXY_PORT=%s",
    os.environ.get("AMAZON_DOMAIN"),
    os.environ.get("ALEXA_PROXY_BASE_URL"),
    os.environ.get("PUBLIC_BASE_URL"),
    os.environ.get("ALEXA_PROXY_PORT", "5001"),
)


class AlexaUnreachable(Exception):
    """Raised when an Alexa call can't complete because the device is offline
    or Amazon didn't respond in time. Carries a user-facing message."""

# How long a validated Amazon session is trusted before we re-check it over the
# network. test_loggedin() is a round-trip to Amazon; running it on every button
# press added 1-2s of lag, so we cache the "logged in" result for this long and
# only re-validate past it (or immediately after a command is rejected).
_LOGIN_RECHECK_TTL = 300  # seconds

# How long the fetched device list (and its online/offline flags) is trusted
# before a command that needs a live device forces a re-fetch. Keeps the
# offline guard honest without adding an Amazon round-trip to every button
# press (volume drags fire many commands in a burst).
_DEVICE_STATE_TTL = 30  # seconds

# Website controls should route through the custom skill instead of Alexa's
# native provider controls. This keeps a custom AudioPlayer skill from being
# mistaken for a first-party music provider.
_TRANSPORT_TEXT = {
    "play": "resume",
    "pause": "pause",
    "stop": "pause",
}

# Credentials are NOT stored on the server. The user types them into the
# /remote/ login form; they are used only to drive one proxy-login session and
# are never persisted. Only the resulting session cookie is kept on disk.
AMAZON_DOMAIN = os.environ.get("AMAZON_DOMAIN", "amazon.in")
ALEXA_COOKIE_DIR = os.environ.get(
    "ALEXA_COOKIE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "alexa_cookies"))
SKILL_INVOCATION_NAME = os.environ.get("SKILL_INVOCATION_NAME", "music box")

# Public HTTPS origin the browser reaches this server at (e.g.
# https://alexa.example.com). Used to build the proxy login URL so Amazon
# redirects land back on us. Falls back to PUBLIC_BASE_URL (already set for the
# audio proxy) so a single env var usually covers both.
PROXY_PUBLIC_BASE_URL = (os.environ.get("ALEXA_PROXY_BASE_URL")
                         or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/")
# Local-only port the proxy's aiohttp app listens on. Caddy reverse-proxies
# the public /alexa/proxy/ path prefix here; it is never exposed directly.
PROXY_PORT = int(os.environ.get("ALEXA_PROXY_PORT", "5001"))
# Public path prefix the proxy is mounted under (must match the Caddy route).
PROXY_PATH = "/alexa/proxy/"

# Subdirectory of ALEXA_COOKIE_DIR holding the other accounts' sessions. The
# owner keeps the bare directory so sessions persisted before multi-user stay
# valid; an account's own cookie files can then never collide with, or be
# purged alongside, anyone else's.
ACCOUNT_COOKIE_SUBDIR = "accounts"


def cookie_dir_for(identity: str) -> str:
    """Where one identity's Amazon session is persisted."""
    if not identity or identity == OWNER:
        return ALEXA_COOKIE_DIR
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in identity)
    return os.path.join(ALEXA_COOKIE_DIR, ACCOUNT_COOKIE_SUBDIR, safe)


def purge_cookie_dir(identity: str) -> bool:
    """Delete one account's persisted Amazon session; refuse anything broader.

    This is the only directory this module removes, so it is paranoid by design:
    an empty identity means *the owner's* directory, and `ALEXA_COOKIE_DIR` itself
    holds every account's session. A symlink is treated as an attack on the
    layout, not as a directory with an unusual name.

    Returns False when there was nothing to remove, and raises for a refusal --
    a caller retrying a refusal would retry it forever.
    """
    if not identity or identity == OWNER:
        raise ValueError("The owner's Alexa cookie directory is not purgeable")
    path = cookie_dir_for(identity)
    if not path.startswith(ALEXA_COOKIE_DIR + os.sep):
        raise ValueError(f"Refusing to remove {path}: not an account cookie directory")
    if os.path.islink(path):
        raise ValueError(f"Refusing to remove {path}: it is a symlink")
    root = os.path.realpath(ALEXA_COOKIE_DIR)
    parent = os.path.realpath(os.path.join(root, ACCOUNT_COOKIE_SUBDIR))
    real = os.path.realpath(path)
    if os.path.dirname(real) != parent:
        raise ValueError(f"Refusing to remove {path}: it escapes {parent}")
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path)
    return True


# Only one interactive Amazon login can be in flight on the whole server: every
# AlexaRemote binds the same PROXY_PORT, so a second proxy would either fail to
# bind or serve the first account's credentials. The holder renews its claim
# while the signing-in browser polls, which is what makes an abandoned login
# expire instead of blocking everyone else forever.
PROXY_PORT_BUSY = ("Another account is connecting to Amazon right now. "
                   "Try again in a minute.")
PROXY_CLAIM_TTL = 300  # seconds without a poll before a login counts as abandoned
_proxy_guard = threading.Lock()
_proxy_holder = [None]
_proxy_claim_expires = [0.0]


def _proxy_claim(remote) -> bool:
    """Claim the login proxy for `remote`, or report it busy. Never blocks: an
    abandoned login is reported busy until _reclaim_proxy can stop it."""
    with _proxy_guard:
        holder = _proxy_holder[0]
        if holder is None or holder is remote:
            _proxy_holder[0] = remote
            _proxy_claim_expires[0] = time.monotonic() + PROXY_CLAIM_TTL
            return True
        return False


def _renew_proxy_claim(remote):
    with _proxy_guard:
        if _proxy_holder[0] is remote:
            _proxy_claim_expires[0] = time.monotonic() + PROXY_CLAIM_TTL


def _release_proxy_claim(remote):
    with _proxy_guard:
        if _proxy_holder[0] is remote:
            _proxy_holder[0] = None
            _proxy_claim_expires[0] = 0.0


def _abandoned_proxy():
    """The holder of a claim that stopped being renewed, if any."""
    with _proxy_guard:
        if (_proxy_holder[0] is not None
                and time.monotonic() >= _proxy_claim_expires[0]):
            return _proxy_holder[0]
    return None


CALL_TIMEOUT = 60  # seconds; Amazon calls can be slow but must not hang a worker


class _Device:
    """Minimal shape AlexaAPI expects of a device."""
    def __init__(self, raw: dict):
        self.device_serial_number = raw["serialNumber"]
        self._device_type = raw["deviceType"]
        self._locale = None  # AlexaAPI falls back to its default locale


class AlexaRemote:
    def __init__(self, cookie_dir: str = None):
        # One instance per identity (see `remote` at the bottom of this module);
        # the directory is the only thing that makes two of them different
        # sessions on disk.
        self.cookie_dir = cookie_dir or ALEXA_COOKIE_DIR
        # alexapy keys its cookie file by email, so the email is stored next to
        # it to find the right file again after a restart.
        self._storage_dir = os.path.join(self.cookie_dir, ".storage")
        self._email_file = os.path.join(self._storage_dir, "last_email.txt")
        self._loop = None
        self._loop_lock = threading.Lock()
        self._login = None
        self._login_checked_at = 0.0  # monotonic time of last successful test_loggedin
        self._devices_raw = []
        self._devices_fetched_at = 0.0  # monotonic time of last device-list fetch
        # None until this identity's session has actually been checked; the web
        # remote reads it to tell "signed out" from "not checked yet".
        self.connection_state = None
        # proxy-login state (all touched only on the background loop)
        self._proxy = None
        self._proxy_login = None
        self._proxy_runner = None
        self._proxy_started = False
        self._proxy_base_url = None
        self._proxy_finalize_task = None
        self._proxy_login_error = None
        self._proxy_login_complete = False
        self._proxy_attempt_id = None
        self._proxy_cookie_stage = None

    # ---------- background loop plumbing ----------

    def _ensure_loop(self):
        with self._loop_lock:
            if self._loop and self._loop.is_running():
                return self._loop
            self._loop = asyncio.new_event_loop()
            thread = threading.Thread(target=self._loop.run_forever, name="alexa-remote", daemon=True)
            thread.start()
            return self._loop

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        try:
            return future.result(timeout=CALL_TIMEOUT)
        except FuturesTimeoutError:
            # The whole Amazon round trip blew past CALL_TIMEOUT — usually an
            # offline/unreachable device or Amazon stalling. Stop the runaway
            # coroutine and force a login re-check on the next call.
            future.cancel()
            self._login_checked_at = 0.0
            logger.warning("Alexa call exceeded %ss and was abandoned", CALL_TIMEOUT)
            raise AlexaUnreachable(
                "Alexa isn't responding. The device may be offline or Amazon is "
                "unreachable — try again in a moment.")

    # ---------- login ----------

    LOGIN_REQUIRED = "Not signed in. Use the login button to sign in to Amazon."

    async def _ensure_login(self, force_check: bool = False):
        """Ensure a usable session, reusing the stored cookie. No credentials
        are held server-side, so this can only validate/restore an existing
        session -- it never does a fresh credential login. Returns an error
        string (prompting a browser login), or None when logged in.

        A recently-validated session is trusted without another network round
        trip (see _LOGIN_RECHECK_TTL) so transport/volume commands stay snappy;
        pass force_check=True to re-validate immediately (e.g. after a command
        was rejected)."""
        if not hasattr(self, '_login_task_lock'):
            self._login_task_lock = asyncio.Lock()
            
        async with self._login_task_lock:
            # already have a live session in memory?
            if self._login:
                fresh = (time.monotonic() - self._login_checked_at) < _LOGIN_RECHECK_TTL
                if fresh and not force_check:
                    self.connection_state = True
                    return None
                try:
                    if await self._login.test_loggedin():
                        self._login_checked_at = time.monotonic()
                        self.connection_state = True
                        return None
                except Exception:
                    logger.exception("test_loggedin failed; will try the stored cookie")
                try:
                    await self._login.close()
                except Exception:
                    pass
                self._login = None
                self._login_checked_at = 0.0

            # try to restore from the persisted cookie (written by the proxy login)
            os.makedirs(self._storage_dir, exist_ok=True)
            # AlexaLogin's cookie file is keyed by email
            # (.storage/alexa_media.<email>.cookies) — read back the email saved
            # after the last successful proxy login (see _proxy_check) so this
            # restore attempt looks for the right file instead of email="".
            email = ""
            try:
                with open(self._email_file, encoding="utf-8") as f:
                    email = f.read().strip()
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception("could not read persisted login email")
            if not email:
                self.connection_state = False
                return AlexaRemote.LOGIN_REQUIRED
            login = AlexaLogin(
                url=AMAZON_DOMAIN,
                email=email,
                password="",
                outputpath=lambda name: os.path.join(self.cookie_dir, name),
            )
            try:
                if await login.restore_cookie_session():
                    self._login = login
                    self._login_checked_at = time.monotonic()
                    self.connection_state = True
                    return None
            except Exception:
                logger.exception("restoring session from cookie failed")
            finally:
                if self._login is not login:
                    try:
                        await login.close()
                    except Exception:
                        pass
            self.connection_state = False
            return AlexaRemote.LOGIN_REQUIRED


    # ---------- proxy login (interactive, browser-driven) ----------
    #
    # AlexaProxy man-in-the-middles Amazon's real login page so the user can
    # complete captcha / 2-step / "approve this device" in their own browser,
    # instead of alexapy trying to script it blind. The proxy's aiohttp app
    # runs on PROXY_PORT on this instance's background loop; Caddy reverse-proxies
    # the public PROXY_PATH to it. On success the captured cookie is persisted
    # to this identity's cookie directory and adopted as the live session.

    @staticmethod
    @web.middleware
    async def _proxy_autoclose_mw(request, handler):
        """Replace AlexaPy's success page with an auto-closing one."""
        resp = await handler(request)
        try:
            if resp.body:
                body_text = resp.body.decode('utf-8', errors='replace') if isinstance(resp.body, bytes) else str(resp.body)
                if 'Successfully' in body_text or 'logged in' in body_text.lower():
                    return web.Response(
                        content_type='text/html',
                        text='''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { background: #0a0a0a; color: #e8e8e8; font-family: -apple-system, system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .box { text-align: center; }
  .check { font-size: 48px; color: #2f9e44; }
  p { margin-top: 12px; font-size: 14px; color: #666; }
</style></head>
<body><div class="box">
  <div class="check">&#10003;</div>
  <h2 style="margin:8px 0;letter-spacing:.05em">CONNECTED</h2>
  <p>Closing this tab&hellip;</p>
</div>
<script>setTimeout(function(){ window.close(); }, 1500);</script>
</body></html>''')
        except Exception:
            pass
        return resp

    async def _claim_proxy(self) -> bool:
        """Take the server's single login-proxy slot.

        A login abandoned mid-way leaves its proxy bound to PROXY_PORT with no
        browser behind it; that claim expires, and the abandoned proxy is shut
        down here so the port is genuinely free before the new login binds it."""
        if _proxy_claim(self):
            return True
        stale = _abandoned_proxy()
        if stale is None:
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                stale._stop_proxy(), stale._ensure_loop()).result(timeout=10)
        except Exception:
            logger.exception("could not stop an abandoned Alexa login proxy")
            return False
        logger.info("reclaimed the Alexa login proxy from an abandoned login")
        return _proxy_claim(self)

    async def _start_proxy(self, email: str, password: str, base_url: str = None):
        """Idempotently bring up the proxy aiohttp app. Credentials come from
        the browser and are used only to seed this login object; they are not
        stored. Returns error or None."""
        if not (email and password):
            return "Enter your Amazon email and password to log in."
        proxy_base_url = base_url or (
            PROXY_PUBLIC_BASE_URL + PROXY_PATH if PROXY_PUBLIC_BASE_URL else ""
        )
        if not proxy_base_url:
            return ("Set ALEXA_PROXY_BASE_URL (or PUBLIC_BASE_URL) to this server's public "
                    "https origin so the proxy login can redirect back correctly.")
        proxy_base_url = proxy_base_url.rstrip("/") + "/"
        if self._proxy_started:
            finalize_failed = (
                self._proxy_finalize_task is not None
                and self._proxy_finalize_task.done()
                and not self._proxy_login_complete
            )
            if (self._proxy_base_url == proxy_base_url and not finalize_failed
                    and self._proxy_login is not None and self._proxy_login.email == email
                    and self._proxy_login.password == password):
                _renew_proxy_claim(self)
                return None
            await self._stop_proxy()

        if not await self._claim_proxy():
            return PROXY_PORT_BUSY

        os.makedirs(self._storage_dir, exist_ok=True)
        # Fresh login object dedicated to the proxy flow; adopted as the live
        # session once the browser completes login. Credentials are held only
        # in this object for the duration of the login, never persisted.
        # Keep the previous account's persisted credentials intact until the
        # replacement verifies, including failed/cancelled same-email attempts.
        self._proxy_attempt_id = secrets.token_urlsafe(24)
        self._proxy_cookie_stage = tempfile.mkdtemp(prefix="pending-", dir=self.cookie_dir)
        os.makedirs(os.path.join(self._proxy_cookie_stage, ".storage"), exist_ok=True)
        stage = self._proxy_cookie_stage
        self._proxy_login = AlexaLogin(
            url=AMAZON_DOMAIN,
            email=email,
            password=password,
            outputpath=lambda name: os.path.join(stage, name),
        )
        self._proxy_login_complete = False
        self._proxy_login_error = None
        self._proxy_finalize_task = None
        self._proxy = AlexaProxy(self._proxy_login, proxy_base_url)
        # Amazon occasionally resets a TLS connection before sending a
        # response. Retry connection establishment inside the proxy so users
        # do not land on alexapy's otherwise unrecoverable error page.
        await self._proxy.session.aclose()
        self._proxy.session = httpx.AsyncClient(
            transport=AmazonLoginTransport(
                proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None)
        )

        app = web.Application(middlewares=[self._proxy_autoclose_mw])
        # AlexaProxy.all_handler serves the whole proxied login tree; mount it
        # under PROXY_PATH for every method and sub-path.
        app.router.add_route("*", PROXY_PATH + "{tail:.*}", self._proxy_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        # Bind on all interfaces so Caddy (running in a separate container) can
        # reach this over the Docker network; Caddy fronts it with TLS publicly.
        site = web.TCPSite(runner, "0.0.0.0", PROXY_PORT)
        await site.start()
        self._proxy_runner = runner
        self._proxy_started = True
        self._proxy_base_url = proxy_base_url
        logger.info("Alexa login proxy listening on 0.0.0.0:%s under %s", PROXY_PORT, PROXY_PATH)
        return None

    async def _proxy_start_url(self, email: str, password: str, base_url: str = None):
        """Bring up the proxy and return (url, error). url is where the user logs in."""
        error = await self._start_proxy(email, password, base_url)
        if error:
            return None, error
        proxy = self._proxy
        if proxy is None:
            return None, "Proxy failed to initialize."
        try:
            # reset() clears any half-finished prior attempt so re-login is clean
            proxy.reset()
        except Exception:
            logger.debug("proxy.reset() unavailable or failed; continuing")
        return str(proxy.access_url()), None

    async def _proxy_handler(self, request):
        """Serve alexapy and start finalization as soon as its callback lands."""
        proxy = self._proxy
        if proxy is None:
            raise web.HTTPServiceUnavailable(text="Alexa login proxy is not running.")
        try:
            response = await proxy.all_handler(request)
            if request.method == 'GET' and response.status >= 500:
                import html
                retry = html.escape(str(request.rel_url), quote=True)
                return web.Response(status=503, content_type='text/html', headers={'Cache-Control':'no-store'}, text=(
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<style>body{margin:0;padding:32px 24px;background:#141414;color:#e8e8e8;font:14px system-ui;line-height:1.6}a{display:inline-block;padding:10px 16px;background:#e8590c;color:white;text-decoration:none}</style>'
                    '<h2>Amazon is temporarily unavailable</h2><p>Your account has not been changed. Retry this sign-in page.</p>'
                    f'<a href="{retry}">Try again</a>'))
            return response
        finally:
            login = self._proxy_login
            if login and (login.authorization_code or login.access_token):
                self._ensure_proxy_finalize_task()

    def _ensure_proxy_finalize_task(self):
        if self._proxy_finalize_task is None:
            self._proxy_finalize_task = asyncio.create_task(
                self._finalize_proxy_login(), name="alexa-login-finalize"
            )

    async def _finalize_proxy_login(self):
        """Exchange Amazon's one-time code once and persist the Alexa session."""
        login = self._proxy_login
        if login is None:
            self._proxy_login_error = "Amazon login session disappeared before finalization."
            return False

        logger.info("Amazon authorization received; finalizing Alexa login")
        done = False
        for attempt in range(1, 4):
            try:
                done = await login.test_loggedin()
            except Exception:
                logger.exception("Alexa login finalization attempt %s failed", attempt)
            if done:
                break
            if attempt < 3:
                logger.warning("Alexa login verification attempt %s failed; retrying", attempt)
                await asyncio.sleep(attempt)

        if login is not self._proxy_login:
            return False
        if not done:
            self._proxy_login_error = (
                "Amazon accepted the login, but Alexa session verification failed. "
                "Click Connect to start a fresh login."
            )
            logger.error(self._proxy_login_error)
            return False

        # alexapy normally saves from test_loggedin(), but its writer handles
        # filesystem errors internally. Save once more and verify the file so
        # the UI cannot report success for a session that will vanish on restart.
        try:
            await login.save_cookiefile()
            cookie_files = getattr(login, "_cookiefile", [])
            cookie_file = cookie_files[0] if cookie_files else None
        except Exception:
            logger.exception("could not save Alexa login cookie")
            cookie_file = None
        if not cookie_file or not os.path.isfile(cookie_file):
            self._proxy_login_error = "Alexa login succeeded, but its cookie file could not be saved."
            logger.error(self._proxy_login_error)
            return False

        if login is not self._proxy_login:
            return False  # A newer attempt superseded this callback.
        stage = getattr(self, '_proxy_cookie_stage', None)
        if stage:
            try:
                relative = os.path.relpath(cookie_file, stage)
                destination = os.path.join(self.cookie_dir, relative)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                os.replace(cookie_file, destination)
                login._cookiefile = [os.path.join(self.cookie_dir, os.path.relpath(path, stage)) for path in login._cookiefile]
                login._outputpath = lambda name: os.path.join(self.cookie_dir, name)
            except OSError:
                self._proxy_login_error = "Could not save the new account. Try again."
                logger.exception("could not promote verified Amazon cookie")
                return False

        # Persist the email separately so _ensure_login can locate alexapy's
        # email-keyed cookie file after a process restart.
        try:
            os.makedirs(self._storage_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self._storage_dir, delete=False) as f:
                pointer = f.name
                f.write(login.email)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.replace(pointer, self._email_file)
            finally:
                if os.path.exists(pointer):
                    os.unlink(pointer)
        except Exception:
            self._proxy_login_error = "The account could not be saved. Your previous account is unchanged. Try again."
            logger.exception("could not persist login email for restore")
            return False

        if self._login and self._login is not login:
            try:
                await self._login.close()
            except Exception:
                pass
        self._login = login
        self.connection_state = True
        self._login_checked_at = time.monotonic()
        self._devices_raw = []
        self._proxy_login_complete = True
        await self._stop_proxy()
        logger.info("Alexa login finalized and cookie persisted")
        return True

    async def _proxy_check(self):
        """Report callback-driven finalization without performing it in a poll."""
        if self._proxy_login_complete:
            return {"running": False, "logged_in": True}
        if self._proxy_login_error:
            return {
                "running": False,
                "logged_in": False,
                "error": self._proxy_login_error,
            }
        if not self._proxy_started or not self._proxy_login:
            return {"running": False, "logged_in": False}

        login = self._proxy_login
        if login.authorization_code or login.access_token:
            self._ensure_proxy_finalize_task()
        _renew_proxy_claim(self)
        return {"running": True, "logged_in": False}

    async def _stop_proxy(self):
        # Capture and detach this attempt before cleanup yields. An old callback
        # must never clear a new attempt's runner or temporary credentials.
        runner = self._proxy_runner
        stage = getattr(self, '_proxy_cookie_stage', None)
        task = self._proxy_finalize_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._proxy_runner = None
        self._proxy = None
        self._proxy_started = False
        self._proxy_base_url = None
        self._proxy_cookie_stage = None
        if runner:
            try:
                await runner.cleanup()
            except Exception:
                logger.debug("proxy runner cleanup failed", exc_info=True)
        if stage:
            shutil.rmtree(stage, ignore_errors=True)
        # Release only once the port is actually free, so the next account that
        # claims it cannot race this runner's socket while it still closes.
        _release_proxy_claim(self)

    # public (thread-safe) wrappers
    def proxy_start_url(self, email: str, password: str, base_url: str = None):
        return self._run(self._proxy_start_url(email, password, base_url))

    def proxy_cancel(self, attempt_id):
        return self._run(self._cancel_proxy_attempt(attempt_id))

    async def _cancel_proxy_attempt(self, attempt_id):
        if not attempt_id or attempt_id != getattr(self, '_proxy_attempt_id', None):
            return False
        pending = self._proxy_login
        if pending is None or pending is self._login:
            return False
        self._proxy_login = None  # Fence callbacks before cleanup yields.
        self._proxy_attempt_id = None
        self._proxy_login_complete = False
        self._proxy_login_error = None
        await self._stop_proxy()
        await pending.close()
        return True

    def proxy_check(self):
        return self._run(self._proxy_check())

    # ---------- public API (called from Flask views, thread-safe) ----------

    def status(self) -> dict:
        # An identity that never connected has no cookie to validate, so say so
        # from disk instead of starting its background loop (and a Amazon
        # round trip) just to report "not signed in".
        if self._login is None and not os.path.isfile(self._email_file):
            return self._status_result(AlexaRemote.LOGIN_REQUIRED)
        return self._run(self._status())

    def is_logged_in(self) -> bool:
        """Cheap, non-network check: is there a live in-memory session for this
        identity right now? The web remote uses it to warn before a second
        submit replaces the session this account is already driving."""
        return self._login is not None

    def logout(self):
        """Sign out of Amazon and clear saved session cookies."""
        self._run(self._logout())

    async def _logout(self):
        # A sign-in still finalizing in the background would promote its cookie
        # right after we deleted this identity's session, so fence it first.
        pending = self._proxy_login
        self._proxy_login = None
        task = self._proxy_finalize_task
        if task is not None and not task.done():
            task.cancel()
        self._proxy_finalize_task = None
        self._proxy_attempt_id = None
        sessions = [s for s in (self._login, pending) if s is not None]
        for session in {id(s): s for s in sessions}.values():
            try:
                await session.close()
            except Exception:
                logger.debug("Alexa login close failed", exc_info=True)
        self._clear_persisted_session()
        self._login = None
        self.connection_state = False
        self._login_checked_at = 0.0
        self._devices_raw = []
        self._proxy_login_complete = False
        self._proxy_login_error = None
        if self._proxy_runner:
            await self._stop_proxy()
        else:
            _release_proxy_claim(self)

    def _clear_persisted_session(self):
        """Delete this identity's saved Amazon session and nothing else.

        Accounts live in subdirectories of the owner's directory, so the files
        at this level (plus alexapy's .storage tree and any half-finished
        sign-in stage) are the only ones this identity owns; removing the
        directory itself would sign out everyone."""
        try:
            entries = os.listdir(self.cookie_dir)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.error("Failed to read Amazon cookie dir: %s", exc)
            return
        for name in entries:
            path = os.path.join(self.cookie_dir, name)
            try:
                if os.path.isdir(path):
                    if name != ".storage" and not name.startswith("pending-"):
                        continue
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except OSError as exc:
                logger.error("Failed to delete Amazon session file %s: %s", path, exc)

    def _status_result(self, error=None) -> dict:
        return {"configured": bool(PROXY_PUBLIC_BASE_URL),
                "logged_in": error is None,
                "error": error,
                "invocation_name": SKILL_INVOCATION_NAME}

    async def _status(self):
        error = await self._ensure_login()
        self.connection_state = error is None
        return self._status_result(error)

    def devices(self, refresh: bool = False):
        """Returns (device list, error string or None)."""
        return self._run(self._devices(refresh))

    async def _fetch_devices(self):
        """Fetch the device list with live connectivity state.

        AlexaAPI.get_devices() hits /api/devices-v2/device without
        cached=false, and Amazon then serves a cached snapshot that keeps
        reporting a powered-off Echo as online long after it dropped off.
        Asking for cached=false returns the real state, so the UI dot and the
        offline guard reflect reality. Falls back to the library call if the
        fresh request fails."""
        try:
            response = await AlexaAPI._static_request(
                "get", self._login, "/api/devices-v2/device",
                query={"cached": "false"})
            try:
                devices, _ = await get_json_value(response, "devices", list)
            finally:
                if hasattr(response, 'close'):
                    response.close()
            if devices is not None:
                return devices
        except Exception:
            logger.exception("fresh device fetch failed; falling back to cached list")
        return await AlexaAPI.get_devices(self._login)

    async def _devices(self, refresh: bool):
        error = await self._ensure_login()
        self.connection_state = error is None
        if error:
            return None, error
        status_ttl = 5 if any(not d.get("online") for d in self._devices_raw) else _DEVICE_STATE_TTL
        if refresh or not self._devices_raw or time.monotonic() - self._devices_fetched_at >= status_ttl:
            raw = await self._fetch_devices()
            if raw is None:
                return None, "Could not fetch the device list from Amazon."
            # only devices that can actually play skill audio
            self._devices_raw = [d for d in raw if "MUSIC_SKILL" in (d.get("capabilities") or [])]
            self._devices_fetched_at = time.monotonic()
        devices = [
            {
                "serial": d["serialNumber"],
                "name": d.get("accountName") or d["serialNumber"],
                "family": d.get("deviceFamily"),
                "online": bool(d.get("online")),
            }
            for d in self._devices_raw
        ]
        devices.sort(key=lambda d: (not d["online"], d["name"].lower()))
        return devices, None

    def _find_device(self, serial: str):
        for d in self._devices_raw:
            if d["serialNumber"] == serial:
                return _Device(d)
        return None

    def _find_device_raw(self, serial: str):
        for d in self._devices_raw:
            if d["serialNumber"] == serial:
                return d
        return None

    async def _api_for(self, serial: str, require_online: bool = False):
        """Returns (AlexaAPI, error string or None).

        When require_online is set, the target Echo must report as online before
        a command is sent. An offline device can't be reached, and Amazon's API
        either hangs or fails opaquely for one, so we reject early with a clear
        message. The cached online flag can be stale, so we refresh the device
        list once before trusting an "offline" reading."""
        error = await self._ensure_login()
        if error:
            return None, error
        if not self._devices_raw:
            _, error = await self._devices(refresh=True)
            if error:
                return None, error
        device = self._find_device(serial)
        if not device:
            return None, "Unknown device. Refresh the device list and pick again."
        if require_online:
            raw = self._find_device_raw(serial)
            # The cached flag goes stale in both directions: it can claim a
            # dead device is online (command would hang for ~30s) or a revived
            # one is offline. Re-fetch before trusting either reading, unless
            # the list is fresh enough.
            stale = time.monotonic() - self._devices_fetched_at > _DEVICE_STATE_TTL
            if stale or (raw is not None and not raw.get("online")):
                _, err = await self._devices(refresh=True)
                if err:
                    return None, err
                raw = self._find_device_raw(serial)
            if raw is None:
                return None, "Unknown device. Refresh the device list and pick again."
            if not raw.get("online"):
                name = raw.get("accountName") or "That device"
                return None, f"{name} is offline. Turn it on (or pick another device) and try again."
        return AlexaAPI(device, self._login), None

    def command(self, serial: str, action: str, value=None):
        """Transport control. Returns an error string, or None on success."""
        return self._run(self._command(serial, action, value))

    async def _command(self, serial, action, value):
        # Reject early if the target Echo is offline: it can't be reached, and a
        # transport/volume command would otherwise fail opaquely or hang.
        api, error = await self._api_for(serial, require_online=True)
        if error:
            return error
        try:
            if action == "volume":
                try:
                    volume = min(100, max(0, int(value)))
                except (TypeError, ValueError):
                    return "Volume must be a number between 0 and 100."
                await api.set_volume(volume / 100, queue_delay=0)
            elif action in _TRANSPORT_TEXT:
                await api.run_custom(f"ask {SKILL_INVOCATION_NAME} to {_TRANSPORT_TEXT[action]}", queue_delay=0)
            else:
                return f'Unknown action "{action}".'
        except asyncio.TimeoutError:
            self._login_checked_at = 0.0
            logger.exception("command %s timed out", action)
            return "Alexa didn't respond in time. Check the device is online and try again."
        except Exception:
            # A rejected command often means the session went stale; force a
            # re-validation on the next call instead of trusting the cache.
            self._login_checked_at = 0.0
            logger.exception("command %s failed", action)
            return "Amazon couldn't run that command. The device may be offline — try again."
        return None

    def volume(self, serial: str):
        """Return the current volume percent for a device, or (None, error)."""
        return self._run(self._volume(serial))

    async def _volume(self, serial: str):
        api, error = await self._api_for(serial)
        if error:
            return None, error
        try:
            state = await api.get_state()
        except Exception:
            logger.exception("volume fetch failed")
            state = None
        volume = self._extract_volume_percent(state)
        if volume is None:
            np_state = await self._now_playing(serial)
            volume = self._extract_volume_percent(np_state)
        if volume is None:
            return None, "Amazon did not return a volume for that device."
        return volume, None

    @staticmethod
    def _normalise_volume(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number <= 1:
            number *= 100
        if 0 <= number <= 100:
            return int(round(number))
        return None

    @classmethod
    def _extract_volume_percent(cls, obj):
        """Best-effort parser for Amazon's now-playing volume shapes."""
        if isinstance(obj, dict):
            for key in ("volume", "speakerVolume", "volumeLevel", "volumePercent"):
                if key not in obj:
                    continue
                value = obj[key]
                if isinstance(value, dict):
                    for nested_key in ("value", "level", "percent", "volume"):
                        direct = cls._normalise_volume(value.get(nested_key))
                        if direct is not None:
                            return direct
                    nested = cls._extract_volume_percent(value)
                    if nested is not None:
                        return nested
                direct = cls._normalise_volume(value)
                if direct is not None:
                    return direct
            for key, value in obj.items():
                if "volume" in str(key).lower():
                    if isinstance(value, dict):
                        for nested_key in ("value", "level", "percent", "volume"):
                            direct = cls._normalise_volume(value.get(nested_key))
                            if direct is not None:
                                return direct
                    nested = cls._extract_volume_percent(value)
                    if nested is not None:
                        return nested
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    nested = cls._extract_volume_percent(value)
                    if nested is not None:
                        return nested
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    nested = cls._extract_volume_percent(item)
                    if nested is not None:
                        return nested
        else:
            return cls._normalise_volume(obj)
        return None

    # Prefix marker the web remote prepends to its play queries. The skill
    # detects it, strips it, and plays silently (no "Searching"/"Playing X"
    # speech) so app-initiated playback doesn't make the Echo talk. Spoken
    # voice commands never carry it, so they still announce normally.
    SILENT_PLAY_MARKER = "silentmode"
    DIRECT_PLAY_MARKER = "silentid"
    # Short, NLU-safe phrase the remote sends for direct (armed) plays. Plain
    # English words survive Alexa's speech recognition intact, unlike the
    # hex-encoded video id the old DIRECT_PLAY_MARKER path relied on (which the
    # NLU split/mangled, so those plays silently failed). The video id itself is
    # armed server-side via /armed_play/ and fetched by the skill on this phrase.
    APP_SELECTION_PHRASE = "the app selection"

    def play_query(self, serial: str, query: str):
        """Ask the skill to play something, as if spoken. Returns error or None.

        The remote marks its plays as silent so Alexa doesn't announce the
        track; the marker is a leading token the skill strips off."""
        marked = f"{self.SILENT_PLAY_MARKER} {query}"
        return self._run(self._text_command(
            serial, f"ask {SKILL_INVOCATION_NAME} to play {marked}"))

    def play_video_id(self, serial: str, video_id: str, offset_ms: int = 0):
        """Ask the skill to play an exact known YouTube video id.

        The video id must already be armed server-side (see server._arm_play):
        we send only a short, NLU-safe trigger phrase, because Alexa's speech
        recognition mangles a hex-encoded id. The skill hears the phrase, calls
        /armed_play/ for this device, and plays what was armed. ``offset_ms`` is
        carried by the arm, not this phrase."""
        marked = f"{self.SILENT_PLAY_MARKER} {self.APP_SELECTION_PHRASE}"
        return self._run(self._text_command(
            serial, f"ask {SKILL_INVOCATION_NAME} to play {marked}"))

    def text_command(self, serial: str, text: str):
        """Send arbitrary text to Alexa, as if spoken. Returns error or None."""
        return self._run(self._text_command(serial, text))

    async def _text_command(self, serial, text):
        api, error = await self._api_for(serial, require_online=True)
        if error:
            return error
        try:
            await api.run_custom(text, queue_delay=0)
        except asyncio.TimeoutError:
            self._login_checked_at = 0.0
            logger.exception("text_command %r timed out", text)
            return "Alexa didn't respond in time. Check the device is online and try again."
        except Exception:
            self._login_checked_at = 0.0
            logger.exception("text_command %r failed", text)
            return "Amazon couldn't run that command. The device may be offline — try again."
        return None

    # ---------- now-playing state ----------

    async def _now_playing(self, serial: str):
        """Amazon's /api/np/player state for a device. Only used as a
        fallback volume source (see _volume) -- custom-skill audio doesn't
        populate title/playing state here, which is why server.py tracks
        now-playing itself instead of relying on this."""
        api, error = await self._api_for(serial)
        if error:
            return {'playing': False, 'error': error}

        state = None
        # Strategy 1: direct request to Amazon's /api/np/player
        try:
            device_raw = next((d for d in self._devices_raw if d['serialNumber'] == serial), None)
            device_type = device_raw.get('deviceType', '') if device_raw else ''
            uri = (f"/api/np/player"
                   f"?deviceSerialNumber={serial}"
                   f"&deviceType={device_type}"
                   f"&screenWidth=480")
            raw = await api._get_request(uri)
            # _get_request returns aiohttp ClientResponse — parse the JSON body
            try:
                if isinstance(raw, dict):
                    state = raw
                elif hasattr(raw, 'json'):
                    state = await raw.json(content_type=None)
            finally:
                if hasattr(raw, 'close'):
                    raw.close()
        except Exception:
            # Strategy 2: fallback to get_state()
            try:
                raw2 = await api.get_state()
                if isinstance(raw2, dict):
                    state = raw2
                elif hasattr(raw2, 'json'):
                    state = await raw2.json(content_type=None)
            except Exception:
                logger.exception("now_playing fetch failed")
                return {'playing': False}

        if not isinstance(state, dict):
            return {'playing': False}
        return self._parse_player_state(state, serial)

    @staticmethod
    def _parse_player_state(state, serial):
        """Extract now-playing info from Amazon's /api/np/player response."""
        if not isinstance(state, dict):
            return {'playing': False}

        # The response is typically {playerInfo: {...}} directly
        player_info = state.get('playerInfo') or {}
        if not player_info:
            if serial in state and isinstance(state[serial], dict):
                player_info = state[serial].get('playerInfo') or {}
            if not player_info:
                for val in state.values():
                    if isinstance(val, dict) and 'playerInfo' in val:
                        player_info = val['playerInfo'] or {}
                        break

        if not player_info:
            return {'playing': False}

        # --- play/pause state ---
        raw_state = str(player_info.get('state') or '').upper()
        is_playing = raw_state == 'PLAYING'

        if not is_playing:
            transport = player_info.get('transport') or {}
            if isinstance(transport, dict):
                if str(transport.get('playPause') or '').lower() == 'pause':
                    is_playing = True

        # --- track info ---
        info_text = player_info.get('infoText') or {}
        main_art = player_info.get('mainArt') or {}
        thumb = ''
        if isinstance(main_art, dict):
            thumb = main_art.get('url') or ''
        elif isinstance(main_art, list) and main_art:
            thumb = main_art[0].get('url') or ''

        title = info_text.get('title') or ''
        artist = info_text.get('subText1') or ''
        volume = AlexaRemote._extract_volume_percent(player_info)
        if volume is None:
            volume = AlexaRemote._extract_volume_percent(state)

        return {
            'playing': is_playing,
            'title': title,
            'artist': artist,
            'thumbnail': thumb,
            'volume': volume,
        }


# The owner keeps the one session this module has always held; every other
# signed-in account gets its own AlexaRemote — its own cookies, its own Echo
# device list — created the first time that identity touches an Alexa call.
# `remote` resolves the caller's identity exactly like playback state does, so
# the existing `alexa_remote.remote.<call>()` sites need no changes.
_owner_remote = AlexaRemote()
_remote_registry = PlayerStateRegistry(
    lambda identity: (_owner_remote if identity in (OWNER, '')
                      else AlexaRemote(cookie_dir=cookie_dir_for(identity)))
)

remote = PlayerStateProxy(_remote_registry)
