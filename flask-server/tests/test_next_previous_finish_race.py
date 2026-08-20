"""Regression tests for the next/previous click race against song-end.

Bug (reported 2026-08-20): clicking next or previous, the moment the current
song ends and the player is about to start the next, Alexa fires
AudioPlayer.PlaybackFailed with `MEDIA_ERROR_UNKNOWN` / 'Device playback error'
and playback stops.

What this exercises: the full server-side state machine around a transport
click that lands while (or just before) the currently playing song finishes
naturally. Each scenario models one variant of the user-visible crash:

  1. The click arrives while the current track is still playing (no webhook
     yet) — Flask should dispatch the new song and stage the now-playing
     update, without losing the old track's confirmation context.

  2. The click arrives and PlaybackFinished fires before the dispatched
     trigger can arm-rewrite the alive slot — Flask must keep the queued
     `finished` event from clobbering the freshly armed next song.

  3. The click arrives during the PlaybackNearlyFinished auto-enqueue window
     (last ~30s of the current song) — the auto-enqueue token the skill sent
     and the click's arm-driven PlayDirective must agree on whose previous-
     token to expect, otherwise the AudioPlayer rejects the directive.

The reproduction does not touch the live yt-dlp /alexa /proxy pipeline; it
fakes the Lambda webhooks and the Alexa dispatch so the server's bookkeeping
flows can be observed directly. If the server loses the song, double-arms,
or mismatches the token, one of the assertions below fails first.
"""
import copy
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock


_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-next-previous-race",
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
import server  # noqa: E402 — only after the stubs are installed


def _make_queue(*video_ids_with_meta):
    out = []
    for vid, title, dur in video_ids_with_meta:
        out.append({
            'video_id': vid,
            'title': title,
            'artist': 'Test Artist',
            'thumbnail': '',
            'duration_ms': dur,
        })
    return out


class _BaseScenario(unittest.TestCase):
    """Common scaffolding: clean state, install fakes for the dispatched
    trigger, install a deterministic alarm path that returns immediately.
    """

    SERIAL = 'ECHO-AAA1'

    def setUp(self):
        server._reset_rate_limit_cooldown()
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()
        with server._stream_inflight_lock:
            server._stream_inflight.clear()
        with server._download_queue_lock:
            server._download_queue_depth = 0
        with server._PENDING_DISPATCH_LOCK:
            server._PENDING_DISPATCH.clear()
        with server._TRIGGER_INFLIGHT_LOCK:
            server._TRIGGER_INFLIGHT.clear()
        with server._ARMED_PLAYS_LOCK:
            server._ARMED_PLAYS.clear()
        with server._np_lock:
            server._saved_now_playing = dict(server._now_playing)
            server._now_playing.clear()
        self.triggers = []
        self.ensure_downloads = []
        self._patches = []
        self.addCleanup(self._restore_now_playing)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._patches.clear()

    def _restore_now_playing(self):
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update(server._saved_now_playing)

    def _patch(self, target, attr, replacement=None, **kwargs):
        # mock.patch.object only needs `(target, attr)` if the attribute
        # itself can be replaced with Mock(); for the cases below we pass an
        # explicit replacement (a function or a sentinel).
        if replacement is not None:
            p = mock.patch.object(target, attr, replacement)
        else:
            p = mock.patch.object(target, attr, **kwargs)
        self._patches.append(p)
        p.start()

    def _record_trigger(self, serial, video_id, offset_ms=0):
        self.triggers.append((serial, video_id, offset_ms))
        return None

    def _record_download(self, video_id, **kwargs):
        self.ensure_downloads.append(video_id)
        return None

    def _install_dispatch_fakes(self):
        # Capture every dispatch + every prewarm so we can assert later.
        self._patch(server.alexa_remote.remote, 'play_video_id',
                    self._record_trigger)
        self._patch(server, '_arm_play', lambda s, v, o=0: None)
        # Both direct `Supporting.ensure_downloaded` calls (legacy path)
        # and the wrapper `_ensure_audio_ready_for_play` flow into the same
        # recorder, so a regression that swaps one for the other still hits
        # the test's asserts.
        self._patch(server.Supporting, 'ensure_downloaded',
                    self._record_download)
        self._patch(server, '_ensure_audio_ready_for_play',
                    lambda video_id, wait=False, generation=None,
                    prefetch=False: self._record_download(video_id))
        # Make `_schedule_play_dispatch` fire synchronously instead of
        # waiting PLAY_DISPATCH_DEBOUNCE (0.7s) -- the test assertion needs
        # to observe the dispatch in the same turn as the click.
        original_arm = server._arm_play

        def _scheduler(serial, video_id, offset_ms=0, delay=None):
            server._note_trigger_sent(serial)
            self._record_trigger(serial, video_id, offset_ms)
        self._patch(server, '_schedule_play_dispatch', _scheduler)
        self._patch(server, '_watch_playback_confirmation',
                    lambda *a, **kw: None)
        self._patch(server, '_notify_sse')
        self._patch(server, '_record_listen')
        self._patch(server, '_lookup_and_update_np')

    def _set_state(self, **fields):
        with mock.patch.object(server, '_notify_sse'):
            server._update_now_playing(**fields)

    def _post_command(self, action, **body):
        payload = {'serial': self.SERIAL, 'action': action}
        payload.update(body)
        return server.app.test_client().post(
            '/alexa/command/?key=' + server.API_KEY,
            json=payload,
        )

    def _post_state_event(self, event, **fields):
        payload = {'event': event}
        payload.update(fields)
        return server.app.test_client().post(
            '/alexa/state_event/?key=' + server.API_KEY,
            json=payload,
        )


