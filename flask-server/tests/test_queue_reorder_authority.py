"""Regression tests: a web-remote queue reorder must not be overwritten by the
Alexa skill's stale window.

Bug scenario (\"dragged a song below a playing song, but the next track played
from the older queue\"): the web remote reorders the live queue while a song is
playing. The Alexa skill keeps its own persisted copy of the queue (a sliding
DynamoDB window) and only learns of web changes when it resolves its next
stream through /next_track/. When the (possibly stale) enqueued track starts,
the skill's 'started' webhook reports its old window with queue_index; without
a guard the server replaced its reordered live queue with that stale window,
so playback -- and the on-screen queue -- fell back to the pre-reorder order.

These tests exercise server alexa_state_event / queue_reorder in isolation:
the queue is installed directly and the skill snapshot is posted through
/state_event/ with yt-dlp / proxy / device dispatch stubbed, so failures here
point at the authority logic, not the network.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault("SECRET_KEY", "test-secret-reorder-authority")
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
        'artists': [],
        'thumbnail': '',
        'duration_ms': 180000,
    }


class ReorderAuthorityBase(unittest.TestCase):
    SERIAL = 'ECHO-AAA1'

    def setUp(self):
        server._reset_rate_limit_cooldown()
        with server._np_lock:
            server._saved_now_playing = dict(server._now_playing)
            server._now_playing.clear()
        self.addCleanup(self._restore_now_playing)
        self.client = server.app.test_client()
        # Keep the webhook's bookkeeping hermetic: playback dispatch, radio
        # builds, listen recording, metadata lookups and SSE all touch the
        # network / shared state, none of which these tests exercise.
        self._patches = []
        for target, attr in [
            (server, '_notify_sse'),
            (server, '_refresh_radio_queue'),
            (server, '_prewarm_queue_audio'),
            (server, '_record_listen'),
            (server, '_lookup_and_update_np'),
            # Audio fetching / dispatch are out of scope for these tests; the
            # auto-skip path must not spawn real yt-dlp or Alexa triggers.
            (server, '_arm_play'),
            (server, '_ensure_audio_ready_for_play'),
        ]:
            p = mock.patch.object(target, attr)
            self._patches.append(p)
            p.start()
        # Record every _schedule_play_dispatch call (the auto-skip trigger).
        self.dispatches = []
        dispatch_patch = mock.patch.object(
            server, '_schedule_play_dispatch',
            lambda serial, video_id, offset_ms=0, delay=None:
                self.dispatches.append(video_id))
        self._patches.append(dispatch_patch)
        dispatch_patch.start()
        self.addCleanup(self._stop_patches)

    def tearDown(self):
        self._stop_patches()
        mock.patch.stopall()

    def _stop_patches(self):
        for p in self._patches:
            if getattr(p, '_is_running', False):
                p.stop()
        self._patches.clear()
        mock.patch.stopall()

    def _restore_now_playing(self):
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update(server._saved_now_playing)

    def _set_state(self, **fields):
        with mock.patch.object(server, '_notify_sse'):
            server._update_now_playing(**fields)

    def _post_state_event(self, event, **fields):
        payload = {'event': event}
        payload.update(fields)
        return self.client.post(
            '/alexa/state_event/?key=' + server.API_KEY,
            json=payload,
        )

    def _post_reorder(self, from_index, to_index):
        return self.client.post(
            '/alexa/queue_reorder/?key=' + server.API_KEY,
            json={'from_index': from_index, 'to_index': to_index},
        )

    def _track(self, after):
        resp = self.client.get('/next_track/',
                               query_string={'after': after, 'key': server.API_KEY})
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()['track']


class QueueReorderMarksWebDirty(ReorderAuthorityBase):
    """The reorder endpoint must flag the live queue as web-changed. Without
    this the next 'started' snapshot would be treated as authoritative even
    though it predates the reorder."""

    def test_reorder_sets_queue_web_dirty(self):
        queue = [_meta('A'), _meta('B'), _meta('C'), _meta('D')]
        self._set_state(queue=list(queue), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True)
        # Before the mutation the marker must be clear.
        self.assertFalse(server._now_playing.get('queue_web_dirty', False))

        # Drag D up so it sits directly under the playing A: [A, D, B, C].
        resp = self._post_reorder(from_index=3, to_index=1)
        self.assertEqual(resp.status_code, 200)
        with server._np_lock:
            reordered = [q['video_id'] for q in server._now_playing['queue']]
            dirty = server._now_playing['queue_web_dirty']
        self.assertEqual(reordered, ['A', 'D', 'B', 'C'])
        self.assertTrue(dirty)


class StaleSkillSnapshotDoesNotRevertWebReorder(ReorderAuthorityBase):
    """The exact reported bug: user reorders the queue while a song plays, the
    skill's window is still the old order, and the track that actually starts
    next reports that stale window. The server must keep the user's order."""

    def test_reorder_survives_stale_started_snapshot(self):
        # A is playing. The user dragged D (index 3) below A -> [A, D, B, C].
        # The skill's copy still says [A, B, C, D] (its window predates the
        # reorder - it had already enqueued B near the end of A). B starts.
        reordered = [_meta('A'), _meta('D'), _meta('B'), _meta('C')]
        stale_skill = [_meta('A'), _meta('B'), _meta('C'), _meta('D')]
        self._set_state(queue=list(reordered), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        queue_web_dirty=True)

        resp = self._post_state_event(
            'started', video_id='B', queue=stale_skill, queue_index=1,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)

        snap = server._get_now_playing()
        # The stale skill window must NOT overwrite the reordered queue.
        self.assertEqual(
            [q['video_id'] for q in snap['queue']],
            ['A', 'D', 'B', 'C'],
            msg="the skill's stale window replaced the web-reordered queue")
        # B (the stale enqueued track) was auto-skipped: now-playing advanced
        # straight to the song the user dragged next (D), at its real index 1.
        self.assertEqual(snap['video_id'], 'D')
        self.assertEqual(snap['queue_index'], 1)
        self.assertEqual(self.dispatches, ['D'])
        # The skill hasn't caught up yet, so the marker must stay set to keep
        # a later stale report from sneaking the old order back in.
        self.assertTrue(snap['queue_web_dirty'])
        # The queue continues in the reordered order after D.
        self.assertEqual(self._track('D')['video_id'], 'B')

    def test_next_follows_reordered_order_after_stale_track(self):
        # Same scenario, but the moved song goes to the *end* so the stale and
        # reordered orders disagree on what follows B: [A, B, C, D] -> the user
        # drags C below D -> [A, B, D, C]. The skill already enqueued B.
        reordered = [_meta('A'), _meta('B'), _meta('D'), _meta('C')]
        stale_skill = [_meta('A'), _meta('B'), _meta('C'), _meta('D')]
        self._set_state(queue=list(reordered), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        queue_web_dirty=True)

        resp = self._post_state_event(
            'started', video_id='B', queue=stale_skill, queue_index=1,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)

        snap = server._get_now_playing()
        self.assertEqual([q['video_id'] for q in snap['queue']],
                         ['A', 'B', 'D', 'C'])
        # The authoritative next after B must be D (reordered order), never C
        # (the song the user moved away, which the stale window would pick).
        self.assertEqual(self._track('B')['video_id'], 'D')


