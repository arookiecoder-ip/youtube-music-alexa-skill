"""Explicit speculative mode: GET /audio/?prefetch=1 (query param only).

Run with ``pytest flask-server/tests/test_prefetch_mode.py``.
"""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    os.environ.setdefault("SECRET_KEY", "test-secret-for-prefetch-mode")
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
            sys.modules["youtube_browser_session"] = ybs


_install_stubs()
import server  # noqa: E402

MB = 1024 * 1024
MISS = "missprf0001"
HIT = "hitprf00001"
WAIT = "waitprf0001"


class PrefetchMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("AUDIO_CACHE_DIR", self.tmp.name),
                            ("AUDIO_CACHE_TTL", 7200.0),
                            ("WARMUP_TTL", 7200.0),
                            ("AUDIO_CACHE_MAX_BYTES", 0),
                            ("WARMUP_MAX_BYTES", 0),
                            ("AUDIO_CACHE_MIN_FREE_BYTES", 0)):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        pin = mock.patch.object(server, "_pinned_cache_video_ids", return_value=set())
        pin.start()
        self.addCleanup(pin.stop)
        self.app = server.app.test_client()
        self.headers = {"X-Api-Key": os.environ["API_KEY"]}

    def get(self, query, **kw):
        headers = dict(self.headers)
        headers.update(kw.pop("headers", {}))
        return self.app.get(f"/audio/?{query}", headers=headers, **kw)

    def make(self, video_id, size=MB, used_ago=600.0):
        path = os.path.join(self.tmp.name, video_id + ".m4a")
        with open(path, "wb") as fp:
            fp.write(b"\0" * size)
        now = time.time()
        os.utime(path, (now - used_ago, now - used_ago))
        return path

    def test_miss_returns_202_and_spawns_background_prefetch(self):
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ready, \
             mock.patch.object(server.Supporting, "ensure_downloaded") as dl:
            started = time.time()
            resp = self.get(f"video_id={MISS}&prefetch=1")
            elapsed = time.time() - started
        self.assertEqual(resp.status_code, 202)
        body = resp.get_json()
        self.assertEqual(body["error"]["code"], "prefetch_deferred")
        self.assertEqual(resp.headers["Retry-After"], "5")
        ready.assert_called_once_with(MISS, wait=False, generation=None, prefetch=True)
        dl.assert_not_called()  # never blocks on a synchronous download
        self.assertLess(elapsed, 5)

    def test_hit_returns_200_cached_without_lru_touch_or_spawn(self):
        path = self.make(HIT, used_ago=1000)
        before = os.stat(path).st_atime
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ready, \
             mock.patch.object(server.Supporting, "ensure_downloaded") as dl:
            resp = self.get(f"video_id={HIT}&prefetch=1")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["cached"])
        self.assertEqual(body["video_id"], HIT)
        self.assertIn("audio_url", body)
        ready.assert_not_called()
        dl.assert_not_called()
        self.assertEqual(os.stat(path).st_atime, before)

    def test_info_wins_over_prefetch(self):
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ready:
            resp = self.get(f"video_id={MISS}&info=1&prefetch=1")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("audio_url", body)
        self.assertNotIn("error", body)
        ready.assert_not_called()

    def test_prefetch_wins_over_wait(self):
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ready, \
             mock.patch.object(server.Supporting, "ensure_downloaded") as dl:
            resp = self.get(f"video_id={WAIT}&wait=1&prefetch=1")
        self.assertEqual(resp.status_code, 202)
        ready.assert_called_once_with(WAIT, wait=False, generation=None, prefetch=True)
        dl.assert_not_called()

    def test_header_alone_keeps_legacy_blocking_behavior(self):
        # No query param: the old header path still does a synchronous
        # (prefetch-flagged) download and answers 503 when it yields nothing.
        # wait=1 skips the live-stream branch so the test never needs yt-dlp.
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ready, \
             mock.patch.object(server.Supporting, "ensure_downloaded",
                               return_value=None) as dl:
            resp = self.get(f"video_id={MISS}&wait=1",
                            headers={"X-MusicBox-Prefetch": "1"})
        self.assertEqual(resp.status_code, 503)
        dl.assert_called_once_with(MISS, prefetch=True)
        ready.assert_not_called()

    def test_dead_video_prefetch_is_404_without_spawn(self):
        server._mark_video_dead(MISS)
        try:
            with mock.patch.object(server, "_ensure_audio_ready_for_play") as ready:
                resp = self.get(f"video_id={MISS}&prefetch=1")
            self.assertEqual(resp.status_code, 404)
            ready.assert_not_called()
        finally:
            with server._dead_video_ids_lock:
                server._dead_video_ids.clear()

    def test_requires_api_key(self):
        resp = self.app.get(f"/audio/?video_id={MISS}&prefetch=1")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