class NextClickMidSongStagesNewTrack(_BaseScenario):
    """Clicking next while the current song still plays must:

      1. dispatch the next song's video_id to Alexa (one and only one trigger),
      2. start a prewarm ensure_downloaded so /proxy/ has warm cache when
         the device starts streaming,
      3. move queue_index forward in the now-playing snapshot so the UI knows
         which song is about to play next.

    A regression here (a missed dispatch, double dispatch, or a wedged
    playback_confirmed flag) is what surfaces as MEDIA_ERROR_UNKNOWN on the
    device: the device either never receives a directive to swap streams or
    receives one for the wrong track and rejects it.
    """

    def test_next_click_dispatches_and_stages_new_track(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
            ('NEXTSONG0002', 'Next B', 240_000),
        )
        self._set_state(
            video_id='CURRENT00001', queue_index=0, playing=True,
            playback_confirmed=True, position_ms=175_000,
            started_at=server.time.time() - 175,
            duration_ms=210_000, queue=copy.deepcopy(queue),
        )
        self._install_dispatch_fakes()

        response = self._post_command('next')
        self.assertEqual(response.status_code, 200,
                         msg="/alexa/command/?action=next must succeed even "
                             "while the current track is still playing")

        self.assertEqual(self.triggers,
                         [(self.SERIAL, 'NEXTSONG0001', 0)],
                         msg="next-button click must dispatch exactly one "
                             "trigger for the queue's next video_id")
        self.assertEqual(self.ensure_downloads, ['NEXTSONG0001'],
                         msg="next must prewarm the song it dispatches, so "
                             "the device's /proxy/ fetch has warm cache")

        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], 'NEXTSONG0001',
                         msg="now-playing must stage the dispatched song, "
                             "not stay wedged on the still-current track")
        self.assertEqual(snap['queue_index'], 1)
        self.assertFalse(snap['playback_confirmed'],
                         msg="a brand-new track must show as unconfirmed; "
                             "PlaybackStarted is the only authoritative "
                             "confirmation, and it has not fired yet")


class PreviousClickMidSongStagesPreviousTrack(_BaseScenario):
    """Same staging expectations apply to `previous`. The previous button
    moves the queue index back: while song B is playing (idx=2), previous
    should drop the queue_index to 1 (A) and dispatch A."""

    def test_previous_click_dispatches_and_stages_previous_track(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
            ('NEXTSONG0002', 'Next B', 240_000),
        )
        self._set_state(
            video_id='NEXTSONG0002', queue_index=2, playing=True,
            playback_confirmed=True, position_ms=60_000,
            started_at=server.time.time() - 60,
            duration_ms=240_000, queue=copy.deepcopy(queue),
        )
        self._install_dispatch_fakes()

        response = self._post_command('previous')
        self.assertEqual(response.status_code, 200,
                         msg="previous while still playing must succeed")

        self.assertEqual(self.triggers, [(self.SERIAL, 'NEXTSONG0001', 0)])
        self.assertEqual(self.ensure_downloads, ['NEXTSONG0001'])

        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], 'NEXTSONG0001')
        self.assertEqual(snap['queue_index'], 1)
        self.assertFalse(snap['playback_confirmed'])


