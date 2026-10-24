"""Regression tests for the cascading-429 failure caused by rapid song clicks.

Bug: clicking song A, then B, then C in quick succession made *no* song play and
filled the log with `HTTP Error 429: Too Many Requests` for every yt-dlp client.

Each click independently started a foreground download, a cold-cache `/proxy/`
stream, and up to 8 queue prewarms, none of which were cancelled when the user
moved on. Three clicks put ~30 yt-dlp processes on YouTube at once, earning the
server's IP a 429. Every client fallback then failed, the 429-induced
"This video is not available" wording poisoned `_dead_video_ids` for an hour,
the playback watchdog could not see streaming downloads so it resent play
commands (spawning yet more duplicate yt-dlp processes for one song), and eight
abandoned `/proxy/` streams sat on waitress worker threads for over 150 s each,
starving every other request.

The five fixes under test here:

1. `_playback_generation` — superseded prewarms/downloads abandon themselves.
2. Superseded `/proxy/` streams are killed and their worker thread freed.
3. Concurrent `/proxy/` requests for one video_id share a single yt-dlp process,
   and `_download_in_progress` can see streaming downloads.
4. HTTP 429 is transient: it aborts the remaining client fallbacks, opens a
   cooldown, and never marks a video permanently dead.
5. `_download_backpressure()` is actually consulted by the prewarm path.

Run with ``pytest flask-server/tests/test_download_backpressure.py`` or
``python -m unittest discover -s flask-server/tests -p 'test_download_backpressure.py'``.
"""
import os
import sys

# Tests live in flask-server/tests/, while application modules remain in the flask-server parent directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import copy
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

# Stub the heavy native deps BEFORE importing server.py, matching the
# convention in test_routing_paste_url.py. The real ytmusicapi/alexapy packages
# are not pip-installable in our test environment and the YouTube Browser
# sidecar cannot run in CI. `setdefault` leaves a neighbouring test file's env
# untouched when the module is already cached.

