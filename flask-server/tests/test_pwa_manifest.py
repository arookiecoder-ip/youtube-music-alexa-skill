"""Regression tests for the installed Music Box PWA manifest."""
import json
import os
import sys
import types
import unittest

# Tests live in flask-server/tests/, while application modules remain in the
# flask-server parent directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault("SECRET_KEY", "test-secret-for-pwa-manifest")
os.environ.setdefault("REMOTE_USER", "test-owner")
os.environ.setdefault("REMOTE_PASSWORD", "test-pass")
os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")

# Keep this test runnable in the lightweight host environment by stubbing the
# external integrations before importing server.py.
if "ytmusicapi" not in sys.modules:
    ytmusicapi = types.ModuleType("ytmusicapi")
    ytmusicapi.YTMusic = type("YTMusic", (), {"__init__": lambda self, **kw: None})
    sys.modules["ytmusicapi"] = ytmusicapi
    auth = types.ModuleType("ytmusicapi.auth")
    auth_types = types.ModuleType("ytmusicapi.auth.types")
    auth_types.AuthType = type("AuthType", (), {"UNAUTHORIZED": "UNAUTHORIZED"})
    browser = types.ModuleType("ytmusicapi.auth.browser")
    browser.setup_browser = lambda *args, **kwargs: None
    sys.modules["ytmusicapi.auth"] = auth
    sys.modules["ytmusicapi.auth.types"] = auth_types
    sys.modules["ytmusicapi.auth.browser"] = browser
sys.modules.setdefault("home_feed", types.ModuleType("home_feed"))

if "alexa_remote" not in sys.modules:
    alexa_remote = types.ModuleType("alexa_remote")
    remote = types.ModuleType("alexa_remote.remote")
    remote.devices = lambda refresh=False: (None, "stubbed")
    remote.volume = lambda serial: (None, "stubbed")
    remote.is_logged_in = lambda: (True, None)
    remote.proxy_start_url = lambda *args, **kwargs: (None, "stubbed")
    alexa_remote.remote = remote
    alexa_remote.AlexaUnreachable = type("AlexaUnreachable", (Exception,), {})
    sys.modules["alexa_remote"] = alexa_remote
    sys.modules["alexa_remote.remote"] = remote

if "youtube_browser_session" not in sys.modules:
    ybs = types.ModuleType("youtube_browser_session")
    ybs.BrowserController = type("BrowserController", (), {"__init__": lambda self, *a, **kw: None})
    ybs.YouTubeBrowserSessionManager = type("YouTubeBrowserSessionManager", (), {"__init__": lambda self, *a, **kw: None})
    ybs.browser_client_is_signed_in = lambda *args, **kwargs: False
    ybs.promote_browser_headers = lambda *args, **kwargs: None
    sys.modules["youtube_browser_session"] = ybs

import server  # noqa: E402


class PwaManifest(unittest.TestCase):
    def test_manifest_allows_physical_tablet_orientation(self):
        self.assertEqual(server._MANIFEST["orientation"], "any")

    def test_manifest_route_returns_the_same_orientation_and_is_not_cached(self):
        response = server.app.test_client().get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_data(as_text=True))["orientation"], "any")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store, max-age=0")


if __name__ == "__main__":
    unittest.main()
import os
import sys

# Tests live in flask-server/tests/, while application modules remain in the flask-server parent directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
