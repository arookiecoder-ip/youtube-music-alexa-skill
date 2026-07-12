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
                     ./alexa_cookies next to this file)
  SKILL_INVOCATION_NAME  Invocation name of the music skill
                     (default: "music box")

AlexaPy's aiohttp session is bound to the event loop it was created in,
but Flask async views each run in a throwaway loop — so all AlexaPy work
runs on one dedicated background loop owned by this module.
"""
import asyncio, logging, os, threading, time
from concurrent.futures import TimeoutError as FuturesTimeoutError

from aiohttp import web
from alexapy import AlexaAPI, AlexaLogin, AlexaProxy
from alexapy.helpers import get_json_value

logger = logging.getLogger(__name__)


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

CALL_TIMEOUT = 60  # seconds; Amazon calls can be slow but must not hang a worker


class _Device:
    """Minimal shape AlexaAPI expects of a device."""
    def __init__(self, raw: dict):
        self.device_serial_number = raw["serialNumber"]
        self._device_type = raw["deviceType"]
        self._locale = None  # AlexaAPI falls back to its default locale


class AlexaRemote:
    def __init__(self):
        self._loop = None
        self._loop_lock = threading.Lock()
        self._login = None
        self._login_checked_at = 0.0  # monotonic time of last successful test_loggedin
        self._devices_raw = []
        self._devices_fetched_at = 0.0  # monotonic time of last device-list fetch
        # proxy-login state (all touched only on the background loop)
        self._proxy = None
        self._proxy_login = None
        self._proxy_runner = None
        self._proxy_started = False

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
                    return None
                try:
                    if await self._login.test_loggedin():
                        self._login_checked_at = time.monotonic()
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
            os.makedirs(os.path.join(ALEXA_COOKIE_DIR, ".storage"), exist_ok=True)
            # AlexaLogin's cookie file is keyed by email
            # (.storage/alexa_media.<email>.cookies) — read back the email saved
            # after the last successful proxy login (see _proxy_check) so this
            # restore attempt looks for the right file instead of email="".
            email = ""
            try:
                email_file = os.path.join(ALEXA_COOKIE_DIR, ".storage", "last_email.txt")
                with open(email_file, encoding="utf-8") as f:
                    email = f.read().strip()
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception("could not read persisted login email")
            if not email:
                return AlexaRemote.LOGIN_REQUIRED
            login = AlexaLogin(
                url=AMAZON_DOMAIN,
                email=email,
                password="",
                outputpath=lambda name: os.path.join(ALEXA_COOKIE_DIR, name),
            )
            try:
                cookies = await login.load_cookie()
                if cookies:
                    await login.login(cookies=cookies)
                if (login.status or {}).get("login_successful"):
                    self._login = login
                    self._login_checked_at = time.monotonic()
                    return None
            except Exception:
                logger.exception("restoring session from cookie failed")
            finally:
                if self._login is not login:
                    try:
                        await login.close()
                    except Exception:
                        pass
            return AlexaRemote.LOGIN_REQUIRED

    # ---------- proxy login (interactive, browser-driven) ----------
    #
    # AlexaProxy man-in-the-middles Amazon's real login page so the user can
    # complete captcha / 2-step / "approve this device" in their own browser,
    # instead of alexapy trying to script it blind. The proxy's aiohttp app
    # runs on PROXY_PORT on this module's background loop; Caddy reverse-proxies
    # the public PROXY_PATH to it. On success the captured cookie is persisted
    # to ALEXA_COOKIE_DIR and adopted as the live session.

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

    async def _start_proxy(self, email: str, password: str):
        """Idempotently bring up the proxy aiohttp app. Credentials come from
        the browser and are used only to seed this login object; they are not
        stored. Returns error or None."""
        if not (email and password):
            return "Enter your Amazon email and password to log in."
        if not PROXY_PUBLIC_BASE_URL:
            return ("Set ALEXA_PROXY_BASE_URL (or PUBLIC_BASE_URL) to this server's public "
                    "https origin so the proxy login can redirect back correctly.")
        if self._proxy_started:
            return None

        os.makedirs(os.path.join(ALEXA_COOKIE_DIR, ".storage"), exist_ok=True)
        # Fresh login object dedicated to the proxy flow; adopted as the live
        # session once the browser completes login. Credentials are held only
        # in this object for the duration of the login, never persisted.
        self._proxy_login = AlexaLogin(
            url=AMAZON_DOMAIN,
            email=email,
            password=password,
            outputpath=lambda name: os.path.join(ALEXA_COOKIE_DIR, name),
        )
        base = PROXY_PUBLIC_BASE_URL + PROXY_PATH
        self._proxy = AlexaProxy(self._proxy_login, base)

        app = web.Application(middlewares=[self._proxy_autoclose_mw])
        # AlexaProxy.all_handler serves the whole proxied login tree; mount it
        # under PROXY_PATH for every method and sub-path.
        app.router.add_route("*", PROXY_PATH + "{tail:.*}", self._proxy.all_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        # Bind on all interfaces so Caddy (running in a separate container) can
        # reach this over the Docker network; Caddy fronts it with TLS publicly.
        site = web.TCPSite(runner, "0.0.0.0", PROXY_PORT)
        await site.start()
        self._proxy_runner = runner
        self._proxy_started = True
        logger.info("Alexa login proxy listening on 0.0.0.0:%s under %s", PROXY_PORT, PROXY_PATH)
        return None

    async def _proxy_start_url(self, email: str, password: str):
        """Bring up the proxy and return (url, error). url is where the user logs in."""
        error = await self._start_proxy(email, password)
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

    async def _proxy_check(self):
        """Poll for completion. Returns dict describing proxy-login state."""
        if not self._proxy_started or not self._proxy_login:
            return {"running": False, "logged_in": False}
        try:
            done = await self._proxy_login.test_loggedin()
        except Exception:
            logger.exception("proxy test_loggedin failed")
            done = False
        if done:
            try:
                await self._proxy_login.finalize_login()
            except Exception:
                logger.debug("finalize_login unavailable; cookie may still be valid")
            try:
                await self._proxy_login.save_cookiefile()
            except Exception:
                logger.exception("could not persist proxy cookie (session still usable)")
            else:
                # AlexaLogin's cookie filename is keyed by email
                # (.storage/alexa_media.<email>.cookies) — a restart has no
                # way to know that email up front, so a restore attempt with
                # email="" always looks for the wrong file and silently fails
                # (see _ensure_login). Persist it here so restores can find
                # the real cookie file.
                try:
                    email_file = os.path.join(ALEXA_COOKIE_DIR, ".storage", "last_email.txt")
                    with open(email_file, "w", encoding="utf-8") as f:
                        f.write(self._proxy_login.email)
                except Exception:
                    logger.exception("could not persist login email for restore")
            # adopt the proxy session as the live one and drop stale device cache
            if self._login and self._login is not self._proxy_login:
                try:
                    await self._login.close()
                except Exception:
                    pass
            self._login = self._proxy_login
            self._login_checked_at = time.monotonic()
            self._devices_raw = []
            await self._stop_proxy()
        return {"running": self._proxy_started, "logged_in": bool(done)}

    async def _stop_proxy(self):
        if self._proxy_runner:
            try:
                await self._proxy_runner.cleanup()
            except Exception:
                logger.debug("proxy runner cleanup failed", exc_info=True)
        self._proxy_runner = None
        self._proxy = None
        self._proxy_started = False

    # public (thread-safe) wrappers
    def proxy_start_url(self, email: str, password: str):
        return self._run(self._proxy_start_url(email, password))

    def proxy_check(self):
        return self._run(self._proxy_check())

    # ---------- public API (called from Flask views, thread-safe) ----------

    def status(self) -> dict:
        return self._run(self._status())

    def is_logged_in(self) -> bool:
        """Cheap, non-network check: is there a live in-memory session right
        now? Used to guard against silently swapping the one shared Amazon
        session out from under an already-connected server (see
        alexa_proxy_login's 'force' check)."""
        return self._login is not None

    def logout(self):
        """Sign out of Amazon and clear saved session cookies."""
        self._run(self._logout())

    async def _logout(self):
        import shutil
        if os.path.exists(ALEXA_COOKIE_DIR):
            try:
                shutil.rmtree(ALEXA_COOKIE_DIR)
            except Exception as e:
                logger.error("Failed to delete Amazon cookie dir: %s", e)
        self._login = None
        self._login_checked_at = 0.0
        if self._proxy_runner:
            await self._stop_proxy()

    async def _status(self):
        error = await self._ensure_login()
        return {"configured": bool(PROXY_PUBLIC_BASE_URL),
                "logged_in": error is None,
                "error": error,
                "invocation_name": SKILL_INVOCATION_NAME}

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
        if error:
            return None, error
        if refresh or not self._devices_raw:
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
                await api.set_volume(volume / 100)
            elif action in _TRANSPORT_TEXT:
                await api.run_custom(f"ask {SKILL_INVOCATION_NAME} to {_TRANSPORT_TEXT[action]}")
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
            await api.run_custom(text)
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


remote = AlexaRemote()
