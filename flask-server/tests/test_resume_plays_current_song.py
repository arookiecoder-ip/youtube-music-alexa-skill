"""Regression tests for resume restarting the wrong (older) song.

Bug (reported): song A playing, user asks Alexa to play song B (song B is
heard on the Echo and the remote shows song B), then taps the YouTube Music
icon (pauses song B), then resumes from the web remote -- and song A plays
instead of song B.

Two cooperating fixes are covered here on the server side:

1. ``/alexa/command/`` 'play' (web resume) now arms the exact track+offset it
   shows (``kind='resume'``). The skill peeks at that arm on resume and plays
   the armed track when its persisted AudioPlayer session disagrees, instead
   of blindly restarting the stale persisted track.
2. ``/alexa/state_event/`` 'stopped' now carries the stopped ``video_id``
   (newer skills send it). A stop for any *other* track -- Alexa's
   PlaybackStopped for the interrupted song arriving after PlaybackStarted for
   the new one -- is ignored instead of freezing/corrupting the new track's
   anchor. Stops without an id keep the legacy always-freeze behavior.

Run with ``pytest flask-server/tests/test_resume_plays_current_song.py``.
"""
import os
import sys
import time
import types
import unittest
from unittest import mock


_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-resume-current-song",
    "REMOTE_USER": "test-owner",
    "REMOTE_PASSWORD": "test-pass",
    "API_KEY": "0123456789abcdef0123456789abcdef",
}


def _install_stubs():
    for key, value in _TEST_ENV.items():
        os.environ.setdefault(key, value)

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
        alexa_remote_remote.command = lambda serial, action, value=None: None
        alexa_remote_remote.play_video_id = lambda serial, video_id, offset_ms=0: None
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
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import server  # noqa: E402 — only after the stubs are installed


NEW_SONG = 'NEWSONG00001'
OLD_SONG = 'OLDSONG00001'


class _CleanState(unittest.TestCase):
    """Isolate module-level now-playing + armed-play state between tests."""

    def setUp(self):
        with server._np_lock:
            self._saved_np = dict(server._now_playing)
        with server._ARMED_PLAYS_LOCK:
            self._saved_arms = dict(server._ARMED_PLAYS)
            server._ARMED_PLAYS.clear()

    def tearDown(self):
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update(self._saved_np)
        with server._ARMED_PLAYS_LOCK:
            server._ARMED_PLAYS.clear()
            server._ARMED_PLAYS.update(self._saved_arms)


def _stage_paused(video_id, position_ms=90_000):
    with server._np_lock:
        server._now_playing.update({
            'video_id': video_id,
            'title': 'Song ' + video_id,
            'artist': 'Test Artist',
            'playing': False,
            'playback_confirmed': True,
            'playback_processing': False,
            'position_ms': position_ms,
            'started_at': time.time(),
            'duration_ms': 240_000,
            'queue': [],
        })


def _stage_playing(video_id, position_ms=10_000):
    with server._np_lock:
        server._now_playing.update({
            'video_id': video_id,
            'title': 'Song ' + video_id,
            'artist': 'Test Artist',
            'playing': True,
            'playback_confirmed': True,
            'playback_processing': False,
            'position_ms': position_ms,
            'started_at': time.time(),
            'duration_ms': 240_000,
            'queue': [],
        })


