"""Regression tests for slow playlist starts.

Bugs:
1. Tapping a single song inside a large playlist built the *entire* playlist
   into the queue before anything played, so the song started seconds late.
2. "Play All" / shuffle on a very large playlist waited for the full fetch,
   full upload, full validation and full queue echo before the first song.

The supported fast pattern is: install/play the first song immediately, then
backfill the remainder in chunks (``/alexa/queue_add/`` bulk appends on the
Alexa queue, ``/api/app/queue/`` ``extend`` on the phone queue). These tests
lock the server side of that contract:

- a single-song play installs exactly one queue item without any blocking
  metadata lookup, and ``suppress_radio`` skips the background radio build;
- ``brief_response`` installs the full queue but omits it from the response,
  so bulk installs stop paying for a giant queue echo;
- sequential backfill appends (``next`` then ``last``) preserve playlist
  order behind the currently playing song.

Run with ``pytest flask-server/tests/test_playlist_fast_start.py``.
"""
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-playlist-fast-start",
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
        alexa_remote_remote.is_logged_in = lambda: (True, None)
        alexa_remote_remote.command = lambda *a, **kw: None
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
import server  # noqa: E402 — only after the stubs are installed


def _vid(i):
    return "v%010d" % i


def _item(i):
    return {'video_id': _vid(i), 'title': 'Song %d' % i, 'artist': 'Artist',
            'thumbnail': '', 'duration_ms': 180000}


