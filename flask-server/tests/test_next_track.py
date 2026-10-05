"""Isolated tests for the authoritative /next_track/ endpoint.

This is step 1 of fixing the "split-brain queue" bug (web ISO queue can
reorder/add/shuffle, but the Alexa skill plays a stale next because it resolves
"next" from its own copy). The endpoint answers "what is next after this
currently-playing video?" purely from the server's live queue, so every queue
mutation takes effect immediately.

These tests exercise the endpoint in isolation: the queue is installed directly
into server._now_playing (or through the real reorder/shuffle endpoints) and NOT
through yt-dlp / proxy / Lambda — no network or device fakes needed beyond the
import stubs. If next-track resolution stops reading the live queue, or shuffle
stops keeping the current song first / stops deciding the next by server order,
an assertion here fails first.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault("SECRET_KEY", "test-secret-next-track")
os.environ.setdefault("REMOTE_USER", "test-owner")
os.environ.setdefault("REMOTE_PASSWORD", "test-pass")
os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")

if "ytmusicapi" not in sys.modules:
    ytmusicapi = types.ModuleType("ytmusicapi")
    ytmusicapi.YTMusic = type("YTMusic", (), {"__init__": lambda self, **kw: None})
    sys.modules["ytmusicapi"] = ytmusicapi
    auth = types.ModuleType("ytmusicapi.auth")
    auth_types = types.ModuleType("ytmusicapi.auth.types")
    auth_types.AuthType = type("AuthType", (), {"UNAUTHORIZED": "UNAUTHORIZED"})
    browser = types.ModuleType("ytmusicapi.auth.browser")
    browser.setup_browser = lambda *args, **kwargs: None
    sys.modules["ytmusicapi.auth"] = auth
    sys.modules["ytmusicapi.auth.types"] = auth_types
    sys.modules["ytmusicapi.auth.browser"] = browser

if "home_feed" not in sys.modules:
    sys.modules["home_feed"] = types.ModuleType("home_feed")

if "alexa_remote" not in sys.modules:
    alexa_remote = types.ModuleType("alexa_remote")
    remote = types.ModuleType("alexa_remote.remote")
    remote.devices = lambda refresh=False: (None, "stubbed")
    remote.volume = lambda serial: (None, "stubbed")
    remote.is_logged_in = lambda: (True, None)
    remote.proxy_start_url = lambda *args, **kwargs: (None, "stubbed")
    alexa_remote.remote = remote
    alexa_remote.AlexaUnreachable = type("AlexaUnreachable", (Exception,), {})
    sys.modules["alexa_remote"] = alexa_remote
    sys.modules["alexa_remote.remote"] = remote

if "youtube_browser_session" not in sys.modules:
    ybs = types.ModuleType("youtube_browser_session")
    ybs.BrowserController = type("BrowserController", (), {"__init__": lambda self, *a, **kw: None})
    ybs.YouTubeBrowserSessionManager = type("YouTubeBrowserSessionManager", (), {"__init__": lambda self, *a, **kw: None})
    ybs.browser_client_is_signed_in = lambda *args, **kwargs: False
    ybs.is_authentication_error = lambda *args, **kwargs: False
    ybs.promote_browser_headers = lambda *args, **kwargs: None
    sys.modules["youtube_browser_session"] = ybs

import server  # noqa: E402


def _meta(video_id, title=None):
    return {
        'video_id': video_id,
        'title': title or ('Song ' + video_id),
        'artist': 'Test Artist',
        'thumbnail': '',
        'duration_ms': 180000,
    }


class NextTrackBase(unittest.TestCase):
    SERIAL = 'ECHO-AAA1'

    def setUp(self):
        server._reset_rate_limit_cooldown()
        with server._np_lock:
            server._saved_now_playing = dict(server._now_playing)
            server._now_playing.clear()
        self.addCleanup(self._restore_now_playing)
        self.client = server.app.test_client()

    def tearDown(self):
        mock.patch.stopall()

    def _restore_now_playing(self):
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update(server._saved_now_playing)

    def _set_queue(self, items, current_index=0, current_video=None):
        with mock.patch.object(server, '_notify_sse'):
            server._update_now_playing(
                queue=list(items),
                queue_index=current_index,
                video_id=current_video or (items[current_index]['video_id']
                                           if items else None),
            )

    def _get(self, after):
        return self.client.get('/next_track/',
                               query_string={'after': after, 'key': server.API_KEY})

    def _track(self, after):
        resp = self._get(after)
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()['track']


class NextTrackEndpoint(NextTrackBase):
    def test_returns_metadata_of_track_after_current(self):
        self._set_queue([_meta('A'), _meta('B'), _meta('C')], current_index=0)
        track = self._track('A')
        self.assertEqual(track['video_id'], 'B')
        self.assertEqual(track['title'], 'Song B')
        self.assertEqual(track['artist'], 'Test Artist')
        self.assertEqual(track['duration_ms'], 180000)

    def test_reads_live_queue_order_not_index(self):
        # current_index says B (index 1) but the authoritative next must come
        # from the actual queue order, not a stale index.
        self._set_queue([_meta('A'), _meta('C'), _meta('B')], current_index=1)
        self.assertEqual(self._track('A')['video_id'], 'C')

    def test_prefers_last_occurrence_of_duplicate(self):
        # B appears twice; the playing occurrence is the second one, so next is
        # the track after it (matches /queue_tracks/).
        self._set_queue([_meta('A'), _meta('B'), _meta('X'), _meta('B'),
                         _meta('C')], current_index=3)
        self.assertEqual(self._track('B')['video_id'], 'C')

    def test_null_when_current_is_last(self):
        self._set_queue([_meta('A'), _meta('B')], current_index=1)
        self.assertIsNone(self._track('B'))

    def test_null_when_current_not_in_queue(self):
        self._set_queue([_meta('A'), _meta('B')], current_index=0)
        self.assertIsNone(self._track('ZZZ'))

    def test_requires_api_key(self):
        self._set_queue([_meta('A'), _meta('B')], current_index=0)
        resp = self.client.get('/next_track/', query_string={'after': 'A'})
        self.assertEqual(resp.status_code, 401)


class NextTrackAfterQueueMutations(NextTrackBase):
    def test_reorder_via_endpoint_changes_next(self):
        # Queue is A,B(current),C. Move C up to right after B (a drag reorder)
        # -> server queue becomes A,B,C; wait, to *reorder so a different song
        # follows the current one*, put B at index 0 and move C in front of A.
        self._set_queue([_meta('A'), _meta('B'), _meta('C')], current_index=0)
        # Reorder: bring C from index 2 to index 1 (directly after A).
        resp = self.client.post(
            '/alexa/queue_reorder/?key=' + server.API_KEY,
            json={'from_index': 2, 'to_index': 1},
        )
        self.assertEqual(resp.status_code, 200)
        # The authoritative next after A is now C, not the original B.
        self.assertEqual(self._track('A')['video_id'], 'C')

    def test_added_next_song_plays_that_song(self):
        # User adds a brand-new song X as "play next" -> it lands right after A.
        self._set_queue([_meta('A'), _meta('B'), _meta('C')], current_index=0)
        with mock.patch.object(server, '_notify_sse'):
            server._update_now_playing(queue=[_meta('A'), _meta('X'), _meta('B'),
                                              _meta('C')], queue_index=0,
                                       video_id='A')
        self.assertEqual(self._track('A')['video_id'], 'X')


class ShuffleByServerOrder(NextTrackBase):
    def test_next_follows_server_shuffled_order_and_keeps_current(self):
        queue = [_meta('A'), _meta('B'), _meta('C'), _meta('D'), _meta('E')]
        self._set_queue(queue, current_index=0)
        # Never let the shuffle endpoint spawn prewarm download threads.
        with mock.patch.object(server, '_prewarm_queue_audio', lambda *a, **k: 0):
            resp = self.client.post(
                '/alexa/shuffle_queue/?key=' + server.API_KEY, json={})
        self.assertEqual(resp.status_code, 200)
        with server._np_lock:
            shuffled = list(server._now_playing.get('queue') or [])
            shuffled_index = server._now_playing.get('queue_index', -1)
        # The currently playing song stays front and center...
        self.assertEqual(shuffled[0]['video_id'], 'A')
        self.assertEqual(shuffled_index, 0)
        # ...and the rest of the queue is the same songs, just in the shuffled
        # server order (contents preserved, none lost or duplicated).
        self.assertEqual({q['video_id'] for q in shuffled[1:]},
                         {'B', 'C', 'D', 'E'})
        # The authoritative next after A is exactly the server's queue[1]:
        # following /next_track/ after a shuffle plays the server-shuffled
        # order, never some separate ordering the skill keeps locally.
        self.assertEqual(self._track('A')['video_id'], shuffled[1]['video_id'])


if __name__ == '__main__':
    unittest.main()