_TEST_ENV = {
    "SECRET_KEY": "test-secret-for-download-backpressure",
    "REMOTE_USER": "test-owner",
    "REMOTE_PASSWORD": "test-pass",
    # Any non-empty value avoids server.py's auto-generate-and-write-to-disk
    # api_key.txt path, which would litter the working directory on first run.
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
        # Prefer the real module (no import-time side effects); only fall back
        # to a stub if it genuinely cannot be imported. Unconditionally stubbing
        # it would poison sys.modules for test_youtube_browser_session.py.
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


class _CleanServerState(unittest.TestCase):
    """Reset the module-level download/playback state around every test.

    server.py keeps this state in module globals (the real process has exactly
    one of each), so tests must not leak it into each other.
    """

    def setUp(self):
        server._playback_failover_budget.reset()
        server._reset_rate_limit_cooldown()
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        server._playback_failover_budget.reset()
        server._reset_rate_limit_cooldown()
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()


# --------------------------------------------------------------------------
# Fix 4: HTTP 429 is a property of our IP, not of the video
# --------------------------------------------------------------------------

# Verbatim yt-dlp stderr from the incident. Note that the *only* real signal is
# the 429 on the first line: everything after it is a downstream symptom of not
# being able to fetch the watch page at all.
_STDERR_429_LOOKS_UNAVAILABLE = (
    "WARNING: [youtube] hhstADmzpqM: Unable to download webpage: "
    "HTTP Error 429: Too Many Requests (caused by <HTTPError 429: Too Many Requests>)\n"
    "ERROR: [youtube] hhstADmzpqM: Video unavailable. This video is not available"
)

_STDERR_429_PLAIN = (
    "WARNING: [youtube] 4VwtfInG-LU: Unable to download webpage: "
    "HTTP Error 429: Too Many Requests (caused by <HTTPError 429: Too Many Requests>)\n"
    "ERROR: [youtube] 4VwtfInG-LU: Requested format is not available."
)

# The 429 as a *recoverable warning* — verbatim from the incident. yt-dlp could
# not fetch the watch page but went on to extract via innertube, and the run
# ultimately died on the bot check. The next client profile is exactly what
# recovers from that, so this must NOT cut the fallback chain short.
_STDERR_429_WARNING_THEN_BOT_CHECK = (
    "WARNING: [youtube] 4VwtfInG-LU: Unable to download webpage: "
    "HTTP Error 429: Too Many Requests (caused by <HTTPError 429: Too Many Requests>)\n"
    "WARNING: [youtube] No title found in player responses; falling back to "
    "title from initial data. Other metadata may also be missing\n"
    "ERROR: [youtube] 4VwtfInG-LU: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)

# The 429 as the *fatal* error: nothing else to try, every further client
# request only deepens the block.
_STDERR_429_FATAL = (
    "ERROR: [youtube] 4VwtfInG-LU: Unable to download webpage: "
    "HTTP Error 429: Too Many Requests (caused by <HTTPError 429: Too Many Requests>)"
)

_STDERR_REALLY_DEAD = (
    "ERROR: [youtube] abcdefghijk: Video unavailable. This video is not available"
)

_STDERR_BOT_CHECK = (
    "ERROR: [youtube] abcdefghijk: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)

# Verbatim-style yt-dlp stderr for a transient/extractor-side failure that is
# neither a 429 nor a recognized permanent-unavailable string: a TLS reset
# (VPN blip / YouTube edge reset) and YouTube's SABR-only rollout leaving no
# downloadable URL for a given client. Neither implies the video is gone.
_STDERR_SSL_RESET = (
    "ERROR: \n[download] Got error: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF "
    "occurred in violation of protocol (_ssl.c:1010). Giving up after 10 retries"
)
_STDERR_SABR_NO_FORMAT = (
    "WARNING: [youtube] flakyvideoid: Some tv client https formats have been "
    "skipped as they are missing a URL. YouTube may have enabled the SABR-only "
    "streaming experiment for your account.\n"
    "ERROR: [youtube] flakyvideoid: Requested format is not available. Use "
    "--list-formats for a list of available formats"
)


class RateLimitClassification(_CleanServerState):
    def test_429_is_detected_anywhere(self):
        for stderr in (_STDERR_429_PLAIN, _STDERR_429_LOOKS_UNAVAILABLE,
                       _STDERR_429_WARNING_THEN_BOT_CHECK, _STDERR_429_FATAL):
            self.assertTrue(server._rate_limit_warning_present(stderr))

    def test_only_an_error_line_429_is_fatal(self):
        """The distinction that keeps the client-fallback chain intact.

        yt-dlp warns about a 429 on the watch page and then extracts via
        innertube all the time; the run's real cause of death is on the ERROR
        line. Treating a warning as fatal stopped us trying android_vr/web/tv,
        which is what actually recovers from a bot check.
        """
        self.assertTrue(server._is_rate_limited_error(_STDERR_429_FATAL))
        self.assertFalse(
            server._is_rate_limited_error(_STDERR_429_WARNING_THEN_BOT_CHECK),
            msg="a recoverable 429 warning was treated as fatal — this cuts the "
                "client-profile fallback chain short")
        self.assertFalse(server._is_rate_limited_error(_STDERR_429_PLAIN))

    def test_non_429_errors_are_not_rate_limits(self):
        for stderr in (_STDERR_REALLY_DEAD, _STDERR_BOT_CHECK, "", None):
            self.assertFalse(server._is_rate_limited_error(stderr))
            self.assertFalse(server._rate_limit_warning_present(stderr))

    def test_429_wording_is_not_permanent_unavailability(self):
        """The core of the hour-long outage: a 429 that happens to *say*
        "This video is not available" must never be classified as permanent."""
        self.assertFalse(
            server._is_video_permanently_unavailable(_STDERR_429_LOOKS_UNAVAILABLE),
            msg="429-induced 'not available' wording was treated as permanent — "
                "this poisons _dead_video_ids and makes a healthy song "
                "unplayable for _DEAD_VIDEO_TTL")

    def test_genuine_unavailability_still_detected(self):
        self.assertTrue(server._is_video_permanently_unavailable(_STDERR_REALLY_DEAD))

    def test_bot_check_still_not_permanent(self):
        # Guards the pre-existing invariant documented in server.py.
        self.assertFalse(server._is_video_permanently_unavailable(_STDERR_BOT_CHECK))


class RateLimitCooldown(_CleanServerState):
    def test_cooldown_starts_inactive(self):
        self.assertFalse(server._ytdlp_rate_limited())
        self.assertEqual(server._ytdlp_cooldown_remaining(), 0.0)

    def test_note_rate_limited_opens_window(self):
        server._note_rate_limited("vid123")
        self.assertTrue(server._ytdlp_rate_limited())
        self.assertGreater(server._ytdlp_cooldown_remaining(), 0)
        self.assertLessEqual(server._ytdlp_cooldown_remaining(),
                             server._YTDLP_COOLDOWN_SECONDS)

    def test_cooldown_is_never_shortened(self):
        server._note_rate_limited("vid123")
        first = server._ytdlp_cooldown_remaining()
        with mock.patch.object(server, "_YTDLP_COOLDOWN_SECONDS", 1.0):
            server._note_rate_limited("vid456")
        self.assertGreaterEqual(
            server._ytdlp_cooldown_remaining(), first - 1.0,
            msg="a second 429 shortened the cooldown — still being throttled "
                "must never mean 'resume sooner'")

    def test_expired_cooldown_is_inactive(self):
        server._note_rate_limited("vid123")
        server._ytdlp_cooldown_until = time.time() - 1
        self.assertFalse(server._ytdlp_rate_limited())


class _FakeCompleted:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class _SemaphoreSpy:
    """Counting stand-in for `_download_semaphore`.

    Lets a test assert that a suppressed download never even queued for a
    permit, without occupying the real semaphore (which would deadlock the test
    process if the suppression regressed, instead of failing it).
    """

    def __init__(self, value):
        self._sem = threading.Semaphore(value)
        self.acquires = 0
        self.releases = 0

    def acquire(self, blocking=True, timeout=None):
        self.acquires += 1
        return self._sem.acquire(blocking, timeout)

    def release(self):
        self.releases += 1
        self._sem.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False




# --------------------------------------------------------------------------
# Cold-stream fallback latency
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Fix 1: playback generation token
# --------------------------------------------------------------------------

class PlaybackGeneration(_CleanServerState):
    def test_bump_increments_monotonically(self):
        first = server._current_playback_generation()
        second = server._bump_playback_generation()
        self.assertEqual(second, first + 1)
        self.assertEqual(server._current_playback_generation(), second)

    def test_none_generation_is_never_superseded(self):
        """A foreground download passes generation=None and must never cancel."""
        server._bump_playback_generation()
        self.assertFalse(server._generation_superseded(None))

    def test_current_generation_is_not_superseded(self):
        gen = server._current_playback_generation()
        self.assertFalse(server._generation_superseded(gen))

    def test_stale_generation_is_superseded(self):
        gen = server._current_playback_generation()
        server._bump_playback_generation()
        self.assertTrue(server._generation_superseded(gen))

    def test_track_change_bumps_generation(self):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="trackAAAAAAA")
            before = server._current_playback_generation()
            server._update_now_playing(video_id="trackBBBBBBB")
            after = server._current_playback_generation()
        self.assertEqual(after, before + 1,
                         msg="clicking a new song must invalidate the previous "
                             "track's background download work")

    def test_same_track_update_does_not_bump(self):
        """Progress/metadata updates for the current track must not cancel its
        own in-flight download."""
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="trackAAAAAAA")
            before = server._current_playback_generation()
            server._update_now_playing(video_id="trackAAAAAAA", position_ms=5000)
            server._update_now_playing(playing=True)
            after = server._current_playback_generation()
        self.assertEqual(after, before)





# --------------------------------------------------------------------------
# Fix 5: backpressure is actually consulted
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Fix 3: one yt-dlp per video_id, and the watchdog can see streaming downloads
# --------------------------------------------------------------------------





# --------------------------------------------------------------------------
# Fix 2: superseded /proxy/ streams are killed
# --------------------------------------------------------------------------

class _EmptyStdout:
    """A yt-dlp stdout that yields no audio, so a stream generator finishes
    immediately instead of the test waiting on a real subprocess."""

    def read(self, _size=None):
        return b""

    def close(self):
        pass


class _FakeProc:
    """Minimal Popen stand-in for the abandon watchdog and stream generator."""

    def __init__(self):
        self.killed = False
        self._returncode = None
        self.stdout = None

    @property
    def returncode(self):
        return self._returncode

    def poll(self):
        return self._returncode

    def kill(self):
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        if self._returncode is None:
            self._returncode = 0
        return self._returncode

    def exit(self, code=0):
        self._returncode = code




# --------------------------------------------------------------------------
# End-to-end: the exact click sequence from the incident
# --------------------------------------------------------------------------







class PlayIntentClaim(_CleanServerState):
    """Rapid clicks must collapse into a single dispatch.

    Five clicks fired five concurrent POST /alexa/play_queue/ requests. Each
    updated now-playing and sent its own trigger phrase to Alexa, so the Echo
    played all five songs in turn and pushed an SSE snapshot per track — on
    screen: "shows 5, then jumps back to 1, then 2, 3, 4, then 5". Whichever
    request's thread finished last also won the now-playing state, which is not
    necessarily the song clicked last.
    """

    def setUp(self):
        super().setUp()
        with server._PLAY_INTENT_LOCK:
            server._PLAY_INTENT_SEQS.clear()
        self.addCleanup(self._clear)

    def _clear(self):
        with server._PLAY_INTENT_LOCK:
            server._PLAY_INTENT_SEQS.clear()

    def test_only_the_newest_click_in_a_burst_claims(self):
        serial = "DEVICE1"
        claims = [server._claim_play_intent(serial, seq) for seq in (1, 2, 3, 4, 5)]
        self.assertEqual(claims, [True, True, True, True, True],
                         msg="clicks arriving in order should each claim")

        # Now the realistic case: the burst arrives with the newest first
        # (concurrent handlers, no ordering guarantee).
        self._clear()
        claims = [server._claim_play_intent(serial, seq) for seq in (5, 1, 2, 3, 4)]
        self.assertEqual(
            claims, [True, False, False, False, False],
            msg="once click 5 is claimed, the older clicks must be dropped — "
                "otherwise the Echo plays all five songs in turn")

    def test_out_of_order_burst_settles_on_the_highest_seq(self):
        serial = "DEVICE1"
        for seq in (3, 1, 5, 2, 4):
            server._claim_play_intent(serial, seq)
        with server._PLAY_INTENT_LOCK:
            self.assertEqual(server._PLAY_INTENT_SEQS[serial], 5)

    def test_a_repeated_sequence_cannot_dispatch_twice(self):
        serial = "DEVICE1"
        self.assertTrue(server._claim_play_intent(serial, 7))
        self.assertFalse(server._claim_play_intent(serial, 7),
                         msg="a retried/duplicated POST must not dispatch again")

    def test_devices_are_independent(self):
        self.assertTrue(server._claim_play_intent("DEVICE1", 5))
        self.assertTrue(server._claim_play_intent("DEVICE2", 1),
                        msg="one device's clicks must not block another's")

    def test_unsequenced_requests_always_claim(self):
        """Voice commands and older clients send no intent_seq."""
        serial = "DEVICE1"
        self.assertTrue(server._claim_play_intent(serial, 5))
        for _ in range(3):
            self.assertTrue(server._claim_play_intent(serial, None))

    def test_non_numeric_sequence_claims_rather_than_breaking_playback(self):
        self.assertTrue(server._claim_play_intent("DEVICE1", "not-a-number"))

    def test_later_clicks_still_work_after_a_burst(self):
        serial = "DEVICE1"
        server._claim_play_intent(serial, 5)
        self.assertTrue(server._claim_play_intent(serial, 6),
                        msg="the next genuine click must not be swallowed")

    def test_web_timestamp_does_not_reject_mobile_client_and_mobile_restarts_are_ordered(self):
        self.assertTrue(server._claim_play_intent("DEVICE1", 1800000000000))
        self.assertTrue(server._claim_play_intent("DEVICE1", 1, 'mobile-client'))
        self.assertTrue(server._claim_play_intent("DEVICE1", 2, 'mobile-client'))
        self.assertFalse(server._claim_play_intent("DEVICE1", 1, 'mobile-client'))
        self.assertTrue(server._claim_play_intent("DEVICE1", 1, 'other-mobile'))

    def test_seq_registry_is_memory_bounded(self):
        for i in range(200):
            server._claim_play_intent(f"DEVICE{i}", 1)
        with server._PLAY_INTENT_LOCK:
            self.assertLessEqual(len(server._PLAY_INTENT_SEQS), 64)

    def test_concurrent_burst_yields_exactly_one_winner(self):
        serial = "DEVICE1"
        results = []
        lock = threading.Lock()
        start = threading.Event()

        def click(seq):
            start.wait(5)
            claimed = server._claim_play_intent(serial, seq)
            with lock:
                results.append((seq, claimed))

        threads = [threading.Thread(target=click, args=(s,), daemon=True)
                   for s in range(1, 21)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(timeout=10)

        claimed = [seq for seq, ok in results if ok]
        self.assertEqual(len(results), 20)
        self.assertIn(20, claimed, msg="the newest click must always win")
        # Under a true race some early claims land before the newest arrives;
        # what must never happen is the newest losing, or claims after it.
        highest = max(claimed)
        self.assertEqual(highest, 20)
        self.assertEqual(sorted(claimed), sorted(set(claimed)),
                         msg="no sequence may claim twice")

    def test_parse_intent_seq(self):
        self.assertEqual(server._play_intent_seq_arg({'intent_seq': 4}), 4)
        self.assertEqual(server._play_intent_seq_arg({'intent_seq': '9'}), 9)
        self.assertIsNone(server._play_intent_seq_arg({}))
        self.assertIsNone(server._play_intent_seq_arg({'intent_seq': None}))
        self.assertIsNone(server._play_intent_seq_arg({'intent_seq': 'x'}))


class PlayQueueSupersededEndpoint(_CleanServerState):
    """The endpoint must do no work at all for a superseded click."""

    def setUp(self):
        super().setUp()
        with server._PLAY_INTENT_LOCK:
            server._PLAY_INTENT_SEQS.clear()
        self.addCleanup(self._clear)
        self.app = server.app.test_client()
        self.key = os.environ["API_KEY"]

    def _clear(self):
        with server._PLAY_INTENT_LOCK:
            server._PLAY_INTENT_SEQS.clear()

    def _post(self, seq, video_id):
        return self.app.post(
            f"/alexa/play_queue/?key={self.key}",
            json={'serial': 'DEVICE1', 'video_id': video_id,
                  'title': 'T', 'intent_seq': seq})

    def test_superseded_click_neither_dispatches_nor_moves_now_playing(self):
        with mock.patch.object(server, "_schedule_play_dispatch",
                               return_value=None) as dispatch, \
                mock.patch.object(server, "_update_now_playing") as update, \
                mock.patch.object(server, "_record_listen"), \
                mock.patch.object(server, "_prewarm_queue_audio"), \
                mock.patch.object(server, "_ensure_audio_ready_for_play"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_notify_sse"):
            newest = self._post(5, "S5555555555")
            stale = self._post(2, "S2222222222")

        self.assertEqual(newest.status_code, 200)
        self.assertEqual(stale.status_code, 200)
        self.assertTrue(stale.get_json().get('superseded'),
                        msg="the stale click should report itself superseded")
        self.assertEqual(
            dispatch.call_count, 1,
            msg="a superseded click still reached the dispatcher")
        dispatched_ids = [c.args[1] for c in dispatch.call_args_list]
        self.assertEqual(dispatched_ids, ["S5555555555"])
        updated_ids = [c.kwargs.get('video_id') for c in update.call_args_list
                       if c.kwargs.get('video_id')]
        self.assertNotIn("S2222222222", updated_ids,
                         msg="a superseded click must not publish an SSE "
                             "snapshot for its track")

    def test_playlist_expansion_is_skipped_for_a_superseded_click(self):
        """The claim happens before the slow work, not after."""
        with mock.patch.object(server, "_schedule_play_dispatch",
                               return_value=None), \
                mock.patch.object(server, "_notify_sse"), \
                mock.patch.object(server.Supporting, "get_playlist_tracks") as tracks:
            self.app.post(f"/alexa/play_queue/?key={self.key}",
                          json={'serial': 'DEVICE1', 'playlist_id': 'PL1',
                                'intent_seq': 9})
            tracks.reset_mock()
            self.app.post(f"/alexa/play_queue/?key={self.key}",
                          json={'serial': 'DEVICE1', 'playlist_id': 'PL2',
                                'intent_seq': 3})
        tracks.assert_not_called()

    def test_unsequenced_client_still_plays(self):
        """Backwards compatibility: a client that sends no intent_seq works."""
        with mock.patch.object(server, "_schedule_play_dispatch",
                               return_value=None) as dispatch, \
                mock.patch.object(server, "_record_listen"), \
                mock.patch.object(server, "_prewarm_queue_audio"), \
                mock.patch.object(server, "_ensure_audio_ready_for_play"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_notify_sse"):
            for vid in ("A1111111111", "B2222222222"):
                resp = self.app.post(
                    f"/alexa/play_queue/?key={self.key}",
                    json={'serial': 'DEVICE1', 'video_id': vid, 'title': 'T'})
                self.assertEqual(resp.status_code, 200)
        self.assertEqual(dispatch.call_count, 2)


class DispatchCoalescing(_CleanServerState):
    """A rapid click burst must produce exactly ONE trigger phrase.

    This is the defect the sequence claim alone could not fix: a human clicking
    songs 1..5 produces five requests in *ascending* order, so every one of them
    claims (each seq is greater than the last) and each used to send its own
    trigger phrase. Alexa queues them and the Echo plays all five in turn, which
    is what appeared on screen as "shows 5, then jumps back to 1, 2, 3, 4, 5".
    """

    def setUp(self):
        super().setUp()
        self._cancel_all()
        self.addCleanup(self._cancel_all)

    def _cancel_all(self):
        with server._PENDING_DISPATCH_LOCK:
            pending = list(server._PENDING_DISPATCH.values())
            server._PENDING_DISPATCH.clear()
        for entry in pending:
            entry['timer'].cancel()
        with server._TRIGGER_INFLIGHT_LOCK:
            server._TRIGGER_INFLIGHT.clear()
        with server._ARMED_PLAYS_LOCK:
            server._ARMED_PLAYS.clear()

    def test_a_five_click_burst_dispatches_only_the_last_song(self):
        sent = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)):
            for vid in ("S1111111111", "S2222222222", "S3333333333",
                        "S4444444444", "S5555555555"):
                server._schedule_play_dispatch("DEVICE1", vid, delay=0.25)
                time.sleep(0.02)   # human clicking is far slower than this
            time.sleep(0.6)        # let the surviving timer fire
        self.assertEqual(
            sent, ["S5555555555"],
            msg=f"expected one trigger for the last clicked song, got {sent} — "
                f"each extra one makes the Echo play another song in turn")

    def test_clicks_spaced_past_the_debounce_still_send_only_one_trigger(self):
        """The reported cadence: taps ~1s apart, i.e. beyond the debounce.

        A debounce alone cannot merge these, and each click used to send its own
        trigger — the Echo then played all five in turn. Because the trigger
        phrase carries no song and /armed_play/ always returns the newest arm,
        one outstanding trigger serves the whole burst.
        """
        sent = []
        armed = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)), \
                mock.patch.object(server, "_arm_play",
                                  side_effect=lambda s, v, o=0: armed.append(v)):
            for vid in ("T1111111111", "T2222222222", "T3333333333",
                        "T4444444444", "T5555555555"):
                server._schedule_play_dispatch("DEVICE1", vid, delay=0.05)
                time.sleep(0.3)    # well past the debounce window
            time.sleep(0.3)

        self.assertEqual(
            sent, ["T1111111111"],
            msg=f"sent {len(sent)} triggers ({sent}); with one already in "
                f"flight the rest must only re-arm")
        self.assertEqual(
            armed[-1], "T5555555555",
            msg="the arm must end on the song the user actually landed on, "
                "because that is what the in-flight trigger will fetch")

    def test_a_new_trigger_is_sent_once_the_previous_one_is_served(self):
        sent = []
        app = server.app.test_client()
        key = os.environ["API_KEY"]
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)):
            server._schedule_play_dispatch("DEVICE1", "FIRST111111", delay=0.05)
            time.sleep(0.25)
            self.assertEqual(sent, ["FIRST111111"])
            # The skill picks up the arm; the trigger is no longer outstanding.
            resp = app.get(f"/armed_play/?key={key}")
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(server._trigger_in_flight("DEVICE1"),
                             msg="/armed_play/ must clear the in-flight marker")
            server._schedule_play_dispatch("DEVICE1", "SECOND22222", delay=0.05)
            time.sleep(0.25)
        self.assertEqual(sent, ["FIRST111111", "SECOND22222"],
                         msg="a later deliberate play must still reach the device")

    def test_in_flight_marker_expires_so_playback_cannot_wedge(self):
        sent = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)):
            server._schedule_play_dispatch("DEVICE1", "FIRST111111", delay=0.05)
            time.sleep(0.25)
            # Simulate Alexa silently dropping the trigger.
            with server._TRIGGER_INFLIGHT_LOCK:
                server._TRIGGER_INFLIGHT["DEVICE1"] = (
                    time.time() - server.TRIGGER_INFLIGHT_TTL - 1)
            self.assertFalse(server._trigger_in_flight("DEVICE1"))
            server._schedule_play_dispatch("DEVICE1", "SECOND22222", delay=0.05)
            time.sleep(0.25)
        self.assertEqual(sent, ["FIRST111111", "SECOND22222"],
                         msg="a dropped trigger must not block playback forever")

    def test_a_lone_click_still_dispatches(self):
        sent = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)):
            server._schedule_play_dispatch("DEVICE1", "LONE1111111", delay=0.1)
            time.sleep(0.4)
        self.assertEqual(sent, ["LONE1111111"])

    def test_devices_do_not_coalesce_with_each_other(self):
        sent = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append((s, v))):
            server._schedule_play_dispatch("DEVICE1", "AAAAAAAAAAA", delay=0.15)
            server._schedule_play_dispatch("DEVICE2", "BBBBBBBBBBB", delay=0.15)
            time.sleep(0.5)
        self.assertEqual(sorted(sent),
                         [("DEVICE1", "AAAAAAAAAAA"), ("DEVICE2", "BBBBBBBBBBB")],
                         msg="one device's burst must not cancel another's play")

    def test_the_echo_ends_up_playing_the_last_clicked_song(self):
        """The end-to-end property that matters, at several click cadences.

        Reported behaviour was "pressed 1,2,3,4,5 ... shows 5, then jumps back to
        1, then 2, 3, 4 and then 5" — because five trigger phrases were sent and
        the Echo played all five in turn. Whatever the cadence, exactly one
        trigger must go out and /armed_play/ (what the skill actually fetches and
        plays) must return the song clicked last.
        """
        app = server.app.test_client()
        key = os.environ["API_KEY"]
        for tag, gap in (("P", 0.05), ("Q", 0.3), ("R", 0.9)):
            with self.subTest(gap=gap):
                self._cancel_all()
                with server._PLAY_INTENT_LOCK:
                    server._PLAY_INTENT_SEQS.clear()
                sent = []
                with mock.patch.object(
                        server, "_dispatch_play_with_retry",
                        side_effect=lambda s, v, o=0: sent.append(v)), \
                        mock.patch.object(server, "PLAY_DISPATCH_DEBOUNCE", 0.2), \
                        mock.patch.object(server, "_record_listen"), \
                        mock.patch.object(server, "_prewarm_queue_audio"), \
                        mock.patch.object(server, "_ensure_audio_ready_for_play"), \
                        mock.patch.object(server, "_refresh_radio_queue"), \
                        mock.patch.object(server, "_notify_sse"):
                    ids = [f"{tag}{i}bcdefghij" for i in range(1, 6)]
                    for seq, vid in enumerate(ids, start=1):
                        resp = app.post(
                            f"/alexa/play_queue/?key={key}",
                            json={'serial': 'DEVICE7', 'video_id': vid,
                                  'title': 'T', 'intent_seq': seq})
                        self.assertEqual(resp.status_code, 200)
                        time.sleep(gap)
                    time.sleep(0.5)

                    self.assertEqual(
                        len(sent), 1,
                        msg=f"{len(sent)} triggers sent ({sent}); the Echo would "
                            f"play that many songs one after another")
                    played = app.get(f"/armed_play/?key={key}").get_json()
                    self.assertEqual(
                        played.get('video_id'), ids[-1],
                        msg=f"the Echo would play {played.get('video_id')} "
                            f"instead of the last clicked song {ids[-1]}")

    def test_scheduling_reports_the_replaced_song(self):
        try:
            self.assertIsNone(
                server._schedule_play_dispatch("DEVICE1", "AAAAAAAAAAA", delay=5))
            self.assertEqual(
                server._schedule_play_dispatch("DEVICE1", "BBBBBBBBBBB", delay=5),
                "AAAAAAAAAAA")
        finally:
            self._cancel_all()

    def test_cancel_pending_dispatch(self):
        sent = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)):
            server._schedule_play_dispatch("DEVICE1", "AAAAAAAAAAA", delay=0.15)
            self.assertTrue(server._cancel_pending_dispatch("DEVICE1"))
            self.assertFalse(server._cancel_pending_dispatch("DEVICE1"))
            time.sleep(0.4)
        self.assertEqual(sent, [], msg="a cancelled dispatch must not fire")

    def test_dispatch_error_surfaces_as_playback_error(self):
        """The response returns before the send, so errors must reach the UI
        through now-playing instead of the HTTP status."""
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               return_value="Device is offline"), \
                mock.patch.dict(server._now_playing, video_id="AAAAAAAAAAA", playback_processing=True), \
                mock.patch.object(server._playback_output, 'snapshot', return_value={
                    'playback_output': 'alexa', 'output_token': 'dispatch-output'}), \
                mock.patch.object(server, "_notify_sse"):
            server._schedule_play_dispatch("DEVICE1", "AAAAAAAAAAA", delay=0.05)
            time.sleep(0.4)
            error = server._now_playing['playback_error']
            self.assertIsNotNone(error, msg="a failed dispatch reported nothing to the UI")
            self.assertEqual(error['type'], 'dispatch_error')
            self.assertIn('offline', error['message'])
            self.assertFalse(server._now_playing['playing'])
            self.assertFalse(server._now_playing['playback_processing'])

    def test_a_dispatch_exception_does_not_escape_the_timer_thread(self):
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(server, "_notify_sse"):
            server._schedule_play_dispatch("DEVICE1", "AAAAAAAAAAA", delay=0.05)
            time.sleep(0.4)
        with server._PENDING_DISPATCH_LOCK:
            self.assertNotIn("DEVICE1", server._PENDING_DISPATCH,
                             msg="a crashed dispatch left a stale pending entry, "
                                 "which would block the next click")

    def test_endpoint_burst_sends_one_trigger(self):
        """End-to-end through the real route, clicks in ascending order."""
        app = server.app.test_client()
        key = os.environ["API_KEY"]
        with server._PLAY_INTENT_LOCK:
            server._PLAY_INTENT_SEQS.clear()
        sent = []
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: sent.append(v)), \
                mock.patch.object(server, "PLAY_DISPATCH_DEBOUNCE", 0.3), \
                mock.patch.object(server, "_record_listen"), \
                mock.patch.object(server, "_prewarm_queue_audio"), \
                mock.patch.object(server, "_ensure_audio_ready_for_play"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_notify_sse"):
            for seq, vid in enumerate(("Q1111111111", "Q2222222222", "Q3333333333",
                                       "Q4444444444", "Q5555555555"), start=1):
                resp = app.post(f"/alexa/play_queue/?key={key}",
                                json={'serial': 'DEVICE9', 'video_id': vid,
                                      'title': 'T', 'intent_seq': seq})
                self.assertEqual(resp.status_code, 200)
            time.sleep(0.8)
        self.assertEqual(
            sent, ["Q5555555555"],
            msg=f"the endpoint sent {len(sent)} triggers ({sent}); the Echo would "
                f"play that many songs in turn")

    def test_reused_in_flight_trigger_still_gets_a_confirmation_watchdog(self):
        """A click that only re-arms (a trigger is already in flight) must still
        start a confirmation watchdog for the newest song. If the in-flight
        trigger is dropped and never serves it, the watchdog retries with a
        fresh trigger instead of leaving the newest song silently unplayed
        while the web remote already shows it as "playing".
        """
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="NEWEST11111", playing=True,
                                       playback_confirmed=False)
        dispatched = []      # _dispatch_play_with_retry calls (initial sends)
        sent_triggers = []   # real play_video_id calls (incl. watchdog retry)
        # `create=True`: the test stub for alexa_remote.remote has no
        # play_video_id; the watchdog's resend closure calls it directly.
        with mock.patch.object(server, "_dispatch_play_with_retry",
                               side_effect=lambda s, v, o=0: dispatched.append(v)), \
                mock.patch.object(server.alexa_remote.remote, "play_video_id",
                                  create=True,
                                  side_effect=lambda s, v, o=0: sent_triggers.append(v) or None), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.1), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.1), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)):
            # First click dispatches normally, leaving a trigger in flight.
            server._schedule_play_dispatch("DEVICE1", "FIRST111111", delay=0.05)
            time.sleep(0.25)
            self.assertEqual(dispatched, ["FIRST111111"])
            self.assertTrue(server._trigger_in_flight("DEVICE1"))
            # Second click while the trigger is in flight: only re-arms, no
            # fresh dispatch, but a confirmation watchdog must start for the
            # newest song.
            server._schedule_play_dispatch("DEVICE1", "NEWEST11111", delay=0.05)
            time.sleep(0.25)
            self.assertEqual(dispatched, ["FIRST111111"],
                             msg="a fresh trigger must not go out while one is "
                                 "already in flight")
            # The in-flight trigger is never served (dropped). The watchdog for
            # the re-armed newest song must retry it with a fresh trigger.
            time.sleep(0.6)
        self.assertIn("NEWEST11111", sent_triggers,
                      msg="a re-armed song whose in-flight trigger never served "
                          "it must be retried with a fresh trigger, not left "
                          "silently unplayed")