class ClickedThenNaturalEndDoesNotWedgeState(_BaseScenario):
    """The exact user-visible race: click next while the song is playing,
    then the song ends naturally before the dispatched trigger rounds out.

    After both events, the now-playing snapshot must point at the *new* song
    and be marked unconfirmed (the device hasn't received PlaybackStarted
    yet from the next track) -- not be stuck on the just-finished track. If
    the server stale-writes video_id back to the finished song, the device
    starts streaming the URL it already finished and either loops in silence
    or fires PlaybackFailed with MEDIA_ERROR_UNKNOWN on reconnect.
    """

    def test_clicked_then_finished_keeps_new_track_selected(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
        )
        self._set_state(
            video_id='CURRENT00001', queue_index=0, playing=True,
            playback_confirmed=True, position_ms=200_000,
            started_at=server.time.time() - 200,
            duration_ms=210_000, queue=copy.deepcopy(queue),
        )
        self._install_dispatch_fakes()

        response = self._post_command('next')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.triggers, [(self.SERIAL, 'NEXTSONG0001', 0)])

        # The web remote now displays A as about-to-play. The Echo is still
        # streaming the last few seconds of Current. Current finishes
        # naturally, the skill's PlaybackFinished event fires, and Flask
        # receives it via /alexa/state_event/.
        finished = self._post_state_event(
            'finished', video_id='CURRENT00001', serial=self.SERIAL,
        )
        self.assertEqual(finished.status_code, 200,
                         msg="finished webhook from the skill must be "
                             "accepted; a 5xx here strands the device")

        snap = server._get_now_playing()
        self.assertEqual(
            snap['video_id'], 'NEXTSONG0001',
            msg="PlaybackFinished for the OLD track must not revert "
                "now-playing to Current: the user's Next click is the "
                "freshest signal about what is about to play")
        self.assertEqual(snap['queue_index'], 1)
        # playing=False / not-confirmed is the correct "awaiting device
        # confirmation for the new track" shape. If this ever flips to
        # True with a fake id, /proxy/ will pull the wrong audio and the
        # device will surface MEDIA_ERROR_UNKNOWN.
        self.assertFalse(snap['playing'])
        self.assertFalse(snap['playback_confirmed'])


class StartedForNewTrackConfirmsAfterClick(_BaseScenario):
    """After the user clicks next and the device catches up, the
    PlaybackStarted webhook for the *new* track arrives. That is the moment
    playback_confirmed flips to True; before it, the UI shows the
    processing spinner. A regressed flow that confirms using the OLD
    track's id will pull the wrong audio URL on /proxy/ and the device
    may blow up on the music endpoint.
    """

    def test_started_webhook_moves_confirmation_to_dispatched_track(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
        )
        self._set_state(
            video_id='CURRENT00001', queue_index=0, playing=True,
            playback_confirmed=True, position_ms=200_000,
            started_at=server.time.time() - 200,
            duration_ms=210_000, queue=copy.deepcopy(queue),
        )
        self._install_dispatch_fakes()

        self._post_command('next')
        self.assertEqual(server._now_playing['video_id'], 'NEXTSONG0001')

        started = self._post_state_event(
            'started', video_id='NEXTSONG0001',
            queue=[
                {'video_id': 'CURRENT00001', 'title': 'Current',
                 'artist': 'Test Artist', 'thumbnail': '',
                 'duration_ms': 210_000},
                {'video_id': 'NEXTSONG0001', 'title': 'Next A',
                 'artist': 'Test Artist', 'thumbnail': '',
                 'duration_ms': 180_000},
            ],
            queue_index=1,
            offset_in_ms=0,
        )
        self.assertEqual(started.status_code, 200)

        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], 'NEXTSONG0001')
        self.assertEqual(snap['queue_index'], 1)
        self.assertTrue(snap['playback_confirmed'],
                        msg="the proper started webhook must mark the new "
                            "track confirmed; otherwise /proxy/ is reading "
                            "the cached row for the old song")
        self.assertTrue(snap['playing'])


