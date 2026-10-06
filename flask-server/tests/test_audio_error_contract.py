"""Error contract: every error carries {code, message, retryable}; retryable ones
carry Retry-After. Existing codes/statuses are unchanged (additive only).

Run with ``pytest flask-server/tests/test_audio_error_contract.py``.
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
    os.environ.setdefault("SECRET_KEY", "test-secret-for-error-contract")
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
VID = "errcon00001"


class ErrorContract(unittest.TestCase):
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

    def test_default_errors_carry_retryable_false(self):
        resp = self.app.get(f"/audio/?video_id={VID}")  # no key
        self.assertEqual(resp.status_code, 401)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "unauthorized")
        self.assertFalse(err["retryable"])

    def test_flaky_is_retryable_503_with_retry_after(self):
        server._mark_video_flaky(VID)
        try:
            resp = self.get(f"video_id={VID}&wait=1")
        finally:
            with server._flaky_video_ids_lock:
                server._flaky_video_ids.clear()
        self.assertEqual(resp.status_code, 503)
        err = resp.get_json()["error"]
        self.assertTrue(err["retryable"])
        self.assertEqual(resp.headers["Retry-After"], str(int(server._FLAKY_VIDEO_TTL)))

    def test_pending_helper_shape(self):
        with server.app.test_request_context():
            resp = server.pending_response(retry_after=7)
        self.assertEqual(resp.status_code, 503)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "pending")
        self.assertTrue(err["retryable"])
        self.assertEqual(resp.headers["Retry-After"], "7")

    def test_rate_limited_is_retryable_with_retry_after(self):
        with mock.patch.dict(server._RATE_LIMITS, {"audio": (2, 60)}):
            # Non-cached video_id keeps every hit in the plain 'audio' group.
            self.get(f"video_id={VID}&info=1")
            self.get(f"video_id={VID}&info=1")
            resp = self.get(f"video_id={VID}&info=1")
        self.assertEqual(resp.status_code, 429)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "rate_limited")
        self.assertTrue(err["retryable"])
        self.assertIn("Retry-After", resp.headers)

    def test_match_rejected_when_duration_far_off(self):
        meta = {"video_id": VID, "title": "X", "artist": "Y",
                "duration_ms": 600_000}  # 10 min vs 200 s wanted
        with mock.patch.object(server, "_audio_search_song", return_value=meta):
            resp = self.get("q=some+song&duration=200")
        self.assertEqual(resp.status_code, 422)
        err = resp.get_json()["error"]
        self.assertEqual(err["code"], "match_rejected")
        self.assertFalse(err["retryable"])

    def test_match_accepted_within_tolerance_then_serves(self):
        path = os.path.join(self.tmp.name, VID + ".m4a")
        with open(path, "wb") as fp:
            fp.write(b"\0" * 1024)
        meta = {"video_id": VID, "title": "X", "artist": "Y", "duration_ms": 205_000}
        with mock.patch.object(server, "_audio_search_song", return_value=meta):
            resp = self.get("q=some+song&duration=200")
        self.assertEqual(resp.status_code, 200)

    def test_match_without_known_duration_serves_as_before(self):
        path = os.path.join(self.tmp.name, VID + ".m4a")
        with open(path, "wb") as fp:
            fp.write(b"\0" * 1024)
        meta = {"video_id": VID, "title": "X", "artist": "Y"}  # no duration_ms
        with mock.patch.object(server, "_audio_search_song", return_value=meta):
            resp = self.get("q=some+song&duration=200")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