class RapidClickScenario(_CleanServerState):
    """Three rapid clicks must cost roughly one track's worth of downloads."""

    def test_three_rapid_clicks_do_not_fan_out(self):
        """Faithful model of the incident's timing.

        `_prewarm_queue_audio` does not download inline — it hands each track to
        a background thread that then queues on `_download_semaphore` (4
        permits). So the clicks *enqueue* work and the work runs later; the
        incident log shows downloads for abandoned tracks still starting three
        minutes after the clicks. The deferred queue below reproduces that, and
        the fix is precisely that those late starters now find their generation
        stale and drop instead of hitting YouTube.
        """
        deferred = []
        spawned = []

        def fake_ensure(video_id, generation=None, prefetch=False):
            # Mirrors the real cancellation checks in Supporting.ensure_downloaded.
            if prefetch and server._ytdlp_rate_limited():
                return None
            if server._generation_superseded(generation):
                return None
            spawned.append(video_id)
            return f"/tmp/{video_id}.m4a"

        def defer(video_id, wait=False, generation=None, prefetch=False):
            # Stands in for threading.Thread(target=ensure_downloaded, ...).
            deferred.append(lambda: fake_ensure(video_id, generation=generation,
                                                prefetch=prefetch))
            return False

        queue = [{'video_id': f"song{i:07d}", 'title': f"s{i}"} for i in range(12)]
        clicks = (0, 5, 9)

        with mock.patch.object(server._youtube_stream.current, "has",
                               staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_ensure_audio_ready_for_play",
                                  side_effect=defer), \
                mock.patch.object(server, "_notify_sse"):
            for clicked in clicks:
                server._update_now_playing(video_id=queue[clicked]['video_id'])
                server._prewarm_queue_audio(queue, clicked, limit=4)
            final_generation = server._current_playback_generation()
            # The download pool now drains, well after the user stopped clicking.
            for run in deferred:
                run()

        self.assertEqual(len(deferred), len(clicks) * 4,
                         msg="prewarm should still enqueue eagerly; the fix is "
                             "about what runs, not what is queued")
        # Only the last click's prewarms are still wanted. Everything the first
        # two clicks queued is now stale and must never reach yt-dlp.
        self.assertEqual(
            len(spawned), 4,
            msg=f"rapid clicks still fanned out into {len(spawned)} downloads "
                f"({spawned}) — this is what earned the server a 429")
        expected = [queue[(clicks[-1] + off) % len(queue)]['video_id']
                    for off in range(1, 5)]
        self.assertEqual(spawned, expected,
                         msg="the surviving downloads must be the ones the user "
                             "is actually heading towards")
        self.assertFalse(server._generation_superseded(final_generation))

    def test_a_429_during_the_burst_stops_all_prefetch(self):
        queue = [{'video_id': f"song{i:07d}", 'title': f"s{i}"} for i in range(12)]
        with mock.patch.object(server._youtube_stream.current, "has",
                               staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_ensure_audio_ready_for_play") as ensure, \
                mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=queue[0]['video_id'])
            server._note_rate_limited(queue[0]['video_id'])
            warmed = server._prewarm_queue_audio(queue, 0, limit=4)
        self.assertEqual(warmed, 0)
        ensure.assert_not_called()



# --------------------------------------------------------------------------
# Playback-failure optimizations: auto-advance, adaptive timeout, buffering
# feedback, rate-limit short-circuit, and the pre-resend race guard.
# --------------------------------------------------------------------------

class PlaybackFailureAutoAdvance(_CleanServerState):
    """A dead/timed-out track should skip to the next queue item instead of
    just stopping, whenever one is available."""

    def _install_queue(self, current_id, upcoming_id):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(
                video_id=current_id, queue_index=0, playing=True,
                # Confirmation is still pending: this is what the watchdog is
                # waiting on, and setting it True here would make the very
                # first check in _wait_once report success immediately.
                playback_confirmed=False,
                queue=[{'video_id': current_id, 'title': 'Current'},
                       {'video_id': upcoming_id, 'title': 'Next', 'artist': 'A'}])

    def test_five_failures_stop_without_consuming_the_rest_of_queue(self):
        queue = [{'video_id': f'song{i:07d}', 'title': str(i)} for i in range(10)]
        with mock.patch.object(server, '_notify_sse'), mock.patch.object(server, '_dispatch_play_with_retry') as dispatch:
            server._update_now_playing(video_id=queue[0]['video_id'], queue_index=0, playing=True, queue=queue)
            for i in range(5):
                self.assertTrue(server._auto_advance_after_failure('serial', queue[i]['video_id'], 'timeout'))
                # Duplicate failure callbacks cannot count or advance again.
                server._auto_advance_after_failure('serial', queue[i]['video_id'], 'timeout')
            self.assertEqual(dispatch.call_count, 4)
            state = server._get_now_playing()
            self.assertFalse(state['playing'])
            self.assertEqual(state['video_id'], queue[4]['video_id'])
            self.assertEqual(len(state['queue']), 10)
            self.assertIn('5 consecutive failed songs', state['playback_error']['message'])
            self.assertIn('did not reply', state['playback_error']['message'])
            server._playback_failover_budget.reset()
            server._auto_advance_after_failure('serial', queue[4]['video_id'], 'timeout')
            self.assertEqual(dispatch.call_count, 5)

    def test_dead_video_auto_advances_to_next_track(self):
        current, nxt = "DEADDEADDEA", "NEXTNEXTNEX"
        self._install_queue(current, nxt)
        server._mark_video_dead(current)
        dispatched = []
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_dispatch_play_with_retry",
                                  side_effect=lambda s, v, o=0: dispatched.append(v)), \
                mock.patch.object(server, "_record_listen"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_lookup_and_update_np"), \
                mock.patch.object(server, "_notify_sse"):
            server._watch_playback_confirmation("DEVICE1", current, lambda: None)
        self.assertEqual(dispatched, [nxt],
                         msg="a dead video must auto-advance to the next queue "
                             "item rather than leaving playback stopped")
        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], nxt)
        self.assertTrue(snap['playing'])

    def test_dead_video_with_no_next_track_falls_back_to_stop_and_error(self):
        current = "LONELYLONEL"
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=current, queue_index=0,
                                       playing=True, playback_confirmed=False,
                                       queue=[{'video_id': current, 'title': 'Only'}])
        server._mark_video_dead(current)
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_notify_sse"), \
                mock.patch.object(server, "_update_now_playing",
                                  wraps=server._update_now_playing) as update:
            server._watch_playback_confirmation("DEVICE1", current, lambda: None)
        errors = [c.kwargs.get('playback_error') for c in update.call_args_list
                  if c.kwargs.get('playback_error')]
        self.assertTrue(errors)
        self.assertEqual(errors[-1]['type'], 'unavailable')

    def test_final_transport_timeout_advances_with_bounded_failure_budget(self):
        current, nxt = "TIMEOUTTIME", "AFTERAFTERA"
        self._install_queue(current, nxt)
        dispatched = []
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_dispatch_play_with_retry",
                                  side_effect=lambda s, v, o=0: dispatched.append(v)), \
                mock.patch.object(server, "_record_listen"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_lookup_and_update_np"), \
                mock.patch.object(server, "_notify_sse"):
            # `resend` models the watchdog's own first retry of `current`; it
            # "succeeds" at sending (no error) but the track still never gets
            # confirmed, so the second _wait_once also times out.
            server._watch_playback_confirmation(
                "DEVICE1", current, lambda: dispatched.append(current) or None)
        self.assertEqual(dispatched, [current, nxt])
        snap = server._get_now_playing()
        self.assertEqual(snap['video_id'], nxt)
        self.assertTrue(snap['playing'])
        self.assertEqual(snap['playback_error']['type'], 'timeout')
        self.assertEqual([t['video_id'] for t in snap['queue']], [current, nxt])


class PlaybackFailureRateLimit(_CleanServerState):
    """The watchdog must not resend into an active 429 cooldown, and must
    report a distinct, actionable error instead."""

    def test_resend_is_skipped_during_cooldown_and_reports_rate_limited(self):
        video_id = "RATELIMITED"
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=video_id, playing=True,
                                       playback_confirmed=False)
        server._note_rate_limited(video_id)
        resends = []
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_notify_sse"), \
                mock.patch.object(server, "_update_now_playing",
                                  wraps=server._update_now_playing) as update:
            server._watch_playback_confirmation(
                "DEVICE1", video_id, lambda: resends.append(1))
        self.assertEqual(resends, [],
                         msg="resending during an active 429 cooldown only "
                             "deepens the throttle and cannot succeed")
        errors = [c.kwargs.get('playback_error') for c in update.call_args_list
                  if c.kwargs.get('playback_error')]
        self.assertTrue(errors, msg="a skipped retry reported nothing to the UI")
        self.assertEqual(errors[-1]['type'], 'rate_limited')
        self.assertIn('retry_after_s', errors[-1])
        self.assertGreater(errors[-1]['retry_after_s'], 0)

    def test_no_cooldown_still_retries_normally(self):
        """Control: outside a cooldown, the resend path is untouched."""
        video_id = "NORMALRETRY"
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=video_id, playing=True,
                                       playback_confirmed=False)
        resends = []
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_notify_sse"):
            server._watch_playback_confirmation(
                "DEVICE1", video_id, lambda: resends.append(1))
        self.assertEqual(resends, [1])


