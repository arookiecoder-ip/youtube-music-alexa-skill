"""Regression tests for silent playback after resuming a long pause.

Bug (reported): pause music for a long time, then resume — the progress bar
moves but no audio actually plays, until the track is restarted or skipped.

Root cause: `_confirm_stream_delivery()` is a fallback that marks
`playback_confirmed=True` as soon as `/proxy/` is hit (because Lambda's
`PlaybackStarted` webhook can be slower to arrive). It unconditionally called
`_reset_progress(0)`, re-anchoring the stored position to 0 regardless of what
it actually was.

`alexa_command`'s 'play' (resume) handler anchors `position_ms` to the frozen
pre-pause offset *before* dispatching the play command. On a warm cache,
`/proxy/` is hit almost immediately -- well before the webhook -- so
`_confirm_stream_delivery` fired first and clobbered that resume offset back to
0. The confirmation flag still flips to True, so the client's progress bar
starts ticking (now anchored at a wrong/stale position) while the actual
device resume offset and the server's bookkeeping have diverged.

Fix: `_confirm_stream_delivery` now re-anchors the *clock* (so the bar still
ticks) but preserves whatever position was already recorded, instead of
hardcoding 0. This is a no-op for a fresh play (which already anchors position
to 0 via `_update_now_playing`'s track-changed branch before /proxy/ can ever
be hit for that video_id) and fixes the resume case.

Run with ``pytest test_resume_after_pause.py`` or
``python -m unittest test_resume_after_pause``.
"""
import os
import sys
import types
import unittest


_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-resume-after-pause",
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


class _CleanNowPlayingState(unittest.TestCase):
    """Isolate the module-level now_playing dict between tests."""

    def setUp(self):
        with server._np_lock:
            self._saved = dict(server._now_playing)

    def tearDown(self):
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update(self._saved)