class MatchingSkillSnapshotClearsDirty(ReorderAuthorityBase):
    """Once the skill's window actually agrees with the reordered queue (it
    re-enqueued through /next_track/ and its window got re-staged), the marker
    clears and its reports are trusted again."""

    def test_matching_snapshot_clears_marker(self):
        reordered = [_meta('A'), _meta('D'), _meta('B'), _meta('C')]
        self._set_state(queue=list(reordered), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        queue_web_dirty=True)

        # Over time the skill re-staged its window to the new order; D starts.
        resp = self._post_state_event(
            'started', video_id='D', queue=list(reordered), queue_index=1,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)

        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], 'D')
        self.assertEqual(snap['queue_index'], 1)
        self.assertFalse(snap['queue_web_dirty'],
                         msg="a matching snapshot must clear the web-dirty marker")


class AutoSkipStaleEnqueuedTrack(ReorderAuthorityBase):
    """When a reorder lands in the final ~30-40s, the skill has already
    enqueued the old next on the Echo; the 'started' webhook for that stale
    track must auto-skip it and dispatch the song the user actually dragged
    next (mirroring the transport Next path)."""

    def test_stale_enqueued_track_is_auto_skipped_to_reordered_next(self):
        # A plays for 3m50s (dragged during A's final ~30s). The skill had
        # already ENQUEUEd B. The user dragged D below A -> [A, D, B, C].
        # B starts (stale) and must be skipped to D.
        reordered = [_meta('A'), _meta('D'), _meta('B'), _meta('C')]
        stale_skill = [_meta('A'), _meta('B'), _meta('C'), _meta('D')]
        self._set_state(queue=list(reordered), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        duration_ms=210000, started_at=server.time.time() - 180,
                        position_ms=180000, queue_web_dirty=True)

        resp = self._post_state_event(
            'started', video_id='B', queue=stale_skill, queue_index=1,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)

        # The stale track was skipped: the true next (D) was dispatched.
        self.assertEqual(self.dispatches, ['D'])
        # The now-playing state advanced directly to D.
        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], 'D')
        self.assertEqual(snap['queue_index'], 1)
        self.assertEqual([q['video_id'] for q in snap['queue']],
                         ['A', 'D', 'B', 'C'])
        # The stale B never became "playing".
        self.assertNotEqual(snap['video_id'], 'B')

    def test_no_skip_when_started_track_is_the_expected_next(self):
        # No reorder: the enqueued B really is the live queue's next.
        queue = [_meta('A'), _meta('B'), _meta('C')]
        self._set_state(queue=list(queue), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        queue_web_dirty=False)
        resp = self._post_state_event(
            'started', video_id='B', queue=list(queue), queue_index=1,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.dispatches,
                         msg="a normal auto-advance must not be treated as stale")
        self.assertEqual(server._get_now_playing()['video_id'], 'B')

    def test_no_skip_when_skill_window_already_matches(self):
        # The queue is web-dirty from an earlier action, but the skill's
        # snapshot matches the live order exactly, so a normal advance to B is
        # legitimate (the reorder has been absorbed) and must not be skipped.
        queue = [_meta('A'), _meta('B'), _meta('C')]
        self._set_state(queue=list(queue), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        queue_web_dirty=True)
        resp = self._post_state_event(
            'started', video_id='B', queue=list(queue), queue_index=1,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.dispatches,
                         msg="a matching skill snapshot clears the dirty marker; "
                             "a normal advance must not be skipped")
        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], 'B')
        self.assertFalse(snap['queue_web_dirty'])


