"""Provider command selection used by cookie validation and metadata probes."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    # Mirror the convention in test_download_backpressure.py: stub the heavy
    # native deps before importing server.py so this file runs in the same
    # slim test environment (no ytmusicapi/alexapy/yt-dlp installed).
    os.environ.setdefault("SECRET_KEY", "test-secret-for-download-latency")
    os.environ.setdefault("REMOTE_USER", "test-owner")
    os.environ.setdefault("REMOTE_PASSWORD", "test-pass")
    os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")

    if "ytmusicapi" not in sys.modules:
        ytmusicapi = types.ModuleType("ytmusicapi")
        ytmusicapi.YTMusic = type("YTMusic", (), {"__init__": lambda self, **kw: None})
        sys.modules["ytmusicapi"] = ytmusicapi

        ytmusicapi_auth = types.ModuleType("ytmusicapi.auth")
        ytmusicapi_auth_types = types.ModuleType("ytmusicapi.auth.types")
        ytmusicapi_auth_types.AuthType = type(
            "AuthType", (), {"UNAUTHORIZED": "UNAUTHORIZED"})
        ytmusicapi_auth_browser = types.ModuleType("ytmusicapi.auth.browser")
        ytmusicapi_auth_browser.setup_browser = lambda *a, **kw: None
        sys.modules["ytmusicapi.auth"] = ytmusicapi_auth
        sys.modules["ytmusicapi.auth.types"] = ytmusicapi_auth_types
        sys.modules["ytmusicapi.auth.browser"] = ytmusicapi_auth_browser

    sys.modules.setdefault("home_feed", types.ModuleType("home_feed"))

    if "alexa_remote" not in sys.modules:
        alexa_remote = types.ModuleType("alexa_remote")
        alexa_remote.AlexaUnreachable = type("AlexaUnreachable", (Exception,), {})
        alexa_remote_remote = types.ModuleType("alexa_remote.remote")
        alexa_remote_remote.devices = lambda refresh=False: (None, "stubbed-no-devices")
        alexa_remote_remote.volume = lambda serial: (None, "stubbed-no-devices")
        alexa_remote_remote.is_logged_in = lambda: (True, None)
        alexa_remote_remote.proxy_start_url = lambda *a, **kw: (None, "stubbed")
        alexa_remote.remote = alexa_remote_remote
        sys.modules["alexa_remote"] = alexa_remote
        sys.modules["alexa_remote.remote"] = alexa_remote_remote

    if "youtube_browser_session" not in sys.modules:
        try:
            import youtube_browser_session  # noqa: F401
        except Exception:
            ybs = types.ModuleType("youtube_browser_session")
            for attr in ("BrowserController", "YouTubeBrowserSessionManager"):
                setattr(ybs, attr,
                        type(attr, (), {"__init__": lambda self, *a, **kw: None}))
            ybs.browser_client_is_signed_in = lambda *a, **kw: False
            ybs.promote_browser_headers = lambda *a, **kw: None
            ybs.normalize_browser_headers = lambda value: value
            sys.modules["youtube_browser_session"] = ybs


_install_stubs()
import server  # noqa: E402


# Documented, non-code timing figures. These are estimates taken verbatim from
# README.md / server.py comments, kept as named constants so the feasibility
# assertions below read as "is 8 s above the floor and below the deadline"
# rather than as magic numbers.
_ECHO_PROXY_DEADLINE_SECONDS = 11.0   # "the Echo gives up on /proxy/ after ~11s"
_AD_SKIP_GATE_FLOOR_SECONDS = 4.5     # "ad-skip gate of ~4-5 s ... isn't bypassable"
_CURRENT_FIRST_BYTE_SECONDS = 7.5     # "the first song of a session takes ~7-8 s to start"
_TARGET_SECONDS = 8.0                 # the 8-second goal under test


class _CleanServerState(unittest.TestCase):
    def setUp(self):
        server._reset_rate_limit_cooldown()
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()




class FormatSelection(_CleanServerState):
    """The chosen format order minimises the bytes transferred per track."""

    def test_format_140_m4a_is_first_choice(self):
        cmd = server.Supporting.ytdlp_download_command("vid", "-")
        fmt = cmd[cmd.index("-f") + 1]
        # 140 is the ~128 kbps m4a AAC audio-only stream: the smallest usable
        # format, so it downloads fastest. It must be tried before any larger
        # fallback.
        self.assertTrue(fmt.startswith("140/"), msg=f"format spec {fmt!r} does not prefer 140")
        self.assertIn("bestaudio[ext=m4a]", fmt)




class ClientOrder(_CleanServerState):
    """The first client tried should be the fastest / authenticated one."""

    def test_authenticated_default_then_android_vr(self):
        clients = server.Supporting.get_ytdlp_clients()
        self.assertEqual(clients[0], "default",
                         msg="the cookie-authenticated default must be tried first "
                             "to avoid a guaranteed-failed cookie-free probe")
        self.assertEqual(clients[1], "android_vr",
                         msg="android_vr exposes AAC/M4A formats and must be the "
                             "first fallback")










if __name__ == "__main__":
    unittest.main()
