"""Bounded wait: cold /audio/ misses on the wait/Range/HEAD path answer within
AUDIO_WAIT_SECONDS (503 pending, download continues) instead of holding a
worker indefinitely. Plain misses still live-stream; /proxy/ is untouched.

Run with ``pytest flask-server/tests/test_bounded_wait.py``.
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
    os.environ.setdefault("SECRET_KEY", "test-secret-for-bounded-wait")
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
FAST = "fastwait0001"
SLOW = "slowwait0001"
STUCK = "stuckwait001"
DYING = "dyingwait001"
RANGED = "rangedwait01"


class BoundedWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("AUDIO_CACHE_DIR", self.tmp.name),
                            ("AUDIO_CACHE_TTL", 7200.0),
                            ("WARMUP_TTL", 7200.0),
                            ("AUDIO_CACHE_MAX_BYTES", 0),
                            ("WARMUP_MAX_BYTES", 0),
                            ("AUDIO_CACHE_MIN_FREE_BYTES", 0),
                            ("AUDIO_WAIT_SECONDS", 25.0)):
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

    def land(self, video_id, size=MB):
        path = os.path.join(self.tmp.name, video_id + ".m4a")
        with open(path, "wb") as fp:
            fp.write(b"\0" * size)
        return path

    def test_hit_unaffected(self):
        self.land(FAST, size=4096)
        resp = self.get(f"video_id={FAST}&wait=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 4096)

    def test_background_completion_serves_file(self):
        def spawn(video_id, **kw):
            self.land(video_id, size=4096)
            return False
        with mock.patch.object(server, "_ensure_audio_ready_for_play",
                               side_effect=spawn):
            resp = self.get(f"video_id={FAST}&wait=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 4096)
        self.assertEqual(resp.headers["X-Cache"], "MISS")

    def test_never_completing_returns_pending_fast(self):
        server.AUDIO_WAIT_SECONDS = 1.0
        with mock.patch.object(server, "_ensure_audio_ready_for_play",
                               return_value=False):
            started = time.time()
            resp = self.get(f"video_id={SLOW}&wait=1")
            elapsed = time.time() - started
        self.assertEqual(resp.status_code, 503)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "pending")
        self.assertTrue(err["retryable"])
        self.assertEqual(resp.headers["Retry-After"], "5")
        self.assertLess(elapsed, 5)

    def test_stuck_stream_join_is_bounded(self):
        # Previously _await_inflight_stream waited 25 s here; the shared
        # deadline must cut it down to AUDIO_WAIT_SECONDS.
        server.AUDIO_WAIT_SECONDS = 0.5
        with mock.patch.object(server, "_stream_is_inflight",
                               return_value=True), \
             mock.patch.object(server, "_ensure_audio_ready_for_play",
                               return_value=False):
            started = time.time()
            resp = self.get(f"video_id={STUCK}&wait=1")
            elapsed = time.time() - started
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()["error"]["code"], "pending")
        self.assertLess(elapsed, 5)

    def test_freshly_dead_during_wait_is_404(self):
        server.AUDIO_WAIT_SECONDS = 1.0

        def spawn_and_die(video_id, **kw):
            server._mark_video_dead(video_id)
            return False

        try:
            with mock.patch.object(server, "_ensure_audio_ready_for_play",
                                   side_effect=spawn_and_die):
                resp = self.get(f"video_id={DYING}&wait=1")
            self.assertEqual(resp.status_code, 404)
        finally:
            with server._dead_video_ids_lock:
                server._dead_video_ids.clear()

    def test_range_miss_completing_returns_206(self):
        def spawn(video_id, **kw):
            self.land(video_id, size=4096)
            return False
        with mock.patch.object(server, "_ensure_audio_ready_for_play",
                               side_effect=spawn):
            resp = self.get(f"video_id={RANGED}",
                            headers={"Range": "bytes=0-99"})
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(len(resp.data), 100)

    def test_proxy_join_defaults_unchanged(self):
        self.assertEqual(server._await_inflight_stream.__defaults__,
                         (server._STREAM_DUPLICATE_WAIT,))
        self.assertEqual(server._await_prewarm_cache.__defaults__,
                         (server._PREWARM_WAIT_SECONDS, 0.0))


if __name__ == "__main__":
    unittest.main()