class PlaybackFailureAdaptiveTimeout(_CleanServerState):
    """A cached track should time out (and thus retry) faster than a cold one."""

    def _time_to_resend(self, cached):
        video_id = "ADAPTIVEVID"
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=video_id, playing=True,
                                       playback_confirmed=False)
        resend_at = []
        start = time.time()
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 1.0), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.2), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: ("/tmp/x.m4a" if cached else None))), \
                mock.patch.object(server, "_notify_sse"):
            server._watch_playback_confirmation(
                "DEVICE1", video_id, lambda: resend_at.append(time.time() - start))
        self.assertEqual(len(resend_at), 1)
        return resend_at[0]

    def test_cached_track_times_out_faster_than_cold_track(self):
        cached_elapsed = self._time_to_resend(cached=True)
        cold_elapsed = self._time_to_resend(cached=False)
        self.assertLess(cached_elapsed, cold_elapsed,
                        msg="a cached (no-download-needed) track should be "
                            "retried sooner than one still being fetched")
        self.assertLess(cached_elapsed, 0.6)
        self.assertGreaterEqual(cold_elapsed, 0.9)


class PlaybackFailureBufferingFeedback(_CleanServerState):
    """A non-fatal 'buffering' hint should surface mid-wait without stopping
    playback or counting as a failure."""

    def test_buffering_hint_is_emitted_before_the_confirm_timeout(self):
        video_id = "BUFFERINGVI"
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=video_id, playing=True,
                                       playback_confirmed=False)
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 2.0), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 2.0), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 0.1), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_notify_sse"), \
                mock.patch.object(server, "_update_now_playing",
                                  wraps=server._update_now_playing) as update:
            # Confirm shortly after the buffering hint should have fired, so
            # the watchdog returns without ever reaching the terminal path.
            def _confirm_soon():
                time.sleep(0.3)
                with mock.patch.object(server, "_notify_sse"):
                    server._update_now_playing(video_id=video_id,
                                               playback_confirmed=True)
            t = threading.Thread(target=_confirm_soon, daemon=True)
            t.start()
            server._watch_playback_confirmation("DEVICE1", video_id, lambda: None)
            t.join(timeout=5)
        errors = [c.kwargs.get('playback_error') for c in update.call_args_list
                  if c.kwargs.get('playback_error')]
        buffering = [e for e in errors if e.get('type') == 'buffering']
        self.assertTrue(buffering, msg="no buffering feedback was surfaced "
                                       "while confirmation was pending")
        self.assertFalse(buffering[-1].get('terminal', True),
                         msg="buffering feedback must be marked non-terminal so "
                             "the frontend does not treat it as a failure")
        terminal_errors = [e for e in errors if e.get('terminal', True)]
        self.assertEqual(terminal_errors, [],
                         msg="confirming playback after a buffering hint must "
                             "not also report a terminal failure")