class ClickedNextBeforeStartedIsResilientToLateFinished(_BaseScenario):
    """The exact crash scenario from the bug report: click next right as the
    current song ends.

    Timeline being modeled:
      1. Current is playing and the user decides to skip it; they click
         Next.
      2. Player arms/fires the next song.
      3. Current finishes naturally — the PlaybackFinished webhook arrives
         in the same window as the click acknowledgement.
      4. Server must NOT lose the click's identity in step 2.

    If the server overwrites video_id back to Current from the late
    finished webhook, the Echo's AudioPlayer state gets confused: the queued
    next-track PlayDirective (from the click) tries to land its stream on
    the Echo but the URL the server fetched earlier with the old video_id is
    no longer the right audio, and MEDIA_ERROR_UNKNOWN fires.
    """

    def test_finished_for_old_track_does_not_clobber_clicked_next(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
            ('NEXTSONG0002', 'Next B', 240_000),
        )
        self._set_state(
            video_id='CURRENT00001', queue_index=0, playing=True,
            playback_confirmed=True, position_ms=209_000,
            started_at=server.time.time() - 209,
            duration_ms=210_000, queue=copy.deepcopy(queue),
        )
        self._install_dispatch_fakes()

        response = self._post_command('next')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.triggers, [(self.SERIAL, 'NEXTSONG0001', 0)])

        # Now the song is essentially over. The skill's webhook for
        # PlaybackFinished arrives here. If the server overwrites
        # `_now_playing.video_id` back to the finished track, the click is
        # silently undone.
        finished = self._post_state_event(
            'finished', video_id='CURRENT00001', serial=self.SERIAL,
        )
        self.assertEqual(finished.status_code, 200)

        snap = server._get_now_playing()
        self.assertNotEqual(
            snap['video_id'], 'CURRENT00001',
            msg="PlaybackFinished for Current must not reclaim the "
                "now-playing slot when the user's click already moved it "
                "to NEXTSONG0001")
        self.assertEqual(snap['video_id'], 'NEXTSONG0001',
                         msg="the dispatched next-track selection must "
                             "survive the late PlaybackFinished webhook")
        self.assertEqual(snap['queue_index'], 1)
        # The cached pre-fetch record (set when Echo fetched the next
        # track) is allowed to be present, but never for the old track.
        if server._prefetched_next:
            self.assertNotEqual(
                server._prefetched_next.get('video_id'), 'CURRENT00001',
                msg="the prefetched-next slot must not be polluted with "
                    "the just-finished track's id")


class NextClickDispatchesThroughSchedulePlayDispatch(_BaseScenario):
    """The transport buttons must fire the trigger through
    `_schedule_play_dispatch` -- the same path `alexa_play_queue` uses
    (debouncing + `_trigger_in_flight` shortcut + single confirmation
    watchdog). Going direct through `_dispatch_play_with_retry` lets a
    duplicate request during the song-end transition stack a second
    REPLACE_ALL on top of the first; the second one lands while the
    device is still swapping from the auto-enqueued next track to the
    dispatched one, and Alexa's AudioPlayer rejects the directive with
    'Device playback error' / MEDIA_ERROR_UNKNOWN.
    """

    def test_next_click_routes_through_scheduler(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
            ('NEXTSONG0002', 'Next B', 240_000),
        )
        self._set_state(
            video_id='CURRENT00001', queue_index=0, playing=True,
            playback_confirmed=True, position_ms=200_000,
            started_at=server.time.time() - 200,
            duration_ms=210_000, queue=copy.deepcopy(queue),
        )

        # Capture every call into the dispatch *scheduler* so we can
        # assert the click reached it.
        scheduler_calls = []

        def spy(serial, video_id, offset_ms=0, delay=None):
            scheduler_calls.append((serial, video_id, offset_ms))
            # Mark in-flight so the watchdogs behave realistically even
            # though we're not actually firing the trigger phrase.
            server._note_trigger_sent(serial)

        # Patch first, then install the dispatch fakes (which themselves
        # patch `_schedule_play_dispatch`); the latest patch wins, so we
        # re-patch the spy AFTER the install so we keep the call log.
        self._install_dispatch_fakes()
        self._patch(server, '_schedule_play_dispatch', spy)

        response = self._post_command('next')
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            scheduler_calls, [(self.SERIAL, 'NEXTSONG0001', 0)],
            msg="next-button click must route through "
                "_schedule_play_dispatch (the same path "
                "alexa_play_queue uses) so debouncing, in-flight "
                "tracking, and the watchdog-retry contract apply. "
                "Currently the click bypasses the scheduler entirely; "
                "that is what produces the song-end MEDIA_ERROR_UNKNOWN "
                "crash. Got: %r" % (scheduler_calls,))


