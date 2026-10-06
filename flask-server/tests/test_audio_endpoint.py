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
        for name, value in (("AUDIO_CACHE_DIR", self.dir),
                            ("AUDIO_CACHE_TTL", 7200.0),
                            ("AUDIO_CACHE_MAX_BYTES", 0),
                            ("AUDIO_CACHE_MIN_FREE_BYTES", 0)):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        pin = mock.patch.object(server, "_pinned_cache_video_ids", return_value=set())
        self.pinned = pin.start()
        self.addCleanup(pin.stop)
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._stream_inflight_lock:
            server._stream_inflight.clear()

    def make(self, video_id, size=MB, written_ago=600.0, used_ago=None, suffix=".m4a"):
        path = os.path.join(self.dir, video_id + suffix)
        with open(path, "wb") as fp:
            fp.write(b"\0" * size)
        now = time.time()
        mtime = now - written_ago
        atime = now - (used_ago if used_ago is not None else written_ago)
        os.utime(path, (atime, mtime))
        return path


class LruPrune(_CacheDir):

    def test_idle_ttl_counts_from_last_use_not_download(self):
        # Downloaded 3 h ago but played 5 min ago: stays. Untouched for 3 h: goes.
        played = self.make("played00000", written_ago=3 * 3600, used_ago=300)
        stale = self.make("stale000000", written_ago=3 * 3600)
        server.Supporting.prune_audio_cache()
        self.assertTrue(os.path.exists(played))
        self.assertFalse(os.path.exists(stale))

    def test_over_budget_evicts_least_recently_used_first(self):
        server.AUDIO_CACHE_MAX_BYTES = 2 * MB
        oldest = self.make("oldest00000", used_ago=3000)
        middle = self.make("middle00000", used_ago=2000)
        newest = self.make("newest00000", used_ago=1000)
        files, size = server.Supporting.prune_audio_cache()
        self.assertEqual((files, size), (1, MB))
        self.assertFalse(os.path.exists(oldest))
        self.assertTrue(os.path.exists(middle))
        self.assertTrue(os.path.exists(newest))

    def test_pinned_and_just_written_files_survive_eviction(self):
        server.AUDIO_CACHE_MAX_BYTES = 1  # everything is over budget
        self.pinned.return_value = {"current0000"}
        current = self.make("current0000", used_ago=5000)
        fresh = self.make("fresh000000", written_ago=5, used_ago=5)
        old = self.make("old00000000", used_ago=4000)
        server.Supporting.prune_audio_cache()
        self.assertTrue(os.path.exists(current))
        self.assertTrue(os.path.exists(fresh))
        self.assertFalse(os.path.exists(old))

    def test_low_disk_space_evicts_until_free_target(self):
        server.AUDIO_CACHE_MIN_FREE_BYTES = 10 * MB
        a = self.make("aaaaaaaaaaa", used_ago=3000)
        b = self.make("bbbbbbbbbbb", used_ago=2000)
        usage = collections.namedtuple("usage", "total used free")(100 * MB, 91 * MB, 9 * MB)
        with mock.patch.object(server.shutil, "disk_usage", return_value=usage):
            server.Supporting.prune_audio_cache()
        self.assertFalse(os.path.exists(a))  # 1 MB short -> one LRU file
        self.assertTrue(os.path.exists(b))

    def test_partial_downloads_are_never_evicted_for_space(self):
        server.AUDIO_CACHE_MAX_BYTES = 1
        part = self.make("partial0000", suffix=".m4a.abc.part", used_ago=5000, written_ago=10)
        server.Supporting.prune_audio_cache()
        self.assertTrue(os.path.exists(part))

    def test_mark_used_moves_atime_but_keeps_mtime(self):
        path = self.make(VID, written_ago=3000)
        before = os.stat(path).st_mtime
        server._mark_audio_used(path)
        st = os.stat(path)
        self.assertEqual(st.st_mtime, before)
        self.assertLess(time.time() - st.st_atime, 5)