class WebResumeArmsCurrentTrack(_CleanState):
    def test_resume_arms_staged_video_id_and_offset(self):
        """The reported bug's server half: resuming must leave an exact
        track+offset arm so the skill can self-correct a stale session."""
        _stage_paused(NEW_SONG, position_ms=95_000)
        with mock.patch.object(server.alexa_remote.remote, 'command',
                               return_value=None), \
             mock.patch.object(server, '_ensure_audio_ready_for_play'), \
             mock.patch.object(server, '_watch_resume_confirmation'), \
             mock.patch.object(server, '_notify_sse'):
            response = server.app.test_client().post(
                '/alexa/command/?key=' + server.API_KEY,
                json={'serial': 'TEST-SERIAL', 'action': 'play'},
            )
        self.assertEqual(response.status_code, 200)
        with server._ARMED_PLAYS_LOCK:
            arm = server._ARMED_PLAYS.get('TEST-SERIAL')
        self.assertIsNotNone(arm, msg="web resume must arm the current track")
        self.assertEqual(arm['video_id'], NEW_SONG)
        self.assertEqual(arm['kind'], 'resume')
        # Frozen pre-pause offset rides along so the correction resumes
        # from the pause point, not from 0.
        self.assertGreaterEqual(arm['offset_ms'], 95_000)
        self.assertLess(arm['offset_ms'], 96_000)

    def test_resume_arm_is_peekable_with_age_and_kind(self):
        _stage_paused(NEW_SONG, position_ms=95_000)
        with mock.patch.object(server.alexa_remote.remote, 'command',
                               return_value=None), \
             mock.patch.object(server, '_ensure_audio_ready_for_play'), \
             mock.patch.object(server, '_watch_resume_confirmation'), \
             mock.patch.object(server, '_notify_sse'):
            server.app.test_client().post(
                '/alexa/command/?key=' + server.API_KEY,
                json={'serial': 'TEST-SERIAL', 'action': 'play'},
            )
        client = server.app.test_client()
        peek = client.get(
            '/armed_play/?key=' + server.API_KEY + '&peek=1').get_json()
        self.assertEqual(peek['video_id'], NEW_SONG)
        self.assertEqual(peek['kind'], 'resume')
        self.assertIsNotNone(peek.get('armed_at'))
        # Peek must not consume: the arm is still there for a real consume.
        again = client.get(
            '/armed_play/?key=' + server.API_KEY + '&peek=1').get_json()
        self.assertEqual(again['video_id'], NEW_SONG)
        consumed = client.get(
            '/armed_play/?key=' + server.API_KEY).get_json()
        self.assertEqual(consumed['video_id'], NEW_SONG)
        gone = client.get(
            '/armed_play/?key=' + server.API_KEY).get_json()
        self.assertIsNone(gone['video_id'])

    def test_failed_resume_leaves_no_stale_arm(self):
        """A rejected dispatch restores the snapshot; it must not leave an
        arm pointing at a track that never resumed for a later resume (or an
        in-flight app-selection trigger) to honor."""
        _stage_paused(NEW_SONG)
        with mock.patch.object(server.alexa_remote.remote, 'command',
                               return_value='device offline'), \
             mock.patch.object(server, '_ensure_audio_ready_for_play'), \
             mock.patch.object(server, '_watch_resume_confirmation'), \
             mock.patch.object(server, '_notify_sse'):
            response = server.app.test_client().post(
                '/alexa/command/?key=' + server.API_KEY,
                json={'serial': 'TEST-SERIAL', 'action': 'play'},
            )
        self.assertEqual(response.status_code, 502)
        with server._ARMED_PLAYS_LOCK:
            arm = server._ARMED_PLAYS.get('TEST-SERIAL')
        self.assertIsNone(arm)

    def test_failed_resume_keeps_newer_arm(self):
        """Clearing on failure must only drop our own arm: if a newer intent
        re-armed while the failing dispatch was in flight, it stays."""
        _stage_paused(NEW_SONG)

        def failing_command(serial, action, value=None):
            # A racing fresh play re-arms while our dispatch is in flight.
            server._arm_play(serial, 'NEWERSONG01', 0, kind='play')
            return 'device offline'

        with mock.patch.object(server.alexa_remote.remote, 'command',
                               side_effect=failing_command), \
             mock.patch.object(server, '_ensure_audio_ready_for_play'), \
             mock.patch.object(server, '_watch_resume_confirmation'), \
             mock.patch.object(server, '_notify_sse'):
            response = server.app.test_client().post(
                '/alexa/command/?key=' + server.API_KEY,
                json={'serial': 'TEST-SERIAL', 'action': 'play'},
            )
        self.assertEqual(response.status_code, 502)
        with server._ARMED_PLAYS_LOCK:
            arm = server._ARMED_PLAYS.get('TEST-SERIAL')
        self.assertIsNotNone(arm)
        self.assertEqual(arm['video_id'], 'NEWERSONG01')


class StaleStoppedWebhook(_CleanState):
    def _post_stopped(self, payload):
        return server.app.test_client().post(
            '/alexa/state_event/?key=' + server.API_KEY,
            json={'event': 'stopped', **payload},
        )

    def test_stale_stop_for_old_track_is_ignored(self):
        """Alexa emits PlaybackStopped for the interrupted song; arriving
        after the new song's PlaybackStarted, it must not pause/rewind it."""
        _stage_playing(NEW_SONG, position_ms=10_000)
        with mock.patch.object(server, '_notify_sse'):
            response = self._post_stopped({'video_id': OLD_SONG})
        self.assertEqual(response.status_code, 200)
        with server._np_lock:
            self.assertEqual(server._now_playing['video_id'], NEW_SONG)
            self.assertTrue(server._now_playing['playing'])
            self.assertTrue(server._now_playing['playback_confirmed'])

    def test_matching_stop_still_freezes(self):
        _stage_playing(NEW_SONG, position_ms=10_000)
        with mock.patch.object(server, '_notify_sse'):
            response = self._post_stopped({'video_id': NEW_SONG})
        self.assertEqual(response.status_code, 200)
        with server._np_lock:
            self.assertFalse(server._now_playing['playing'])
            self.assertGreaterEqual(server._now_playing['position_ms'], 10_000)

    def test_legacy_stop_without_id_still_freezes(self):
        """Older skills (and failure paths) send no video_id: keep the
        previous always-freeze behavior for them."""
        _stage_playing(NEW_SONG, position_ms=10_000)
        with mock.patch.object(server, '_notify_sse'):
            response = self._post_stopped({})
        self.assertEqual(response.status_code, 200)
        with server._np_lock:
            self.assertFalse(server._now_playing['playing'])

    def test_malformed_stop_id_is_treated_as_legacy(self):
        _stage_playing(NEW_SONG, position_ms=10_000)
        with mock.patch.object(server, '_notify_sse'):
            response = self._post_stopped({'video_id': 'has spaces'})
        self.assertEqual(response.status_code, 200)
        with server._np_lock:
            self.assertFalse(server._now_playing['playing'])


if __name__ == "__main__":
    unittest.main()
