"""Extraction order: YTDLP_CLIENT_ORDER is validated (unknown names dropped,
fully-invalid falls back to default), and fallback wins are logged with
latency for order decisions.

Run with ``pytest flask-server/tests/test_extraction_clients.py``.
"""
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    os.environ.setdefault("SECRET_KEY", "test-secret-for-extraction-clients")
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

MB = 1024 * 1024
DEFAULT = ["default", "android_vr", "web", "tv"]


class ClientOrder(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("YTDLP_CLIENT_ORDER", None)

    def test_default_order_unchanged(self):
        self.assertEqual(server.Supporting.get_ytdlp_clients(), DEFAULT)

    def test_custom_valid_order(self):
        os.environ["YTDLP_CLIENT_ORDER"] = "tv,web"
        self.assertEqual(server.Supporting.get_ytdlp_clients(), ["tv", "web"])

    def test_unknown_names_dropped_dupes_collapsed(self):
        os.environ["YTDLP_CLIENT_ORDER"] = " web, bogus , web ,default "
        self.assertEqual(server.Supporting.get_ytdlp_clients(),
                         ["web", "default"])

    def test_fully_invalid_falls_back_to_default(self):
        for bad in ("bogus", "", " , "):
            os.environ["YTDLP_CLIENT_ORDER"] = bad
            with self.assertLogs(server.logger, level="WARNING"):
                self.assertEqual(server.Supporting.get_ytdlp_clients(), DEFAULT)

    def test_bgutil_first_allowed_when_explicit(self):
        os.environ["YTDLP_CLIENT_ORDER"] = "tv_simply,web_embedded,default"
        self.assertEqual(server.Supporting.get_ytdlp_clients(),
                         ["tv_simply", "web_embedded", "default"])




if __name__ == "__main__":
    unittest.main()
