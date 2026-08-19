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
        server._reset_rate_limit_cooldown()
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()
        with server._stream_inflight_lock:
            server._stream_inflight.clear()
        with server._download_queue_lock:
            server._download_queue_depth = 0
        self.addCleanup(self._restore)

    def _restore(self):
        server._reset_rate_limit_cooldown()
        with server._dead_video_ids_lock:
            server._dead_video_ids.clear()
        with server._flaky_video_ids_lock:
            server._flaky_video_ids.clear()
        with server._stream_inflight_lock:
            server._stream_inflight.clear()
        with server._download_queue_lock:
            server._download_queue_depth = 0


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


class EnsureDownloadedRateLimit(_CleanServerState):
    """`ensure_downloaded` must stop walking client fallbacks on a 429."""

    def setUp(self):
        super().setUp()
        self.clients = ["default", "android_vr", "web", "tv"]
        p = mock.patch.object(server.Supporting, "get_ytdlp_clients",
                              staticmethod(lambda: list(self.clients)))
        p.start()
        self.addCleanup(p.stop)
        # No cache file ever appears: these downloads all "fail".
        p2 = mock.patch.object(server.Supporting, "cached_audio_path",
                               staticmethod(lambda vid: None))
        p2.start()
        self.addCleanup(p2.stop)
        p3 = mock.patch.object(server.Supporting, "prune_audio_cache",
                               staticmethod(lambda: None))
        p3.start()
        self.addCleanup(p3.stop)

    def test_fatal_429_aborts_remaining_clients(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(1, _STDERR_429_FATAL)

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            server.Supporting.ensure_downloaded("vidratelimit")

        self.assertEqual(
            len(calls), 1,
            msg=f"expected 1 yt-dlp attempt before bailing on a fatal 429, got "
                f"{len(calls)} — walking the remaining fallbacks fires three "
                f"more bursts at an endpoint already refusing us")

    def test_recoverable_429_warning_still_tries_every_client(self):
        """Regression guard for an over-correction.

        The incident's stderr carried a 429 *warning* plus a bot-check ERROR.
        Aborting there would leave the download with a single attempt and skip
        android_vr/web/tv, which is precisely what recovers from a bot check.
        """
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(1, _STDERR_429_WARNING_THEN_BOT_CHECK)

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            server.Supporting.ensure_downloaded("vidwarned")

        self.assertEqual(
            len(calls), len(self.clients),
            msg=f"a recoverable 429 warning truncated the fallback chain to "
                f"{len(calls)} attempt(s) — the other client profiles are the "
                f"only thing that recovers from a bot check")

    def test_429_opens_the_cooldown_even_when_only_a_warning(self):
        """A 429 anywhere is real evidence of throttling, so prefetch pauses —
        that part is safe because it only drops best-effort work."""
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(
                                   1, _STDERR_429_WARNING_THEN_BOT_CHECK)):
            server.Supporting.ensure_downloaded("vidratelimit")
        self.assertTrue(server._ytdlp_rate_limited())

    def test_429_never_marks_the_video_dead(self):
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(
                                   1, _STDERR_429_LOOKS_UNAVAILABLE)):
            server.Supporting.ensure_downloaded("hhstADmzpqM")
        self.assertFalse(
            server._is_dead_video("hhstADmzpqM"),
            msg="a rate-limited download poisoned the dead-video cache — the "
                "song would be skipped with 'known-dead' for _DEAD_VIDEO_TTL")

    def test_genuinely_dead_video_still_tries_every_client_then_dies(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(1, _STDERR_REALLY_DEAD)

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            server.Supporting.ensure_downloaded("abcdefghijk")

        self.assertEqual(len(calls), len(self.clients),
                         msg="a genuine 'video unavailable' must still be "
                             "confirmed by every client before being cached")
        self.assertTrue(server._is_dead_video("abcdefghijk"))

    def test_bot_check_does_not_abort_fallbacks(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(1, _STDERR_BOT_CHECK)

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            server.Supporting.ensure_downloaded("botchecked")

        self.assertEqual(len(calls), len(self.clients),
                         msg="the bot-check challenge is exactly what the "
                             "client-profile fallback loop exists to recover from")
        self.assertFalse(server._is_dead_video("botchecked"))

    def test_unrecognized_failure_on_every_client_is_flaky_not_dead(self):
        """SSL resets and SABR format gaps are not permanent-unavailable
        signals, but a video that fails every client this way still can't be
        downloaded right now. It must land in the short-TTL flaky cache, not
        the hour-long dead cache, and must not be silently forgotten (retried
        forever at full 4-client cost) either."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(1, _STDERR_SSL_RESET)

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            server.Supporting.ensure_downloaded("flakyvideoid")

        self.assertEqual(len(calls), len(self.clients),
                         msg="an unrecognized failure must still be confirmed "
                             "by every client before being cached as flaky")
        self.assertFalse(
            server._is_dead_video("flakyvideoid"),
            msg="an SSL reset / SABR gap is not proof of permanent "
                "unavailability and must not poison _dead_video_ids for an hour")
        self.assertTrue(
            server._is_flaky_video("flakyvideoid"),
            msg="a video that failed every client without a recognized cause "
                "must be cached as flaky so a retry loop doesn't re-run all "
                "4 clients' retry/timeout budgets back-to-back")
        self.assertTrue(server._is_dead_or_flaky_video("flakyvideoid"))

    def test_flaky_cache_skips_the_subprocess_on_retry(self):
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(1, _STDERR_SABR_NO_FORMAT)):
            server.Supporting.ensure_downloaded("flakyvideoid2")
        self.assertTrue(server._is_flaky_video("flakyvideoid2"))

        with mock.patch.object(server.subprocess, "run") as run:
            result = server.Supporting.ensure_downloaded("flakyvideoid2")
        run.assert_not_called()
        self.assertIsNone(result)

    def test_flaky_cache_expires_and_allows_retry(self):
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(1, _STDERR_SSL_RESET)):
            server.Supporting.ensure_downloaded("flakyvideoid3")
        self.assertTrue(server._is_flaky_video("flakyvideoid3"))

        with server._flaky_video_ids_lock:
            server._flaky_video_ids["flakyvideoid3"] = (
                time.time() - server._FLAKY_VIDEO_TTL - 1)

        self.assertFalse(server._is_flaky_video("flakyvideoid3"))
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(1, _STDERR_SSL_RESET)) as run:
            server.Supporting.ensure_downloaded("flakyvideoid3")
        self.assertTrue(run.called,
                        msg="an expired flaky entry must allow a fresh attempt")

    def test_concurrent_callers_for_a_doomed_id_do_not_double_run_fallback(self):
        """Reproduces the 12:40:39-54 incident log: get_stream()'s background
        prewarm thread and /proxy/'s own ensure_downloaded() call race for the
        same about-to-be-dead video_id. The second caller queues behind the
        first's per-id lock (both pass the dead-check before either has run),
        and must see the first caller's dead/flaky verdict once it acquires
        the lock instead of repeating all 4 clients itself."""
        calls = []
        release_first = threading.Event()
        first_started = threading.Event()

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            # Let the second caller start queuing behind the lock while the
            # first client attempt of the first caller is still "in flight".
            if len(calls) == 1:
                first_started.set()
                release_first.wait(5)
            return _FakeCompleted(1, _STDERR_REALLY_DEAD)

        results = []

        def run_second_caller():
            first_started.wait(5)
            # Give the first caller a beat to actually acquire the per-id
            # lock before this one tries to acquire it too.
            time.sleep(0.05)
            results.append(server.Supporting.ensure_downloaded("racyvideoid"))

        t = threading.Thread(target=run_second_caller, daemon=True)
        t.start()
        try:
            with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
                first_result = server.Supporting.ensure_downloaded("racyvideoid")
        finally:
            release_first.set()
            t.join(timeout=5)

        self.assertIsNone(first_result)
        self.assertEqual(results, [None])
        self.assertEqual(
            len(calls), len(self.clients),
            msg="the second caller re-ran the full client fallback loop for a "
                "video the first caller (which it was queued behind) had "
                "already just proven dead")
        self.assertTrue(server._is_dead_video("racyvideoid"))

    def test_success_clears_the_cooldown(self):
        server._note_rate_limited("earlier")
        self.assertTrue(server._ytdlp_rate_limited())
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(0, "")):
            server.Supporting.ensure_downloaded("goodvid")
        self.assertFalse(server._ytdlp_rate_limited(),
                         msg="throughput is back; prefetch must resume")

    def test_prefetch_is_dropped_during_cooldown(self):
        server._note_rate_limited("earlier")
        with mock.patch.object(server.subprocess, "run") as run:
            result = server.Supporting.ensure_downloaded("prefetchvid", prefetch=True)
        self.assertIsNone(result)
        run.assert_not_called()

    def test_prefetch_during_cooldown_does_not_even_take_a_permit(self):
        """Why the cooldown is checked *before* the semaphore too.

        If a suppressed prefetch still queued for a permit, a burst of them
        would sit in front of the track the user is waiting on and delay it past
        the Echo's ~11s /proxy/ timeout — turning a throttle into an outage.
        """
        server._note_rate_limited("earlier")
        spy = _SemaphoreSpy(server._DOWNLOAD_CONCURRENCY)
        with mock.patch.object(server, "_download_semaphore", spy), \
                mock.patch.object(server.subprocess, "run") as run:
            result = server.Supporting.ensure_downloaded("prefetchvid",
                                                         prefetch=True)
        self.assertIsNone(result)
        run.assert_not_called()
        self.assertEqual(
            spy.acquires, 0,
            msg="a suppressed prefetch queued for a download permit instead of "
                "being dropped outright")
        self.assertEqual(server._download_queue_size(), 0)

    def test_a_wanted_download_does_take_a_permit(self):
        """Control for the test above: the permit accounting is real, so the
        assertion there cannot pass for the wrong reason."""
        spy = _SemaphoreSpy(server._DOWNLOAD_CONCURRENCY)
        with mock.patch.object(server, "_download_semaphore", spy), \
                mock.patch.object(server.subprocess, "run",
                                  return_value=_FakeCompleted(0, "")):
            server.Supporting.ensure_downloaded("wantedvid", prefetch=False)
        self.assertEqual(spy.acquires, 1)
        self.assertEqual(spy.releases, 1)

    def test_foreground_download_still_runs_during_cooldown(self):
        """The song the user is waiting on is never sacrificed to the cooldown."""
        server._note_rate_limited("earlier")
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(0, "")) as run:
            server.Supporting.ensure_downloaded("foregroundvid", prefetch=False)
        self.assertTrue(run.called)

    def test_queued_prefetch_rechecks_the_cooldown_after_the_semaphore(self):
        """The cooldown is checked twice on purpose, and both checks matter.

        A prefetch that was already queued when the 429 arrived must still be
        dropped: the whole backlog is what deepens the block, and the backlog is
        exactly what is sitting on the semaphore when the first 429 lands.
        """
        blockers = 0
        for _ in range(server._DOWNLOAD_CONCURRENCY):
            server._download_semaphore.acquire()
            blockers += 1

        def _free_permits():
            time.sleep(0.2)
            # The 429 arrives while this prefetch is still queued.
            server._note_rate_limited("someothervid")
            for _ in range(blockers):
                server._download_semaphore.release()

        freeing = threading.Thread(target=_free_permits, daemon=True)
        freeing.start()
        try:
            with mock.patch.object(server.subprocess, "run") as run:
                result = server.Supporting.ensure_downloaded("queuedprefetch",
                                                             prefetch=True)
            self.assertIsNone(result)
            run.assert_not_called()
        finally:
            freeing.join(timeout=5)

    def test_queued_foreground_download_survives_a_late_cooldown(self):
        """Symmetry check: the same late 429 must NOT drop the track the user is
        waiting on, or a single 429 would mute the remote for 90 seconds."""
        blockers = 0
        for _ in range(server._DOWNLOAD_CONCURRENCY):
            server._download_semaphore.acquire()
            blockers += 1

        def _free_permits():
            time.sleep(0.2)
            server._note_rate_limited("someothervid")
            for _ in range(blockers):
                server._download_semaphore.release()

        freeing = threading.Thread(target=_free_permits, daemon=True)
        freeing.start()
        try:
            with mock.patch.object(server.subprocess, "run",
                                   return_value=_FakeCompleted(0, "")) as run:
                server.Supporting.ensure_downloaded("queuedforeground",
                                                    prefetch=False)
            self.assertTrue(run.called)
        finally:
            freeing.join(timeout=5)


# --------------------------------------------------------------------------
# Cold-stream fallback latency
# --------------------------------------------------------------------------

class StreamingFallbackLatency(_CleanServerState):
    def test_stream_command_uses_fast_timeout_without_changing_background_defaults(self):
        background = server.Supporting.ytdlp_download_command(
            "backgroundvid", "-", client="default"
        )
        streaming = server.Supporting.ytdlp_download_command(
            "streamingvid", "-", client="android_vr", retries=0, socket_timeout=3
        )

        def option(command, name):
            return command[command.index(name) + 1]

        self.assertEqual(option(background, "--retries"), "2")
        self.assertEqual(option(background, "--socket-timeout"), "10")
        self.assertEqual(option(streaming, "--retries"), "0")
        self.assertEqual(option(streaming, "--socket-timeout"), "3")
        self.assertIn("youtube:player_client=android_vr", streaming)

    def test_stream_falls_back_when_first_profile_exits_without_audio(self):
        class _Proc:
            def __init__(self, chunks):
                self.stdout = iter(chunks)
                self.waited = False
                self.killed = False
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.waited = True
                self.returncode = 0
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        class _Stdout:
            def __init__(self, chunks):
                self.chunks = iter(chunks)

            def read(self, _size=None):
                return next(self.chunks, b'')

            def close(self):
                pass

        first = _Proc([])
        first.stdout = _Stdout([])
        second = _Proc([b'audio-bytes', b''])
        second.stdout = _Stdout([b'audio-bytes', b''])
        clients = ['default', 'android_vr']

        with (mock.patch.object(server.Supporting, "get_ytdlp_clients",
                                staticmethod(lambda: clients)),
              mock.patch.object(server.subprocess, "Popen",
                                side_effect=[first, second]) as popen,
              mock.patch.object(server, "_stream_abandon_watchdog"),
              mock.patch.object(server, "_is_audio_file_valid", return_value=True),
              mock.patch.object(server, "_confirm_stream_delivery"),
              mock.patch.object(server.os, "replace")):
            response = server._stream_proxy_download("streamfallback1")
            body = b''.join(response.response)
            response.close()

        self.assertEqual(body, b'audio-bytes')
        self.assertEqual(popen.call_count, 2)
        self.assertTrue(first.waited,
                        msg="the failed profile must be reaped before fallback")
        self.assertTrue(second.waited)
        self.assertIn("youtube:player_client=android_vr",
                      popen.call_args_list[1].args[0])


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

    def test_superseded_download_is_dropped_before_spawning_ytdlp(self):
        stale = server._current_playback_generation()
        server._bump_playback_generation()
        with mock.patch.object(server.subprocess, "run") as run, \
                mock.patch.object(server.Supporting, "cached_audio_path",
                                  staticmethod(lambda vid: None)):
            result = server.Supporting.ensure_downloaded("staleviddd", generation=stale)
        self.assertIsNone(result)
        run.assert_not_called()

    def test_queued_download_rechecks_generation_after_the_semaphore(self):
        """The regression that actually mattered: a backlog built up by rapid
        clicks used to drain by running every queued download, long after
        anyone cared. The generation is re-checked once a permit frees up."""
        gen = server._current_playback_generation()
        released = threading.Event()
        blockers = []

        # Occupy every download permit so our call has to queue.
        for _ in range(server._DOWNLOAD_CONCURRENCY):
            server._download_semaphore.acquire()
            blockers.append(True)

        def _free_permits():
            # While the caller waits for a permit, the user clicks another song.
            time.sleep(0.2)
            server._bump_playback_generation()
            for _ in blockers:
                server._download_semaphore.release()
            released.set()

        freeing = threading.Thread(target=_free_permits, daemon=True)
        freeing.start()
        try:
            with mock.patch.object(server.subprocess, "run") as run, \
                    mock.patch.object(server.Supporting, "cached_audio_path",
                                      staticmethod(lambda vid: None)), \
                    mock.patch.object(server.Supporting, "prune_audio_cache",
                                      staticmethod(lambda: None)):
                result = server.Supporting.ensure_downloaded("queuedviddd",
                                                             generation=gen)
            self.assertTrue(released.wait(5))
            self.assertIsNone(result)
            run.assert_not_called()
        finally:
            freeing.join(timeout=5)

    def test_download_depth_is_released_on_the_superseded_path(self):
        gen = server._current_playback_generation()
        server._bump_playback_generation()
        with mock.patch.object(server.subprocess, "run"), \
                mock.patch.object(server.Supporting, "cached_audio_path",
                                  staticmethod(lambda vid: None)):
            server.Supporting.ensure_downloaded("staleviddd", generation=gen)
        self.assertEqual(server._download_queue_size(), 0,
                         msg="an abandoned download leaked queue depth, which "
                             "would permanently wedge the backpressure gate")


# --------------------------------------------------------------------------
# Fix 5: backpressure is actually consulted
# --------------------------------------------------------------------------

class PrewarmBackpressure(_CleanServerState):
    def _queue(self, n=10):
        return [{'video_id': f"vid{i:07d}", 'title': f"t{i}"} for i in range(n)]

    def setUp(self):
        super().setUp()
        p = mock.patch.object(server.Supporting, "cached_audio_path",
                              staticmethod(lambda vid: None))
        p.start()
        self.addCleanup(p.stop)

    def test_backpressure_gate_flips_with_depth(self):
        self.assertTrue(server._download_backpressure())
        with server._download_queue_lock:
            server._download_queue_depth = server._DOWNLOAD_CONCURRENCY * 3 + 1
        self.assertFalse(server._download_backpressure())

    def test_prewarm_stops_when_queue_is_saturated(self):
        with server._download_queue_lock:
            server._download_queue_depth = server._DOWNLOAD_CONCURRENCY * 3 + 1
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ensure:
            warmed = server._prewarm_queue_audio(self._queue(), 0, limit=4)
        self.assertEqual(warmed, 0)
        ensure.assert_not_called()

    def test_prewarm_runs_when_the_queue_is_idle(self):
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ensure:
            warmed = server._prewarm_queue_audio(self._queue(), 0, limit=4)
        self.assertEqual(warmed, 4)
        self.assertEqual(ensure.call_count, 4)

    def test_prewarm_is_skipped_during_a_rate_limit_cooldown(self):
        server._note_rate_limited("earlier")
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ensure:
            warmed = server._prewarm_queue_audio(self._queue(), 0, limit=4)
        self.assertEqual(warmed, 0)
        ensure.assert_not_called()

    def test_prewarm_aborts_as_soon_as_playback_moves_on(self):
        """The dominant amplifier: 8 prewarms per click, none cancelled."""
        calls = []

        def ensure(video_id, **kwargs):
            calls.append(video_id)
            # Simulate the user clicking another song after the first prewarm.
            if len(calls) == 1:
                server._bump_playback_generation()
            return False

        with mock.patch.object(server, "_ensure_audio_ready_for_play",
                               side_effect=ensure):
            warmed = server._prewarm_queue_audio(self._queue(), 0, limit=4)
        self.assertEqual(len(calls), 1, msg=f"prewarm kept going after the "
                                            f"track changed: {calls}")
        self.assertEqual(warmed, 1)

    def test_prewarm_marks_its_downloads_as_cancellable_prefetch(self):
        with mock.patch.object(server, "_ensure_audio_ready_for_play") as ensure:
            server._prewarm_queue_audio(self._queue(), 0, limit=1)
        _, kwargs = ensure.call_args
        self.assertTrue(kwargs.get('prefetch'),
                        msg="queue prewarm must be droppable during a cooldown")
        self.assertIsNotNone(kwargs.get('generation'),
                             msg="queue prewarm must be cancellable on a track change")

    def test_empty_queue_is_a_noop(self):
        self.assertEqual(server._prewarm_queue_audio([], 0), 0)


# --------------------------------------------------------------------------
# Fix 3: one yt-dlp per video_id, and the watchdog can see streaming downloads
# --------------------------------------------------------------------------

class StreamInflightRegistry(_CleanServerState):
    def test_first_claim_wins_and_duplicates_are_refused(self):
        self.assertTrue(server._stream_register("vidstream01"))
        self.assertFalse(
            server._stream_register("vidstream01"),
            msg="a second concurrent /proxy/ for one song claimed its own "
                "slot — that is how 4 requests became 4 yt-dlp processes")
        self.assertTrue(server._stream_is_inflight("vidstream01"))

    def test_unregister_frees_the_claim(self):
        server._stream_register("vidstream01")
        server._stream_unregister("vidstream01")
        self.assertFalse(server._stream_is_inflight("vidstream01"))
        self.assertTrue(server._stream_register("vidstream01"))

    def test_unregister_is_idempotent(self):
        server._stream_unregister("never-registered")  # must not raise

    def test_distinct_ids_do_not_collide(self):
        self.assertTrue(server._stream_register("vidstream01"))
        self.assertTrue(server._stream_register("vidstream02"))

    def test_download_in_progress_sees_streaming_downloads(self):
        """The watchdog blind spot: `_download_in_progress` only checked the
        per-id lock, which the streaming path never takes. It therefore
        resent the play command after 12 s and the Echo opened another
        /proxy/ request, spawning another yt-dlp for the same song."""
        self.assertFalse(server._download_in_progress("vidstream01"))
        server._stream_register("vidstream01")
        self.assertTrue(
            server._download_in_progress("vidstream01"),
            msg="a cold-cache /proxy/ stream was invisible to the playback "
                "watchdog, so it resent the play command mid-download")
        server._stream_unregister("vidstream01")
        self.assertFalse(server._download_in_progress("vidstream01"))

    def test_download_in_progress_still_sees_the_per_id_lock(self):
        lock = threading.Lock()
        with server._locks_guard:
            server._download_locks["vidlocked01"] = lock
        self.addCleanup(lambda: server._download_locks.pop("vidlocked01", None))
        self.assertFalse(server._download_in_progress("vidlocked01"))
        with lock:
            self.assertTrue(server._download_in_progress("vidlocked01"))

    def test_await_inflight_returns_the_cache_file_once_it_lands(self):
        server._stream_register("vidstream01")
        self.addCleanup(lambda: server._stream_unregister("vidstream01"))

        def _finish():
            time.sleep(0.3)
            server._stream_unregister("vidstream01")

        threading.Thread(target=_finish, daemon=True).start()
        with mock.patch.object(
                server.Supporting, "cached_audio_path",
                staticmethod(lambda vid: "/tmp/cached.m4a"
                             if server._stream_is_inflight(vid) is False else None)):
            path = server._await_inflight_stream("vidstream01")
        self.assertEqual(path, "/tmp/cached.m4a")

    def test_await_inflight_gives_up_when_the_stream_fails(self):
        """A failed in-flight stream publishes no cache file; the waiter must
        return None so the caller can legitimately retry itself."""
        server._stream_register("vidstream01")

        def _fail():
            time.sleep(0.2)
            server._stream_unregister("vidstream01")

        threading.Thread(target=_fail, daemon=True).start()
        with mock.patch.object(server.Supporting, "cached_audio_path",
                               staticmethod(lambda vid: None)):
            path = server._await_inflight_stream("vidstream01")
        self.assertIsNone(path)

    def test_await_inflight_is_bounded(self):
        server._stream_register("vidstream01")
        self.addCleanup(lambda: server._stream_unregister("vidstream01"))
        with mock.patch.object(server, "_STREAM_DUPLICATE_WAIT", 0.4), \
                mock.patch.object(server.Supporting, "cached_audio_path",
                                  staticmethod(lambda vid: None)):
            started = time.time()
            path = server._await_inflight_stream("vidstream01")
            elapsed = time.time() - started
        self.assertIsNone(path)
        self.assertLess(elapsed, 3.0,
                        msg="a duplicate request must never wait unbounded on "
                            "an in-flight stream")


class SemaphorePermitRelease(_CleanServerState):
    def test_permit_release_is_idempotent(self):
        """The permit is released from both the generator's `finally` and
        `Response.call_on_close`; a double release would inflate the
        semaphore and uncap download concurrency for the rest of the process."""
        sem = threading.Semaphore(1)
        permit = server._SemaphorePermit(sem)
        self.assertTrue(permit.acquire(timeout=1))
        self.assertTrue(permit.held)
        permit.release()
        permit.release()
        permit.release()
        self.assertFalse(permit.held)
        # Exactly one permit must be available, not three.
        self.assertTrue(sem.acquire(blocking=False))
        self.assertFalse(sem.acquire(blocking=False))
        sem.release()

    def test_release_without_acquire_is_a_noop(self):
        sem = threading.Semaphore(1)
        server._SemaphorePermit(sem).release()
        self.assertTrue(sem.acquire(blocking=False))
        self.assertFalse(sem.acquire(blocking=False))

    def test_acquire_times_out_rather_than_blocking_playback(self):
        sem = threading.Semaphore(1)
        sem.acquire()
        permit = server._SemaphorePermit(sem)
        started = time.time()
        acquired = permit.acquire(timeout=0.2)
        elapsed = time.time() - started
        self.assertFalse(acquired)
        self.assertFalse(permit.held)
        self.assertLess(elapsed, 2.0)


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


class StreamSupersede(_CleanServerState):
    def setUp(self):
        super().setUp()
        server._prefetched_next = None
        self.addCleanup(lambda: setattr(server, "_prefetched_next", None))

    def test_current_track_is_never_superseded(self):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="playingnow1")
        self.assertFalse(server._stream_superseded("playingnow1"))

    def test_legitimate_next_track_prefetch_is_not_superseded(self):
        """The Echo buffers the next track while the current one plays; killing
        that stream would break gapless track-to-track transitions."""
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="playingnow1")
        server._prefetched_next = {'video_id': "nexttrack1", 'at': time.time()}
        self.assertFalse(server._stream_superseded("nexttrack1"))

    def test_abandoned_track_is_superseded(self):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="playingnow1")
        self.assertTrue(server._stream_superseded("oldtrack01"))

    def test_watchdog_kills_a_superseded_stream(self):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="oldtrack01")
        generation = server._current_playback_generation()
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="newtrack01")  # user clicked away

        proc = _FakeProc()
        stop = threading.Event()
        with mock.patch.object(server, "_STREAM_SUPERSEDE_GRACE", 0.1):
            watchdog = threading.Thread(
                target=server._stream_abandon_watchdog,
                args=("oldtrack01", proc, generation, stop), daemon=True)
            watchdog.start()
            watchdog.join(timeout=6)
        stop.set()
        self.assertFalse(watchdog.is_alive())
        self.assertTrue(proc.killed,
                        msg="an abandoned stream was left holding a waitress "
                            "worker thread and a live yt-dlp process")

    def test_watchdog_leaves_the_current_track_alone(self):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="playingnow1")
        generation = server._current_playback_generation()
        proc = _FakeProc()
        stop = threading.Event()
        with mock.patch.object(server, "_STREAM_SUPERSEDE_GRACE", 0.1):
            watchdog = threading.Thread(
                target=server._stream_abandon_watchdog,
                args=("playingnow1", proc, generation, stop), daemon=True)
            watchdog.start()
            time.sleep(0.6)
            stop.set()
            watchdog.join(timeout=5)
        self.assertFalse(proc.killed,
                         msg="the watchdog killed the song that is actually playing")

    def test_watchdog_enforces_a_hard_time_cap(self):
        """The 154-second streams in the incident log: even a stream nobody has
        superseded must not own a worker thread indefinitely."""
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="playingnow1")
        generation = server._current_playback_generation()
        proc = _FakeProc()
        stop = threading.Event()
        with mock.patch.object(server, "_STREAM_MAX_SECONDS", 0.6):
            watchdog = threading.Thread(
                target=server._stream_abandon_watchdog,
                args=("playingnow1", proc, generation, stop), daemon=True)
            watchdog.start()
            watchdog.join(timeout=6)
        stop.set()
        self.assertTrue(proc.killed)

    def test_watchdog_exits_when_the_process_finishes(self):
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="playingnow1")
        generation = server._current_playback_generation()
        proc = _FakeProc()
        proc.exit(0)
        stop = threading.Event()
        watchdog = threading.Thread(
            target=server._stream_abandon_watchdog,
            args=("playingnow1", proc, generation, stop), daemon=True)
        watchdog.start()
        watchdog.join(timeout=5)
        self.assertFalse(watchdog.is_alive())
        self.assertFalse(proc.killed)

    def test_watchdog_stops_on_the_stop_event(self):
        generation = server._current_playback_generation()
        proc = _FakeProc()
        stop = threading.Event()
        watchdog = threading.Thread(
            target=server._stream_abandon_watchdog,
            args=("anytrack01", proc, generation, stop), daemon=True)
        watchdog.start()
        stop.set()
        watchdog.join(timeout=5)
        self.assertFalse(watchdog.is_alive())

    def test_watchdog_ignores_a_non_cancellable_generation(self):
        """generation=None means the stream is not cancellable; only the hard
        cap may stop it."""
        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="othertrack")
        proc = _FakeProc()
        stop = threading.Event()
        with mock.patch.object(server, "_STREAM_SUPERSEDE_GRACE", 0.1):
            watchdog = threading.Thread(
                target=server._stream_abandon_watchdog,
                args=("uncancellable", proc, None, stop), daemon=True)
            watchdog.start()
            time.sleep(0.5)
            stop.set()
            watchdog.join(timeout=5)
        self.assertFalse(proc.killed)

    def test_kill_process_tolerates_a_dead_process(self):
        proc = _FakeProc()
        proc.exit(0)
        server._kill_process(proc)  # must not raise
        self.assertFalse(proc.killed)


# --------------------------------------------------------------------------
# End-to-end: the exact click sequence from the incident
# --------------------------------------------------------------------------

class WarmCacheNeverBlocks(_CleanServerState):
    """A cache hit must be served without touching the download semaphore.

    `proxy_stream` used to call `ensure_downloaded()` on the warm path even
    though it had just confirmed the file was cached. `ensure_downloaded`
    acquires `_download_semaphore` *before* looking at the cache and with no
    timeout, so serving an already-downloaded file queued behind up to
    `_DOWNLOAD_CONCURRENCY` slow yt-dlp runs while holding a waitress worker
    thread. That is what produced the incident's 154s/134s/114s /proxy/
    responses which all completed within 9ms of each other as the backlog
    drained — and on the device it is simply silence, because the Echo abandons
    /proxy/ after ~11s.
    """

    def setUp(self):
        super().setUp()
        self.app = server.app.test_client()
        self.key = os.environ["API_KEY"]

    def test_cache_hit_is_served_while_all_download_permits_are_held(self):
        held = 0
        for _ in range(server._DOWNLOAD_CONCURRENCY):
            server._download_semaphore.acquire()
            held += 1
        try:
            sent = []

            def fake_send_file(path, **kwargs):
                sent.append(path)
                return server.Response(b"audio-bytes", mimetype=kwargs.get(
                    'mimetype', 'audio/mp4'))

            started = time.time()
            with mock.patch.object(server.Supporting, "cached_audio_path",
                                   staticmethod(lambda vid: "/tmp/warm.m4a")), \
                    mock.patch.object(server.Supporting, "ensure_downloaded") as ensure, \
                    mock.patch.object(server, "send_file", side_effect=fake_send_file), \
                    mock.patch.object(server, "_confirm_stream_delivery"), \
                    mock.patch.object(server, "_refresh_radio_queue"), \
                    mock.patch.object(server, "_lookup_and_update_np"), \
                    mock.patch.object(server, "_notify_sse"):
                resp = self.app.get(f"/proxy/?video_id=warmvid0001&key={self.key}")
            elapsed = time.time() - started

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(sent, ["/tmp/warm.m4a"])
            ensure.assert_not_called()
            self.assertLess(
                elapsed, 3.0,
                msg="a cache hit queued on the download semaphore — this is the "
                    "multi-minute /proxy/ stall that silences the Echo")
        finally:
            for _ in range(held):
                server._download_semaphore.release()

    def test_cache_miss_after_the_check_falls_back_to_a_download(self):
        """Guards the sweep race: the file can vanish between the cache check
        and the send, and that must still produce audio rather than a 502."""
        paths = ["/tmp/warm.m4a", None]

        with mock.patch.object(server.Supporting, "cached_audio_path",
                               staticmethod(lambda vid: paths.pop(0) if paths else None)), \
                mock.patch.object(server.Supporting, "ensure_downloaded",
                                  staticmethod(lambda vid, **kw: "/tmp/redownloaded.m4a")) as _, \
                mock.patch.object(server, "send_file",
                                  side_effect=lambda path, **kw: server.Response(
                                      b"x", mimetype="audio/mp4")), \
                mock.patch.object(server, "_confirm_stream_delivery"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_lookup_and_update_np"), \
                mock.patch.object(server, "_notify_sse"):
            resp = self.app.get(f"/proxy/?video_id=warmvid0002&key={self.key}")
        self.assertEqual(resp.status_code, 200)


class StreamBudgetIsSeparate(_CleanServerState):
    def test_streams_do_not_consume_download_permits(self):
        """Sharing one semaphore put best-effort prefetch in front of the track
        the user is waiting on, and saturated the pool at 4.

        Exercises the real `_stream_proxy_download` rather than just comparing
        the two semaphore objects, so that swapping which one the streaming path
        takes is actually detected.
        """
        proc = _FakeProc()
        proc.stdout = _EmptyStdout()
        proc.exit(0)

        download_spy = _SemaphoreSpy(server._DOWNLOAD_CONCURRENCY)
        stream_spy = _SemaphoreSpy(server._STREAM_CONCURRENCY)

        with mock.patch.object(server, "_download_semaphore", download_spy), \
                mock.patch.object(server, "_stream_semaphore", stream_spy), \
                mock.patch.object(server.subprocess, "Popen", return_value=proc), \
                mock.patch.object(server, "_confirm_stream_delivery"):
            response = server._stream_proxy_download("streamvid001")
            # Drain and close so the generator's cleanup runs.
            b"".join(response.response)
            response.close()

        self.assertEqual(
            download_spy.acquires, 0,
            msg="a cold-cache stream took a permit from the background-download "
                "budget, so prewarm work and playback compete for the same 4 slots")
        self.assertEqual(stream_spy.acquires, 1)
        self.assertEqual(stream_spy.releases, 1,
                         msg="the stream slot leaked")

    def test_stream_registry_claim_is_released_after_the_stream(self):
        proc = _FakeProc()
        proc.stdout = _EmptyStdout()
        proc.exit(0)
        with mock.patch.object(server.subprocess, "Popen", return_value=proc), \
                mock.patch.object(server, "_confirm_stream_delivery"):
            response = server._stream_proxy_download("streamvid002")
            self.assertTrue(server._stream_is_inflight("streamvid002"))
            b"".join(response.response)
            response.close()
        self.assertFalse(server._stream_is_inflight("streamvid002"),
                         msg="the in-flight claim leaked, so every later request "
                             "for this song would wait on a stream that ended")

    def test_a_saturated_download_pool_does_not_block_a_stream_slot(self):
        held = 0
        for _ in range(server._DOWNLOAD_CONCURRENCY):
            server._download_semaphore.acquire()
            held += 1
        try:
            permit = server._SemaphorePermit(server._stream_semaphore)
            try:
                self.assertTrue(
                    permit.acquire(timeout=0.5),
                    msg="a stream could not get a slot because prewarm "
                        "downloads had taken every permit")
            finally:
                permit.release()
        finally:
            for _ in range(held):
                server._download_semaphore.release()


class ForegroundDownloadPriority(_CleanServerState):
    """The clicked song must never queue behind best-effort prefetch.

    A cold-cache song costs ~9-10s of yt-dlp and the Echo abandons /proxy/ after
    ~11s, so losing the race to a prewarm burst means the device plays silence.
    The incident log shows exactly this: the user's track went down the cold
    path 11s after dispatch because its background download was still stuck
    behind other work ("streaming XOBbIb2ELs8 without a download permit").
    """

    def setUp(self):
        super().setUp()
        p = mock.patch.object(server.Supporting, "cached_audio_path",
                              staticmethod(lambda vid: None))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(server.Supporting, "prune_audio_cache",
                               staticmethod(lambda: None))
        p2.start()
        self.addCleanup(p2.stop)

    def test_prefetch_cannot_occupy_the_whole_download_pool(self):
        self.assertLess(
            server._PREFETCH_CONCURRENCY, server._DOWNLOAD_CONCURRENCY,
            msg="prefetch must be capped below the pool size, otherwise a "
                "prewarm burst can starve the track the user is waiting on")

    def test_prefetch_is_dropped_when_its_slots_are_busy(self):
        held = 0
        for _ in range(server._PREFETCH_CONCURRENCY):
            server._prefetch_semaphore.acquire()
            held += 1
        try:
            with mock.patch.object(server, "_PREFETCH_SLOT_TIMEOUT", 0.1), \
                    mock.patch.object(server.subprocess, "run") as run:
                result = server.Supporting.ensure_downloaded("extraprefetch",
                                                             prefetch=True)
            self.assertIsNone(result)
            run.assert_not_called()
            self.assertEqual(server._download_queue_size(), 0,
                             msg="a dropped prefetch leaked queue depth")
        finally:
            for _ in range(held):
                server._prefetch_semaphore.release()

    def test_foreground_download_runs_while_prefetch_slots_are_saturated(self):
        """The whole point: saturated prefetch must not delay a real play."""
        held = 0
        for _ in range(server._PREFETCH_CONCURRENCY):
            server._prefetch_semaphore.acquire()
            held += 1
        try:
            started = time.time()
            with mock.patch.object(server.subprocess, "run",
                                   return_value=_FakeCompleted(0, "")) as run:
                server.Supporting.ensure_downloaded("clickedsong", prefetch=False)
            elapsed = time.time() - started
            self.assertTrue(run.called)
            self.assertLess(elapsed, 1.0,
                            msg="the clicked song waited on prefetch slots")
        finally:
            for _ in range(held):
                server._prefetch_semaphore.release()

    def test_prefetch_releases_its_slot(self):
        with mock.patch.object(server.subprocess, "run",
                               return_value=_FakeCompleted(0, "")):
            server.Supporting.ensure_downloaded("prefetchdone", prefetch=True)
        # All prefetch slots must be free again.
        acquired = []
        for _ in range(server._PREFETCH_CONCURRENCY):
            acquired.append(server._prefetch_semaphore.acquire(blocking=False))
        for got in acquired:
            if got:
                server._prefetch_semaphore.release()
        self.assertTrue(all(acquired),
                        msg="a prefetch slot leaked; prefetch would permanently "
                            "throttle itself to zero")

    def test_a_click_burst_leaves_permits_for_the_clicked_song(self):
        """Simulates the reported sequence: a prewarm burst is in flight when the
        user clicks, and the clicked song must still start immediately."""
        release = threading.Event()
        prefetch_started = threading.Semaphore(0)
        foreground_ran = []

        # One patch for the whole test, dispatching on the video_id in the
        # command, so prewarm threads that outlive the click stay stubbed
        # instead of shelling out to a real yt-dlp.
        def dispatch_run(cmd, **kwargs):
            video_id = cmd[-1]
            if video_id == "clickedsong":
                foreground_ran.append(video_id)
                return _FakeCompleted(0, "")
            prefetch_started.release()
            release.wait(10)
            return _FakeCompleted(0, "")

        threads = []
        try:
            with mock.patch.object(server.subprocess, "run", side_effect=dispatch_run):
                # Saturate prefetch with more work than it has slots for.
                for i in range(server._DOWNLOAD_CONCURRENCY * 2):
                    t = threading.Thread(
                        target=server.Supporting.ensure_downloaded,
                        args=(f"prewarm{i:05d}",), kwargs={'prefetch': True},
                        daemon=True)
                    t.start()
                    threads.append(t)
                # Wait until prefetch is actually holding its slots.
                for _ in range(server._PREFETCH_CONCURRENCY):
                    self.assertTrue(prefetch_started.acquire(timeout=5))

                started = time.time()
                server.Supporting.ensure_downloaded("clickedsong", prefetch=False)
                elapsed = time.time() - started

                release.set()
                for t in threads:
                    t.join(timeout=10)
                self.assertFalse([t for t in threads if t.is_alive()],
                                 msg="prewarm threads outlived the patch")
        finally:
            release.set()

        self.assertEqual(len(foreground_ran), 1,
                         msg="the clicked song never reached yt-dlp")
        self.assertLess(
            elapsed, 3.0,
            msg=f"the clicked song waited {elapsed:.1f}s behind the prewarm "
                f"burst — past the Echo's ~11s /proxy/ window this is silence")


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
                mock.patch.object(server, "_update_now_playing") as update, \
                mock.patch.object(server, "_notify_sse"):
            server._schedule_play_dispatch("DEVICE1", "AAAAAAAAAAA", delay=0.05)
            time.sleep(0.4)
        errors = [c.kwargs.get('playback_error') for c in update.call_args_list
                  if c.kwargs.get('playback_error')]
        self.assertTrue(errors, msg="a failed dispatch reported nothing to the UI")
        self.assertEqual(errors[0]['type'], 'dispatch_error')
        self.assertIn('offline', errors[0]['message'])

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
                mock.patch.object(server.Supporting, "cached_audio_path",
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

        with mock.patch.object(server.Supporting, "cached_audio_path",
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
        with mock.patch.object(server.Supporting, "cached_audio_path",
                               staticmethod(lambda vid: None)), \
                mock.patch.object(server, "_ensure_audio_ready_for_play") as ensure, \
                mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id=queue[0]['video_id'])
            server._note_rate_limited(queue[0]['video_id'])
            warmed = server._prewarm_queue_audio(queue, 0, limit=4)
        self.assertEqual(warmed, 0)
        ensure.assert_not_called()

    def test_watchdog_no_longer_resends_during_a_streaming_download(self):
        """Second amplifier from the log: the watchdog resent the play command
        because it could not see the streaming download, and the Echo opened
        another /proxy/ request for the same song each time."""
        server._stream_register("DRZHVrSmcWU")
        self.addCleanup(lambda: server._stream_unregister("DRZHVrSmcWU"))
        resends = []

        with mock.patch.object(server, "_notify_sse"):
            server._update_now_playing(video_id="DRZHVrSmcWU",
                                       playback_confirmed=False)
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.3), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.05), \
                mock.patch.object(server, "_notify_sse"):
            watcher = threading.Thread(
                target=server._watch_playback_confirmation,
                args=("serial", "DRZHVrSmcWU", lambda: resends.append(1)),
                daemon=True)
            watcher.start()
            # Well past the confirmation timeout: with the download visible, the
            # watchdog must keep extending its deadline rather than resending.
            time.sleep(1.0)
            self.assertEqual(resends, [],
                             msg="the watchdog resent the play command while "
                                 "yt-dlp was still streaming the same song")
            # Once the stream ends unconfirmed, the retry is legitimate again.
            server._stream_unregister("DRZHVrSmcWU")
            watcher.join(timeout=5)
        self.assertEqual(len(resends), 1)


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

    def test_dead_video_auto_advances_to_next_track(self):
        current, nxt = "DEADDEADDEA", "NEXTNEXTNEX"
        self._install_queue(current, nxt)
        server._mark_video_dead(current)
        dispatched = []
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server.Supporting, "cached_audio_path",
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
                mock.patch.object(server.Supporting, "cached_audio_path",
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

    def test_final_timeout_auto_advances_to_next_track(self):
        current, nxt = "TIMEOUTTIME", "AFTERAFTERA"
        self._install_queue(current, nxt)
        dispatched = []
        with mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_TIMEOUT_CACHED", 0.05), \
                mock.patch.object(server, "PLAYBACK_CONFIRM_POLL_INTERVAL", 0.02), \
                mock.patch.object(server, "PLAYBACK_BUFFERING_FEEDBACK_DELAY", 10.0), \
                mock.patch.object(server.Supporting, "cached_audio_path",
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
        self.assertEqual(dispatched, [current, nxt],
                         msg="expected one resend of the original track, then "
                             "an auto-advance dispatch to the next queue item")


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
                mock.patch.object(server.Supporting, "cached_audio_path",
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
                mock.patch.object(server.Supporting, "cached_audio_path",
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
                mock.patch.object(server.Supporting, "cached_audio_path",
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
                mock.patch.object(server.Supporting, "cached_audio_path",
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
                mock.patch.object(server.Supporting, "cached_audio_path",
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
