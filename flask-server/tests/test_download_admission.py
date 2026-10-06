"""Coordinator: admission cap fails fast when saturated; duplicate same-id work
is joined, never doubled; a cancelled caller leaves shared work alive.

Run with ``pytest flask-server/tests/test_download_admission.py``.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    os.environ.setdefault("SECRET_KEY", "test-secret-for-admission")
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
SAT = "saturated001"
OPEN = "openqueue0001"
JOIN = "joininflight1"
DUP = "duplicate0001"
SHARED = "sharedwork001"


class Admission(unittest.TestCase):
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
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()
        with server._stream_inflight_lock:
            server._stream_inflight.clear()
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

    def test_saturated_queue_rejects_requested_with_pending(self):
        with mock.patch.object(server, "_download_queue_depth", 99), \
             mock.patch.object(server, "_ensure_audio_ready_for_play") as ready:
            resp = self.get(f"video_id={SAT}&wait=1")
        self.assertEqual(resp.status_code, 503)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "pending")
        self.assertTrue(err["retryable"])
        self.assertEqual(resp.headers["Retry-After"], "5")
        ready.assert_not_called()  # no new download queued behind saturation

    def test_open_queue_proceeds(self):
        def spawn(video_id, **kw):
            self.land(video_id, size=4096)
            return False
        with mock.patch.object(server, "_download_queue_depth", 0), \
             mock.patch.object(server, "_ensure_audio_ready_for_play",
                               side_effect=spawn):
            resp = self.get(f"video_id={OPEN}&wait=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 4096)

    def test_legacy_header_prefetch_bypasses_admission(self):
        # Best-effort prefetch keeps its own drop rules (slot + cooldown);
        # the requested-only admission gate must not hijack its shape.
        with mock.patch.object(server, "_download_queue_depth", 99), \
             mock.patch.object(server.Supporting, "ensure_downloaded",
                               return_value=None):
            resp = self.get(f"video_id={SAT}&wait=1",
                            headers={"X-MusicBox-Prefetch": "1"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()["error"]["code"], "prefetch_deferred")

    def test_inflight_prewarm_is_joined_not_respawned(self):
        def fake_join(video_id, **kw):
            path = os.path.join(self.tmp.name, video_id + ".m4a")
            with open(path, "wb") as fp:
                fp.write(b"\0" * 4096)
            return path
        with mock.patch.object(server, "_download_in_progress",
                               return_value=True), \
             mock.patch.object(server, "_await_prewarm_cache",
                               side_effect=fake_join) as joined, \
             mock.patch.object(server, "_ensure_audio_ready_for_play") as ready:
            resp = self.get(f"video_id={JOIN}&wait=1")
        self.assertEqual(resp.status_code, 200)
        joined.assert_called_once()
        ready.assert_not_called()  # joined existing work; spawned nothing new


class SharedDownload(unittest.TestCase):
    """ensure_downloaded-level dedup: one yt-dlp run per video_id no matter how
    many callers arrive, and a second caller joins work already running."""

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
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()
        server._last_prune[0] = time.time()  # skip prune scans in these tests

    def run_succeeds(self, command, **kw):
        out = command[command.index("-o") + 1].replace("%(ext)s", "m4a")
        with open(out, "wb") as fp:
            fp.write(b"\0" * MB)
        return subprocess.CompletedProcess(command, 0)

    def test_simultaneous_same_id_downloads_once(self):
        with mock.patch.object(server.subprocess, "run",
                               side_effect=self.run_succeeds) as run, \
             mock.patch.object(server, "_is_audio_file_valid",
                               return_value=True):
            threads = [threading.Thread(
                target=server.Supporting.ensure_downloaded, args=(DUP,),
                kwargs={"prefetch": bool(i)}) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
                self.assertFalse(t.is_alive())
        self.assertEqual(run.call_count, 1)
        self.assertIsNotNone(server.Supporting.cached_audio_path(DUP))

    def test_second_caller_joins_running_download(self):
        go = threading.Event()
        started = threading.Event()

        def slow_run(command, **kw):
            started.set()
            self.assertTrue(go.wait(timeout=30))
            return self.run_succeeds(command, **kw)

        with mock.patch.object(server.subprocess, "run",
                               side_effect=slow_run) as run, \
             mock.patch.object(server, "_is_audio_file_valid",
                               return_value=True):
            first = threading.Thread(
                target=server.Supporting.ensure_downloaded, args=(SHARED,))
            first.start()
            self.assertTrue(started.wait(timeout=30))
            # The "cancelled" first caller is gone; a new caller for the same
            # id joins the running work instead of starting a second yt-dlp.
            threading.Timer(1.0, go.set).start()
            path = server.Supporting.ensure_downloaded(SHARED)
            first.join(timeout=30)
            self.assertFalse(first.is_alive())
        self.assertEqual(run.call_count, 1)
        self.assertIsNotNone(path)


if __name__ == "__main__":
    unittest.main()