class PlaybackFailureRetryRaceGuard(_CleanServerState):
    """If a download starts in the instant before the retry fires, the
    watchdog must wait for it instead of resending into it."""

    def test_download_starting_just_before_retry_defers_the_resend(self):
        video_id = "RACEGUARDVI"
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=video_id, playing=True,
                                       playback_confirmed=False)
        resends = []
        call_count = {'n': 0}
        real_in_progress = server._download_in_progress

        def fake_in_progress(vid):
            call_count['n'] += 1
            # First call happens inside the initial _wait_once loop (must stay
            # False so the loop actually times out); the second call is the
            # race-guard check right before resending — flip it on there to
            # simulate a download starting in that exact window, then let the
            # subsequent _wait_once call see it end.
            if call_count['n'] <= 2:
                return False
            if call_count['n'] == 3:
                return True
            return False

        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server._youtube_stream.current, "has",
                                  staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_download_in_progress",
                                  side_effect=fake_in_progress), \
                mock.patch.object(server, "_notify_sse"):
            server._watch_playback_confirmation(
                "DEVICE1", video_id, lambda: resends.append(1))
        # However many times _download_in_progress was consulted, the resend
        # must still have fired exactly once once the simulated download
        # cleared — the guard defers, it does not cancel, the retry.
        self.assertEqual(resends, [1])


