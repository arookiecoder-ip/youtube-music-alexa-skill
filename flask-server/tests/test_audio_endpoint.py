"""/audio/ (side-effect-free app audio) and the LRU audio cache.

Run with ``pytest flask-server/tests/test_audio_endpoint.py``.
"""
import collections
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    # Same convention as test_download_latency.py: stub the heavy deps so
    # server.py imports in a slim test environment.
    os.environ.setdefault("SECRET_KEY", "test-secret-for-audio-endpoint")
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

VID = "abcdefghijk"
MB = 1024 * 1024


class _CacheDir(unittest.TestCase):
    """Point the audio cache at a fresh temp dir with known limits."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        from current_audio import CurrentAudio
        current = CurrentAudio()
        patcher = mock.patch.object(server._youtube_stream_for(), 'current', current)
        patcher.start(); self.addCleanup(patcher.stop); self.addCleanup(current.close)
        from audio_test_support import fake_relay
        patcher = mock.patch.object(server._youtube_stream_for(), 'open', side_effect=fake_relay)
        patcher.start(); self.addCleanup(patcher.stop)

    def make(self, video_id, size=MB, written_ago=600.0, used_ago=None, suffix=".m4a"):
        path = os.path.join(self.dir, video_id + suffix)
        with open(path, "wb") as fp:
            fp.write(b"\0" * size)
        now = time.time()
        mtime = now - written_ago
        atime = now - (used_ago if used_ago is not None else written_ago)
        os.utime(path, (atime, mtime))
        return path






class AudioSearch(_CacheDir):

    RESULTS = [
        {"videoId": "liveversion", "title": "Song (Live)", "artists": [{"name": "A"}],
         "duration_seconds": 300},
        {"videoId": "studiotrack", "title": "Song", "artists": [{"name": "A"}],
         "duration_seconds": 211},
        {"title": "no id"},
    ]

    def setUp(self):
        super().setUp()
        self.app = server.app.test_client()
        self.headers = {"X-Api-Key": os.environ["API_KEY"]}
        server._audio_search_cache._data.clear()
        self.ytm = mock.Mock()
        self.ytm.search.return_value = self.RESULTS
        patcher = mock.patch.object(server, "_get_ytmusic", return_value=self.ytm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_duration_hint_picks_matching_version(self):
        self.assertEqual(server._audio_search_song("song a")["video_id"], "liveversion")
        self.assertEqual(server._audio_search_song("song a", 213)["video_id"], "studiotrack")

    def test_far_off_duration_falls_back_to_closest(self):
        self.assertEqual(server._audio_search_song("song a", 270)["video_id"], "liveversion")

    def test_search_results_are_cached(self):
        server._audio_search_song("Song A", 213)
        server._audio_search_song("song a", 213)
        self.assertEqual(self.ytm.search.call_count, 1)

    def test_info_mode_returns_json_without_downloading(self):
        with mock.patch.object(server._youtube_stream_for(), "open") as dl, \
             mock.patch.object(server, "_serve_youtube_stream") as stream:
            resp = self.app.get("/audio/?q=song%20a&duration=213&info=1", headers=self.headers)
        body = resp.get_json()
        self.assertEqual(body["video_id"], "studiotrack")
        self.assertEqual(body["duration_ms"], 211000)
        self.assertFalse(body["cached"])
        self.assertTrue(body["audio_url"].endswith("/audio/?video_id=studiotrack"))
        self.assertNotIn("key=", body["audio_url"])
        dl.assert_not_called()
        stream.assert_not_called()

    def test_search_hit_sets_encoded_metadata_headers(self):
        self.ytm.search.return_value = [{"videoId": VID, "title": "Tum Hi Ho é",
                                         "artists": [{"name": "Arijit"}],
                                         "duration_seconds": 262}]
        self.make(VID, size=10)
        resp = self.app.get("/audio/?q=tum%20hi%20ho", headers=self.headers)
        self.assertEqual(resp.headers["X-Title"], "Tum%20Hi%20Ho%20%C3%A9")
        self.assertEqual(resp.headers["X-Duration-Ms"], "262000")

    def test_no_results_is_404(self):
        self.ytm.search.return_value = []
        resp = self.app.get("/audio/?q=nothing", headers=self.headers)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
