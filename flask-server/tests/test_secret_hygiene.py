"""Secrets hygiene: generated key files are 0600, the API key gate still works,
and rejected keys are never echoed back in responses or logs.

Run with ``pytest flask-server/tests/test_secret_hygiene.py``.
"""
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    os.environ.setdefault("SECRET_KEY", "test-secret-for-hygiene")
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

VID = "hygiene00001"


class SecretHygiene(unittest.TestCase):
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

    def test_fresh_boot_key_files_are_0600(self):
        data = os.path.join(self.tmp.name, "freshboot")
        os.makedirs(data)
        env = dict(os.environ, DB_FILE=os.path.join(data, "data.db"),
                   API_KEY="", SECRET_KEY="", REMOTE_USER="u",
                   REMOTE_PASSWORD="p")
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'flask-server'); import server"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        for name, length in (("api_key.txt", 32), ("secret_key.txt", 64)):
            path = os.path.join(data, name)
            self.assertTrue(os.path.isfile(path), name)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600, f"{name} mode is {oct(mode)}")
            with open(path) as fp:
                value = fp.read().strip()
            self.assertEqual(len(value), length)
            int(value, 16)  # hex, no whitespace/quotes

    def test_wrong_key_401_never_echoes_key(self):
        bogus = "bogus-key-0123456789-decoy"
        resp = self.app.get(f"/audio/?video_id={VID}&key={bogus}")
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(bogus, resp.get_data(as_text=True))
        resp = self.app.get(f"/audio/?video_id={VID}",
                            headers={"X-Api-Key": bogus})
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(bogus, resp.get_data(as_text=True))

    def test_correct_key_still_gates(self):
        path = os.path.join(self.tmp.name, VID + ".m4a")
        with open(path, "wb") as fp:
            fp.write(b"\0" * 1024)
        resp = self.app.get(
            f"/audio/?video_id={VID}",
            headers={"X-Api-Key": os.environ["API_KEY"]})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