class PausedSeekProcessing(unittest.TestCase):
    """A paused seek completes locally and must settle every remote client."""

    def setUp(self):
        with server._np_lock:
            self._previous_now_playing = copy.deepcopy(server._now_playing)
        self.addCleanup(self._restore_now_playing)
        with server._np_lock:
            server._now_playing.update({
                'playing': False,
                'title': 'Paused track',
                'artist': 'Artist',
                'video_id': 'seekvideo01',
                'duration_ms': 180000,
                'position_ms': 1000,
                'playback_confirmed': True,
                'playback_processing': True,
                'playback_revision': 41,
                'started_at': time.time(),
                'updated_at': time.time(),
            })

    def _restore_now_playing(self):
        with server._np_lock:
            server._now_playing.clear()
            server._now_playing.update(self._previous_now_playing)

    def test_paused_seek_clears_marker_updates_revision_and_broadcasts(self):
        snapshots = []
        client = server.app.test_client()
        with mock.patch.object(
                server, '_notify_sse',
                side_effect=lambda: snapshots.append(server._np_snapshot('DEVICE1'))):
            response = client.post(
                f"/alexa/seek/?key={os.environ['API_KEY']}",
                json={'serial': 'DEVICE1', 'position_ms': 42000})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'ok': True, 'paused': True})
        with server._np_lock:
            self.assertFalse(server._now_playing['playback_processing'])
            self.assertEqual(server._now_playing['position_ms'], 42000)
            self.assertEqual(server._now_playing['playback_revision'], 42)
        self.assertEqual(len(snapshots), 1)
        self.assertFalse(snapshots[0]['playback_processing'])
        self.assertEqual(snapshots[0]['position_ms'], 42000)
        self.assertEqual(snapshots[0]['playback_revision'], 42)


if __name__ == "__main__":
    unittest.main(verbosity=2)