class AudioEndpoint(_CacheDir):

    def setUp(self):
        super().setUp()
        self.app = server.app.test_client()
        self.headers = {"X-Api-Key": os.environ["API_KEY"]}

    def get(self, query, **kw):
        headers = dict(self.headers)
        headers.update(kw.pop("headers", {}))
        return self.app.get(f"/audio/?{query}", headers=headers, **kw)

    def test_requires_api_key_with_json_error(self):
        resp = self.app.get(f"/audio/?video_id={VID}")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json()["error"]["code"], "unauthorized")

    def test_cache_hit_serves_file_and_never_touches_echo_state(self):
        path = self.make(VID, size=4096, used_ago=1000)
        with mock.patch.object(server, "_update_now_playing") as np, \
             mock.patch.object(server, "_confirm_stream_delivery") as confirm, \
             mock.patch.object(server, "_refresh_radio_queue") as radio:
            resp = self.get(f"video_id={VID}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 4096)
        self.assertEqual(resp.headers["X-Cache"], "HIT")
        self.assertEqual(resp.headers["X-Video-Id"], VID)
        self.assertEqual(resp.headers["Accept-Ranges"], "bytes")
        np.assert_not_called()
        confirm.assert_not_called()
        radio.assert_not_called()
        self.assertLess(time.time() - os.stat(path).st_atime, 5, "hit not recorded for LRU")

    def test_range_on_cache_hit_returns_206(self):
        self.make(VID, size=4096)
        resp = self.get(f"video_id={VID}", headers={"Range": "bytes=1000-1999"})
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(len(resp.data), 1000)

    def test_etag_is_stable_across_pause_touch(self):
        path = self.make(VID, size=4096)
        first = self.get(f"video_id={VID}").headers["ETag"]
        os.utime(path, None)  # what pause/resume does to the mtime
        second = self.get(f"video_id={VID}").headers["ETag"]
        self.assertEqual(first, second)

    def test_youtube_link_is_accepted(self):
        self.make(VID, size=10)
        resp = self.get(f"url=https%3A%2F%2Fmusic.youtube.com%2Fwatch%3Fv%3D{VID}%26si%3Dx")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["X-Video-Id"], VID)

    def test_bad_input_is_400(self):
        self.assertEqual(self.get("url=https%3A%2F%2Fexample.com%2F").status_code, 400)
        self.assertEqual(self.get("video_id=bad%20id%21").status_code, 400)
        self.assertEqual(self.get("").status_code, 400)

    def test_known_dead_video_is_404_without_download(self):
        server._mark_video_dead(VID)
        with mock.patch.object(server.Supporting, "ensure_downloaded") as dl:
            resp = self.get(f"video_id={VID}&wait=1")
        self.assertEqual(resp.status_code, 404)
        dl.assert_not_called()

    def test_cold_miss_streams_without_echo_side_effects(self):
        with mock.patch.object(server, "_stream_proxy_download",
                               return_value=server.Response(b"LIVE", mimetype="audio/mp4")) as stream, \
             mock.patch.object(server.Supporting, "ensure_downloaded") as dl:
            resp = self.get(f"video_id={VID}")
        self.assertEqual(resp.get_data(), b"LIVE")
        self.assertEqual(resp.headers["X-Cache"], "MISS")
        stream.assert_called_once_with(VID, confirm=False, cancellable=False)
        dl.assert_not_called()

    def test_wait_downloads_then_serves_file_with_length(self):
        def spawn(video_id, **kw):
            self.make(video_id, size=2048, written_ago=0, used_ago=0)
            return False
        with mock.patch.object(server, "_ensure_audio_ready_for_play",
                               side_effect=spawn) as ready, \
             mock.patch.object(server, "_stream_proxy_download") as stream:
            resp = self.get(f"video_id={VID}&wait=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Length"], "2048")
        self.assertEqual(resp.headers["X-Cache"], "MISS")
        ready.assert_called_once_with(VID, wait=False, generation=None,
                                      prefetch=False)
        stream.assert_not_called()

    def test_recently_failed_video_is_503_without_new_ytdlp_run(self):
        server._mark_video_flaky(VID)
        self.addCleanup(lambda: server._flaky_video_ids.pop(VID, None))
        with mock.patch.object(server, "_stream_proxy_download") as stream, \
             mock.patch.object(server.Supporting, "ensure_downloaded") as dl:
            resp = self.get(f"video_id={VID}")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.headers["Retry-After"], str(server._FLAKY_VIDEO_TTL))
        stream.assert_not_called()
        dl.assert_not_called()

    def test_failed_download_is_pending_while_retry_runs(self):
        # Bounded wait: a download that yields nothing answers retryable 503
        # (the fetch keeps running, so a retry lands warm) instead of 502.
        with mock.patch.object(server, "_ensure_audio_ready_for_play",
                               return_value=False):
            resp = self.get(f"video_id={VID}&wait=1")
        self.assertEqual(resp.status_code, 503)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "pending")
        self.assertTrue(err["retryable"])
        self.assertEqual(resp.headers["Retry-After"], "5")


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
        with mock.patch.object(server.Supporting, "ensure_downloaded") as dl, \
             mock.patch.object(server, "_stream_proxy_download") as stream:
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
