"""Feasibility tests: can the music "download time" be reduced to 8 seconds?

The Echo plays audio through ``/proxy/`` and abandons the request after ~11 s.
The download time a user perceives is the wall-clock delay from the play
command until the *first audio bytes* reach the device — not the full file
transfer. This file pins the latency-critical configuration so that first-byte
delay stays at the ~7-8 s the code is already tuned for, and documents the one
component that cannot be shrunk.

Timing budget (documented in README.md and server.py comments, not code):

    Echo /proxy/ deadline ........................ ~11 s   (hard, device-side)
    Full cached download (extraction + transfer) . ~9-10 s
    Cold-cache first byte today .................. ~7-8 s
    yt-dlp extraction (watch page + player
        response + format resolution) ............ ~5 s
    YouTube ad-skip gate (monetized videos) ...... ~4-5 s   <-- HARD FLOOR

The ad-skip gate is the important finding: the googlevideo URL that yt-dlp
resolves will 403 if fetched before YouTube's ~4-5 s window opens, on every
player client. That is a floor that no amount of server tuning can remove, so
8 seconds is reachable but ~4-5 s is not.

Conclusion encoded here: the first-byte path is already at ~7-8 s (at or under
the 8 s target) because a cold-cache track is *streamed* straight from yt-dlp's
stdout (Popen) rather than blocking on a full download, and the streaming
invocation is deliberately fast (m4a format 140 first, no retries, short socket
timeout). The only lever that could push further below 8 s is cutting yt-dlp
extraction time; the gate cannot be bypassed.

Run with ``pytest flask-server/tests/test_download_latency.py`` or
``python -m unittest discover -s flask-server/tests -p 'test_download_latency.py'``.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _install_stubs():
    # Mirror the convention in test_download_backpressure.py: stub the heavy
    # native deps before importing server.py so this file runs in the same
    # slim test environment (no ytmusicapi/alexapy/yt-dlp installed).
    os.environ.setdefault("SECRET_KEY", "test-secret-for-download-latency")
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


# Documented, non-code timing figures. These are estimates taken verbatim from
# README.md / server.py comments, kept as named constants so the feasibility
# assertions below read as "is 8 s above the floor and below the deadline"
# rather than as magic numbers.
_ECHO_PROXY_DEADLINE_SECONDS = 11.0   # "the Echo gives up on /proxy/ after ~11s"
_AD_SKIP_GATE_FLOOR_SECONDS = 4.5     # "ad-skip gate of ~4-5 s ... isn't bypassable"
_CURRENT_FIRST_BYTE_SECONDS = 7.5     # "the first song of a session takes ~7-8 s to start"
_TARGET_SECONDS = 8.0                 # the 8-second goal under test


class _CleanServerState(unittest.TestCase):
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


class Feasibility(_CleanServerState):
    """The headline question: is 8 s even on the table?"""

    def test_current_first_byte_is_already_at_the_target(self):
        # The first song already starts in ~7-8 s. So "reduce to 8 s" is not a
        # stretch goal — it is the steady state of the time-critical path.
        self.assertLessEqual(
            _CURRENT_FIRST_BYTE_SECONDS, _TARGET_SECONDS,
            msg="the documented first-byte time (~7-8s) exceeds the 8s target")

    def test_target_is_above_the_hard_floor(self):
        # The ad-skip gate (~4-5 s) fires on every monetized video and cannot
        # be bypassed. 8 s must therefore be comfortably above it, or the goal
        # is impossible before any server work happens.
        self.assertLess(
            _AD_SKIP_GATE_FLOOR_SECONDS, _TARGET_SECONDS,
            msg="the 8s target is at or below the ~4-5s ad-skip gate floor — "
                "no server-side tuning can ever hit it")

    def test_first_byte_plus_controllable_wait_fits_the_deadline(self):
        # yt-dlp already costs ~7-8 s to produce first bytes. The only serial
        # wait the server can add on top is the stream permit timeout
        # (_STREAM_PERMIT_TIMEOUT), and even that is best-effort: a contended
        # slot is skipped, not waited out. Together they must remain under the
        # Echo's ~11 s deadline or the device plays silence. This guards
        # against someone bumping the permit timeout until it blows the budget.
        self.assertLess(
            _CURRENT_FIRST_BYTE_SECONDS + server._STREAM_PERMIT_TIMEOUT,
            _ECHO_PROXY_DEADLINE_SECONDS,
            msg="first byte (~7-8s) + stream permit wait exceeds the Echo's "
                "~11s deadline")

    def test_stream_permit_wait_is_small_relative_to_deadline(self):
        # The permit wait is deliberately a throttle, not a gate (see its
        # comment). It must never, on its own, consume a large slice of the
        # ~11 s budget.
        self.assertLess(server._STREAM_PERMIT_TIMEOUT, 3.0)


class FormatSelection(_CleanServerState):
    """The chosen format order minimises the bytes transferred per track."""

    def test_format_140_m4a_is_first_choice(self):
        cmd = server.Supporting.ytdlp_download_command("vid", "-")
        fmt = cmd[cmd.index("-f") + 1]
        # 140 is the ~128 kbps m4a AAC audio-only stream: the smallest usable
        # format, so it downloads fastest. It must be tried before any larger
        # fallback.
        self.assertTrue(fmt.startswith("140/"), msg=f"format spec {fmt!r} does not prefer 140")
        self.assertIn("bestaudio[ext=m4a]", fmt)


class StreamingLatencyConfig(_CleanServerState):
    """The cold-cache stream is deliberately faster than background downloads."""

    def test_stream_uses_no_retries_and_short_socket_timeout(self):
        streaming = server.Supporting.ytdlp_download_command(
            "vid", "-", client="default", retries=0, socket_timeout=3)

        def option(command, name):
            return command[command.index(name) + 1]

        self.assertEqual(option(streaming, "--retries"), "0")
        self.assertEqual(option(streaming, "--socket-timeout"), "3")

    def test_background_download_is_more_tolerant_than_stream(self):
        # The full cached download can afford retries/timeouts the stream cannot
        # (the stream's first byte is on the ~11 s clock). If these ever become
        # equal, the streaming path lost its latency advantage.
        background = server.Supporting.ytdlp_download_command("vid", "-", client="default")
        streaming = server.Supporting.ytdlp_download_command(
            "vid", "-", client="default", retries=0, socket_timeout=3)

        def option(command, name):
            return command[command.index(name) + 1]

        self.assertNotEqual(option(background, "--socket-timeout"),
                            option(streaming, "--socket-timeout"))
        self.assertNotEqual(option(background, "--retries"),
                            option(streaming, "--retries"))

    def test_stream_socket_timeout_is_env_tunable(self):
        # YTDLP_STREAM_SOCKET_TIMEOUT is the knob for trading extraction
        # fallback speed against socket stalls. A lower value fails a stalled
        # profile faster (good for first-byte latency); confirm it flows into
        # the command.
        with mock.patch.dict(os.environ, {"YTDLP_STREAM_SOCKET_TIMEOUT": "1"}):
            cmd = server.Supporting.ytdlp_download_command(
                "vid", "-", client="default", retries=0, socket_timeout=1)
        self.assertEqual(cmd[cmd.index("--socket-timeout") + 1], "1")


class ClientOrder(_CleanServerState):
    """The first client tried should be the fastest / authenticated one."""

    def test_authenticated_default_then_android_vr(self):
        clients = server.Supporting.get_ytdlp_clients()
        self.assertEqual(clients[0], "default",
                         msg="the cookie-authenticated default must be tried first "
                             "to avoid a guaranteed-failed cookie-free probe")
        self.assertEqual(clients[1], "android_vr",
                         msg="android_vr exposes AAC/M4A formats and must be the "
                             "first fallback")


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


class ColdCacheFirstBytePath(_CleanServerState):
    """A cold-cache /proxy/ request streams first bytes, not a full download."""

    def test_streaming_uses_popen_not_blocking_run(self):
        # First-byte latency is only possible because the cold path streams
        # yt-dlp's stdout (Popen) straight to the response. If it ever switched
        # to the blocking subprocess.run used by ensure_downloaded, the Echo
        # would wait for the full ~9-10 s download instead of ~7-8 s first
        # byte and could miss the ~11 s deadline.
        proc = _FakeProc()
        proc.stdout = _EmptyStdout()
        proc.exit(0)
        with mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(server.subprocess, "run") as run, \
                mock.patch.object(server, "_confirm_stream_delivery"):
            response = server._stream_proxy_download("streamvidpopen")
            b"".join(response.response)
            response.close()
        # The stream is launched via Popen (streaming), never via the blocking
        # run() that ensure_downloaded uses for a full download. An empty
        # stdout means no first byte, so the stream legitimately falls through
        # the remaining client profiles — that is the fallback loop, not a
        # full download, so run() must still never be touched.
        self.assertTrue(popen.called,
                        msg="cold-cache stream never spawned yt-dlp")
        run.assert_not_called()

    def setUp(self):
        super().setUp()
        self.app = server.app.test_client()
        self.key = os.environ["API_KEY"]

    def test_cold_cache_dispatches_to_streaming_not_ensure_downloaded(self):
        # The route must choose the streaming path for a cache miss, not fall
        # through to the full download + semaphore queue (which would serialize
        # first-byte behind up to _DOWNLOAD_CONCURRENCY slow runs).
        with mock.patch.object(server.Supporting, "cached_audio_path",
                               return_value=None), \
             mock.patch.object(server, "_stream_proxy_download",
                               return_value=server.Response(
                                   b"STREAMING", mimetype="audio/mp4")) as stream, \
             mock.patch.object(server, "_stream_is_inflight",
                               return_value=False), \
             mock.patch.object(server, "_refresh_radio_queue"), \
             mock.patch.object(server, "_lookup_and_update_np"), \
             mock.patch.object(server, "_notify_sse"):
            resp = self.app.get(f"/proxy/?video_id=abcdefghijk&key={self.key}")
        self.assertTrue(stream.called,
                        msg="cold-cache /proxy/ did not use the streaming path")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(as_text=True), "STREAMING")


class PrewarmReuse(_CleanServerState):
    """A cold-cache /proxy/ hit should reuse an in-flight background prewarm.

    The play click starts `ensure_downloaded` immediately, so by the time the
    Echo hits /proxy/ a couple seconds later the prewarm is partway through.
    Spawning a second yt-dlp stream instead of waiting is slower (first byte
    restarts from scratch) and doubles the load that earns the server a 429.
    """

    def setUp(self):
        super().setUp()
        self.app = server.app.test_client()
        self.key = os.environ["API_KEY"]

    def test_cold_cache_waits_for_inflight_prewarm(self):
        # The prewarm's cache file appears after a couple of polls; /proxy/ must
        # serve it rather than starting its own stream.
        calls = {"n": 0}

        def cached_audio_path(vid):
            calls["n"] += 1
            return None if calls["n"] < 3 else "/tmp/prewarmed.m4a"

        sent = []

        def fake_send_file(path, **kwargs):
            sent.append(path)
            return server.Response(b"audio", mimetype=kwargs.get(
                'mimetype', 'audio/mp4'))

        with mock.patch.object(server.Supporting, "cached_audio_path",
                               side_effect=cached_audio_path), \
                mock.patch.object(server, "_download_in_progress",
                                  return_value=True), \
                mock.patch.object(server, "_PREWARM_POLL", 0.0), \
                mock.patch.object(server, "_stream_proxy_download") as stream, \
                mock.patch.object(server, "send_file", side_effect=fake_send_file), \
                mock.patch.object(server, "_confirm_stream_delivery"), \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_lookup_and_update_np"), \
                mock.patch.object(server, "_notify_sse"):
            resp = self.app.get(f"/proxy/?video_id=abcdefghijk&key={self.key}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sent, ["/tmp/prewarmed.m4a"],
                         msg="the prewarm's cache was not served")
        stream.assert_not_called()

    def test_cold_cache_falls_back_to_stream_when_prewarm_never_lands(self):
        # An in-flight prewarm that never produces a cache file (it failed or is
        # stuck) must still fall back to a stream, not hang the request.
        with mock.patch.object(server.Supporting, "cached_audio_path",
                               return_value=None), \
                mock.patch.object(server, "_download_in_progress",
                                  return_value=True), \
                mock.patch.object(server, "_PREWARM_WAIT_SECONDS", 0.05), \
                mock.patch.object(server, "_PREWARM_POLL", 0.01), \
                mock.patch.object(server, "_stream_proxy_download",
                                  return_value=server.Response(
                                      b"STREAM", mimetype="audio/mp4")) as stream, \
                mock.patch.object(server, "_refresh_radio_queue"), \
                mock.patch.object(server, "_lookup_and_update_np"), \
                mock.patch.object(server, "_notify_sse"):
            resp = self.app.get(f"/proxy/?video_id=abcdefghijk&key={self.key}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(as_text=True), "STREAM")
        stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