class VoiceShuffleStillAuthoritative(ReorderAuthorityBase):
    """When the *skill* changed the order (a voice shuffle) and no web mutation
    happened since the last sync, its snapshot stays authoritative -- the guard
    must not break voice shuffle propagation to the UI."""

    def test_voice_shuffle_updates_queue_when_not_dirty(self):
        old = [_meta('A'), _meta('B'), _meta('C'), _meta('D')]
        shuffled = [_meta('A'), _meta('B'), _meta('D'), _meta('C')]
        self._set_state(queue=list(old), current_index=0, video_id='A',
                        playing=True, playback_confirmed=True,
                        queue_web_dirty=False)

        # The skill shuffled locally and reports its new order when C starts.
        resp = self._post_state_event(
            'started', video_id='C', queue=shuffled, queue_index=3,
            offset_in_ms=0, serial=self.SERIAL)
        self.assertEqual(resp.status_code, 200)

        snap = server._get_now_playing()
        self.assertEqual([q['video_id'] for q in snap['queue']],
                         ['A', 'B', 'D', 'C'],
                         msg="a voice shuffle must remain authoritative")
        self.assertEqual(snap['video_id'], 'C')
        self.assertEqual(snap['queue_index'], 3)
        self.assertFalse(snap['queue_web_dirty'])


if __name__ == '__main__':
    unittest.main()