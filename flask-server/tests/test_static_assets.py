"""Regression tests: the web-remote shell loads its CSS/JS as external,
versioned static files (not inlined into the HTML), and the service worker
precaches those files for installed PWAs."""
import json
import os
import sys
import types
import unittest

# Tests live in flask-server/tests/, while application modules remain in the
# flask-server parent directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault("SECRET_KEY", "test-secret-for-static-assets")
os.environ.setdefault("REMOTE_USER", "test-owner")
os.environ.setdefault("REMOTE_PASSWORD", "test-pass")
os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")
# A configured public URL puts the static route in its production long-cache
# mode, making the Cache-Control assertion deterministic.
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.example")

# Keep this test runnable in the lightweight host environment by stubbing the
# external integrations before importing server.py (mirrors test_pwa_manifest).
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
    ybs.is_authentication_error = lambda *args, **kwargs: False
    sys.modules["youtube_browser_session"] = ybs

import server  # noqa: E402


class StaticAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = server.app.test_client()

    def test_shell_references_versioned_static_files(self):
        response = self.client.get("/?key=" + os.environ["API_KEY"])
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # CSS and JS load as external, versioned files in dependency order.
        self.assertIn('<link rel="stylesheet" href="/static/css/base.css?v=', html)
        self.assertIn('<link rel="stylesheet" href="/static/css/mobile.css?v=', html)
        self.assertIn('<script src="/static/js/api.js?v=', html)
        self.assertIn('<script src="/static/playlists.js?v=', html)
        self.assertIn('<script src="/static/js/click-effects.js?v=', html)
        # The shell no longer inlines the assets.
        self.assertNotIn("{% include \"static/", html)
        self.assertNotIn("<style>{% include", html)

    def test_static_assets_are_served_with_long_cache(self):
        for url in ("/static/css/base.css", "/static/js/api.js", "/static/playlists.js"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            expected_type = "text/css" if url.endswith(".css") else "javascript"
            self.assertIn(expected_type, response.content_type)
            self.assertTrue(response.data)
            self.assertEqual(response.headers.get("Cache-Control"),
                             "public, max-age=604800", url)

    def test_versioned_static_urls_serve(self):
        response = self.client.get("/static/js/api.js?v=" + server._STATIC_VERSION)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/javascript", response.content_type)
        self.assertTrue(response.data)

    def test_missing_static_file_is_404(self):
        self.assertEqual(self.client.get("/static/definitely-missing.css").status_code, 404)

    def test_service_worker_precaches_shell_assets(self):
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for asset in ("/static/js/api.js", "/static/css/base.css", "/static/playlists.js"):
            self.assertIn(json.dumps(asset), body)
        self.assertNotIn("__PRECACHE_JSON__", body)


if __name__ == "__main__":
    unittest.main()