class PlaylistFastStartTests(unittest.TestCase):
    def setUp(self):
        server._reset_rate_limit_cooldown()
        with server._PLAY_INTENT_LOCK:
            server._PLAY_INTENT_SEQS.clear()
        with server._PENDING_DISPATCH_LOCK:
            for pending in server._PENDING_DISPATCH.values():
                pending['timer'].cancel()
            server._PENDING_DISPATCH.clear()
        with server._TRIGGER_INFLIGHT_LOCK:
            server._TRIGGER_INFLIGHT.clear()
        # server.py keeps ownership and the queue in module globals; every
        # test starts from an idle Alexa output with an empty queue so neither
        # a leaked phone lease (claim -> 409) nor a leftover queue can pollute
        # the next test.
        server._playback_output = server.PlaybackOutput()
        with server._np_lock:
            server._now_playing.update(playing=False, playback_confirmed=True,
                                       playback_processing=False, video_id='',
                                       queue=[], queue_index=0)
        self.app = server.app.test_client()
        self.key = os.environ["API_KEY"]

    def _quiet(self):
        """Patch out every side effect except the queue-state writes."""
        return (
            mock.patch.object(server, "_schedule_play_dispatch", return_value=None),
            mock.patch.object(server, "_ensure_audio_ready_for_play", return_value=False),
            mock.patch.object(server, "_prewarm_queue_audio", return_value=0),
            mock.patch.object(server, "_record_listen"),
            mock.patch.object(server, "_refresh_radio_queue"),
            mock.patch.object(server, "_lookup_and_update_np"),
            mock.patch.object(server, "_notify_sse"),
        )

    def _play(self, body):
        return self.app.post("/alexa/play_queue/?key=%s" % self.key, json=body)

    def _add(self, body):
        return self.app.post("/alexa/queue_add/?key=%s" % self.key, json=body)

    def _now_playing(self):
        with server._np_lock:
            return dict(server._now_playing)

    def test_single_song_installs_single_item_queue_without_blocking_lookup(self):
        """The tap-a-song fast path: tiny request, one queue item, no upstream."""
        patches = self._quiet()
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with mock.patch.object(server, "_lookup_video_metadata",
                               side_effect=AssertionError("must not block on metadata")):
            response = self._play({'serial': 'DEVICE1', 'video_id': _vid(7),
                                   'title': 'Song 7', 'artist': 'Artist',
                                   'suppress_radio': True})
        self.assertEqual(response.status_code, 200)
        state = self._now_playing()
        self.assertEqual(state['video_id'], _vid(7))
        self.assertEqual([row['video_id'] for row in state['queue']], [_vid(7)])
        self.assertEqual(state['queue_index'], 0)

    def test_suppress_radio_skips_background_radio_build(self):
        patches = self._quiet()
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with mock.patch.object(server, "_refresh_radio_queue") as radio:
            self._play({'serial': 'DEVICE1', 'video_id': _vid(1),
                        'title': 'Song 1', 'suppress_radio': True})
        radio.assert_not_called()
        with mock.patch.object(server, "_refresh_radio_queue") as radio:
            self._play({'serial': 'DEVICE1', 'video_id': _vid(2),
                        'title': 'Song 2'})
        radio.assert_called_once()

    def test_brief_response_installs_full_queue_but_omits_the_echo(self):
        """Bulk installs stay complete server-side while the response stays small."""
        patches = self._quiet()
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        items = [_item(i) for i in range(50)]
        response = self._play({'serial': 'DEVICE1', 'queue_items': items,
                               'start_index': 3, 'brief_response': True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertNotIn('queue', payload,
                         msg="brief responses must not echo a large queue back")
        self.assertEqual(payload.get('queue_count'), 50)
        self.assertEqual(payload.get('queue_index'), 3)
        self.assertEqual(payload['now_playing']['video_id'], _vid(3))
        state = self._now_playing()
        self.assertEqual(len(state['queue']), 50)
        self.assertEqual(state['queue_index'], 3)

    def test_full_response_shape_is_unchanged_by_default(self):
        patches = self._quiet()
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        items = [_item(i) for i in range(5)]
        response = self._play({'serial': 'DEVICE1', 'queue_items': items,
                               'start_index': 1})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload.get('queue') or []), 5)
        self.assertNotIn('queue_count', payload)

    def test_backfill_chunks_append_in_order_behind_current_song(self):
        """Play-first then backfill: next-chunk first, last-chunks after."""
        patches = self._quiet()
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        first = self._play({'serial': 'DEVICE1', 'video_id': _vid(0),
                            'title': 'Song 0', 'suppress_radio': True})
        self.assertEqual(first.status_code, 200)
        chunk_one = self._add({'serial': 'DEVICE1', 'position': 'next',
                               'queue_items': [_item(i) for i in (1, 2, 3)]})
        self.assertEqual(chunk_one.status_code, 200)
        chunk_two = self._add({'serial': 'DEVICE1', 'position': 'last',
                               'queue_items': [_item(i) for i in (4, 5)]})
        self.assertEqual(chunk_two.status_code, 200)
        state = self._now_playing()
        self.assertEqual([row['video_id'] for row in state['queue']],
                         [_vid(i) for i in range(6)])
        self.assertEqual(state['video_id'], _vid(0))
        self.assertEqual(state['queue_index'], 0)

    def test_backfill_does_not_restart_playback_dispatch(self):
        """Appends must not schedule triggers: only the first song dispatches."""
        patches = self._quiet()
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with mock.patch.object(server, "_schedule_play_dispatch",
                               return_value=None) as dispatch:
            self._play({'serial': 'DEVICE1', 'video_id': _vid(0),
                        'title': 'Song 0', 'suppress_radio': True})
            self._add({'serial': 'DEVICE1', 'position': 'next',
                       'queue_items': [_item(i) for i in (1, 2)]})
        dispatch.assert_called_once_with('DEVICE1', _vid(0))

    def test_phone_extend_accepts_backfill_chunks(self):
        """The phone queue equivalent: start small, extend in chunks."""
        claim = self.app.post("/api/app/output/?key=%s" % self.key,
                              json={'action': 'claim', 'output_owner': 'phone-one'})
        self.assertEqual(claim.status_code, 200)
        token = claim.get_json()['output_token']
        first = [_item(0)]
        started = self.app.post("/api/app/queue/?key=%s" % self.key,
                                json={'action': 'start', 'after': _vid(0),
                                      'tracks': first, 'output_owner': 'phone-one',
                                      'output_token': token})
        self.assertEqual(started.status_code, 200)
        chunk = [_item(i) for i in (1, 2, 3)]
        extended = self.app.post(
            "/api/app/queue/?key=%s" % self.key,
            json={'action': 'extend', 'after': _vid(0), 'tracks': chunk,
                  'output_owner': 'phone-one', 'output_token': token,
                  'command_id': 'backfill-chunk-1'})
        self.assertEqual(extended.status_code, 200)
        state = self._now_playing()
        self.assertEqual([row['video_id'] for row in state['queue']],
                         [_vid(i) for i in range(4)])
        self.assertEqual(state['video_id'], _vid(0))


if __name__ == "__main__":
    unittest.main()
