"""Two-pool audio cache: requested (AUDIO_CACHE_TTL) + warmup (WARMUP_TTL, capped).

Run with ``pytest flask-server/tests/test_warmup_pool.py``.
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
    os.environ.setdefault("SECRET_KEY", "test-secret-for-warmup-pool")
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


class _WarmupDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        for name, value in (("AUDIO_CACHE_DIR", self.dir),
                            ("AUDIO_CACHE_TTL", 7200.0),
                            ("WARMUP_TTL", 7200.0),
                            ("AUDIO_CACHE_MAX_BYTES", 0),
                            ("WARMUP_MAX_BYTES", 0),
                            ("AUDIO_CACHE_MIN_FREE_BYTES", 0)):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        pin = mock.patch.object(server, "_pinned_cache_video_ids", return_value=set())
        self.pinned = pin.start()
        self.addCleanup(pin.stop)

    def make(self, video_id, size=MB, used_ago=600.0, suffix=".m4a"):
        path = os.path.join(self.dir, video_id + suffix)
        with open(path, "wb") as fp:
            fp.write(b"\0" * size)
        now = time.time()
        os.utime(path, (now - used_ago, now - used_ago))
        return path


class WarmupPool(_WarmupDir):
    def test_warmup_expires_while_requested_survives(self):
        server.WARMUP_TTL = 3600.0
        server.AUDIO_CACHE_TTL = 7200.0
        warm = self.make("warmup00001", used_ago=4000)
        req = self.make("request0001", used_ago=4000)
        server._mark_warmup("warmup00001")
        self.assertTrue(server._is_warmup("warmup00001"))
        self.assertFalse(server._is_warmup("request0001"))
        server.Supporting.prune_audio_cache()
        self.assertFalse(os.path.exists(warm))
        self.assertTrue(os.path.exists(req))

    def test_promotion_reuses_file_and_removes_expiry(self):
        server.WARMUP_TTL = 3600.0
        server.AUDIO_CACHE_TTL = 7200.0
        path = self.make("promote00001", used_ago=4000)
        server._mark_warmup("promote00001")
        server._promote_warmup("promote00001")
        self.assertFalse(server._is_warmup("promote00001"))
        server.Supporting.prune_audio_cache()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(server.Supporting.cached_audio_path("promote00001"), path)

    def test_mark_audio_used_promotes(self):
        path = self.make("playpromo001")
        server._mark_warmup("playpromo001")
        self.assertTrue(server._is_warmup("playpromo001"))
        server._mark_audio_used(path)
        self.assertFalse(server._is_warmup("playpromo001"))
        self.assertTrue(os.path.exists(path))

    def test_duplicate_warmup_preserves_expiry(self):
        self.make("dupwarmup001")
        server._mark_warmup("dupwarmup001")
        marker = os.path.join(self.dir, "dupwarmup001.warmup")
        first = os.stat(marker).st_mtime
        time.sleep(0.02)
        server._mark_warmup("dupwarmup001")
        self.assertEqual(os.stat(marker).st_mtime, first)

    def test_sidecar_excluded_from_audio_path_and_collected(self):
        # Marker alone is not audio.
        with open(os.path.join(self.dir, "orphan000001.warmup"), "w"):
            pass
        self.assertIsNone(server.Supporting.cached_audio_path("orphan000001"))
        # Audio + marker resolves to the audio file, never the marker.
        audio = self.make("real0000001")
        server._mark_warmup("real0000001")
        self.assertEqual(server.Supporting.cached_audio_path("real0000001"), audio)
        # Orphan marker collected by prune.
        server.Supporting.prune_audio_cache()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "orphan000001.warmup")))
        self.assertTrue(os.path.exists(audio))

    def test_evict_removes_marker_with_audio(self):
        self.make("evict0000001")
        server._mark_warmup("evict0000001")
        server.Supporting.evict_cached_audio("evict0000001")
        self.assertIsNone(server.Supporting.cached_audio_path("evict0000001"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "evict0000001.warmup")))

    def test_warmup_cap_enforced_inside_total(self):
        server.AUDIO_CACHE_MAX_BYTES = 10 * MB
        server.WARMUP_MAX_BYTES = 1 * MB
        server._AUDIO_CACHE_EVICT_MIN_AGE = 0
        try:
            self.make("capwarmup0001", used_ago=3000)
            self.make("capwarmup0002", used_ago=2000)
            req = self.make("caprequest001", used_ago=1000)
            server._mark_warmup("capwarmup0001")
            server._mark_warmup("capwarmup0002")
            server.Supporting.prune_audio_cache()
            # 2 MB warmup over the 1 MB cap: oldest warmup goes, requested stays.
            self.assertFalse(os.path.exists(os.path.join(self.dir, "capwarmup0001.m4a")))
            self.assertTrue(os.path.exists(req))
        finally:
            server._AUDIO_CACHE_EVICT_MIN_AGE = 60.0

    def test_warmup_evicted_first_when_over_total(self):
        server.AUDIO_CACHE_MAX_BYTES = 1 * MB
        server._AUDIO_CACHE_EVICT_MIN_AGE = 0
        try:
            warm = self.make("firstwarm0001", used_ago=1000)
            req = self.make("firstreq00001", used_ago=2000)
            server._mark_warmup("firstwarm0001")
            server.Supporting.prune_audio_cache()
            # 2 MB total over the 1 MB budget: warmup goes first even
            # though the requested file is older.
            self.assertFalse(os.path.exists(warm))
            self.assertTrue(os.path.exists(req))
        finally:
            server._AUDIO_CACHE_EVICT_MIN_AGE = 60.0

    def test_pinned_warmup_survives_sweep(self):
        server.WARMUP_TTL = 60.0
        self.pinned.return_value = {"pinnedwarm001"}
        path = self.make("pinnedwarm001", used_ago=5000)
        server._mark_warmup("pinnedwarm001")
        server.Supporting.prune_audio_cache()
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()

class RecentWarmupEvictionTests(_WarmupDir):
    def test_recent_warmup_does_not_block_older_requested_eviction(self):
        old = self.make('olderplay01', used_ago=600)
        recent = self.make('warmup00001', used_ago=10)
        server._mark_warmup('warmup00001')
        with mock.patch.object(server, 'AUDIO_CACHE_MAX_BYTES', MB):
            server.Supporting.prune_audio_cache()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(recent))
