import asyncio, difflib, glob, hmac, itertools, json, os, random, secrets, sys, threading, time, re, subprocess, traceback
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlparse
from ytmusicapi import YTMusic
import alexa_remote
from flask import Flask, request, render_template, jsonify, send_file, session, redirect, Response
from werkzeug.exceptions import HTTPException

app = Flask(__name__)

# Signs the session cookie used by the web remote login. Set SECRET_KEY in the
# environment so sessions survive restarts; otherwise a random one is generated
# (logins reset on every restart, which is fine for a personal tool).
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_INSECURE") != "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Human login for the web remote. When both are set, /remote/ and the /alexa/*
# endpoints accept a logged-in session cookie *instead of* the long ?key=, so
# the API key never has to appear in the browser URL. The API key still works
# (Alexa devices fetch /proxy/ with it), and still gates the ytmusic endpoints.
REMOTE_USER = os.environ.get("REMOTE_USER")
REMOTE_PASSWORD = os.environ.get("REMOTE_PASSWORD")
if REMOTE_USER and not REMOTE_PASSWORD:
    # bool("") is False, so _remote_login_enabled() would silently treat this
    # as "login not configured" and fall back to key-only access -- warn loudly
    # instead of disabling auth without telling anyone.
    print(f"WARNING: REMOTE_USER is set but REMOTE_PASSWORD is empty/unset; "
          f"web-remote login is DISABLED and /remote/ falls back to key-only access.")

# Optional TOTP 2FA on top of the username/password login. Set REMOTE_TOTP_SECRET
# to a base32 secret (same one you add to Google Authenticator / Authy). When set,
# login also requires the current 6-digit code. Generate a secret with e.g.
#   python -c "import base64,os;print(base64.b32encode(os.urandom(20)).decode())"
REMOTE_TOTP_SECRET = os.environ.get("REMOTE_TOTP_SECRET", "").replace(" ", "").upper()


def _totp_enabled():
    return bool(REMOTE_TOTP_SECRET)


def _totp_at(secret_b32: str, counter: int) -> str:
    import base64, hashlib, struct
    # base32 decode, padding to a multiple of 8 chars
    padded = secret_b32 + "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _totp_verify(code: str, window: int = 1) -> bool:
    if not _totp_enabled() or not re.fullmatch(r"\d{6}", code or ""):
        return False
    counter = int(time.time()) // 30
    # accept the current step plus +/- `window` steps for clock drift
    for step in range(-window, window + 1):
        try:
            candidate = _totp_at(REMOTE_TOTP_SECRET, counter + step)
        except Exception:
            return False
        if hmac.compare_digest(candidate, code):
            return True
    return False

# When set (e.g. https://130-162-223-226.sslip.io), audio_url points at /proxy/
# instead of the direct googlevideo URL. googlevideo URLs are IP-locked to the
# machine that resolved them (and direct fetches 403 from datacenter IPs even
# then), so devices can only play via the proxy, which serves yt-dlp downloads.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip('/')

AUDIO_CACHE_DIR = os.environ.get("AUDIO_CACHE_DIR", "/tmp/ytm_audio_cache")
AUDIO_CACHE_TTL = 24 * 60 * 60

# When set, every request must carry ?key=<API_KEY> (or X-Api-Key header).
# Key rides in the URL because Alexa devices fetch /proxy/ with no custom headers.
API_KEY = os.environ.get("API_KEY")

# ---- Armed-play store (web-remote direct plays) ----
# The web remote can't reliably smuggle a video id through Alexa's speech NLU
# (a hex blob gets split/mangled), so instead it "arms" the exact video id here
# per device and sends the skill a short, NLU-safe trigger phrase. The skill
# then GETs /armed_play/ to learn what to play. Latest-arm-wins, single slot per
# serial, with a short TTL so a stale arm never plays unexpectedly.
_ARMED_PLAYS = {}
_ARMED_PLAYS_LOCK = threading.Lock()
ARMED_PLAY_TTL = 60.0  # seconds


def _arm_play(serial, video_id, offset_ms=0):
    with _ARMED_PLAYS_LOCK:
        _ARMED_PLAYS[serial] = {
            'video_id': video_id,
            'offset_ms': max(0, int(offset_ms or 0)),
            'armed_at': time.time(),
        }


def _consume_armed_play(serial=None):
    """Return (video_id, offset_ms) and clear the arm, or None if there is no
    fresh arm. Expired arms (> TTL) are dropped without playing.

    The skill can't map its opaque Alexa deviceId to the AlexaPy serialNumber
    used when arming, so when ``serial`` is None (the skill's call) we return the
    single most-recently-armed play. This is safe for a single-user remote where
    the trigger phrase follows its arm within ~1s."""
    with _ARMED_PLAYS_LOCK:
        if serial is not None:
            entry = _ARMED_PLAYS.pop(serial, None)
        elif _ARMED_PLAYS:
            latest_serial = max(_ARMED_PLAYS, key=lambda s: _ARMED_PLAYS[s]['armed_at'])
            entry = _ARMED_PLAYS.pop(latest_serial, None)
        else:
            entry = None
    if not entry:
        return None
    if time.time() - entry['armed_at'] > ARMED_PLAY_TTL:
        return None
    return entry['video_id'], entry['offset_ms']

# ---- Recently-listened history (web remote) ----
# Persisted to a JSON file so it survives restarts; single-user tool, so a
# flat file with a process-wide lock is enough. Entries are recorded when the
# skill's 'started' webhook confirms real playback (not when a play is merely
# requested), newest first, deduped by video_id.
HISTORY_FILE = os.environ.get("HISTORY_FILE", "/tmp/ytm_listen_history.json")
HISTORY_MAX = 100
_history_lock = threading.Lock()


def _load_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    # Guards against a hand-edited or partially-corrupted file (e.g. a stray
    # null or string entry) crashing every caller that does e.get(...).
    return [e for e in data if isinstance(e, dict) and e.get('video_id')]


def _save_history(history):
    # Write-then-replace so a crash mid-save can't leave a half-written file.
    tmp = f"{HISTORY_FILE}.tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(history, f)
        os.replace(tmp, HISTORY_FILE)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


# Set right before /alexa/seek/ or a resume-from-pause (/alexa/command/ with
# action=play on an already-loaded track) re-dispatches playback on the *same*
# track at an arbitrary offset. The started webhook that follows can land at
# any offset (including near-zero — dragging the scrubber back to 0:02, or
# resuming a track that was paused seconds in), which would otherwise look
# identical to "track finished and restarted" and wrongly count as a fresh
# listen. A short suppression window lets the webhook tell the two apart
# without needing a seek/resume-specific field on the webhook body (Lambda's
# PlaybackStarted event carries no such distinction).
_last_reposition_at = 0.0
_REPOSITION_SUPPRESS_WINDOW = 8.0  # seconds; covers the spoken-command round-trip


def _record_listen(video_id, title, artist, thumbnail_url):
    if not video_id:
        return
    with _history_lock:
        history = _load_history()
        history = [e for e in history if e.get('video_id') != video_id]
        history.insert(0, {
            'video_id': video_id,
            'title': title or '',
            'artist': artist or '',
            'thumbnail_url': thumbnail_url or '',
            'played_at': time.time(),
        })
        _save_history(history[:HISTORY_MAX])


def _backfill_history_metadata(video_id, title, artist, thumbnail_url):
    """Fill in title/artist/thumbnail on an existing history entry.

    Tracks that aren't in the visible queue get recorded at 'started' time with
    blank metadata (the real metadata arrives later via _lookup_and_update_np);
    this patches the entry in place without bumping played_at."""
    if not video_id or not title:
        return
    with _history_lock:
        history = _load_history()
        changed = False
        for e in history:
            if e.get('video_id') == video_id and not e.get('title'):
                e['title'] = title
                e['artist'] = artist or ''
                if thumbnail_url:
                    e['thumbnail_url'] = thumbnail_url
                changed = True
        if changed:
            _save_history(history)


# Endpoints that never need auth (public policy pages + the login flow itself,
# plus the PWA manifest / service worker / icons, which the browser fetches with
# no ?key= and which contain no private data).
_PUBLIC_PATHS = ('/', '/privacy_policy', '/terms_of_use', '/login', '/logout', '/favicon.ico',
                 '/manifest.webmanifest', '/service-worker.js')
# Public path prefixes (startswith match) — static assets (CSS, JS, icons).
_PUBLIC_PREFIXES = ('/static/',)

# Endpoints reachable with a logged-in web-remote session cookie (so the long
# API key stays out of the browser URL). Everything here plus the remote page.
_SESSION_PATHS = ('/remote', '/alexa/status', '/alexa/init', '/alexa/devices', '/alexa/command',
                  '/alexa/play', '/alexa/suggest', '/alexa/proxy_login',
                  '/alexa/proxy_check', '/alexa/now_playing', '/alexa/state_event',
                  '/alexa/seek', '/alexa/volume', '/alexa/play_queue',
                  '/alexa/shuffle_queue', '/alexa/search', '/alexa/clear',
                  '/alexa/queue_add', '/alexa/queue_remove',
                  '/alexa/queue_reorder', '/history', '/recommendations')
# Sub-paths that also count as session-accessible (startswith match).
_SESSION_PREFIXES = ('/alexa/now_playing/', '/history/')

# API/device endpoints: the Alexa skill and web-remote JS hit these directly
# and need a machine-readable JSON error, never an HTML redirect, on failure.
_API_PREFIXES = ('/alexa/', '/proxy/', '/get_stream/', '/get_radio/',
                  '/find_stream_list/', '/armed_play/', '/stream_video/',
                  '/stream_playlist/', '/get_playlist_info/', '/history',
                  '/recommendations')


def _remote_login_enabled():
    return bool(REMOTE_USER and REMOTE_PASSWORD)


def _logged_in():
    return _remote_login_enabled() and session.get('remote_user') == REMOTE_USER


@app.before_request
def require_api_key():
    path = request.path.rstrip('/') or '/'
    if path in _PUBLIC_PATHS or any(request.path.startswith(p) for p in _PUBLIC_PREFIXES):
        return None
    # A valid session cookie authorizes the remote page and its /alexa/* calls.
    if _logged_in() and (path in _SESSION_PATHS or any(path.startswith(p) for p in _SESSION_PREFIXES)):
        # Mutating requests must be JSON. A cross-site HTML form or plain
        # <script> fetch cannot set Content-Type: application/json without
        # triggering a CORS preflight that our lack of CORS headers would
        # fail, so this blocks classic CSRF against the cookie-authenticated
        # command endpoints without needing a token.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not (request.content_type or '').startswith('application/json'):
                return jsonify({'error': 'unauthorized'}), 401
        return None
    if not API_KEY:
        return None
    supplied = request.args.get('key') or request.headers.get('X-Api-Key')
    if supplied != API_KEY:
        # Device/skill endpoints must get a JSON error, never an HTML redirect.
        if any(request.path.startswith(p) for p in _API_PREFIXES):
            return jsonify({'error': 'unauthorized'}), 401
        # Anything else is a browser hitting the site directly (root, /remote,
        # /setup, a typo, whatever) with no valid key/session: send them to the
        # login screen instead of a bare JSON 401.
        return redirect('/login/')
    return None

_download_locks = {}
_locks_guard = threading.Lock()
# Bounds how many yt-dlp download subprocesses can run at once, independent of
# how many distinct video_ids are requested (the per-id lock in
# ensure_downloaded only prevents duplicate downloads of the *same* id, not
# unbounded concurrent downloads of many different ids).
_DOWNLOAD_CONCURRENCY = 4
_download_semaphore = threading.Semaphore(_DOWNLOAD_CONCURRENCY)
_stream_list_cache = {}
_stream_list_pending = {}
_stream_list_cache_lock = threading.Lock()
_STREAM_LIST_CACHE_TTL = 120

# ---------- server-side now-playing state ----------
# Amazon's /api/np/player returns empty data for custom-skill audio, so we
# track the state ourselves. Every stream goes through /proxy/, so we capture
# the video_id there and look up metadata.
_now_playing = {
    'playing': False,
    'title': '',
    'artist': '',
    'thumbnail': '',
    'video_id': '',
    'queue': [],        # [{title, artist, thumbnail, video_id}, ...]
    'queue_index': -1,  # current position in queue (-1 = unknown)
    'updated_at': 0,
    # Progress-bar anchor. The web remote runs a local timer for a smooth bar;
    # the server only supplies the anchor: when the current track's playback
    # position was last known (started_at, epoch seconds) and what position it
    # was at then (position_ms). An app opened partway through, or after a seek,
    # computes position = position_ms + (now - started_at)*1000 and ticks from
    # there. duration_ms is the track length (0 = unknown → bar shows elapsed
    # time counting up with no fill target).
    'duration_ms': 0,
    'position_ms': 0,    # known playback position at started_at
    'started_at': 0.0,   # epoch seconds when position_ms was captured
    'playback_confirmed': False,
    'volume': None,
    # One-shot: set when the playback watchdog gives up on a dispatch (see
    # _watch_playback_confirmation). Cleared once read via _np_snapshot so it
    # surfaces to the client exactly once instead of re-toasting forever.
    'playback_error': None,
}
_volume_by_serial = {}
_np_lock = threading.Lock()

# ---------- SSE (Server-Sent Events) push ----------
import queue as _queue_mod
_sse_subscribers = {}
_sse_lock = threading.Lock()

def _np_snapshot(serial=None):
    """Return the public-facing now-playing dict.

    The progress anchor is normalised to *now*: position_ms is the live computed
    position and started_at is set to the current server time. This keeps the
    client's local tick accurate even if its clock differs from the server's, and
    means an app opened partway through a song lands on the right spot."""
    s = _now_playing  # caller holds _np_lock
    now = time.time()
    volume = _volume_by_serial.get(serial) if serial else None
    if volume is None:
        volume = s.get('volume')
    playback_error = s.get('playback_error')
    s['playback_error'] = None  # one-shot: clear once surfaced
    return {
        'playing': s['playing'], 'title': s['title'],
        'artist': s['artist'], 'thumbnail': s['thumbnail'],
        'video_id': s.get('video_id', ''),
        'queue': s.get('queue', []), 'queue_index': s.get('queue_index', -1),
        'duration_ms': s.get('duration_ms', 0),
        'position_ms': _computed_position_ms(),
        'started_at': now,
        'playback_confirmed': bool(s.get('playback_confirmed')),
        'volume': volume,
        'playback_error': playback_error,
    }


def _reset_progress(position_ms=0):
    """Re-anchor the progress bar to position_ms as of now. Call on any track
    change or seek. Caller must hold _np_lock (or use _update_now_playing, which
    goes through the same lock)."""
    _now_playing['position_ms'] = max(0, int(position_ms or 0))
    _now_playing['started_at'] = time.time()


def _computed_position_ms():
    """Current playback position in ms, derived from the anchor. While playing,
    it advances with wall-clock; frozen otherwise. Clamped to duration when
    known. Caller must hold _np_lock."""
    pos = int(_now_playing.get('position_ms', 0) or 0)
    if (_now_playing.get('playing') and _now_playing.get('playback_confirmed')
            and _now_playing.get('started_at')):
        pos += int((time.time() - _now_playing['started_at']) * 1000)
    duration = int(_now_playing.get('duration_ms', 0) or 0)
    if duration:
        pos = min(pos, duration)
    return max(0, pos)

def _notify_sse():
    """Push current state to all SSE subscriber queues (non-blocking)."""
    with _sse_lock:
        subscribers = list(_sse_subscribers.items())
    # Snapshot (and clear the one-shot playback_error) once per broadcast, not
    # once per subscriber — otherwise only the first subscriber in the loop
    # would ever see a given error.
    snapshots = {}
    dead = set()
    for q, serial in subscribers:
        if serial not in snapshots:
            with _np_lock:
                snapshots[serial] = json.dumps(_np_snapshot(serial))
        data = snapshots[serial]
        try:
            q.put_nowait(data)
        except Exception:
            dead.add(q)
    if dead:
        with _sse_lock:
            for q in dead:
                _sse_subscribers.pop(q, None)


def _set_volume_state(serial, volume):
    try:
        volume = int(volume)
    except (TypeError, ValueError):
        return False
    volume = min(100, max(0, volume))
    with _np_lock:
        if serial:
            _volume_by_serial[serial] = volume
        _now_playing['volume'] = volume
        _now_playing['updated_at'] = time.time()
    return True


def _get_volume_state(serial=None):
    with _np_lock:
        volume = _volume_by_serial.get(serial) if serial else None
        if volume is None:
            volume = _now_playing.get('volume')
    return volume


def _record_volume_state(serial, volume, notify=False):
    with _np_lock:
        changed = _volume_by_serial.get(serial) != volume
    if _set_volume_state(serial, volume) and (notify or changed):
        _notify_sse()
    return volume


def _refresh_volume(serial, notify=False):
    if not serial:
        return None
    volume, error = alexa_remote.remote.volume(serial)
    if error:
        return None
    return _record_volume_state(serial, volume, notify=notify)

# Periodic re-sync so multiple open pages (laptop + phone, etc.) don't drift
# apart while a song plays without any events. This only re-broadcasts local
# now-playing state; it does not poll Amazon for volume. Volume is event-driven
# via Lambda /alexa/state_event/ updates and immediate web-slider updates.
_SSE_HEARTBEAT_INTERVAL = 8  # seconds
_heartbeat_thread = None
_heartbeat_guard = threading.Lock()


def _sse_heartbeat_loop():
    while True:
        time.sleep(_SSE_HEARTBEAT_INTERVAL)
        # Recovery for lost PlaybackStarted webhooks: if the timer has run well
        # past the track's known duration, the Echo has moved on to the next
        # (buffered) track without us hearing about it — advance the card.
        try:
            _check_track_overrun()
        except Exception:
            traceback.print_exc()
        with _sse_lock:
            serials = {serial for serial in _sse_subscribers.values() if serial}
        with _np_lock:
            playing = _now_playing.get('playing')
        if serials and playing:
            _notify_sse()


def _ensure_heartbeat():
    """Start the heartbeat thread once, lazily (on first SSE subscriber)."""
    global _heartbeat_thread
    with _heartbeat_guard:
        if _heartbeat_thread is None or not _heartbeat_thread.is_alive():
            _heartbeat_thread = threading.Thread(
                target=_sse_heartbeat_loop, name="sse-heartbeat", daemon=True)
            _heartbeat_thread.start()


def _update_now_playing(**kwargs):
    with _np_lock:
        # A change of video_id means a new track: re-anchor the progress bar to
        # the start (or to an explicit position_ms if the caller passed one, e.g.
        # a seek) and drop the previous track's duration until it's looked up
        # again. Callers that already pass position_ms/started_at/duration_ms
        # explicitly win over this default.
        new_video_id = kwargs.get('video_id')
        track_changed = (new_video_id is not None
                         and new_video_id != _now_playing.get('video_id'))
        _now_playing.update(kwargs)
        if track_changed and 'started_at' not in kwargs:
            _now_playing['position_ms'] = int(kwargs.get('position_ms', 0) or 0)
            _now_playing['started_at'] = time.time()
            if 'duration_ms' not in kwargs:
                _now_playing['duration_ms'] = 0
            if 'playback_confirmed' not in kwargs:
                _now_playing['playback_confirmed'] = False
        _now_playing['updated_at'] = time.time()
    _notify_sse()

def _get_now_playing():
    with _np_lock:
        return dict(_now_playing)


# Uncaught errors (ytmusicapi hiccups, YouTube layout changes, etc.) become
# JSON 500s instead of Flask's HTML error page, so the Alexa skill can tell
# "the service broke" apart from "the URL is unreachable".
@app.errorhandler(alexa_remote.AlexaUnreachable)
def handle_alexa_unreachable(error):
    # Device offline / Amazon not responding: a clear 503 so the web remote can
    # show a useful message instead of a generic 500.
    return jsonify({'error': str(error)}), 503


@app.errorhandler(Exception)
def handle_uncaught(error):
    if isinstance(error, HTTPException):
        return error
    traceback.print_exc()
    return jsonify({'error': 'internal server error'}), 500


def error_response(message: str, status: int):
    return jsonify({'error': message}), status


def _stream_list_cache_key(query: str, filter_name: str):
    return ((filter_name or 'songs').strip().lower(), (query or '').strip().lower())


def _get_cached_stream_list(query: str, filter_name: str):
    key = _stream_list_cache_key(query, filter_name)
    with _stream_list_cache_lock:
        item = _stream_list_cache.get(key)
        if not item:
            return None
        expires_at, response = item
        if expires_at < time.time():
            _stream_list_cache.pop(key, None)
            return None
        return response


def _set_cached_stream_list(query: str, filter_name: str, response):
    if not response:
        return
    key = _stream_list_cache_key(query, filter_name)
    with _stream_list_cache_lock:
        _stream_list_cache[key] = (time.time() + _STREAM_LIST_CACHE_TTL, response)


def _pending_stream_list_event(query: str, filter_name: str):
    key = _stream_list_cache_key(query, filter_name)
    with _stream_list_cache_lock:
        return _stream_list_pending.get(key)


def _prepare_stream_list_cache(query: str, filter_name: str = 'songs'):
    key = _stream_list_cache_key(query, filter_name)
    with _stream_list_cache_lock:
        cached = _stream_list_cache.get(key)
        if cached and cached[0] >= time.time():
            return cached[1]
        event = _stream_list_pending.get(key)
        if event:
            owner = False
        else:
            event = threading.Event()
            _stream_list_pending[key] = event
            owner = True
    if not owner:
        event.wait(3)
        return _get_cached_stream_list(query, filter_name)
    try:
        response = asyncio.run(Supporting.find_stream_list(query, filter_name))
        _set_cached_stream_list(query, filter_name, response)
        return response
    except Exception:
        traceback.print_exc()
        return None
    finally:
        with _stream_list_cache_lock:
            _stream_list_pending.pop(key, None)
        event.set()


def _thumbnail_url(thumbnail):
    if isinstance(thumbnail, dict):
        return thumbnail.get('url', '')
    if isinstance(thumbnail, str):
        return thumbnail
    return ''


def _thumbnail_metadata(thumbnail):
    if isinstance(thumbnail, dict):
        return thumbnail
    url = _thumbnail_url(thumbnail)
    if not url:
        return None
    return {'url': url, 'width': 0, 'height': 0}


def _metadata_from_queue(video_id):
    cur = _get_now_playing()
    for item in cur.get('queue') or []:
        if item.get('video_id') == video_id:
            return {
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'video_id': video_id,
                'thumbnail': _thumbnail_metadata(item.get('thumbnail')),
                'duration_ms': item.get('duration_ms', 0),
            }
    return None


def _lookup_video_metadata(video_id):
    if not _valid_video_id(video_id):
        return None
    queued = _metadata_from_queue(video_id)
    if queued:
        return queued
    try:
        ytmusic = YTMusic()
        info = ytmusic.get_song(video_id)
        details = (info or {}).get('videoDetails') or {}
        title = details.get('title') or ''
        author = details.get('author') or ''
        if author.endswith(' - Topic'):
            author = author[:-len(' - Topic')]
        thumbs = details.get('thumbnail', {}).get('thumbnails', [])
        thumb = thumbs[-1] if thumbs else None
        try:
            duration_ms = int(details.get('lengthSeconds') or 0) * 1000
        except (TypeError, ValueError):
            duration_ms = 0
        if title:
            return {
                'title': title,
                'artist': author,
                'video_id': video_id,
                'thumbnail': thumb,
                'duration_ms': duration_ms,
            }
    except Exception:
        traceback.print_exc()
    # ytmusicapi only knows YT Music catalog tracks; arbitrary YouTube links
    # (what a pasted URL usually is) have no get_song entry, so fall back to
    # yt-dlp for the title/artist/thumbnail instead of showing "YouTube video".
    return Supporting.probe_metadata(video_id)


def _current_queue_for_video(video_id):
    cur = _get_now_playing()
    queue = cur.get('queue') or []
    if any(item.get('video_id') == video_id for item in queue):
        normalized = []
        for item in queue:
            normalized.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'video_id': item.get('video_id', ''),
                'thumbnail': _thumbnail_metadata(item.get('thumbnail')),
                'duration_ms': item.get('duration_ms', 0),
            })
        return normalized
    return []


def _prewarm_queue_audio(queue, current_index=0, limit=4):
    if not queue:
        return
    warmed = 0
    for offset in range(1, len(queue)):
        if warmed >= limit:
            break
        item = queue[(current_index + offset) % len(queue)]
        video_id = item.get('video_id', '')
        if not _valid_video_id(video_id):
            continue
        if Supporting.cached_audio_path(video_id):
            continue
        _ensure_audio_ready_for_play(video_id, wait=False)
        warmed += 1


def _ensure_audio_ready_for_play(video_id, wait=False):
    if not _valid_video_id(video_id):
        return False
    if Supporting.cached_audio_path(video_id):
        return True
    if wait:
        return bool(Supporting.ensure_downloaded(video_id))
    threading.Thread(target=Supporting.ensure_downloaded, args=(video_id,), daemon=True).start()
    return False


# YouTube video ids are [A-Za-z0-9_-]. Validate before handing an id to yt-dlp
# (an id beginning with "-" would otherwise be read as a CLI flag) or to the
# filesystem glob/-o template (blocks path traversal via "../").
_VIDEO_ID_RE = re.compile(r"\A[\w-]{1,64}\Z")


def _valid_video_id(video_id) -> bool:
    # isinstance: some callers pass raw JSON body values, and re.match raises
    # TypeError on a non-string (a number here must fail the check, not 500).
    return isinstance(video_id, str) and bool(_VIDEO_ID_RE.match(video_id))

_NUM_ONES = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
             'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
             'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
             'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
             'nineteen': 19}
_NUM_TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
             'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}

# words marking an alternate rendition; results carrying one get pushed down
# the ranking — but only when the user asked for a plain song. If the request
# itself names any rendition ("mashup", "cover"...), no penalty applies, since
# uploaders use these words loosely (a "mashup" may be titled "megamix").
_MODIFIER_TOKENS = {'cover', 'covers', 'karaoke', 'remix', 'remixes', 'instrumental',
                    'acoustic', 'unplugged', 'live', 'lofi', 'slowed', 'reverb',
                    'mashup', 'megamix', 'medley', 'remake', 'parody', '8d',
                    'nightcore', 'tribute', 'reprise'}

# promo material is never what someone wants to *listen* to; penalize hard
_PROMO_TOKENS = {'teaser', 'trailer', 'promo', 'preview', 'snippet', 'announcement'}

class Supporting:
    @staticmethod
    def duration_ms(track):
        """Best-effort duration extraction from ytmusicapi result shapes."""
        if not isinstance(track, dict):
            return 0
        for key in ('duration_seconds', 'lengthSeconds'):
            try:
                seconds = int(track.get(key) or 0)
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                return seconds * 1000
        # get_watch_playlist's radio tracks store it as "length" (e.g. "3:40")
        # instead of "duration".
        duration = track.get('duration') or track.get('length')
        if not isinstance(duration, str) or not duration.strip():
            return 0
        try:
            parts = [int(p) for p in duration.strip().split(':')]
        except ValueError:
            return 0
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds * 1000

    @staticmethod
    def digitize_numbers(text):
        # Alexa transcribes numbers as words ("twenty twenty five"); YouTube
        # titles use digits ("2025"). Converts number-word runs, merging
        # year-style pairs (twenty + twenty five -> 2025, nineteen + ninety
        # nine -> 1999, two thousand + five -> 2005).
        out, nums = [], []

        def flush():
            merged, j = [], 0
            while j < len(nums):
                v, nxt = nums[j], nums[j + 1] if j + 1 < len(nums) else None
                if nxt is not None and 10 <= v <= 99 and 0 <= nxt <= 99:
                    merged.append(v * 100 + nxt); j += 2
                elif nxt is not None and v >= 1000 and v % 1000 == 0 and nxt < 1000:
                    merged.append(v + nxt); j += 2
                else:
                    merged.append(v); j += 1
            out.extend(str(m) for m in merged)
            nums.clear()

        prev_tens = False
        for word in re.split(r'[\s-]+', text):
            lw = word.lower()
            if lw in _NUM_TENS:
                nums.append(_NUM_TENS[lw]); prev_tens = True
            elif lw in _NUM_ONES:
                if prev_tens and _NUM_ONES[lw] < 10:
                    nums[-1] += _NUM_ONES[lw]
                else:
                    nums.append(_NUM_ONES[lw])
                prev_tens = False
            elif lw == 'hundred' and nums:
                nums[-1] *= 100; prev_tens = False
            elif lw == 'thousand' and nums:
                nums[-1] *= 1000; prev_tens = False
            else:
                flush(); out.append(word); prev_tens = False
        flush()
        return ' '.join(out)

    @staticmethod
    def query_variants(text):
        variants = [text]
        digitized = Supporting.digitize_numbers(text)
        if digitized.lower() != text.lower():
            variants.append(digitized)
        return variants

    @staticmethod
    def tokens(text):
        return re.findall(r"[a-z0-9]+", text.lower().replace('&', ' and '))

    @staticmethod
    def match_score(query_variants, text):
        # 0..1: how much of what the user said appears in the result's
        # title/artist/album, with fuzzy per-token matching for near-misses
        result_tokens = set(Supporting.tokens(text))
        if not result_tokens:
            return 0.0
        best = 0.0
        for query in query_variants:
            # "by" is utterance glue ("play X by Y"), not part of any name
            query_tokens = [t for t in Supporting.tokens(query) if t != 'by']
            if not query_tokens:
                continue
            hit = 0.0
            for token in query_tokens:
                if token in result_tokens:
                    hit += 1
                else:
                    close = difflib.get_close_matches(token, result_tokens, n=1, cutoff=0.8)
                    if close:
                        hit += difflib.SequenceMatcher(None, token, close[0]).ratio()
            best = max(best, hit / len(query_tokens))
        return best

    @staticmethod
    def result_text(track):
        parts = [track.get('title') or '']
        parts += [a.get('name') or '' for a in track.get('artists') or []]
        album = track.get('album')
        if isinstance(album, dict):
            parts.append(album.get('name') or '')
        return ' '.join(parts)

    @staticmethod
    def artist_text(track):
        return ' '.join(a.get('name') or '' for a in track.get('artists') or [])

    async def get_radiolist(song_name: str):
        ytmusic = YTMusic()
        # "song by artist": boost results actually by that artist
        artist_hint = None
        lowered = song_name.lower()
        if ' by ' in lowered:
            artist_hint = song_name[lowered.rindex(' by ') + 4:].strip()

        # Mashups, mixes, covers, and remixes often exist only as videos, so
        # always search both catalogs (and both number spellings) and pick the
        # candidate that best matches what the user actually said.
        variants = Supporting.query_variants(song_name)
        plan = [(query, search_filter) for query in variants for search_filter in ('songs', 'videos')]
        results = await asyncio.gather(
            *[asyncio.to_thread(ytmusic.search, query=query, filter=search_filter, ignore_spelling=True)
              for query, search_filter in plan],
            return_exceptions=True)

        requested_tokens = set().union(*[set(Supporting.tokens(v)) for v in variants])
        candidates = []
        for (_query, search_filter), tracks in zip(plan, results):
            if isinstance(tracks, BaseException) or not tracks:
                continue
            for rank, track in enumerate(tracks[:10]):
                if not track.get('videoId'):
                    continue
                track_text = Supporting.result_text(track)
                score = Supporting.match_score(variants, track_text)
                if artist_hint and Supporting.match_score([artist_hint], Supporting.artist_text(track)) >= 0.8:
                    score += 0.15
                track_tokens = set(Supporting.tokens(track_text))
                # push down renditions the user didn't ask for (karaoke, remix, live...)
                if not (_MODIFIER_TOKENS & requested_tokens):
                    score -= 0.12 * len(_MODIFIER_TOKENS & track_tokens)
                score -= 0.3 * len(_PROMO_TOKENS & (track_tokens - requested_tokens))
                # prefer catalog songs over videos, and earlier results, on near-ties
                if search_filter == 'songs':
                    score += 0.05
                score -= rank * 0.005
                candidates.append((score, track))
        if not candidates:
            return None
        top = max(candidates, key=lambda c: c[0])[1]

        video_id = top.get('videoId')
        if not video_id:
            return None

        # Seed track built straight from the search hit, no extra round-trip.
        # This is all the device needs to START playing; the rest of the radio
        # queue (tracks 2+) is fetched lazily by the skill after playback starts
        # (see /get_radio/), so the user doesn't wait through a second YT Music
        # call before audio begins.
        seed = {
            'title': top.get('title') or '',
            'artist': " and ".join(a.get('name') or '' for a in top.get('artists') or []),
            'video_id': video_id,
            'thumbnail': (top.get('thumbnails') or [None])[-1],
            'duration_ms': Supporting.duration_ms(top),
        }
        return [seed]

    @staticmethod
    async def get_charts_queue():
        """Genre-agnostic trending tracks. Used as the discovery half of
        recommendations, and as the sole source on a true cold start (no
        history yet). Country defaults to India so a cold start doesn't show a
        US Top-40 list; falls back to the global chart if 'IN' isn't supported
        by the installed ytmusicapi."""
        ytmusic = YTMusic()
        charts = None
        for country in (CHARTS_COUNTRY, 'ZZ'):
            try:
                charts = await asyncio.to_thread(ytmusic.get_charts, country)
                if charts:
                    break
            except Exception:
                traceback.print_exc()
                charts = None
        songs = ((charts or {}).get('songs') or {}).get('items', [])
        if not songs:
            return None
        return [
            {
                'title': track.get('title', ''),
                'artist': " and ".join(a.get('name') or '' for a in track.get('artists') or []),
                'video_id': track.get('videoId', ''),
                'thumbnail': track['thumbnails'][-1] if track.get('thumbnails') else None,
                'duration_ms': 0,
            }
            for track in songs
            if track.get('videoId')
        ]

    @staticmethod
    async def get_radio_queue(video_id: str):
        """Full radio/autoplay queue seeded from one video. Used by /get_radio/
        for lazy queue expansion once playback has already started."""
        if not _valid_video_id(video_id):
            return None
        ytmusic = YTMusic()
        try:
            radio_results = await asyncio.to_thread(
                ytmusic.get_watch_playlist, videoId=video_id, radio=True)
        except Exception:
            traceback.print_exc()
            return None
        songs = (radio_results or {}).get('tracks', [])
        if not songs:
            return None
        # Use .get() throughout: a single malformed track (missing title/etc.)
        # must not KeyError and take down the whole radio (which would push
        # recommendations onto the generic fallback).
        out = []
        for track in songs:
            vid = track.get("videoId")
            if not vid:
                continue
            out.append({
                'title': track.get("title", ""),
                'artist': " and ".join(a.get("name", "") for a in (track.get("artists") or [])),
                'video_id': vid,
                'thumbnail': track['thumbnail'][-1] if track.get('thumbnail') else None,
                'duration_ms': Supporting.duration_ms(track),
            })
        return out or None

    async def get_artist(artist_name: str):
        ytmusic = YTMusic()
        search_results = await asyncio.to_thread(ytmusic.search, query=artist_name, filter='songs', ignore_spelling=True)
        if not search_results:
            return None

        # surface songs actually by the requested artist first (stable sort
        # keeps YouTube's relevance order within equal scores)
        variants = Supporting.query_variants(artist_name)
        search_results = sorted(
            search_results,
            key=lambda track: Supporting.match_score(variants, Supporting.artist_text(track)),
            reverse=True)

        return [
            {
                'title': track["title"],
                'artist': " and ".join([artist["name"] for artist in track.get("artists", [])]),
                'video_id': track["videoId"],
                'thumbnail': track['thumbnails'][-1] if track.get('thumbnails') else None,
                'duration_ms': Supporting.duration_ms(track),
            }
            for track in search_results
        ]

    async def get_album(album_name: str):
        ytmusic = YTMusic()
        search_results = await asyncio.to_thread(ytmusic.search, query=album_name, filter='albums', ignore_spelling=True)
        if not search_results:
            return None

        # pick the album whose title/artist best matches the request, not
        # just whatever YouTube ranks first
        variants = Supporting.query_variants(album_name)
        scored = [(Supporting.match_score(variants, Supporting.result_text(album)), rank, album)
                  for rank, album in enumerate(search_results[:10]) if album.get('browseId')]
        if not scored:
            return None
        browse_id = max(scored, key=lambda s: (s[0], -s[1]))[2]['browseId']

        album_results = await asyncio.to_thread(ytmusic.get_album, browseId=browse_id)
        songs = album_results.get("tracks", [])
        if not songs:
            return None

        return [
            {
                'title': track["title"],
                'artist': " and ".join([artist["name"] for artist in track.get("artists", [])]),
                'video_id': track["videoId"],
                'thumbnail': track['thumbnails'][-1] if track.get('thumbnails') else None,
                'duration_ms': Supporting.duration_ms(track),
            }
            for track in songs
        ]
    
    async def get_playlist_tracks(playlist_id: str):
        """Normalized track list for a playlist id, or None if unreadable/empty.
        Unavailable tracks (no videoId) are dropped."""
        ytmusic = YTMusic()
        try:
            search_results = await asyncio.to_thread(ytmusic.get_playlist, playlistId=playlist_id)
        except Exception:
            # ytmusicapi raises on unknown/private playlist ids; treat as not found
            traceback.print_exc()
            return None
        playlist_raw = (search_results or {}).get('tracks')
        if not playlist_raw:
            return None
        playlist = [
            {
                'title': track.get("title") or '',
                'artist': " and ".join([artist["name"] for artist in track.get("artists", [])]),
                'video_id': track.get("videoId"),
                'thumbnail': track['thumbnails'][-1] if track.get('thumbnails') else None,
                'duration_ms': Supporting.duration_ms(track),
            }
            for track in playlist_raw
            if _valid_video_id(track.get("videoId"))
        ]
        return playlist or None

    async def stream_playlist(playlist_id: str):
        playlist = await Supporting.get_playlist_tracks(playlist_id)
        if not playlist:
            return None
        stream = await Supporting.get_stream(playlist[0]['video_id'])
        if not stream:
            return None
        return {'song_info': {'metadata': playlist[0], 'stream': stream}, 'playlist': playlist}

    def resolve_direct_url(video_id: str):
        if not _valid_video_id(video_id):
            return None
        # ios client requires a PO token since 2025; default clients (android_vr/tv) still work
        # ejs:github lets yt-dlp fetch its JS challenge solver (needed for signature decryption)
        # "--" ends option parsing so an id can never be read as a flag.
        command = ["yt-dlp", "--get-url", "--no-playlist", "--quiet", "-f", "ba",
                   "--remote-components", "ejs:github", "--", video_id]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            print("Error: ", result.stderr)
            return None
        return result.stdout.strip()

    def probe_metadata(video_id: str):
        """Best-effort title/artist/thumbnail/duration via yt-dlp, for videos
        ytmusicapi doesn't know (arbitrary YouTube links aren't in the YT Music
        catalog, so get_song returns nothing). Returns a metadata dict or None.
        "--" ends option parsing so an id can never be read as a flag."""
        if not _valid_video_id(video_id):
            return None
        # Tab-separated so a title containing the delimiter can't split fields.
        fmt = "%(title)s\t%(uploader)s\t%(thumbnail)s\t%(duration)s"
        command = ["yt-dlp", "--no-playlist", "--quiet", "--no-warnings",
                   "--remote-components", "ejs:github",
                   "--print", fmt]
        cookies = os.environ.get("YTDLP_COOKIES")
        if cookies:
            command += ["--cookies", cookies]
        command += ["--", video_id]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=25)
        except (subprocess.SubprocessError, OSError):
            return None
        if result.returncode != 0:
            return None
        parts = (result.stdout.strip().split('\t') + ['', '', '', ''])[:4]
        title, uploader, thumbnail, duration = parts
        if not title or title == 'NA':
            return None
        if uploader == 'NA':
            uploader = ''
        if uploader.endswith(' - Topic'):
            uploader = uploader[:-len(' - Topic')]
        try:
            duration_ms = int(float(duration)) * 1000
        except (TypeError, ValueError):
            duration_ms = 0
        return {
            'title': title,
            'artist': uploader,
            'video_id': video_id,
            'thumbnail': _thumbnail_metadata(thumbnail if thumbnail != 'NA' else None),
            'duration_ms': duration_ms,
        }

    def cached_audio_path(video_id: str):
        paths = [p for p in glob.glob(os.path.join(AUDIO_CACHE_DIR, f"{video_id}.*"))
                 if not p.endswith('.part')]
        return paths[0] if paths else None

    def prune_audio_cache():
        os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
        for old in glob.glob(os.path.join(AUDIO_CACHE_DIR, "*")):
            try:
                if time.time() - os.path.getmtime(old) > AUDIO_CACHE_TTL:
                    os.remove(old)
            except OSError:
                pass

    def ytdlp_download_command(video_id: str, output, client: str = "tv"):
        # tv has slightly less client-config/JS-challenge overhead than mweb
        # (~1s). YouTube's ad-skip forced sleep ("Sleeping N seconds as
        # required by the site") fires on any monetized video regardless of
        # client (confirmed: mweb, tv, and web_music all hit it) and is NOT
        # just yt-dlp being polite — it's load-bearing: the googlevideo URL
        # 403s if fetched before that timestamp, confirmed by forcibly
        # skipping the wait, so it's not something to bypass.
        command = ["yt-dlp", "--no-playlist", "--quiet",
                   "-f", "140/bestaudio[ext=m4a]/bestaudio",
                   "--remote-components", "ejs:github",
                   "--extractor-args", f"youtube:player_client={client}"]
        cookies = os.environ.get("YTDLP_COOKIES")
        if cookies:
            command += ["--cookies", cookies]
        command += ["-o", output, "--", video_id]
        return command

    def ensure_downloaded(video_id: str):
        if not _valid_video_id(video_id):
            return None
        # Direct googlevideo fetches 403 from this IP even with cookies + PO token,
        # but yt-dlp's own download path (tv/mweb client + bgutil GVS token) works —
        # so download server-side and serve the file.
        Supporting.prune_audio_cache()
        with _locks_guard:
            lock = _download_locks.setdefault(video_id, threading.Lock())
        with lock:
            path = Supporting.cached_audio_path(video_id)
            if path:
                return path
            output = os.path.join(AUDIO_CACHE_DIR, f"{video_id}.%(ext)s")
            with _download_semaphore:
                result = subprocess.run(
                    Supporting.ytdlp_download_command(video_id, output, client="tv"),
                    capture_output=True, text=True)
                if result.returncode != 0:
                    # tv occasionally can't extract a given video (e.g. some
                    # age-restricted content) — retry once with mweb, the
                    # previously proven-working client, before giving up.
                    print("Error (tv client): ", result.stderr)
                    result = subprocess.run(
                        Supporting.ytdlp_download_command(video_id, output, client="mweb"),
                        capture_output=True, text=True)
                    if result.returncode != 0:
                        print("Error (mweb client): ", result.stderr)
            return Supporting.cached_audio_path(video_id)

    async def get_stream(video_id: str):
        if PUBLIC_BASE_URL:
            # Answer immediately; pre-warm the cache so the device's fetch is fast.
            threading.Thread(target=Supporting.ensure_downloaded, args=(video_id,), daemon=True).start()
            key_param = f"&key={API_KEY}" if API_KEY else ""
            return {'audio_url': f"{PUBLIC_BASE_URL}/proxy/?video_id={video_id}{key_param}"}
        url = await asyncio.to_thread(Supporting.resolve_direct_url, video_id)
        if not url:
            return None
        return {'audio_url': url}


    async def find_stream_list(query: str, filter: str = 'songs'):
        if filter == 'songs':
            playlist = await Supporting.get_radiolist(query)
        elif filter == 'artists':
            playlist = await Supporting.get_artist(query)
        elif filter == 'albums':
            playlist = await Supporting.get_album(query)
        else:
            raise Exception(f'Unknown filter "{filter}"')

        if not playlist:
            return None

        stream = await Supporting.get_stream(playlist[0]['video_id'])
        if not stream:
            return None
        return {'song_info': {'metadata': playlist[0], 'stream': stream}, 'playlist': playlist}

    def playlist_url_to_encoded_id(url):
        match = re.match(r"^[\w-]+", url.split('list=')[-1])
        if not match:
            return None
        return Supporting.encode_to_hex(match.group())
    
    def encode_to_hex(string):
        return ''.join([hex(ord(c))[2:].zfill(2) for c in string])

    async def get_playlist_info(playlist_id: str):
        ytmusic = YTMusic()
        try:
            playlist_raw = await asyncio.to_thread(ytmusic.get_playlist, playlist_id)
        except Exception:
            traceback.print_exc()
            return None
        if not playlist_raw or not playlist_raw.get('id'):
            return None

        return {'id': playlist_raw['id'], 'title': playlist_raw.get('title', 'Untitled')}
    
@app.route("/get_playlist_info/", methods=["GET"])
async def get_playlist_info():
    start_time = time.time()
    playlist_id = request.args.get("id")
    if not playlist_id:
        return error_response('missing required parameter "id"', 400)
    response = await Supporting.get_playlist_info(playlist_id)
    print(f'Completed request in {time.time() - start_time:.2f} seconds.')
    if response is None:
        return error_response('playlist not found', 404)
    return jsonify(response)

@app.route("/stream_playlist/", methods=["GET"])
async def stream_playlist():
    start_time = time.time()
    playlist_id = request.args.get("id")
    if not playlist_id:
        return error_response('missing required parameter "id"', 400)
    response = await Supporting.stream_playlist(playlist_id)
    print(f'Completed request in {time.time() - start_time:.2f} seconds.')
    if response is None:
        return error_response('playlist not found or empty', 404)
    return jsonify(response)


@app.route("/get_stream/", methods=["GET"])
async def get_stream():
    start_time = time.time()
    video_id = request.args.get("video_id")
    if not video_id:
        return error_response('missing required parameter "video_id"', 400)
    if not _valid_video_id(video_id):
        return error_response('invalid "video_id"', 400)
    response = await Supporting.get_stream(video_id)
    print(f'Completed request in {time.time() - start_time:.2f} seconds.')
    if response is None:
        return error_response('stream unavailable', 404)
    return jsonify(response)


@app.route("/stream_video/", methods=["GET"])
async def stream_video():
    start_time = time.time()
    video_id = request.args.get("video_id")
    if not video_id:
        return error_response('missing required parameter "video_id"', 400)
    if not _valid_video_id(video_id):
        return error_response('invalid "video_id"', 400)
    metadata = await asyncio.to_thread(_lookup_video_metadata, video_id)
    if not metadata:
        metadata = {
            'title': 'YouTube video',
            'artist': '',
            'video_id': video_id,
            'thumbnail': None,
            'duration_ms': 0,
        }
    stream = await Supporting.get_stream(video_id)
    if not stream:
        return error_response('stream unavailable', 404)
    queue = _current_queue_for_video(video_id) or [metadata]
    print(f'Completed stream_video in {time.time() - start_time:.2f} seconds.')
    return jsonify({'song_info': {'metadata': metadata, 'stream': stream}, 'playlist': queue})


@app.route("/get_radio/", methods=["GET"])
async def get_radio():
    """Radio/autoplay continuation for a seed video. The skill calls this
    lazily after playback starts so the initial play isn't blocked waiting for
    the queue. Returns {'playlist': [ {title, artist, video_id, thumbnail}, ... ]}."""
    start_time = time.time()
    video_id = request.args.get("video_id")
    if not video_id:
        return error_response('missing required parameter "video_id"', 400)
    if not _valid_video_id(video_id):
        return error_response('invalid "video_id"', 400)
    playlist = await Supporting.get_radio_queue(video_id)
    print(f'Completed get_radio in {time.time() - start_time:.2f} seconds.')
    if not playlist:
        return error_response('no radio queue found', 404)
    # Refresh the web remote's "Up Next" queue now that we have the full list
    # (find_stream_list only knew the seed). Keep the currently-playing track as
    # index 0; the radio queue is seeded from it so it's normally first anyway.
    try:
        queue = []
        for item in playlist:
            t = item.get('thumbnail')
            thumb = t.get('url', '') if isinstance(t, dict) else (t if isinstance(t, str) else '')
            queue.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'thumbnail': thumb,
                'video_id': item.get('video_id', ''),
                'duration_ms': item.get('duration_ms', 0),
            })
        cur = _get_now_playing()
        existing_queue = cur.get('queue') or []
        # Overwrite the queue when it's empty/single, or when it's left over
        # from a previous play (the seed track isn't in it). A populated queue
        # that *does* contain the seed (e.g. user-shuffled) is never clobbered
        # — mirrors _refresh_radio_queue.
        queue_stale = not any(q.get('video_id') == video_id for q in existing_queue)
        if ((len(existing_queue) <= 1 or queue_stale)
                and (cur.get('video_id') == video_id or not existing_queue)):
            idx = next((i for i, q in enumerate(queue) if q['video_id'] == video_id), 0)
            _update_now_playing(queue=queue, queue_index=idx)
    except Exception:
        pass
    return jsonify({'playlist': playlist})


@app.route("/find_stream_list/", methods=["GET"])
async def find_stream_list():
    start_time = time.time()
    query = request.args.get("query")
    filter = request.args.get("filter", "songs")
    if not query:
        return error_response('missing required parameter "query"', 400)
    if filter not in ('songs', 'artists', 'albums'):
        return error_response(f'unknown filter "{filter}"', 400)
    response = _get_cached_stream_list(query, filter)
    if response is None:
        pending = _pending_stream_list_event(query, filter)
        if pending:
            pending.wait(2)
            response = _get_cached_stream_list(query, filter)
    if response is None:
        response = await Supporting.find_stream_list(query, filter)
        _set_cached_stream_list(query, filter, response)
    print(f'Completed request in {time.time() - start_time:.2f} seconds.')
    if response is None:
        return error_response('no results found', 404)
    # Capture now-playing metadata + queue (the skill calls this before /proxy/)
    try:
        meta = response.get('song_info', {}).get('metadata', {})
        playlist = response.get('playlist', [])
        queue = []
        for item in playlist:
            thumb = ''
            t = item.get('thumbnail')
            if isinstance(t, dict):
                thumb = t.get('url', '')
            elif isinstance(t, str):
                thumb = t
            queue.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'thumbnail': thumb,
                'video_id': item.get('video_id', ''),
                'duration_ms': item.get('duration_ms', 0),
            })
        if meta.get('title'):
            thumb = ''
            t = meta.get('thumbnail')
            if isinstance(t, dict):
                thumb = t.get('url', '')
            elif isinstance(t, str):
                thumb = t
            # Keep an existing populated queue only when the requested track
            # is already part of it (replay/skip within the same queue, or a
            # user-shuffled queue). A play of a track *outside* the current
            # queue is a fresh request — the old queue is obsolete and must be
            # replaced, otherwise the remote keeps showing the previous play's
            # "Up Next" against the new song.
            cur_queue = (_get_now_playing().get('queue') or [])
            new_vid = meta.get('video_id', '')
            np_fields = dict(playing=False, title=meta['title'],
                             artist=meta.get('artist', ''),
                             thumbnail=thumb,
                             video_id=new_vid,
                             duration_ms=meta.get('duration_ms', 0))
            cur_idx = next((i for i, q in enumerate(cur_queue)
                            if q.get('video_id') == new_vid), -1)
            if len(cur_queue) <= 1 or cur_idx < 0:
                np_fields['queue'] = queue
                np_fields['queue_index'] = 0
            else:
                np_fields['queue_index'] = cur_idx
            _update_now_playing(**np_fields)
            sys.stderr.write(f"[np] from find_stream_list: {meta['title']!r} queue={len(queue)}\n")
            sys.stderr.flush()
    except Exception:
        pass
    return jsonify(response)


@app.route("/proxy/", methods=["GET"])
def proxy_stream():
    video_id = request.args.get("video_id")
    if not _valid_video_id(video_id):
        return error_response('missing or invalid "video_id"', 400)
    # Track playback state. Check queue first for instant metadata.
    with _np_lock:
        current_video_id = _now_playing.get('video_id')
        current_confirmed = bool(_now_playing.get('playing') and _now_playing.get('playback_confirmed'))
    if video_id != current_video_id and current_confirmed:
        # The Echo is buffering the NEXT track while the current one plays.
        # Don't switch the card yet, but remember what it buffered: if the
        # Lambda's PlaybackStarted webhook for it gets lost (fire-and-forget,
        # short timeout), this is the only record of what actually plays next.
        global _prefetched_next
        with _np_lock:
            _prefetched_next = {'video_id': video_id, 'at': time.time()}
        sys.stderr.write(f"[np] proxy prefetch ignored: current={current_video_id} requested={video_id}\n")
        sys.stderr.flush()
    elif video_id != current_video_id:
        # Check if the new video_id is in our queue (instant metadata, no lookup needed)
        queue = _now_playing.get('queue', [])
        found_in_queue = False
        for i, item in enumerate(queue):
            if item.get('video_id') == video_id:
                _update_now_playing(playing=False, video_id=video_id,
                                    title=item['title'], artist=item['artist'],
                                    thumbnail=item['thumbnail'],
                                    duration_ms=item.get('duration_ms', 0),
                                    playback_confirmed=False,
                                    queue_index=i)
                sys.stderr.write(f"[np] proxy metadata: queue hit #{i}: {item['title']!r}\n")
                sys.stderr.flush()
                found_in_queue = True
                break
        if not found_in_queue:
            # queue_index=-1: the old highlight would point at the wrong row
            # until _refresh_radio_queue rebuilds the queue for this track.
            _update_now_playing(playing=False, video_id=video_id,
                                playback_confirmed=False, queue_index=-1)
            threading.Thread(target=_lookup_and_update_np, args=(video_id,), daemon=True).start()
            sys.stderr.write(f"[np] proxy metadata: new video_id={video_id} (not in queue, looking up)\n")
            sys.stderr.flush()
    else:
        pass
    # Always look up duration if we don't have it yet. The queue and
    # find_stream_list paths set title/artist but never duration_ms, so
    # without this the progress bar has no total time.
    if not _now_playing.get('duration_ms'):
        threading.Thread(target=_lookup_and_update_np, args=(video_id,), daemon=True).start()
    threading.Thread(target=_refresh_radio_queue, args=(video_id,), daemon=True).start()
    path = Supporting.ensure_downloaded(video_id)
    if not path:
        return error_response('download failed', 502)
    _confirm_stream_delivery(video_id)
    mimetype = 'audio/mp4' if path.endswith(('.m4a', '.mp4')) else 'audio/webm'
    return send_file(path, mimetype=mimetype, conditional=True)

def _lookup_and_update_np(video_id):
    """Fallback: look up song metadata from video_id."""
    try:
        ytmusic = YTMusic()
        info = ytmusic.get_song(video_id)
        details = (info or {}).get('videoDetails') or {}
        title = details.get('title') or ''
        author = details.get('author') or ''
        if author.endswith(' - Topic'):
            author = author[:-len(' - Topic')]
        thumb = ''
        thumbs = details.get('thumbnail', {}).get('thumbnails', [])
        if thumbs:
            thumb = thumbs[-1].get('url', '')
        # lengthSeconds drives the progress bar's total. Absent/garbage -> 0
        # (bar falls back to counting elapsed time with no fill target).
        try:
            duration_ms = int(details.get('lengthSeconds') or 0) * 1000
        except (TypeError, ValueError):
            duration_ms = 0
        # ytmusicapi only knows YT Music catalog tracks. For a pasted/arbitrary
        # YouTube link get_song returns no title (and often no duration), so fall
        # back to yt-dlp for whatever's missing — but only while this is still the
        # current track, since the probe is a subprocess and can take a moment.
        still_current = _get_now_playing().get('video_id') == video_id
        if still_current and (not title or not duration_ms):
            probed = Supporting.probe_metadata(video_id)
            if probed:
                if not title:
                    title = probed.get('title') or ''
                    author = probed.get('artist') or author
                    thumb = _thumbnail_url(probed.get('thumbnail')) or thumb
                if not duration_ms:
                    duration_ms = probed.get('duration_ms', 0)
        if title:
            # The lookup can be slow; if the track changed while it ran, writing
            # these fields would revert now_playing to the previous song (wrong
            # card + progress reset) even though the queue moved on. Drop it.
            if _get_now_playing().get('video_id') != video_id:
                sys.stderr.write(f"[np] lookup discarded (track changed): {video_id}\n")
                sys.stderr.flush()
                return
            # Only attach duration; the video_id/title/etc. were already set by
            # the caller. Passing duration_ms here (without video_id) updates it
            # in place without re-triggering the track-change reset.
            fields = {'title': title, 'artist': author, 'video_id': video_id}
            # Don't overwrite an already-known thumbnail with a blank one just
            # because this particular lookup came back without one (ytmusicapi
            # occasionally returns an empty thumbnails list transiently).
            if thumb:
                fields['thumbnail'] = thumb
            if duration_ms:
                fields['duration_ms'] = duration_ms
            _update_now_playing(**fields)
            _backfill_history_metadata(video_id, title, author, thumb)
            sys.stderr.write(f"[np] lookup OK: {title!r} dur={duration_ms}ms\n")
        else:
            sys.stderr.write(f"[np] lookup: no title for {video_id}\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"[np] lookup FAILED: {e}\n")
        sys.stderr.flush()
        traceback.print_exc()


def _refresh_radio_queue(video_id):
    """Populate recommendations for the web remote after playback is real.

    Lambda also expands the queue, but that update can arrive late or not at all
    if Alexa routing is flaky. The proxy/webhook path knows the current video id,
    so it can refresh the visible recommendations independently.
    """
    if not _valid_video_id(video_id):
        return False
    try:
        cur = _get_now_playing()
        if cur.get('video_id') != video_id:
            return False
        cur_queue = cur.get('queue') or []
        # A populated queue is only authoritative if the current track is in
        # it. Otherwise it's left over from a previous play (e.g. an app play
        # followed by a fresh voice request) and must be rebuilt around the
        # new track.
        if len(cur_queue) > 1 and any(q.get('video_id') == video_id for q in cur_queue):
            return True
        playlist = asyncio.run(Supporting.get_radio_queue(video_id))
        if not playlist:
            return False
        queue = []
        seen = set()
        for item in playlist:
            vid = item.get('video_id', '')
            if not vid or vid in seen:
                continue
            seen.add(vid)
            t = item.get('thumbnail')
            thumb = t.get('url', '') if isinstance(t, dict) else (t if isinstance(t, str) else '')
            queue.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'thumbnail': thumb,
                'video_id': vid,
                'duration_ms': item.get('duration_ms', 0),
            })
        if len(queue) <= 1:
            return False
        idx = next((i for i, item in enumerate(queue) if item.get('video_id') == video_id), 0)
        cur = _get_now_playing()
        cur_queue = cur.get('queue') or []
        queue_stale = not any(q.get('video_id') == video_id for q in cur_queue)
        if cur.get('video_id') == video_id and (len(cur_queue) <= 1 or queue_stale):
            fields = {'queue': queue, 'queue_index': idx}
            # If the now-playing track still has no duration (came from a
            # recommendation/chart with duration_ms=0), fill it from the radio
            # queue's matching entry so the progress bar gets a total.
            match = next((q for q in queue if q.get('video_id') == video_id), None)
            if match:
                if not int(cur.get('duration_ms') or 0) and int(match.get('duration_ms') or 0):
                    fields['duration_ms'] = match['duration_ms']
                if not cur.get('title') or cur.get('title') in ['YouTube video', 'YouTube link']:
                    fields['title'] = match.get('title', '')
                    fields['artist'] = match.get('artist', '')
                    if match.get('thumbnail'):
                        fields['thumbnail'] = match.get('thumbnail')
            _update_now_playing(**fields)
            _prewarm_queue_audio(queue, idx)
            return True
    except Exception:
        traceback.print_exc()
    return False


# What the Echo most recently buffered via /proxy/ while another track was
# still playing (see proxy_stream). Guarded by _np_lock.
_prefetched_next = None
_PREFETCH_MAX_AGE = 15 * 60          # ignore prefetch records older than this
_OVERRUN_GRACE_MS = 12000            # position past duration before assuming advance
_FINISH_PROMOTE_DELAY = 5.0          # wait for the real 'started' webhook first


def _promote_next_track(reason, position_ms=0):
    """Fallback when the Lambda's PlaybackStarted webhook is lost: the Echo has
    started the next (pre-buffered) track but the server never heard about it,
    so the now-playing card would stay wedged on the finished song. Promote the
    best-known next track: prefer what /proxy/ actually saw the Echo buffer,
    else the next visible queue item."""
    global _prefetched_next
    with _np_lock:
        queue = list(_now_playing.get('queue') or [])
        idx = _now_playing.get('queue_index', -1)
        cur_id = _now_playing.get('video_id')
        pf = dict(_prefetched_next) if _prefetched_next else None
    next_item = None
    next_idx = -1
    if pf and pf.get('video_id') and pf['video_id'] != cur_id \
            and time.time() - pf.get('at', 0) < _PREFETCH_MAX_AGE:
        vid = pf['video_id']
        next_idx = next((i for i, it in enumerate(queue)
                         if it.get('video_id') == vid), -1)
        next_item = queue[next_idx] if next_idx >= 0 else {'video_id': vid}
    elif 0 <= idx < len(queue) - 1:
        next_idx = idx + 1
        next_item = queue[next_idx]
    if not next_item or next_item.get('video_id') == cur_id:
        return False
    vid = next_item['video_id']
    sys.stderr.write(f"[np] promote next ({reason}): {vid} {next_item.get('title', '?')!r}\n")
    sys.stderr.flush()
    fields = dict(playing=True, video_id=vid, position_ms=max(0, int(position_ms)),
                  playback_confirmed=True, queue_index=next_idx)
    if next_item.get('title'):
        fields.update(title=next_item['title'], artist=next_item.get('artist', ''),
                      thumbnail=next_item.get('thumbnail', ''),
                      duration_ms=next_item.get('duration_ms', 0))
    _update_now_playing(**fields)
    with _np_lock:
        if _prefetched_next and _prefetched_next.get('video_id') == vid:
            _prefetched_next = None
    # This path exists because the real 'started' webhook for this track never
    # arrived — it's still a genuine new track starting, just detected late,
    # so it must be recorded the same as any other listen (blank metadata here
    # gets backfilled once the lookup below lands, same as the webhook path).
    _record_listen(vid, next_item.get('title', ''), next_item.get('artist', ''),
                   _thumbnail_url(next_item.get('thumbnail')))
    if not next_item.get('title'):
        threading.Thread(target=_lookup_and_update_np, args=(vid,), daemon=True).start()
    threading.Thread(target=_refresh_radio_queue, args=(vid,), daemon=True).start()
    return True


def _check_track_overrun():
    """Heartbeat watchdog: playing+confirmed but the position ran well past the
    known duration means both 'finished' and 'started' webhooks were lost."""
    with _np_lock:
        if not (_now_playing.get('playing') and _now_playing.get('playback_confirmed')):
            return
        duration = int(_now_playing.get('duration_ms') or 0)
        started = _now_playing.get('started_at') or 0
        if not duration or not started:
            return
        pos = int(_now_playing.get('position_ms', 0) or 0) \
            + int((time.time() - started) * 1000)
        overshoot = pos - duration
    if overshoot > _OVERRUN_GRACE_MS:
        _promote_next_track('overran duration', position_ms=overshoot)


def _promote_after_finish(prev_video_id):
    """'finished' arrived but no 'started' followed. Give the real webhook a
    grace period; if state still points at the finished track, promote."""
    time.sleep(_FINISH_PROMOTE_DELAY)
    cur = _get_now_playing()
    if cur.get('video_id') != prev_video_id or cur.get('playing'):
        return  # a real webhook (or user action) already moved us on
    _promote_next_track('finished, no started webhook',
                        position_ms=int(_FINISH_PROMOTE_DELAY * 1000))


def _queue_neighbor(action):
    cur = _get_now_playing()
    queue = cur.get('queue') or []
    video_id = cur.get('video_id', '')
    if len(queue) <= 1 and video_id:
        _refresh_radio_queue(video_id)
        cur = _get_now_playing()
        queue = cur.get('queue') or []
    if len(queue) <= 1:
        return None, "Recommendations are still loading. Try again in a moment."
    idx = cur.get('queue_index', -1)
    if idx < 0 and video_id:
        idx = next((i for i, item in enumerate(queue) if item.get('video_id') == video_id), -1)
    if idx < 0:
        idx = 0
    target_idx = idx + (1 if action == 'next' else -1)
    if target_idx < 0:
        return None, "You're already at the first recommendation."
    if target_idx >= len(queue):
        # End of the visible queue: extend it with the radio continuation of
        # the last track instead of dead-ending, mirroring what the skill does
        # at PlaybackNearlyFinished.
        extended = _extend_server_queue()
        cur = _get_now_playing()
        queue = cur.get('queue') or []
        if not extended or target_idx >= len(queue):
            return None, "No more recommendations are loaded yet."
    return (target_idx, queue[target_idx]), None


def _extend_server_queue():
    """Append the radio continuation of the queue's last track to the visible
    queue (dedup against what's already there). Returns True iff it grew."""
    try:
        cur = _get_now_playing()
        queue = cur.get('queue') or []
        if not queue:
            return False
        last_id = queue[-1].get('video_id', '')
        if not _valid_video_id(last_id):
            return False
        playlist = asyncio.run(Supporting.get_radio_queue(last_id))
        if not playlist:
            return False
        have = {q.get('video_id') for q in queue}
        new_items = []
        for item in playlist:
            vid = item.get('video_id', '')
            if not vid or vid in have:
                continue
            have.add(vid)
            t = item.get('thumbnail')
            thumb = t.get('url', '') if isinstance(t, dict) else (t if isinstance(t, str) else '')
            new_items.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'thumbnail': thumb,
                'video_id': vid,
                'duration_ms': item.get('duration_ms', 0),
            })
        if not new_items:
            return False
        with _np_lock:
            live = list(_now_playing.get('queue') or [])
            # Only append if the queue hasn't been replaced meanwhile.
            if not live or live[-1].get('video_id') != last_id:
                return False
            _now_playing['queue'] = live + new_items
            _now_playing['updated_at'] = time.time()
        _notify_sse()
        return True
    except Exception:
        traceback.print_exc()
        return False


def _confirm_stream_delivery(video_id):
    """Fallback playback confirmation when Lambda's PlaybackStarted is late.

    This runs only after the audio file is ready to be sent to the Echo. It is
    later than command dispatch and ignores prefetches for another current song,
    so the progress bar does not start just because a command was clicked.
    """
    if not _valid_video_id(video_id):
        return
    with _np_lock:
        if _now_playing.get('video_id') != video_id:
            return
        if _now_playing.get('playback_confirmed'):
            return
        _reset_progress(_now_playing.get('position_ms', 0))
        _now_playing['playing'] = True
        _now_playing['playback_confirmed'] = True
        _now_playing['updated_at'] = time.time()
    _notify_sse()


# Amazon's unofficial remote-control API occasionally accepts an injected voice
# command (returns no error) but the Echo never actually acts on it — the skill
# is never invoked, so /proxy/ is never hit and playback silently never starts.
# This watchdog waits for confirmation (either PlaybackStarted's webhook or
# /proxy/ being hit — see _confirm_stream_delivery) and, if it doesn't arrive in
# time, resends the command once before giving up and surfacing an error.
PLAYBACK_CONFIRM_TIMEOUT = 12.0
PLAYBACK_CONFIRM_POLL_INTERVAL = 0.5


def _download_in_progress(video_id):
    """True while some thread's ensure_downloaded currently holds the per-id
    download lock for video_id (i.e. yt-dlp is still fetching it)."""
    with _locks_guard:
        lock = _download_locks.get(video_id)
    return bool(lock and lock.locked())


def _watch_playback_confirmation(video_id, resend):
    """Background watchdog: retries `resend()` once if `video_id` isn't
    confirmed playing within PLAYBACK_CONFIRM_TIMEOUT. `resend` is a zero-arg
    callable that re-arms and resends the play command; it should return an
    error string or None. Never raises."""
    if not _valid_video_id(video_id):
        return

    def _confirmed():
        with _np_lock:
            return (_now_playing.get('video_id') == video_id
                    and bool(_now_playing.get('playback_confirmed')))

    def _still_relevant():
        # Give up quietly if the user has since moved on to another track.
        with _np_lock:
            return _now_playing.get('video_id') == video_id

    def _wait_once():
        deadline = time.time() + PLAYBACK_CONFIRM_TIMEOUT
        while time.time() < deadline:
            if _confirmed() or not _still_relevant():
                return True
            # Play commands are dispatched while yt-dlp may still be fetching
            # the audio, so a missing confirmation can just mean the Echo is
            # blocked on /proxy/'s first byte. Don't count download time
            # toward the timeout — a resend mid-download wouldn't start
            # playback any sooner.
            if _download_in_progress(video_id):
                deadline = time.time() + PLAYBACK_CONFIRM_TIMEOUT
            time.sleep(PLAYBACK_CONFIRM_POLL_INTERVAL)
        return _confirmed()

    try:
        if _wait_once():
            return
        if not _still_relevant():
            return
        print(f"[playback-watchdog] no confirmation for {video_id} in "
              f"{PLAYBACK_CONFIRM_TIMEOUT}s, retrying once")
        error = resend()
        if error:
            print(f"[playback-watchdog] retry dispatch failed: {error}")
            _update_now_playing(playback_error=error)
            return
        if _wait_once():
            return
        if not _still_relevant():
            return
        print(f"[playback-watchdog] retry for {video_id} also unconfirmed")
        _update_now_playing(
            playback_error="Playback didn't start. Check the device and try again.")
    except Exception:
        traceback.print_exc()


def _dispatch_play_with_retry(serial, video_id, offset_ms=0):
    """Arm+send video_id for playback, watchdog-guarded: if the Echo never
    confirms within PLAYBACK_CONFIRM_TIMEOUT, resend once automatically.
    Returns an error string from the *initial* send, or None. The retry (if
    any) happens in the background and reports failure via 'playback_error'
    in now_playing rather than this return value."""
    def _send():
        _arm_play(serial, video_id, offset_ms)
        return alexa_remote.remote.play_video_id(serial, video_id, offset_ms)

    error = _send()
    if error:
        return error
    threading.Thread(
        target=_watch_playback_confirmation, args=(video_id, _send), daemon=True
    ).start()
    return None


# ---------- web remote (controls Echo devices via alexa_remote) ----------

# How long a password-verified login has to enter its 2FA code before the
# pending state expires and it must start over.
_PENDING_TTL = 300  # seconds


@app.route("/login/", methods=["GET", "POST"])
def login():
    if not _remote_login_enabled():
        # No credentials configured; fall back to the key-in-URL scheme.
        return redirect('/remote/')
    if request.method == "GET":
        if _logged_in():
            return redirect('/')
        return render_template("login.html", totp=_totp_enabled())

    body = request.get_json(silent=True) or request.form

    # Step 2: a password-verified session submitting its 2FA code.
    if body.get("step") == "totp":
        pending_at = session.get('pending_at', 0)
        if session.get('pending_user') != REMOTE_USER or (time.time() - pending_at) > _PENDING_TTL:
            session.pop('pending_user', None)
            session.pop('pending_at', None)
            return error_response('login timed out, start again', 401)
        # str(): a JSON body can carry the 6-digit code as a number, and
        # .strip() on an int would 500 instead of failing the check cleanly.
        if not _totp_verify(str(body.get("code") or "").strip()):
            return error_response('invalid authentication code', 401)
        session.pop('pending_user', None)
        session.pop('pending_at', None)
        session['remote_user'] = REMOTE_USER
        session.permanent = True
        return jsonify({'ok': True})

    # Step 1: verify username + password. Coerce to str: a JSON body can send
    # a non-string (number/bool/null) for either field, and compare_digest
    # raises TypeError on mismatched types instead of just failing the check.
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not (hmac.compare_digest(username, REMOTE_USER) and hmac.compare_digest(password, REMOTE_PASSWORD)):
        return error_response('invalid username or password', 401)
    if _totp_enabled():
        # Hold the verified identity briefly while the browser collects the code.
        session['pending_user'] = REMOTE_USER
        session['pending_at'] = int(time.time())
        return jsonify({'ok': True, 'totp_required': True})
    session['remote_user'] = REMOTE_USER
    session.permanent = True
    return jsonify({'ok': True})


@app.route("/logout/", methods=["POST", "GET"])
def logout():
    session.pop('remote_user', None)
    if request.method == "GET":
        return redirect('/login/')
    return jsonify({'ok': True})


@app.route("/remote/", methods=["GET"])
def remote_page():
    # The canonical URL is now the bare domain; keep /remote/ alive for old
    # bookmarks and installed PWAs but bounce them to the clean URL. The
    # key-in-URL scheme still serves here directly (see root()).
    if _remote_login_enabled():
        return redirect('/')
    return render_template("remote.html")


@app.route("/alexa/status/", methods=["GET"])
def alexa_status():
    return jsonify(alexa_remote.remote.status())


@app.route("/alexa/init/", methods=["GET"])
def alexa_init():
    """Combined startup endpoint: returns auth status + device list + now-playing
    in one round-trip so the page can render instantly on load."""
    status = alexa_remote.remote.status()
    result = {'status': status, 'devices': [], 'now_playing': None}
    if status.get('logged_in'):
        devices, _ = alexa_remote.remote.devices(refresh=False)
        result['devices'] = devices
        # If a serial is provided, include now-playing for that device
        serial = request.args.get('serial') or (devices[0]['serial'] if devices else None)
        if serial:
            with _np_lock:
                result['now_playing'] = _np_snapshot(serial)
            result['serial'] = serial
    return jsonify(result)


@app.route("/alexa/proxy_login/", methods=["POST"])
def alexa_proxy_login():
    """Start the interactive proxy login; returns the URL for the user to open.
    Credentials come from the request body, are used only to seed the proxy
    login, and are never stored server-side."""
    body = request.get_json(silent=True) or {}
    # str(): a non-string JSON value would 500 on .strip() / break AlexaLogin.
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if not email or not password:
        return error_response('Enter your Amazon email and password.', 400)
    # There is only one live Alexa session for the whole server (see
    # AlexaRemote); logging in again silently replaces whichever Amazon
    # account currently controls every Echo. Require an explicit 'force' from
    # a stale tab/accidental resubmit instead of swapping accounts unnoticed.
    if alexa_remote.remote.is_logged_in() and not body.get("force"):
        return error_response(
            'Already signed in to Amazon. Signing in again will replace the '
            'current session for every device. Resubmit with "force": true '
            'to continue.', 409)
    url, error = alexa_remote.remote.proxy_start_url(email, password)
    if error:
        return error_response(error, 502)
    return jsonify({'login_url': url})


@app.route("/alexa/proxy_check/", methods=["GET"])
def alexa_proxy_check():
    """Poll whether the browser has finished the proxy login."""
    return jsonify(alexa_remote.remote.proxy_check())


@app.route("/alexa/devices/", methods=["GET"])
def alexa_devices():
    refresh = request.args.get("refresh") == "1"
    devices, error = alexa_remote.remote.devices(refresh=refresh)
    if error:
        return error_response(error, 502)
    return jsonify({'devices': devices})


@app.route("/alexa/volume/", methods=["GET"])
def alexa_volume():
    serial = request.args.get("serial")
    if not serial:
        return error_response('missing "serial"', 400)
    volume, error = alexa_remote.remote.volume(serial)
    if error:
        cached_volume = _get_volume_state(serial)
        return jsonify({
            'volume': cached_volume,
            'available': cached_volume is not None,
            'stale': cached_volume is not None,
            'error': error,
        })
    _record_volume_state(serial, volume, notify=True)
    return jsonify({'volume': volume, 'available': True, 'stale': False})


def _device_dispatch_failed(error):
    """A command to the Echo failed (usually offline/unreachable). Playback is
    no longer under our control, so stop claiming it's live — this freezes the
    progress bar on every open remote instead of ticking on forever — then
    surface a clear 502."""
    _update_now_playing(playing=False)
    return error_response(error or 'Device is offline or unreachable.', 502)


@app.route("/alexa/command/", methods=["POST"])
def alexa_command():
    body = request.get_json(silent=True) or {}
    serial, action = body.get("serial"), body.get("action")
    if not serial or not action:
        return error_response('missing "serial" or "action"', 400)
    if action in ('next', 'previous'):
        target, target_error = _queue_neighbor(action)
        if target_error:
            return error_response(target_error, 409)
        target_idx, item = target
        video_id = item.get('video_id', '')
        if not _valid_video_id(video_id):
            return error_response('That recommendation is missing a video id.', 409)
        threading.Thread(target=Supporting.ensure_downloaded, args=(video_id,), daemon=True).start()
        error = _dispatch_play_with_retry(serial, video_id)
        if error:
            return _device_dispatch_failed(error)
        thumb = item.get('thumbnail', '')
        if isinstance(thumb, dict):
            thumb = thumb.get('url', '')
        _update_now_playing(playing=False,
                            title=item.get('title', ''),
                            artist=item.get('artist', ''),
                            thumbnail=thumb,
                            video_id=video_id,
                            duration_ms=item.get('duration_ms', 0),
                            position_ms=0,
                            playback_confirmed=False,
                            queue_index=target_idx)
        return jsonify({'ok': True, 'now_playing': item, 'queue_index': target_idx})
    resume_position_ms = None
    if action == 'play':
        cur = _get_now_playing()
        video_id = cur.get('video_id', '')
        if _valid_video_id(video_id):
            with _np_lock:
                position_ms = _computed_position_ms()
            resume_position_ms = position_ms
            # Alexa custom skills can't resume a paused stream directly, so resume
            # replays the track from where it was frozen. The offset rides in the
            # arm (play_video_id ignores its position argument and reads it back
            # from /armed_play/), so we must arm before triggering — otherwise the
            # skill gets no offset and resume silently does nothing.
            global _last_reposition_at
            _last_reposition_at = time.time()
            error = _dispatch_play_with_retry(serial, video_id, position_ms)
        else:
            error = alexa_remote.remote.command(serial, action, body.get("value"))
    else:
        error = alexa_remote.remote.command(serial, action, body.get("value"))
    if error:
        return _device_dispatch_failed(error)
    # Track play/pause state server-side
    if action == 'volume':
        _set_volume_state(serial, body.get("value"))
        _notify_sse()
    elif action == 'pause':
        # Freeze the progress anchor at the current computed position so the bar
        # stops advancing while paused (and doesn't jump on resume).
        with _np_lock:
            _reset_progress(_computed_position_ms())
            _now_playing['playing'] = False
            _now_playing['updated_at'] = time.time()
        _notify_sse()
    elif action == 'play':
        # Resume from where we froze: re-anchor started_at to now, keep position.
        with _np_lock:
            _reset_progress(resume_position_ms if resume_position_ms is not None else _now_playing.get('position_ms', 0))
            _now_playing['playing'] = False
            _now_playing['playback_confirmed'] = False
            _now_playing['updated_at'] = time.time()
        _notify_sse()
    return jsonify({'ok': True})

@app.route("/alexa/seek/", methods=["POST"])
def alexa_seek():
    """Seek the current track to an absolute position (seconds). Alexa custom
    skills have no seek directive, so we ask the skill (as a spoken command) to
    re-issue playback at the new offset — the same routing the transport buttons
    use. The skill's PlaybackStarted webhook then re-anchors the progress bar to
    the new offset; we also anchor optimistically here so the app updates now."""
    body = request.get_json(silent=True) or {}
    serial = body.get("serial")
    if not serial:
        return error_response('missing "serial"', 400)
    # Coerce inside the try: a JSON *string* like "12" for position_seconds
    # would otherwise be string-repeated by * 1000 (Python), and int() would
    # then parse the 2000-char result as an absurd position instead of 12000.
    try:
        position = body.get("position_ms")
        if position is None:
            position = float(body.get("position_seconds") or 0) * 1000
        position_ms = max(0, int(position))
    except (TypeError, ValueError):
        return error_response('"position_ms" must be a number', 400)
    cur = _get_now_playing()
    video_id = cur.get('video_id', '')
    if not _valid_video_id(video_id):
        return error_response('nothing is playing to seek', 409)
    duration_ms = int(cur.get('duration_ms') or 0)
    if duration_ms > 0:
        position_ms = min(position_ms, max(0, duration_ms - 1000))
    # Arm the play with the seek offset before triggering. play_video_id sends a
    # short NLU-safe phrase and the skill reads the video id + offset back from
    # /armed_play/ — the offset is carried by the arm, not the phrase, so without
    # arming here the seek would replay from the start (or do nothing).
    global _last_reposition_at
    _last_reposition_at = time.time()
    error = _dispatch_play_with_retry(serial, video_id, position_ms)
    if error:
        return _device_dispatch_failed(error)
    # Optimistically re-anchor so the bar jumps immediately; the skill's
    # PlaybackStarted webhook will confirm/correct shortly after.
    with _np_lock:
        _reset_progress(position_ms)
        _now_playing['playing'] = False
        _now_playing['playback_confirmed'] = False
        _now_playing['updated_at'] = time.time()
    _notify_sse()
    return jsonify({'ok': True})


# Webhook from Lambda: the skill POSTs state events (started/stopped/finished)
# directly to the server so we don't need to poll Amazon's API.
@app.route("/alexa/state_event/", methods=["POST"])
def alexa_state_event():
    body = request.get_json(silent=True) or {}
    # Auth already handled by require_api_key middleware (key in ?key= param).
    event = body.get('event', '')
    if 'volume' in body:
        _record_volume_state(body.get('serial'), body.get('volume'), notify=True)
    sys.stderr.write(f"[np] webhook: event={event!r} video_id={body.get('video_id', '')!r}\n")
    sys.stderr.flush()
    if event == 'stopped':
        _update_now_playing(playing=False)
    elif event == 'started':
        video_id = body.get('video_id', '')
        if video_id and not _valid_video_id(video_id):
            # Malformed id from a bad/forged webhook call: ignore the id but
            # still process the event so a stray play state isn't left stuck.
            video_id = ''
        # Offset the track started at (non-zero after a seek, or when the skill
        # resumes partway through). Anchors the web remote's progress bar.
        try:
            offset_in_ms = int(body.get('offset_in_ms') or 0)
        except (TypeError, ValueError):
            offset_in_ms = 0
        same_track = video_id and video_id == _now_playing.get('video_id')
        if video_id and not same_track:
            # New track: pull instant metadata from the queue if we have it.
            queue = _now_playing.get('queue', [])
            matched = next((item for item in queue if item.get('video_id') == video_id), None)
            if matched:
                i = queue.index(matched)
                _update_now_playing(playing=True, video_id=video_id, title=matched['title'],
                                    artist=matched['artist'], thumbnail=matched['thumbnail'],
                                    duration_ms=matched.get('duration_ms', 0),
                                    playback_confirmed=True,
                                    queue_index=i, position_ms=offset_in_ms)
                if offset_in_ms < 5000:
                    _record_listen(video_id, matched.get('title', ''),
                                   matched.get('artist', ''),
                                   _thumbnail_url(matched.get('thumbnail')))
            else:
                # queue_index=-1: this track isn't in the visible queue, so the
                # old highlight is wrong until _refresh_radio_queue rebuilds it.
                # The previous track's title/artist/thumbnail are stale for this
                # video_id — look the real metadata up, otherwise the now-playing
                # card stays wedged on the old song for every later auto-advance
                # whose track is missing from the visible queue.
                _update_now_playing(playing=True, video_id=video_id, position_ms=offset_in_ms,
                                    playback_confirmed=True, queue_index=-1)
                # Record with blank metadata now (the np card's title is still
                # the previous track's here); _lookup_and_update_np backfills
                # the real title/artist/thumbnail once the lookup lands.
                if offset_in_ms < 5000:
                    _record_listen(video_id, '', '', '')
                threading.Thread(target=_lookup_and_update_np, args=(video_id,), daemon=True).start()
            threading.Thread(target=_refresh_radio_queue, args=(video_id,), daemon=True).start()
        else:
            # Same track re-starting (a seek): re-anchor to the reported offset
            # without disturbing the metadata. started_at is passed explicitly so
            # _update_now_playing doesn't treat it as a no-op.
            with _np_lock:
                _reset_progress(offset_in_ms)
                _now_playing['playing'] = True
                _now_playing['playback_confirmed'] = True
                _now_playing['updated_at'] = time.time()
            _notify_sse()
            if video_id:
                # A same-track restart from the top is a replay: bump it in
                # history. A low offset alone doesn't prove that though — a
                # seek (or a resume-from-pause) to near the start of the same
                # track reports the same shape (same video_id, low offset)
                # and must not count as a fresh listen, so also require that
                # neither repositioned this very playback.
                repositioned_recently = (time.time() - _last_reposition_at) < _REPOSITION_SUPPRESS_WINDOW
                if offset_in_ms < 5000 and not repositioned_recently:
                    np = _get_now_playing()
                    _record_listen(video_id, np.get('title', ''), np.get('artist', ''),
                                   _thumbnail_url(np.get('thumbnail')))
                threading.Thread(target=_refresh_radio_queue, args=(video_id,), daemon=True).start()
        # Pre-download the next few songs in the queue now that playback has
        # started. This gives the full song duration (~3-5 min) to download,
        # instead of racing against PlaybackNearlyFinished's ~30s window.
        queue = _now_playing.get('queue', [])
        idx = _now_playing.get('queue_index', 0)
        if len(queue) > 1:
            threading.Thread(target=_prewarm_queue_audio,
                             args=(queue, idx), daemon=True).start()
    elif event == 'finished':
        prev_video_id = _now_playing.get('video_id')
        _update_now_playing(playing=False)
        # The Echo starts the pre-buffered next track right away; if the
        # matching 'started' webhook gets lost, promote it ourselves after a
        # grace period so the card doesn't stay wedged on the finished song.
        threading.Thread(target=_promote_after_finish,
                         args=(prev_video_id,), daemon=True).start()
    return jsonify({'ok': True})



# ---- Blank-state recommendations (web remote) ----
# Mixes radios seeded from a couple of randomly chosen recent history tracks,
# shuffled, so the idle screen varies between visits instead of showing the
# same deterministic YouTube radio every time. Cold start (no history) falls
# back to YT Music's charts. Short cache so a refresh a few minutes later gets
# a fresh mix, without re-hitting YouTube on every single page load.
_RECS_CACHE_TTL = 3 * 60
_recs_cache = {'built_at': 0, 'items': []}
_recs_lock = threading.Lock()


async def _none():
    return None


# Country for the cold-start charts (ISO 3166 alpha-2). Defaults to India so a
# user with no history yet sees Indian trending music, not a US Top-40 list.
CHARTS_COUNTRY = os.environ.get("CHARTS_COUNTRY", "IN")

# Well-known, durable YouTube Music videos used purely as radio *seeds* for the
# cold-start fallback (only reached when there's no history AND charts failed).
# Indian tracks so the fallback matches the expected audience rather than a
# generic Western radio. get_radio_queue() (get_watch_playlist) is the same
# stable call used for real playback everywhere else in this app.
#   - 1-YZS_TQhBc  Kesariya (Brahmastra)
#   - RQf6ozD6EhE  Apna Bana Le (Bhediya)
#   - lFhKQjxIsGw  Chaleya (Jawan)
_FALLBACK_SEED_IDS = ['1-YZS_TQhBc', 'RQf6ozD6EhE', 'lFhKQjxIsGw']


async def _get_fallback_queue(exclude_ids):
    for seed in _FALLBACK_SEED_IDS:
        queue = await Supporting.get_radio_queue(seed)
        if queue:
            return [item for item in queue if item.get('video_id') not in exclude_ids]
    return []


async def _build_recommendations():
    with _history_lock:
        history = _load_history()
    seen_ids = {e['video_id'] for e in history if e.get('video_id')}
    sys.stderr.write(f"[recs] history has {len(history)} entries\n")

    if not history:
        sys.stderr.write("[recs] no history -> charts/fallback (generic)\n")
        sys.stderr.flush()
        charts = await Supporting.get_charts_queue()
        pool = charts if charts else await _get_fallback_queue(seen_ids)
        random.shuffle(pool)
        return pool[:40]

    # Seed radios from *several* randomly chosen history tracks so the mix is
    # firmly grounded in what the user actually listens to (e.g. Indian music),
    # and yields enough tracks to fill the grid without falling through to the
    # generic fallback. More seeds also means a different mix each refresh.
    pool = [e['video_id'] for e in history if e.get('video_id')]
    random.shuffle(pool)
    # Bias toward recent history (first ~8), but pull up to 5 seeds total.
    recent = [e['video_id'] for e in history[:8] if e.get('video_id')]
    random.shuffle(recent)
    seeds = list(dict.fromkeys(recent[:3] + pool))[:5]

    # return_exceptions so one failed/raising seed radio doesn't abort the whole
    # build (which would return the stale cache — possibly a generic fallback).
    radios = await asyncio.gather(
        *[Supporting.get_radio_queue(s) for s in seeds], return_exceptions=True)
    ok = sum(1 for r in radios if isinstance(r, list) and r)
    sys.stderr.write(f"[recs] {len(seeds)} seeds -> {ok} non-empty radios\n")
    sys.stderr.flush()
    radios = [r if isinstance(r, list) else None for r in radios]

    mixed, out_ids = [], set()
    # Interleave the radios so the list reads as one mixed feed rather than
    # seed-1's whole radio then seed-2's. zip_longest so a shorter/failed radio
    # on one side doesn't discard the others' results.
    for group in itertools.zip_longest(*[r or [] for r in radios]):
        for item in group:
            if not item:
                continue
            vid = item.get('video_id')
            if vid and vid not in out_ids:
                out_ids.add(vid)
                mixed.append(item)

    # Prefer genuinely new tracks (not already in history), then fill with
    # familiar ones from the same (user-grounded) radios.
    fresh = [i for i in mixed if i['video_id'] not in seen_ids]
    familiar = [i for i in mixed if i['video_id'] in seen_ids]
    random.shuffle(fresh)
    result = (fresh + familiar)[:40]
    sys.stderr.write(f"[recs] built {len(result)} from history radios "
                     f"(fresh={len(fresh)} familiar={len(familiar)})\n")
    sys.stderr.flush()

    # Only reach for the generic Western fallback when the user's own radios
    # produced almost nothing (e.g. every seed's radio failed) — never just to
    # top up an already-decent list, since that pollutes an Indian feed with
    # unrelated tracks.
    if len(result) < 6:
        sys.stderr.write("[recs] history radios nearly empty -> generic fallback\n")
        sys.stderr.flush()
        topoff = await Supporting.get_charts_queue() or await _get_fallback_queue(out_ids | seen_ids)
        for item in topoff:
            vid = item.get('video_id')
            if vid and vid not in out_ids and vid not in seen_ids:
                out_ids.add(vid)
                result.append(item)
            if len(result) >= 40:
                break
    return result


@app.route("/recommendations/", methods=["GET"])
def get_recommendations():
    force = request.args.get('refresh') == '1'
    # Held across the (slow, network-bound) rebuild so concurrent requests on
    # a cold/expired cache queue up behind one rebuild instead of each firing
    # their own set of YT Music calls (this is a single-user tool, so a few
    # hundred ms of serialization here is a non-issue).
    with _recs_lock:
        fresh_enough = (time.time() - _recs_cache['built_at']) < _RECS_CACHE_TTL
        if fresh_enough and not force:
            return jsonify(_recs_cache['items'])
        try:
            items = asyncio.run(_build_recommendations())
        except Exception:
            traceback.print_exc()
            return jsonify(_recs_cache['items'])
        _recs_cache['items'] = items
        _recs_cache['built_at'] = time.time()
        return jsonify(items)


# ---- Recently-listened history endpoints (web remote) ----
@app.route("/history/", methods=["GET"])
def get_history():
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, HISTORY_MAX))
    with _history_lock:
        history = _load_history()
    return jsonify(history[:limit])


@app.route("/history/", methods=["DELETE"])
def clear_history():
    with _history_lock:
        _save_history([])
    # Recs are seeded from history; a stale cache would keep suggesting the
    # same tracks derived from history the user just wiped.
    with _recs_lock:
        _recs_cache['built_at'] = 0
    return jsonify({'ok': True})


@app.route("/history/<video_id>", methods=["DELETE"])
def remove_history_item(video_id):
    with _history_lock:
        history = _load_history()
        history = [e for e in history if e.get('video_id') != video_id]
        _save_history(history)
    return jsonify({'ok': True})


@app.route("/alexa/now_playing/", methods=["GET"])
def alexa_now_playing():
    serial = request.args.get("serial")
    if not serial:
        return error_response('missing "serial"', 400)
    # Same shape as the SSE payload (progress fields included) so the poll
    # fallback keeps the bar in sync just like the live stream does.
    with _np_lock:
        return jsonify(_np_snapshot(serial))


@app.route("/alexa/now_playing/stream")
def now_playing_stream():
    serial = request.args.get("serial")
    """SSE endpoint — pushes now-playing state to the browser in real time."""
    def generate():
        q = _queue_mod.Queue()
        with _sse_lock:
            _sse_subscribers[q] = serial
        _ensure_heartbeat()  # start the periodic re-sync (idempotent)
        if serial:
            threading.Thread(target=_refresh_volume, args=(serial, True), daemon=True).start()
        try:
            # Send current state immediately on connect
            with _np_lock:
                data = json.dumps(_np_snapshot(serial))
            yield f"data: {data}\n\n"
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                except _queue_mod.Empty:
                    # Keepalive to prevent proxy / browser from closing
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                _sse_subscribers.pop(q, None)
    return Response(generate(), content_type='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# The remote's play box accepts pasted YouTube / YT Music links as well as typed
# song names. Song URLs play by exact video id; playlist-only URLs still fall
# back through the older resolver below.
_YT_VIDEO_RE = re.compile(r'(?:youtu\.be/|[?&]v=|/watch/|/shorts/)([\w-]{11})')
_YT_LIST_RE = re.compile(r'[?&]list=([\w-]+)')
_YT_URL_LIKE_RE = re.compile(r'(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com/|youtu\.be/)', re.I)


def extract_youtube_video_id(url: str):
    """Return an exact YouTube video id from common URL shapes, or None."""
    url = (url or '').strip()
    if not re.match(r'https?://', url, re.I) and _YT_URL_LIKE_RE.match(url):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.netloc or '').lower().split(':', 1)[0]
    path = unquote(parsed.path or '').strip('/')
    query = parse_qs(parsed.query or '')
    candidate = None

    if query.get('v'):
        candidate = query['v'][0]
    elif host.endswith('youtu.be') and path:
        candidate = path.split('/')[0]
    elif host.endswith('youtube.com') or host.endswith('music.youtube.com') or host.endswith('m.youtube.com'):
        parts = [p for p in path.split('/') if p]
        if len(parts) >= 2 and parts[0] in {'shorts', 'embed', 'live'}:
            candidate = parts[1]
        elif len(parts) >= 2 and parts[0] == 'watch':
            candidate = parts[1]

    if not candidate:
        match = _YT_VIDEO_RE.search(url)
        candidate = match.group(1) if match else None
    if not candidate:
        return None
    candidate = candidate.split('&', 1)[0].split('?', 1)[0]
    return candidate if _valid_video_id(candidate) else None


def resolve_play_query(query: str):
    """(spoken_query, error). Plain text passes straight through; a YouTube /
    YT Music link is looked up and turned into a searchable song phrase."""
    if not re.match(r'https?://', query, re.I):
        return query, None
    ytmusic = YTMusic()
    video_match = _YT_VIDEO_RE.search(query)
    list_match = _YT_LIST_RE.search(query)
    # A watch link may carry both v= and list=; the specific video wins.
    if video_match:
        video_id = video_match.group(1)
        # Strategy 1: get_song (direct player endpoint)
        try:
            info = ytmusic.get_song(video_id)
            details = (info or {}).get('videoDetails') or {}
            title = details.get('title')
            author = (details.get('author') or '')
            if author.endswith(' - Topic'):
                author = author[:-len(' - Topic')]
            if title:
                return (f"{title} {author}").strip(), None
        except Exception:
            print(f"[resolve] get_song({video_id}) failed, trying fallback")
            traceback.print_exc()
        # Strategy 2: get_watch_playlist (proven to work on VPS)
        try:
            radio = ytmusic.get_watch_playlist(videoId=video_id)
            tracks = (radio or {}).get('tracks') or []
            if tracks:
                t = tracks[0]
                title = t.get('title') or ''
                artist = ' '.join(a.get('name') or '' for a in (t.get('artists') or []))
                if title:
                    return (f"{title} {artist}").strip(), None
        except Exception:
            print(f"[resolve] get_watch_playlist({video_id}) failed, trying search")
            traceback.print_exc()
        # Strategy 3: search by video ID
        try:
            results = ytmusic.search(video_id, ignore_spelling=True)
            for r in (results or [])[:3]:
                if r.get('videoId') == video_id or r.get('title'):
                    title = r.get('title') or ''
                    artist = ' '.join(a.get('name') or '' for a in (r.get('artists') or []))
                    if title:
                        return (f"{title} {artist}").strip(), None
        except Exception:
            traceback.print_exc()
        return None, "Couldn't resolve that YouTube link."
    if list_match:
        try:
            playlist = ytmusic.get_playlist(list_match.group(1), 1)
        except Exception:
            traceback.print_exc()
            return None, "Couldn't read that playlist link."
        tracks = (playlist or {}).get('tracks') or []
        if not tracks:
            return None, "That playlist looks empty."
        first = tracks[0]
        title = first.get('title') or ''
        artist = " ".join(a.get('name') or '' for a in (first.get('artists') or []))
        if not title:
            return None, "Couldn't read that playlist link."
        return (f"{title} {artist}").strip(), None
    return None, "That link isn't a YouTube or YT Music song or playlist."


def _quick_song_lookup(query):
    """Fast metadata lookup for the now-playing display on the web remote."""
    try:
        ytmusic = YTMusic()
        results = ytmusic.search(query, filter='songs', ignore_spelling=True)
        if results:
            track = results[0]
            thumbnails = track.get('thumbnails') or []
            return {
                'title': track.get('title', query),
                'artist': ' & '.join(a.get('name', '') for a in (track.get('artists') or [])),
                'thumbnail': thumbnails[-1].get('url', '') if thumbnails else '',
                'duration_ms': Supporting.duration_ms(track),
            }
    except Exception:
        traceback.print_exc()
    return {'title': query, 'artist': '', 'thumbnail': ''}


@app.route("/alexa/play/", methods=["POST"])
async def alexa_play():
    body = request.get_json(silent=True) or {}
    # str(): a non-string JSON "query" would 500 on .strip() instead of 400.
    serial, query = body.get("serial"), str(body.get("query") or "").strip()
    if not serial or not query:
        return error_response('missing "serial" or "query"', 400)
    print(f"[alexa/play] query={query!r} serial={serial}")
    # For plain-text queries (non-URLs), skip the blocking resolve and fire
    # everything in the background so the web remote gets an instant response.
    # Exact YouTube URLs bypass fuzzy search; playlist-only links still use the
    # older resolver below.
    is_link = bool(re.match(r'https?://', query, re.I) or _YT_URL_LIKE_RE.match(query))
    direct_video_id = extract_youtube_video_id(query) if is_link else None
    list_match = _YT_LIST_RE.search(query) if is_link else None
    if direct_video_id:
        optimistic_info = {
            'title': 'YouTube link',
            'artist': '',
            'thumbnail': '',
            'video_id': direct_video_id,
            'duration_ms': 0,
        }
        _update_now_playing(playing=False,
                            title=optimistic_info['title'],
                            artist='',
                            thumbnail='',
                            video_id=direct_video_id,
                            duration_ms=0,
                            position_ms=0,
                            started_at=time.time(),
                            playback_confirmed=False,
                            queue=[],
                            queue_index=0)

        def _bg_play_direct_link():
            try:
                metadata = _lookup_video_metadata(direct_video_id) or {
                    'title': 'YouTube video',
                    'artist': '',
                    'thumbnail': '',
                    'video_id': direct_video_id,
                    'duration_ms': 0,
                }
                thumb = _thumbnail_url(metadata.get('thumbnail'))
                queue_item = {
                    'title': metadata.get('title', 'YouTube video'),
                    'artist': metadata.get('artist', ''),
                    'thumbnail': thumb,
                    'video_id': direct_video_id,
                    'duration_ms': metadata.get('duration_ms', 0),
                }
                queue, queue_index = [queue_item], 0
                # A watch link opened from inside a playlist (v= and list=)
                # queues the rest of that playlist, like YouTube does. Radio
                # ids (list=RD...) aren't readable playlists; the fetch fails
                # and we keep the single-track queue.
                if list_match:
                    try:
                        tracks = asyncio.run(Supporting.get_playlist_tracks(list_match.group(1)))
                    except Exception:
                        traceback.print_exc()
                        tracks = None
                    if tracks:
                        pl_queue = [{
                            'title': t['title'],
                            'artist': t['artist'],
                            'thumbnail': _thumbnail_url(t.get('thumbnail')),
                            'video_id': t['video_id'],
                            'duration_ms': t.get('duration_ms', 0),
                        } for t in tracks]
                        idx = next((i for i, t in enumerate(pl_queue)
                                    if t['video_id'] == direct_video_id), None)
                        if idx is not None:
                            queue, queue_index = pl_queue, idx
                            # Override fallback metadata with the real playlist item's metadata
                            queue_item = pl_queue[idx]
                            thumb = queue_item.get('thumbnail', '')
                _update_now_playing(playing=False,
                                    title=queue_item.get('title', 'YouTube video'),
                                    artist=queue_item.get('artist', ''),
                                    thumbnail=thumb,
                                    video_id=direct_video_id,
                                    duration_ms=queue_item['duration_ms'],
                                    position_ms=0,
                                    playback_confirmed=False,
                                    queue=queue,
                                    queue_index=queue_index)
                # Dispatch now and let the download overlap Amazon's
                # command→NLU→Lambda round trip; /proxy/ blocks until the
                # file is ready, so the Echo just waits for the first byte.
                _ensure_audio_ready_for_play(direct_video_id)
                error = _dispatch_play_with_retry(serial, direct_video_id)
                if error:
                    print(f"[alexa/play] direct link play failed: {error}")
                    _update_now_playing(playing=False)
                elif not _ensure_audio_ready_for_play(direct_video_id, wait=True):
                    print(f"[alexa/play] direct link download failed video_id={direct_video_id}")
                    _update_now_playing(playing=False)
                else:
                    print(f"[alexa/play] direct link sent successfully video_id={direct_video_id}")
                    _prewarm_queue_audio(queue, queue_index)
            except Exception:
                traceback.print_exc()
                _update_now_playing(playing=False)

        threading.Thread(target=_bg_play_direct_link, daemon=True).start()
        print(f"[alexa/play] dispatched direct link video_id={direct_video_id}")
        return jsonify({'ok': True, 'now_playing': optimistic_info})

    # Playlist-only link: queue the playlist's own tracks (in order) instead of
    # collapsing it to a text search for the first song, which would leave a
    # 1-song queue that the skill then fills with radio recommendations.
    if list_match:
        tracks = await Supporting.get_playlist_tracks(list_match.group(1))
        if tracks:
            queue = [{
                'title': t['title'],
                'artist': t['artist'],
                'thumbnail': _thumbnail_url(t.get('thumbnail')),
                'video_id': t['video_id'],
                'duration_ms': t.get('duration_ms', 0),
            } for t in tracks]
            first = queue[0]
            _update_now_playing(playing=False,
                                title=first['title'],
                                artist=first['artist'],
                                thumbnail=first['thumbnail'],
                                video_id=first['video_id'],
                                duration_ms=first['duration_ms'],
                                position_ms=0,
                                started_at=time.time(),
                                playback_confirmed=False,
                                queue=queue,
                                queue_index=0)

            def _bg_play_playlist():
                try:
                    _ensure_audio_ready_for_play(first['video_id'])
                    error = _dispatch_play_with_retry(serial, first['video_id'])
                    if error:
                        print(f"[alexa/play] playlist play failed: {error}")
                        _update_now_playing(playing=False)
                    elif not _ensure_audio_ready_for_play(first['video_id'], wait=True):
                        print(f"[alexa/play] playlist download failed video_id={first['video_id']}")
                        _update_now_playing(playing=False)
                    else:
                        print(f"[alexa/play] playlist sent successfully ({len(queue)} tracks)")
                        _prewarm_queue_audio(queue, 0)
                except Exception:
                    traceback.print_exc()
                    _update_now_playing(playing=False)

            threading.Thread(target=_bg_play_playlist, daemon=True).start()
            print(f"[alexa/play] dispatched playlist ({len(queue)} tracks) video_id={first['video_id']}")
            return jsonify({'ok': True, 'now_playing': first})
        # Unreadable playlist (private, radio-only id, etc.): fall through to
        # the older resolver so the user still gets a sensible error/first song.

    if is_link:
        spoken, error = await asyncio.to_thread(resolve_play_query, query)
        if error:
            print(f"[alexa/play] resolve_play_query failed: {error}")
            return error_response(error, 502)
        print(f"[alexa/play] resolved link to: {spoken!r}")
    else:
        spoken = query

    # Optimistic now-playing: show what we know immediately (the query text).
    # The real metadata fills in via the background lookup → SSE push.
    optimistic_info = {'title': spoken, 'artist': '', 'thumbnail': ''}
    _update_now_playing(playing=False, title=spoken, artist='',
                        thumbnail='', duration_ms=0, position_ms=0,
                        started_at=time.time(), playback_confirmed=False)

    # Resolve once on Flask, start warming the audio, then ask the custom skill
    # to play that exact video id. This avoids a second Lambda-side YT search on
    # first play and gives the cache a head start before Echo fetches /proxy/.
    def _bg_prepare_and_play():
        try:
            response = _prepare_stream_list_cache(spoken, 'songs')
            meta = (response or {}).get('song_info', {}).get('metadata', {})
            if not meta.get('title'):
                error = alexa_remote.remote.play_query(serial, spoken)
                if error:
                    print(f"[alexa/play] play_query failed: {error}")
                    _update_now_playing(playing=False)
                return
            thumb = ''
            t = meta.get('thumbnail')
            if isinstance(t, dict):
                thumb = t.get('url', '')
            elif isinstance(t, str):
                thumb = t
            fields = {
                'title': meta['title'],
                'artist': meta.get('artist', ''),
                'thumbnail': thumb,
                'video_id': meta.get('video_id', ''),
            }
            if meta.get('duration_ms'):
                fields['duration_ms'] = meta['duration_ms']
            _update_now_playing(**fields)
            video_id = meta.get('video_id', '')
            if _valid_video_id(video_id):
                # Dispatch now and let the download (already warming via
                # get_stream) overlap Amazon's command→NLU→Lambda round trip.
                # /proxy/ blocks until the file is ready, so the Echo just
                # waits for the first byte instead of us waiting here.
                _ensure_audio_ready_for_play(video_id)
                error = _dispatch_play_with_retry(serial, video_id)
                if not error and not _ensure_audio_ready_for_play(video_id, wait=True):
                    print(f"[alexa/play] download failed video_id={video_id}")
                    _update_now_playing(playing=False)
                    return
            else:
                error = alexa_remote.remote.play_query(serial, spoken)
            if error:
                print(f"[alexa/play] play failed: {error}")
                _update_now_playing(playing=False)
            else:
                print(f"[alexa/play] sent successfully")
        except Exception:
            traceback.print_exc()
            _update_now_playing(playing=False)

    threading.Thread(target=_bg_prepare_and_play, daemon=True).start()

    print(f"[alexa/play] dispatched to background")
    return jsonify({'ok': True, 'now_playing': optimistic_info})


@app.route("/alexa/shuffle_queue/", methods=["POST"])
def alexa_shuffle_queue():
    """Shuffle the queue while keeping the currently playing song in place.
    Updates now_playing and triggers lazy pre-download for the new order."""
    import random as _random
    cur = _get_now_playing()
    queue = list(cur.get('queue') or [])
    queue_index = cur.get('queue_index', -1)
    if len(queue) <= 1:
        return jsonify({'ok': True, 'queue': queue, 'queue_index': queue_index})

    # Pull the current song out, shuffle the rest, put it back
    if 0 <= queue_index < len(queue):
        current = queue.pop(queue_index)
        _random.shuffle(queue)
        queue.insert(0, current)  # current song goes to front
        new_index = 0
    else:
        _random.shuffle(queue)
        new_index = queue_index

    _update_now_playing(queue=queue, queue_index=new_index)
    # Re-trigger lazy pre-download in the new order
    _prewarm_queue_audio(queue, current_index=new_index, limit=4)
    return jsonify({'ok': True, 'queue': queue, 'queue_index': new_index})


@app.route("/alexa/play_queue/", methods=["POST"])
def alexa_play_queue():
    body = request.get_json(silent=True) or {}
    serial = body.get("serial")
    video_id = body.get("video_id")
    if not serial or not video_id:
        return error_response('missing "serial" or "video_id"', 400)
    if not _valid_video_id(video_id):
        return error_response('invalid "video_id"', 400)

    cur = _get_now_playing()
    queue = cur.get('queue') or []
    item = next((q for q in queue if q.get('video_id') == video_id), None)
    if not item:
        # The web remote's search results pass their metadata along, so a
        # fresh play doesn't need a blocking ytmusic lookup here.
        title = str(body.get("title") or "").strip()
        if title:
            try:
                duration_ms = int(body.get("duration_ms") or 0)
            except (TypeError, ValueError):
                duration_ms = 0
            item = {
                'title': title,
                'artist': str(body.get("artist") or ""),
                'thumbnail': str(body.get("thumbnail") or ""),
                'video_id': video_id,
                'duration_ms': duration_ms,
            }
        else:
            metadata = _lookup_video_metadata(video_id)
            if not metadata:
                return error_response('That queue item is no longer available.', 404)
            item = metadata
        queue = [item]
        target_idx = 0
    else:
        target_idx = queue.index(item)

    _ensure_audio_ready_for_play(video_id, wait=False)
    error = _dispatch_play_with_retry(serial, video_id)
    if error:
        return _device_dispatch_failed(error)

    thumb = _thumbnail_url(item.get('thumbnail'))
    _update_now_playing(playing=False,
                        title=item.get('title', ''),
                        artist=item.get('artist', ''),
                        thumbnail=thumb,
                        video_id=video_id,
                        duration_ms=item.get('duration_ms', 0),
                        position_ms=0,
                        playback_confirmed=False,
                        queue=queue,
                        queue_index=target_idx)
    # Recommendations/charts carry no duration, so the progress bar would show
    # --:-- with no total. Look the real metadata (incl. length) up in the
    # background and patch it into now-playing without blocking playback.
    if not int(item.get('duration_ms') or 0):
        threading.Thread(target=_lookup_and_update_np, args=(video_id,), daemon=True).start()
    # Build the "Up Next" radio queue for this track right away. When a song is
    # played from a recommendation/search it lands here with a single-item
    # queue; without this the UP NEXT panel stays empty until (and unless) the
    # skill's started webhook happens to rebuild it.
    threading.Thread(target=_refresh_radio_queue, args=(video_id,), daemon=True).start()
    return jsonify({'ok': True, 'now_playing': {
        'title': item.get('title', ''),
        'artist': item.get('artist', ''),
        'thumbnail': thumb,
        'video_id': video_id,
        'duration_ms': item.get('duration_ms', 0),
    }, 'queue_index': target_idx})


@app.route("/alexa/queue_add/", methods=["POST"])
def alexa_queue_add():
    """Add a song to the queue without starting playback.

    Body params:
      serial      – device serial (required)
      video_id    – YouTube video id (required)
      position    – 'next' (insert after current track) or 'last' (append to end)
      title, artist, thumbnail, duration_ms – optional metadata
    """
    body = request.get_json(silent=True) or {}
    serial = body.get("serial")
    video_id = body.get("video_id")
    position = body.get("position", "last")  # 'next' or 'last'
    if not serial:
        return error_response('missing "serial"', 400)
    if not video_id or not _valid_video_id(video_id):
        return error_response('missing or invalid "video_id"', 400)
    if position not in ('next', 'last'):
        return error_response('"position" must be "next" or "last"', 400)

    # Build the item from the body metadata, or look it up
    title = str(body.get("title") or "").strip()
    if title:
        try:
            duration_ms = int(body.get("duration_ms") or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        new_item = {
            'title': title,
            'artist': str(body.get("artist") or ""),
            'thumbnail': str(body.get("thumbnail") or ""),
            'video_id': video_id,
            'duration_ms': duration_ms,
        }
    else:
        metadata = _lookup_video_metadata(video_id)
        if not metadata:
            return error_response('Could not find metadata for that video.', 404)
        new_item = metadata
        # Ensure thumbnail is a string URL for consistency
        if isinstance(new_item.get('thumbnail'), dict):
            new_item['thumbnail'] = new_item['thumbnail'].get('url', '')

    # Pre-warm the audio cache in the background
    _ensure_audio_ready_for_play(video_id, wait=False)

    with _np_lock:
        queue = list(_now_playing.get('queue') or [])
        current_idx = _now_playing.get('queue_index', -1)

        if position == 'next':
            # Insert right after the currently playing track
            insert_at = (current_idx + 1) if current_idx >= 0 else 0
            queue.insert(insert_at, new_item)
            # If current_idx was valid and we inserted before it would shift,
            # the current_idx stays the same (we inserted after it).
            queue_pos = insert_at
        else:
            # Append to the end
            queue.append(new_item)
            queue_pos = len(queue) - 1

        _now_playing['queue'] = queue
        _now_playing['updated_at'] = time.time()

    _notify_sse()
    return jsonify({
        'ok': True,
        'queue_position': queue_pos,
        'title': new_item.get('title', ''),
    })


@app.route("/alexa/queue_remove/", methods=["POST"])
def alexa_queue_remove():
    """Remove a song from the queue by its index.

    Body params:
      index     – 0-based index of the item to remove (required)
      video_id  – id of the song the client meant to remove (optional). The
                  client resolves the index from its last snapshot; if the
                  queue changed since (another removal, a reorder), that index
                  may now point at a different song. When provided, verify the
                  slot still holds this song and re-resolve by id otherwise.
    """
    body = request.get_json(silent=True) or {}
    try:
        idx = int(body.get("index", -1))
    except (TypeError, ValueError):
        return error_response('invalid "index"', 400)
    video_id = body.get("video_id") or ''

    with _np_lock:
        queue = list(_now_playing.get('queue') or [])
        current_idx = _now_playing.get('queue_index', -1)

        if idx < 0 or idx >= len(queue):
            return error_response('index out of range', 400)

        if video_id and queue[idx].get('video_id') != video_id:
            idx = next((i for i, q in enumerate(queue)
                        if q.get('video_id') == video_id), -1)
            if idx == -1:
                return error_response('That song is no longer in the queue', 409)

        # Don't allow removing the currently playing track
        if idx == current_idx:
            return error_response('Cannot remove the currently playing track', 400)

        removed = queue.pop(idx)

        # Adjust current_idx if we removed before it
        if current_idx >= 0 and idx < current_idx:
            current_idx -= 1

        _now_playing['queue'] = queue
        _now_playing['queue_index'] = current_idx
        _now_playing['updated_at'] = time.time()

    _notify_sse()
    return jsonify({
        'ok': True,
        'removed_title': removed.get('title', ''),
    })


@app.route("/alexa/queue_reorder/", methods=["POST"])
def alexa_queue_reorder():
    """Move a queue item from one position to another.

    Body params:
      from_index  – current 0-based index of the item
      to_index    – target 0-based index
    """
    body = request.get_json(silent=True) or {}
    try:
        from_idx = int(body.get("from_index", -1))
        to_idx = int(body.get("to_index", -1))
    except (TypeError, ValueError):
        return error_response('invalid indices', 400)

    with _np_lock:
        queue = list(_now_playing.get('queue') or [])
        current_idx = _now_playing.get('queue_index', -1)

        if from_idx < 0 or from_idx >= len(queue):
            return error_response('from_index out of range', 400)
        if to_idx < 0 or to_idx >= len(queue):
            return error_response('to_index out of range', 400)
        if from_idx == to_idx:
            return jsonify({'ok': True})

        item = queue.pop(from_idx)
        queue.insert(to_idx, item)

        # Track how the current playing index moved
        if current_idx >= 0:
            if current_idx == from_idx:
                # The playing track was the one moved
                current_idx = to_idx
            else:
                # Something else moved around the playing track
                if from_idx < current_idx <= to_idx:
                    current_idx -= 1
                elif to_idx <= current_idx < from_idx:
                    current_idx += 1

        _now_playing['queue'] = queue
        _now_playing['queue_index'] = current_idx
        _now_playing['updated_at'] = time.time()

    _notify_sse()
    return jsonify({'ok': True})


@app.route("/armed_play/", methods=["GET"])
def armed_play():
    """Called by the Alexa skill after the user triggers an app-selection play.
    Returns the video id the web remote armed for this device, then clears it.
    API-key protected (the skill sends ?key=); not a session endpoint.

    The skill calls this without a serial (it can't map its Alexa deviceId to
    the AlexaPy serial), so we return the most-recently-armed play."""
    serial = request.args.get("serial") or None
    armed = _consume_armed_play(serial)
    if not armed:
        return jsonify({'video_id': None})
    video_id, offset_ms = armed
    return jsonify({'video_id': video_id, 'offset_ms': offset_ms})


@app.route("/alexa/search/", methods=["GET"])
async def alexa_search():
    """Full search results for the web remote's results page (thumbnail +
    title + artist per row). Catalog songs first, then videos (mashups and
    covers often exist only as videos), deduped by video id."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return error_response('missing required parameter "q"', 400)

    def _collect_results(raw):
        """Extract deduplicated result dicts from ytmusic raw responses."""
        results, seen = [], set()
        for tracks in raw:
            if isinstance(tracks, BaseException) or not tracks:
                continue
            for track in tracks:
                video_id = track.get('videoId')
                if not _valid_video_id(video_id) or video_id in seen:
                    continue
                seen.add(video_id)
                thumbs = track.get('thumbnails') or []
                results.append({
                    'title': track.get('title') or '',
                    'artist': " and ".join(a.get('name') or '' for a in track.get('artists') or []),
                    'video_id': video_id,
                    'thumbnail': thumbs[-1].get('url', '') if thumbs else '',
                    'duration_ms': Supporting.duration_ms(track),
                })
                if len(results) >= 50:
                    break
            if len(results) >= 50:
                break
        return results

    ytmusic = YTMusic()

    # First try with spelling correction (ignore_spelling=False) so typos
    # like "dhdadak" get auto-corrected to "dhadak" by YouTube Music.
    raw = await asyncio.gather(
        *[asyncio.to_thread(ytmusic.search, query=query, filter=f,
                            ignore_spelling=False, limit=30)
          for f in ('songs', 'videos')],
        return_exceptions=True)
    results = _collect_results(raw)

    # If spelling-corrected search found nothing, retry with exact spelling
    # in case the user intentionally typed an unusual query.
    if not results:
        raw = await asyncio.gather(
            *[asyncio.to_thread(ytmusic.search, query=query, filter=f,
                                ignore_spelling=True, limit=30)
              for f in ('songs', 'videos')],
            return_exceptions=True)
        results = _collect_results(raw)

    if not results:
        return error_response('no results found', 404)
    return jsonify({'results': results})



@app.route("/alexa/clear/", methods=["POST"])
def alexa_clear():
    """Web remote 'clear all': stop playback on the device and wipe the
    server-side now-playing state and queue. The local state is cleared even
    if the stop command fails (e.g. device offline), so the UI always resets;
    any stop error is reported alongside ok."""
    body = request.get_json(silent=True) or {}
    serial = body.get("serial")
    stop_error = None
    if serial:
        try:
            stop_error = alexa_remote.remote.command(serial, 'stop')
        except alexa_remote.AlexaUnreachable as e:
            stop_error = str(e)
    with _np_lock:
        _now_playing.update({
            'playing': False, 'title': '', 'artist': '', 'thumbnail': '',
            'video_id': '', 'queue': [], 'queue_index': -1,
            'duration_ms': 0, 'position_ms': 0, 'started_at': 0.0,
            'playback_confirmed': False, 'playback_error': None,
            'updated_at': time.time(),
        })
    _notify_sse()
    return jsonify({'ok': True, 'stop_error': stop_error})


@app.route("/alexa/suggest/", methods=["GET"])
async def alexa_suggest():
    """Return YouTube Music search suggestions for the remote's search bar.
    Client-side JS can't call YT Music directly (CORS / no public key), so we
    proxy get_search_suggestions() here."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({'suggestions': []})
    try:
        ytmusic = YTMusic()
        raw = await asyncio.to_thread(ytmusic.get_search_suggestions, query)
    except Exception as e:
        print(f"[alexa/suggest] failed: {e}")
        return jsonify({'suggestions': []})
    # get_search_suggestions returns a list of strings (or dicts with 'text'
    # when detailed=True). Normalise to plain strings, keep the top few.
    suggestions = []
    for item in raw or []:
        if isinstance(item, str):
            suggestions.append(item)
        elif isinstance(item, dict) and item.get("text"):
            suggestions.append(item["text"])
        if len(suggestions) >= 8:
            break
    return jsonify({'suggestions': suggestions})


@app.route("/", methods=["GET"])
def root():
    # Serve the remote UI at the bare domain so the address bar shows just
    # the host, not /remote/. Unauthenticated visitors go to the login page.
    if not _remote_login_enabled():
        # Key-in-URL scheme: keep the old path, a redirect would drop ?key=.
        return redirect('/remote/')
    if _logged_in():
        return render_template("remote.html")
    return redirect('/login/')


@app.route("/setup/", methods=["GET", "POST"])
def index():
    hex_value = ""
    if request.method == "POST":
        apiurl_input = request.form.get("apiurl_input")
        playlist_input = request.form.get("playlist_input")
        if apiurl_input: hex_value = Supporting.encode_to_hex(apiurl_input)
        elif playlist_input: hex_value = Supporting.playlist_url_to_encoded_id(playlist_input) or 'Could not find a playlist id in that URL.'
        else: hex_value = 'Please fill the form to get encoded output.'
    return render_template("index.html", hex_value=hex_value)


@app.route("/privacy_policy/", methods=["GET"])
def privacy_policy():
    return render_template("privacy_policy.html")


@app.route("/terms_of_use/", methods=["GET"])
def terms_of_use():
    return render_template("terms_of_use.html")


@app.route("/favicon.ico")
def favicon():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="6" fill="#0a0a0a"/>
<rect x="4" y="14" width="4" height="14" rx="1" fill="#e8590c"/>
<rect x="10" y="6" width="4" height="22" rx="1" fill="#e8590c"/>
<rect x="16" y="10" width="4" height="18" rx="1" fill="#e8590c"/>
<rect x="22" y="4" width="4" height="24" rx="1" fill="#e8590c"/>
</svg>'''
    return Response(svg, mimetype='image/svg+xml')


# ---------- PWA (installable web remote) ----------
# Served inline (no template) so the remote can be "Add to Home Screen" /
# installed as a standalone app. The manifest + service worker are listed in
# _PUBLIC_PATHS because the browser fetches them without the ?key= param.

_MANIFEST = {
    "name": "Music Box Remote",
    "short_name": "Music Box",
    "description": "Control YouTube Music playback on your Alexa devices.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0a0a0a",
    "theme_color": "#0a0a0a",
    "icons": [
        {"src": "/static/icons/icon-192-any.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512-any.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


@app.route("/manifest.webmanifest")
def manifest():
    return Response(json.dumps(_MANIFEST), mimetype="application/manifest+json")


# Minimal service worker. A service worker is required for the browser to offer
# installation. This one does no offline caching: the remote needs a live server
# to reach Alexa anyway, and skipping a cache avoids serving stale HTML/JS after
# an update. We deliberately register NO 'fetch' handler -- an empty one is a
# no-op that forces the browser to wake the worker on every navigation (Chrome
# warns about this); without it, requests take the default network path directly.
_SERVICE_WORKER = """\
self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
"""


@app.route("/service-worker.js")
def service_worker():
    # Served from the origin root so its scope covers the whole site.
    return Response(_SERVICE_WORKER, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# Main entry point
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