class ConfirmStreamDeliveryPreservesResumeOffset(_CleanNowPlayingState):
    def test_resume_offset_is_preserved_not_reset_to_zero(self):
        """The exact reported bug: resuming a long pause must not snap the
        stored anchor back to 0 when the cached-audio /proxy/ hit confirms
        before Lambda's webhook does."""
        with server._np_lock:
            server._now_playing.update({
                'video_id': 'RESUME000001',
                'playing': False,
                'playback_confirmed': False,
                'position_ms': 125_000,  # resumed from 2:05 into the track
                'duration_ms': 240_000,
                'queue': [],
            })
        server._confirm_stream_delivery('RESUME000001')
        with server._np_lock:
            self.assertEqual(server._now_playing['position_ms'], 125_000,
                             msg="resume offset was clobbered back to 0")
            self.assertTrue(server._now_playing['playback_confirmed'])
            self.assertTrue(server._now_playing['playing'])

    def test_fresh_play_still_starts_at_zero(self):
        """A brand new track (position already 0 from _update_now_playing's
        track-changed branch) must not regress -- confirming it is still a
        no-op at position 0."""
        with server._np_lock:
            server._now_playing.update({
                'video_id': 'FRESH0000001',
                'playing': False,
                'playback_confirmed': False,
                'position_ms': 0,
                'duration_ms': 200_000,
                'queue': [],
            })
        server._confirm_stream_delivery('FRESH0000001')
        with server._np_lock:
            self.assertEqual(server._now_playing['position_ms'], 0)
            self.assertTrue(server._now_playing['playback_confirmed'])

    def test_reanchors_the_clock_so_the_bar_still_ticks(self):
        """Preserving position must not mean freezing it: started_at has to
        move to "now" so _computed_position_ms keeps advancing from here,
        not from whenever the anchor was last set (which could be seconds
        ago, mid dispatch)."""
        stale_started_at = 1000.0
        with server._np_lock:
            server._now_playing.update({
                'video_id': 'CLOCK0000001',
                'playing': False,
                'playback_confirmed': False,
                'position_ms': 60_000,
                'started_at': stale_started_at,
                'duration_ms': 200_000,
                'queue': [],
            })
        server._confirm_stream_delivery('CLOCK0000001')
        with server._np_lock:
            self.assertGreater(server._now_playing['started_at'], stale_started_at,
                               msg="clock anchor was not refreshed to now")

    def test_wrong_video_id_is_ignored(self):
        """Confirmation for a track that isn't current any more (user moved on
        while the stale cold-cache stream was still resolving) must not touch
        the now-playing state at all."""
        with server._np_lock:
            server._now_playing.update({
                'video_id': 'CURRENT00001',
                'playing': True,
                'playback_confirmed': True,
                'position_ms': 42_000,
                'duration_ms': 200_000,
                'queue': [],
            })
        server._confirm_stream_delivery('SOME_OTHER_ID')
        with server._np_lock:
            self.assertEqual(server._now_playing['position_ms'], 42_000)
            self.assertEqual(server._now_playing['video_id'], 'CURRENT00001')

    def test_invalid_video_id_does_not_raise(self):
        # A malformed id (e.g. from a forged/garbled call) must be a no-op,
        # never a crash.
        server._confirm_stream_delivery(None)
        server._confirm_stream_delivery('')
        server._confirm_stream_delivery('has spaces')
        server._confirm_stream_delivery('a' * 200)  # too long

    def test_already_confirmed_does_not_move_position(self):
        """Redundant confirmations (duplicate /proxy/ hits for the same warm
        track) must not re-run the anchor logic at all -- only backfill
        duration if it was missing."""
        with server._np_lock:
            server._now_playing.update({
                'video_id': 'ALREADY00001',
                'playing': True,
                'playback_confirmed': True,
                'position_ms': 77_000,
                'duration_ms': 0,
                'queue': [{'video_id': 'ALREADY00001', 'duration_ms': 180_000}],
            })
        server._confirm_stream_delivery('ALREADY00001')
        with server._np_lock:
            self.assertEqual(server._now_playing['position_ms'], 77_000,
                             msg="already-confirmed branch must not touch position")
            self.assertEqual(server._now_playing['duration_ms'], 180_000,
                             msg="duration should still backfill from the queue")

    def test_missing_position_ms_key_defaults_safely(self):
        """Defensive: if position_ms is somehow absent from the dict (should
        not happen in practice, but _confirm_stream_delivery must not KeyError)."""
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update({
                'video_id': 'NOPOSKEY0001',
                'playing': False,
                'playback_confirmed': False,
                'duration_ms': 100_000,
                'queue': [],
            })
        server._confirm_stream_delivery('NOPOSKEY0001')
        with server._np_lock:
            self.assertEqual(server._now_playing.get('position_ms', 0), 0)
            self.assertTrue(server._now_playing['playback_confirmed'])


class ResumeDispatchAnchorsBeforeConfirmation(_CleanNowPlayingState):
    """Integration-style check of the two pieces working together: the resume
    handler's anchor call followed by a simulated fast /proxy/ confirmation."""

    def test_resume_then_immediate_confirm_keeps_resume_position(self):
        with server._np_lock:
            server._now_playing.update({
                'video_id': 'INTEG0000001',
                'playing': False,
                'playback_confirmed': False,
                'position_ms': 0,
                'duration_ms': 180_000,
                'queue': [],
            })
        # Simulate what alexa_command's 'play' branch does on resume: freeze
        # the anchor at the previously-paused offset before dispatch.
        with server._np_lock:
            server._reset_progress(90_000)
            server._now_playing['playing'] = False
            server._now_playing['playback_confirmed'] = False

        # Warm cache: /proxy/ resolves near-instantly, well before the
        # Lambda webhook would arrive.
        server._confirm_stream_delivery('INTEG0000001')

        with server._np_lock:
            pos = server._computed_position_ms()
        # Immediately after confirmation the elapsed wall-clock time is
        # ~0, so computed position should still read essentially 90s.
        self.assertGreaterEqual(pos, 90_000)
        self.assertLess(pos, 91_000, msg="resume position drifted far from "
                                         "the frozen pre-pause offset")


if __name__ == "__main__":
    unittest.main()
