#!/usr/bin/env python3
"""Measure real yt-dlp download time (first byte + full download) for one video.

Reuses server.py's own `ytdlp_download_command` / `get_ytdlp_clients` so the
numbers reflect exactly what the Echo's /proxy/ path runs, not a hand-rolled
approximation. Not a unit test: it needs yt-dlp, network, and a cookies.txt.

Usage:
    .venv/bin/python scripts/benchmark_download.py [VIDEO_ID]

Default VIDEO_ID is a monetized music video (the ad-skip-gate scenario).
Set YTDLP_BGUTIL_BASE_URL="" to skip the bgutil PO-token sidecar (local dev).

Run it *inside* the running ytmusic container (which has deno, the bgutil
PO-token sidecar, cookies, and egresses through the VPN), e.g.:
    docker exec -i ytmusic python3 - < scripts/benchmark_download.py
Running it from a raw datacenter IP will fail every client with 403 / "only
images are available" — the exact bot-check this project's fallback chain
works around — so the timing numbers will not be meaningful there.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_SERVER = os.path.join(os.path.dirname(HERE), "flask-server")
sys.path.insert(0, FLASK_SERVER)

# Local dev has no bgutil sidecar; opt out so yt-dlp doesn't wait on it.
os.environ.setdefault("YTDLP_BGUTIL_BASE_URL", "")
os.environ.setdefault("SECRET_KEY", "benchmark-secret")
os.environ.setdefault("API_KEY", "0123456789abcdef0123456789abcdef")

# Stub the heavy deps (not installed) so server.py imports; mirror the test
# convention. yt-dlp itself IS installed, so no stub is needed for it.
import types  # noqa: E402
if "ytmusicapi" not in sys.modules:
    ytmusicapi = types.ModuleType("ytmusicapi")
    ytmusicapi.YTMusic = type("YTMusic", (), {"__init__": lambda self, **kw: None})
    sys.modules["ytmusicapi"] = ytmusicapi
    ytmusicapi_auth = types.ModuleType("ytmusicapi.auth")
    ytmusicapi_auth_types = types.ModuleType("ytmusicapi.auth.types")
    ytmusicapi_auth_types.AuthType = type("AuthType", (), {"UNAUTHORIZED": "UNAUTHORIZED"})
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
    alexa_remote_remote.devices = lambda refresh=False: (None, "stubbed")
    alexa_remote_remote.volume = lambda serial: (None, "stubbed")
    alexa_remote_remote.is_logged_in = lambda: (True, None)
    alexa_remote_remote.proxy_start_url = lambda *a, **kw: (None, "stubbed")
    alexa_remote.remote = alexa_remote_remote
    sys.modules["alexa_remote"] = alexa_remote
    sys.modules["alexa_remote.remote"] = alexa_remote_remote
if "youtube_browser_session" not in sys.modules:
    # Prefer the real module (it has no import-time side effects); only stub if
    # it truly cannot be imported.
    try:
        import youtube_browser_session  # noqa: F401
    except Exception:
        ybs = types.ModuleType("youtube_browser_session")
        for attr in ("BrowserController", "YouTubeBrowserSessionManager"):
            setattr(ybs, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))
        ybs.browser_client_is_signed_in = lambda *a, **kw: False
        ybs.is_authentication_error = lambda *a, **kw: False
        ybs.promote_browser_headers = lambda *a, **kw: None
        sys.modules["youtube_browser_session"] = ybs

import server  # noqa: E402

STREAM_TIMEOUT = max(1, int(os.environ.get("YTDLP_STREAM_SOCKET_TIMEOUT", "3")))
STREAM_RETRIES = max(0, int(os.environ.get("YTDLP_STREAM_RETRIES", "0")))


def time_first_byte(video_id: str, client: str):
    """Spawn the exact streaming command and time until the first stdout byte."""
    cmd = server.Supporting.ytdlp_download_command(
        video_id, "-", client=client, retries=STREAM_RETRIES, socket_timeout=STREAM_TIMEOUT)
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=64 * 1024)
    first = proc.stdout.read(64 * 1024)
    first_byte = time.time() - t0
    # Drain so the process can finish; then wait.
    try:
        while proc.stdout.read(64 * 1024):
            pass
    except Exception:
        pass
    proc.wait(timeout=150)
    return first_byte, len(first), proc.returncode


def time_full_download(video_id: str, client: str):
    """Time a full background download (spawn -> exit), like ensure_downloaded."""
    import tempfile
    out = os.path.join(tempfile.gettempdir(), f"bench_{video_id}.%(ext)s")
    cmd = server.Supporting.ytdlp_download_command(video_id, out, client=client)
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
    elapsed = time.time() - t0
    return elapsed, result.returncode


def main():
    video_id = sys.argv[1] if len(sys.argv) > 1 else "dQw4w9WgXcQ"
    clients = server.Supporting.get_ytdlp_clients()
    print(f"video={video_id}  clients={clients}  "
          f"stream_socket_timeout={STREAM_TIMEOUT}s stream_retries={STREAM_RETRIES}")
    print(f"cookies_file={server.Supporting.get_ytdlp_cookies_file()}")
    print("-" * 64)

    for client in ["default", "android_vr"]:
        try:
            fb, nbytes, rc = time_first_byte(video_id, client)
            print(f"[{client}] first-byte: {fb:5.2f}s  ({nbytes} bytes, rc={rc})")
        except Exception as exc:
            print(f"[{client}] first-byte: ERROR {exc}")

    for client in ["default", "android_vr"]:
        try:
            total, rc = time_full_download(video_id, client)
            print(f"[{client}] full download: {total:5.2f}s  (rc={rc})")
        except Exception as exc:
            print(f"[{client}] full download: ERROR {exc}")


if __name__ == "__main__":
    main()