class PreviousClickDispatchesThroughSchedulePlayDispatch(_BaseScenario):
    """Same expectations apply to the previous-button click path."""

    def test_previous_click_routes_through_scheduler(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
            ('NEXTSONG0002', 'Next B', 240_000),
        )
        self._set_state(
            video_id='NEXTSONG0002', queue_index=2, playing=True,
            playback_confirmed=True, position_ms=60_000,
            started_at=server.time.time() - 60,
            duration_ms=240_000, queue=copy.deepcopy(queue),
        )

        scheduler_calls = []

        def spy(serial, video_id, offset_ms=0, delay=None):
            scheduler_calls.append((serial, video_id, offset_ms))
            server._note_trigger_sent(serial)

        self._install_dispatch_fakes()
        self._patch(server, '_schedule_play_dispatch', spy)

        response = self._post_command('previous')
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            scheduler_calls, [(self.SERIAL, 'NEXTSONG0001', 0)],
            msg="previous-button click must also route through "
                "_schedule_play_dispatch; the previous-side bypass is "
                "the same regression. Got: %r" % (scheduler_calls,))


class NextClickUsesGenerationTrackedPrewarm(_BaseScenario):
    """The transport buttons must thread the post-update playback
    generation into the prewarm, the same way `alexa_play_queue` does.
    The generation check inside `ensure_downloaded` then observes a
    follow-up click's bump and drops this prewarm instead of holding
    yt-dlp slots for a track the user already moved past -- the
    remaining race is what lets a cold-cache /proxy/ stream return
    partially-cached bytes to the Echo at song-end and surface as
    MEDIA_ERROR_UNKNOWN.
    """

    def test_next_click_prewarm_is_generation_scoped(self):
        queue = _make_queue(
            ('CURRENT00001', 'Current', 210_000),
            ('NEXTSONG0001', 'Next A', 180_000),
            ('NEXTSONG0002', 'Next B', 240_000),
        )
        self._set_state(
            video_id='CURRENT00001', queue_index=0, playing=True,
            playback_confirmed=True, position_ms=200_000,
            started_at=server.time.time() - 200,
            duration_ms=210_000, queue=copy.deepcopy(queue),
        )

        # Capture every prewarm call (kwargs show whether a generation was
        # threaded through; the legacy code passes none).
        prewarm_kwargs = []

        def spy(video_id, wait=False, generation=None, prefetch=False):
            prewarm_kwargs.append({
                'video_id': video_id,
                'generation': generation,
                'prefetch': prefetch,
            })
            return None

        self._install_dispatch_fakes()
        self._patch(server, '_ensure_audio_ready_for_play', spy)

        response = self._post_command('next')
        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(prewarm_kwargs), 1,
                         msg="exactly one prewarm must run for the clicked "
                             "song; got: %r" % (prewarm_kwargs,))
        kwargs = prewarm_kwargs[0]
        self.assertEqual(kwargs['video_id'], 'NEXTSONG0001')
        self.assertIsNotNone(
            kwargs['generation'],
            msg="next-button click must thread the post-update playback "
                "generation into the prewarm so a stale previous-track "
                "download can be told to drop instead of competing with "
                "the new audio for yt-dlp slots at song-end")
        self.assertEqual(
            kwargs['generation'], server._current_playback_generation(),
            msg="the prewarm's generation must be the *current* "
                "generation captured AFTER the now-playing update, so a "
                "later click's bump makes the old prewarm drop via the "
                "existing generation guard inside ensure_downloaded")


if __name__ == '__main__':
    unittest.main()
