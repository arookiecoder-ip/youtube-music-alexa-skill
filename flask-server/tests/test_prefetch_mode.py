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
        self.app=server.app.test_client()
        self.headers={'X-Api-Key':server.API_KEY}

    def get(self, query, **kwargs):
        return self.app.get('/audio/?'+query, headers=self.headers, **kwargs)

    def test_prefetch_prepares_only_a_url_and_never_opens_audio(self):
        with mock.patch.object(server, '_ensure_audio_ready_for_play') as prepare, mock.patch.object(server._youtube_stream,'open') as audio:
            r=self.get('video_id='+MISS+'&prefetch=1')
            self.assertEqual(r.status_code,202);self.assertTrue(r.json['streaming'])
            prepare.assert_called_once_with(MISS,prefetch=True);audio.assert_not_called()

    def test_info_precedes_prefetch(self):
        with mock.patch.object(server,'_ensure_audio_ready_for_play') as prepare:
            r=self.get('video_id='+MISS+'&info=1&prefetch=1')
            self.assertEqual(r.status_code,200);self.assertIn('audio_url',r.json);prepare.assert_not_called()

    def test_prefetch_precedes_wait_without_touching_current(self):
        current=server._youtube_stream.current
        before=current.video
        with mock.patch.object(server,'_ensure_audio_ready_for_play') as prepare:
            r=self.get('video_id='+MISS+'&wait=1&prefetch=1')
            self.assertEqual(r.status_code,202);prepare.assert_called_once_with(MISS,prefetch=True)
        self.assertEqual(current.video,before)

    def test_requires_api_key(self):
        self.assertEqual(self.app.get('/audio/?video_id='+MISS+'&prefetch=1').status_code,401)
