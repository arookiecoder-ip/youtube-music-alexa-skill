import asyncio, difflib, glob, hashlib, hmac, itertools, json, os, random, secrets, sys, threading, time, re, subprocess, logging, copy, uuid
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlparse
from ytmusicapi import YTMusic
import alexa_remote
from flask import Flask, request, render_template, jsonify, send_file, session, redirect, Response
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# YTDLP_COOKIES normally points at a read-only bind mount (protecting the
# host's real cookie file from corruption) -- but yt-dlp always writes back
# refreshed session cookies to whatever file --cookies points at, so every
# invocation logged "OSError: Read-only file system" on exit even though
# extraction itself succeeded. Copy to a writable path once at startup and
# point every yt-dlp call there instead, so the host file stays untouched.
_cookies_src = os.environ.get("YTDLP_COOKIES")
if _cookies_src and os.path.isfile(_cookies_src):
    import shutil
    _cookies_writable = "/tmp/ytdlp_cookies.txt"
    shutil.copyfile(_cookies_src, _cookies_writable)
    os.environ["YTDLP_COOKIES"] = _cookies_writable

# Signs the session cookie used by the web remote login. Set SECRET_KEY in the
# environment so sessions survive restarts; otherwise a random one is generated
# (logins reset on every restart, which is fine for a personal tool).
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_INSECURE") != "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    # Static assets are safe to cache long-term: remote.html references them
    # with a ?v=<fingerprint> that changes on every deploy (see
    # _STATIC_VERSION), so browsers re-fetch exactly when the files change
    # instead of revalidating on every page load.
    SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),
)

# Fingerprint of the front-end assets. Changing any static file changes this,
# which (a) busts browser HTTP caches via the ?v= asset URLs and (b) byte-
# changes the service worker source, making installed PWAs re-install it and
# precache the fresh copies.
def _compute_static_version():
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    h = hashlib.sha1()
    for root_dir, _, files in os.walk(static_dir):
        for name in sorted(files):
            path = os.path.join(root_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            rel = os.path.relpath(path, static_dir).replace(os.sep, '/')
            h.update(f'{rel}:{st.st_mtime_ns}:{st.st_size};'.encode())
    return h.hexdigest()[:12]

_STATIC_VERSION = _compute_static_version()

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
    logger.warning("REMOTE_USER is set but REMOTE_PASSWORD is empty/unset; "
                   "web-remote login is DISABLED and /remote/ falls back to key-only access.")

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
# If not set in env, read from api_key.txt or generate a persistent one
API_KEY = os.environ.get("API_KEY")
if not API_KEY:
    api_key_path = os.path.join(os.path.dirname(os.environ.get("DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db"))), 'api_key.txt')
    try:
        if os.path.exists(api_key_path):
            with open(api_key_path, 'r') as f:
                API_KEY = f.read().strip()
        else:
            API_KEY = secrets.token_hex(16)
            with open(api_key_path, 'w') as f:
                f.write(API_KEY)
    except Exception:
        API_KEY = secrets.token_hex(16)

if not os.environ.get("API_KEY"):
    logger.warning("NO API KEY SET IN ENV. USING AUTO-GENERATED KEY: %s", API_KEY)

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
# ---- Database (SQLite) ----
import sqlite3

DB_FILE = os.environ.get("DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db"))
HISTORY_MAX = 100

_history_lock = threading.Lock() # Dummy lock to avoid breaking other routes
_playlists_lock = threading.Lock() # Dummy lock to avoid breaking other routes

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS history (
                video_id TEXT PRIMARY KEY,
                title TEXT,
                artist TEXT,
                thumbnail_url TEXT,
                played_at REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT,
                source_url TEXT,
                updated_at REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS web_sessions (
                sid TEXT PRIMARY KEY,
                created_at REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS kv (
                k TEXT PRIMARY KEY,
                v TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS jam_tokens (
                token TEXT PRIMARY KEY,
                created_at REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                uuid TEXT PRIMARY KEY,
                playlist_id TEXT,
                video_id TEXT,
                title TEXT,
                artist TEXT,
                thumbnail_url TEXT,
                duration_ms INTEGER,
                added_at REAL,
                FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            )
        ''')
        
        # Ensure default 'liked' playlist exists
        conn.execute("INSERT OR IGNORE INTO playlists (id, name, updated_at) VALUES ('liked', 'Liked Songs', ?)", (time.time(),))
        
        # Migrate history table from video_id PK to auto-increment id PK
        try:
            cur = conn.execute("PRAGMA table_info(history)")
            cols = [row[1] for row in cur.fetchall()]
            if 'id' in cols:
                pass
            else:
                conn.execute('''CREATE TABLE IF NOT EXISTS history_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    thumbnail_url TEXT,
                    played_at REAL
                )''')
                conn.execute('''INSERT INTO history_new (video_id, title, artist, thumbnail_url, played_at)
                    SELECT video_id, title, artist, thumbnail_url, played_at FROM history''')
                conn.execute('DROP TABLE history')
                conn.execute('ALTER TABLE history_new RENAME TO history')
        except Exception:
            logger.exception("history schema migration failed")

        # Migrations for existing databases
        try:
            conn.execute("ALTER TABLE playlists ADD COLUMN source_url TEXT")
        except sqlite3.OperationalError:
            pass # Column already exists
            
        try:
            conn.execute("ALTER TABLE playlist_tracks ADD COLUMN duration_ms INTEGER")
        except sqlite3.OperationalError:
            pass
            
        try:
            conn.execute("ALTER TABLE playlist_tracks ADD COLUMN added_at REAL")
        except sqlite3.OperationalError:
            pass
        
        # (Data migration script removed per user request)


_db_initialized = False


def _ensure_db():
    """Lazy DB init on first request instead of at import time."""
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


def _load_history():
    with get_db() as conn:
        rows = conn.execute('SELECT video_id, title, artist, thumbnail_url, MAX(played_at) AS played_at, COUNT(*) AS play_count FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY played_at DESC) - ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY played_at DESC) AS grp FROM history) AS t GROUP BY video_id, grp ORDER BY MAX(played_at) DESC LIMIT ?', (HISTORY_MAX,)).fetchall()
        return [dict(r) for r in rows]

def _record_listen(video_id, title, artist, thumbnail_url):
    if not video_id:
        return
    with get_db() as conn:
        conn.execute('''
            INSERT INTO history (video_id, title, artist, thumbnail_url, played_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (video_id, title or '', artist or '', thumbnail_url or '', time.time()))
        
        conn.execute('''
            DELETE FROM history WHERE id NOT IN (
                SELECT id FROM history ORDER BY played_at DESC LIMIT ?
            )
        ''', (HISTORY_MAX,))
        conn.commit()

def _backfill_history_metadata(video_id, title, artist, thumbnail_url):
    if not video_id or not title:
        return
    with get_db() as conn:
        row = conn.execute('SELECT title FROM history WHERE video_id = ?', (video_id,)).fetchone()
        if row and not row['title']:
            if thumbnail_url:
                conn.execute('UPDATE history SET title = ?, artist = ?, thumbnail_url = ? WHERE video_id = ?', 
                             (title, artist or '', thumbnail_url, video_id))
            else:
                conn.execute('UPDATE history SET title = ?, artist = ? WHERE video_id = ?', 
                             (title, artist or '', video_id))
            conn.commit()

def _load_playlists():
    with get_db() as conn:
        data = {"playlists": {}, "liked_songs": []}
        pl_rows = conn.execute('SELECT * FROM playlists').fetchall()
        for r in pl_rows:
            pl_id = r['id']
            data["playlists"][pl_id] = {
                "id": pl_id,
                "name": r['name'],
                "source_url": r['source_url'],
                "updated_at": r['updated_at'],
                "tracks": []
            }
        
        # Newest additions first, matching YT Music's playlist view (a fresh
        # save shows at the top). rowid ASC breaks the tie inside a sync batch
        # (all rows share one added_at) so imported playlists keep their
        # source order instead of reversing.
        tr_rows = conn.execute('SELECT * FROM playlist_tracks ORDER BY added_at DESC, rowid ASC').fetchall()
        for t in tr_rows:
            pl_id = t['playlist_id']
            if pl_id in data["playlists"]:
                track = dict(t)
                track['thumbnail'] = track.pop('thumbnail_url')
                data["playlists"][pl_id]["tracks"].append(track)
        # Liked Songs keeps its established oldest-first order: its client-side
        # sort and the un-like/re-like timestamp restore both assume it.
        liked = data["playlists"].get("liked")
        if liked:
            liked["tracks"].reverse()
            data["liked_songs"] = [t['video_id'] for t in liked["tracks"]]
        return data




# Endpoints that never need auth (public policy pages + the login flow itself,
# plus the PWA manifest / service worker / icons, which the browser fetches with
# no ?key= and which contain no private data).
_PUBLIC_PATHS = ('/', '/privacy_policy', '/terms_of_use', '/login', '/logout', '/favicon.ico',
                 '/manifest.webmanifest', '/service-worker.js')
# Public path prefixes (startswith match) — static assets (CSS, JS, icons),
# plus jam join links (/j/<token> and legacy /jam/<token> — the token itself is
# the credential).
_PUBLIC_PREFIXES = ('/static/', '/j/', '/jam/')

# Endpoints reachable with a logged-in web-remote session cookie (so the long
# API key stays out of the browser URL). Everything here plus the remote page.
_SESSION_PATHS = ('/remote', '/alexa/status', '/alexa/init', '/alexa/devices', '/alexa/command',
                  '/alexa/play', '/alexa/suggest', '/alexa/proxy_login',
                  '/alexa/proxy_check', '/alexa/now_playing', '/alexa/state_event',
                  '/alexa/seek', '/alexa/volume', '/alexa/play_queue',
                  '/alexa/shuffle_queue', '/alexa/search', '/alexa/clear',
                  '/alexa/queue_add', '/alexa/queue_remove',
                  '/alexa/queue_reorder', '/history', '/recommendations',
                  '/alexa/jam/start', '/alexa/jam/stop', '/alexa/jam/status',
                  '/alexa/jam/qr')
_SESSION_PREFIXES = ('/alexa/now_playing/', '/history/', '/api/playlists/', '/recommendations/')

# API/device endpoints: the Alexa skill and web-remote JS hit these directly
# and need a machine-readable JSON error, never an HTML redirect, on failure.
_API_PREFIXES = ('/alexa/', '/proxy/', '/get_stream/', '/get_radio/',
                  '/find_stream_list/', '/armed_play/', '/stream_video/',
                  '/stream_playlist/', '/get_playlist_info/', '/queue_tracks/',
                  '/play_genre/',
                  '/history', '/recommendations', '/api/playlists/')


def _remote_login_enabled():
    return bool(REMOTE_USER and REMOTE_PASSWORD)


# Server-side registry of live web-remote sessions. The session cookie is a
# signed client-side token, so on its own any copy of it stays valid until it
# expires — even after "sign out". Each login mints a random sid recorded in
# the web_sessions table; logout deletes the row, which invalidates every copy
# of that cookie immediately. SQLite persistence means revocations (and live
# sessions) survive restarts when SECRET_KEY is set.
_SID_MAX_AGE = timedelta(days=30).total_seconds()  # mirrors PERMANENT_SESSION_LIFETIME
_valid_sids = None  # in-memory mirror of web_sessions; None = not loaded yet
_sids_lock = threading.Lock()


def _sid_cache():
    global _valid_sids
    with _sids_lock:
        if _valid_sids is None:
            with get_db() as conn:
                conn.execute('DELETE FROM web_sessions WHERE created_at < ?',
                             (time.time() - _SID_MAX_AGE,))
                rows = conn.execute('SELECT sid FROM web_sessions').fetchall()
                conn.commit()
            _valid_sids = {r['sid'] for r in rows}
        return _valid_sids


def _session_open():
    """Record a new login and return its sid (store it in the cookie)."""
    sid = secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.execute('INSERT INTO web_sessions (sid, created_at) VALUES (?, ?)',
                     (sid, time.time()))
        conn.commit()
    _sid_cache().add(sid)
    return sid


def _session_close(sid):
    with get_db() as conn:
        conn.execute('DELETE FROM web_sessions WHERE sid = ?', (sid,))
        conn.commit()
    _sid_cache().discard(sid)


def _sessions_close_all():
    """Sign out everywhere: invalidate every session cookie ever issued.
    Also permanently closes the legacy-cookie adoption path in _logged_in()
    — after this, a pre-sid cookie is revoked, not grandfathered."""
    global _legacy_adoption_closed
    with get_db() as conn:
        conn.execute('DELETE FROM web_sessions')
        conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('sessions_revoked_all', '1')")
        conn.commit()
    _sid_cache().clear()
    _legacy_adoption_closed = True


# Whether "sign out everywhere" has ever been used (None = not checked yet).
# Once it has, cookies from before the sid upgrade must be treated as revoked
# rather than adopted, or a stale legacy cookie would outlive the revocation.
_legacy_adoption_closed = None


def _legacy_adoption_allowed():
    global _legacy_adoption_closed
    if _legacy_adoption_closed is None:
        with get_db() as conn:
            row = conn.execute("SELECT v FROM kv WHERE k = 'sessions_revoked_all'").fetchone()
        _legacy_adoption_closed = row is not None
    return not _legacy_adoption_closed


def _logged_in():
    if not (_remote_login_enabled() and session.get('remote_user') == REMOTE_USER):
        return False
    sid = session.get('sid')
    if sid in _sid_cache():
        return True
    if sid is None and _legacy_adoption_allowed():
        # Cookie issued before server-side sids existed. It carries the same
        # signed remote_user that made it fully valid pre-upgrade, so adopt it:
        # mint a sid now, making it revocable like every other session instead
        # of forcing a one-time re-login on every device after deploy. Only
        # until the first "sign out everywhere" — that must kill these too.
        session['sid'] = _session_open()
        return True
    return False  # sid revoked (signed out here or everywhere)


# ---- Jam (guest) sessions ----
# Spotify-style "Jam": the owner mints a share link (/jam/<token>) from the
# remote UI. Anyone opening it gets a guest session cookie tied to that token.
# Guests can search, play, and control playback / the live queue, but every
# private account surface (playlists, likes, listening history, recommendations,
# Amazon login) is refused server-side. Ending the jam deletes the token row,
# which invalidates every guest cookie on their very next request (and closes
# their SSE streams within one heartbeat).
JAM_MAX_AGE = float(os.environ.get("JAM_TTL_HOURS", "24")) * 3600  # auto-expiry safety net
_valid_jams = None  # in-memory mirror of jam_tokens ({token: created_at}); None = not loaded
_jams_lock = threading.Lock()


def _jam_cache():
    global _valid_jams
    with _jams_lock:
        if _valid_jams is None:
            with get_db() as conn:
                conn.execute('DELETE FROM jam_tokens WHERE created_at < ?',
                             (time.time() - JAM_MAX_AGE,))
                rows = conn.execute('SELECT token, created_at FROM jam_tokens').fetchall()
                conn.commit()
            _valid_jams = {r['token']: r['created_at'] for r in rows}
        return _valid_jams


def _jam_token_valid(token):
    if not token:
        return False
    created = _jam_cache().get(token)
    if created is None:
        return False
    if time.time() - created > JAM_MAX_AGE:
        _jam_close_all()  # expired: purge so guests are cut off, not just this check
        return False
    return True


def _jam_open():
    """Start a jam (revoking any previous one) and return its share token."""
    token = secrets.token_urlsafe(9)
    now = time.time()
    with get_db() as conn:
        conn.execute('DELETE FROM jam_tokens')
        conn.execute('INSERT INTO jam_tokens (token, created_at) VALUES (?, ?)', (token, now))
        conn.commit()
    cache = _jam_cache()
    cache.clear()
    cache[token] = now
    return token


def _jam_close_all():
    with get_db() as conn:
        conn.execute('DELETE FROM jam_tokens')
        conn.commit()
    _jam_cache().clear()


def _jam_active_token():
    # Copy before iterating: _jam_open()/_jam_close_all() on another thread
    # mutate the dict, which would raise RuntimeError mid-iteration.
    for token, created in list(_jam_cache().items()):
        if time.time() - created <= JAM_MAX_AGE:
            return token
    return None


def _jam_guest():
    """True when this request carries a guest cookie for a still-live jam."""
    return _jam_token_valid(session.get('jam'))


def _clear_stale_jam_cookie():
    if session.get('jam') and not _jam_token_valid(session.get('jam')):
        session.pop('jam', None)
        return True
    return False


def _valid_key_supplied():
    """True when the request itself proves owner access via the API key. Used
    to avoid rendering the guest UI for an owner (key-in-URL scheme) who also
    happens to carry a jam cookie from opening their own share link."""
    if not API_KEY:
        return False
    supplied = request.args.get('key') or request.headers.get('X-Api-Key')
    return hmac.compare_digest(supplied or "", API_KEY)


def _jam_url(token):
    base = PUBLIC_BASE_URL or request.url_root.rstrip('/')
    return f"{base}/j/{token}"


# What a jam guest may reach. Playback, search, queue manipulation and the
# remote page itself are fine; recommendations are allowed as a playable mix,
# while private account data is not.
# Deliberately absent: /alexa/proxy_login + /alexa/proxy_check (Amazon auth),
# /alexa/clear (wipes the owner's queue), /alexa/jam/* (owner-only controls),
# /api/playlists/*, /history/*.
_JAM_PATHS = ('/remote', '/alexa/status', '/alexa/init', '/alexa/devices',
              '/alexa/command', '/alexa/play', '/alexa/suggest',
              '/alexa/now_playing', '/alexa/seek', '/alexa/volume',
              '/alexa/play_queue', '/alexa/shuffle_queue', '/alexa/search',
              '/alexa/queue_add', '/alexa/queue_remove', '/alexa/queue_reorder',
              '/recommendations')


def _jam_request_allowed(path):
    """Allow-list for guest sessions; `path` is the trailing-slash-normalized
    request path."""
    return path in _JAM_PATHS or request.path.startswith('/alexa/now_playing/')


@app.before_request
def require_api_key():
    _ensure_db()
    path = request.path.rstrip('/') or '/'
    if path in _PUBLIC_PATHS or any(request.path.startswith(p) for p in _PUBLIC_PREFIXES):
        return None
    if request.method in ('GET', 'HEAD') and path == '/remote':
        if not _logged_in():
            _clear_stale_jam_cookie()
    # A valid session cookie authorizes the remote page and its /alexa/* calls.
    if _logged_in() and (path in _SESSION_PATHS or any(request.path.startswith(p) for p in _SESSION_PREFIXES)):
        # Mutating requests must be JSON. A cross-site HTML form or plain
        # <script> fetch cannot set Content-Type: application/json without
        # triggering a CORS preflight that our lack of CORS headers would
        # fail, so this blocks classic CSRF against the cookie-authenticated
        # command endpoints without needing a token.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not (request.content_type or '').startswith('application/json'):
                return jsonify({'error': 'unauthorized'}), 401
        return None
    # Jam guests: a valid guest cookie authorizes only the play/search/read
    # subset in _jam_request_allowed. Same JSON-body CSRF rule as above.
    # Checked after _logged_in() so the owner opening their own jam link
    # keeps full access.
    if _jam_guest() and _jam_request_allowed(path):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not (request.content_type or '').startswith('application/json'):
                return jsonify({'error': 'unauthorized'}), 401
        return None
    if not API_KEY:
        return None
    supplied = request.args.get('key') or request.headers.get('X-Api-Key')
    if not hmac.compare_digest(supplied or "", API_KEY):
        # Device/skill endpoints must get a JSON error, never an HTML redirect.
        if any(request.path.startswith(p) for p in _API_PREFIXES):
            return jsonify({'error': 'unauthorized'}), 401
        # Anything else is a browser hitting the site directly (root, /remote,
        # /setup, a typo, whatever) with no valid key/session: send them to the
        # login screen instead of a bare JSON 401.
        return redirect('/login/')
    return None

_last_prune = [0.0]
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

# Version counter for the local Liked Songs playlist, included in SSE
# snapshots. Open remotes re-fetch /api/playlists/ when it changes so a like
# made elsewhere (voice command, another device) updates their heart icons
# live instead of waiting for the next page load.
_liked_version = 0


def _bump_liked_version():
    global _liked_version
    with _np_lock:
        _liked_version += 1
    _notify_sse()

# Seek/resume-with-offset dispatch arms position_ms just before triggering
# playback (see alexa_seek and the 'play' action in alexa_command). The skill
# then reports a PlaybackStarted for the same track at a low offset, which
# looks identical in shape to a genuine replay-from-the-top -- this window
# lets alexa_state_event tell them apart and not miscount a reposition as a
# fresh listen.
_last_reposition_at = 0.0
_REPOSITION_SUPPRESS_WINDOW = 5

# ---------- SSE (Server-Sent Events) push ----------
import queue as _queue_mod
# q -> {'serial': str|None, 'qv': int|None (queue_version last sent with a
# queue payload)}. qv lets _notify_sse omit the (potentially 1000-item) queue
# from pushes when it hasn't changed since that subscriber last received it.
_sse_subscribers = {}
_sse_lock = threading.Lock()

# Queue change detection for SSE diffing. Every code path that changes the
# queue installs a *new* list object into _now_playing['queue'] (they all build
# via list()/+/literal), so object identity is a reliable change signal — this
# also catches the direct _now_playing['queue'] = ... writes that bypass
# _update_now_playing. Holding a reference to the last-seen list prevents its
# id from being reused by a successor allocation.
_queue_seen_obj = None
_queue_version = 0

def _queue_version_locked():
    """Current queue version, bumping it if the queue list was replaced.
    Caller must hold _np_lock."""
    global _queue_seen_obj, _queue_version
    q = _now_playing.get('queue')
    if q is not _queue_seen_obj:
        _queue_seen_obj = q
        _queue_version += 1
    return _queue_version

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
        'queue_version': _queue_version_locked(),
        'duration_ms': s.get('duration_ms', 0),
        'position_ms': _computed_position_ms(),
        'started_at': now,
        'playback_confirmed': bool(s.get('playback_confirmed')),
        'volume': volume,
        'playback_error': playback_error,
        'liked_version': _liked_version,
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
    full = {}   # serial -> (json with queue, queue_version)
    slim = {}   # serial -> json without the queue field
    dead = set()
    for q, sub in subscribers:
        serial = sub.get('serial')
        if serial not in full:
            with _np_lock:
                snap = _np_snapshot(serial)
            full[serial] = (json.dumps(snap), snap['queue_version'])
            # Subscribers already at this queue_version get the payload without
            # the queue: a long queue dominates the message size, and most
            # pushes (progress heartbeats, volume) don't change it. The client
            # keeps its last queue when the field is absent.
            slim[serial] = json.dumps({k: v for k, v in snap.items() if k != 'queue'})
        data, qv = full[serial]
        if sub.get('qv') == qv:
            data = slim[serial]
        try:
            q.put_nowait(data)
            sub['qv'] = qv
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
            logger.exception("")
        with _sse_lock:
            serials = {sub.get('serial') for sub in _sse_subscribers.values() if sub.get('serial')}
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
    logger.exception("")
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
        logger.exception("")
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
        logger.exception("")
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
    def query_variants(text):
        return [text]

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

    @staticmethod
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
                logger.exception("")
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
            logger.exception("")
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

    @staticmethod
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

    @staticmethod
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
    
    @staticmethod
    async def get_playlist_tracks(playlist_id: str):
        """Normalized track list for a playlist id, or None if unreadable/empty.
        Unavailable tracks (no videoId) are dropped."""
        ytmusic = YTMusic()
        try:
            # limit=None: fetch every track. The default limit=100 would make
            # sync's now-playing deletion pass (see api_sync_playlist) wrongly
            # treat tracks beyond the cutoff as removed from the source.
            search_results = await asyncio.to_thread(ytmusic.get_playlist, playlistId=playlist_id, limit=None)
        except Exception:
            # ytmusicapi raises on unknown/private playlist ids; treat as not found
            logger.exception("")
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
        logger.info("get_playlist_tracks: playlist %s returned %d tracks", playlist_id, len(playlist))
        return playlist or None

    @staticmethod
    async def stream_playlist(playlist_id: str):
        playlist = await Supporting.get_playlist_tracks(playlist_id)
        if not playlist:
            return None
        stream = await Supporting.get_stream(playlist[0]['video_id'])
        if not stream:
            return None
        return {'song_info': {'metadata': playlist[0], 'stream': stream}, 'playlist': playlist}

    def get_ytdlp_clients():
        if os.environ.get("YTDLP_COOKIES"):
            return ["default", "tv", "web"]
        return ["default", "ios", "tv", "web"]

    def resolve_direct_url(video_id: str):
        if not _valid_video_id(video_id):
            return None
        
        clients = Supporting.get_ytdlp_clients()
        last_error = ""
        for client in clients:
            extractor_args = []
            if client != "default":
                extractor_args.append(f"youtube:player_client={client}")
                if po_token := os.environ.get("YTDLP_PO_TOKEN"):
                    extractor_args.append(f"youtube:po_token=mweb.gvs+{po_token}")
            command = ["yt-dlp", "--get-url", "--no-playlist", "--quiet", "-f", "ba",
                       "--remote-components", "ejs:github"]
            if extractor_args:
                command.extend(["--extractor-args", ",".join(extractor_args)])
            cookies = os.environ.get("YTDLP_COOKIES")
            if cookies:
                command += ["--cookies", cookies]
            command += ["--", video_id]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
            last_error = result.stderr.strip()
        
        logger.error("yt-dlp get-url failed: %s", last_error)
        return None

    def probe_metadata(video_id: str):
        """Best-effort title/artist/thumbnail/duration via yt-dlp, for videos
        ytmusicapi doesn't know (arbitrary YouTube links aren't in the YT Music
        catalog, so get_song returns nothing). Returns a metadata dict or None.
        "--" ends option parsing so an id can never be read as a flag."""
        if not _valid_video_id(video_id):
            return None
        # Tab-separated so a title containing the delimiter can't split fields.
        fmt = "%(title)s\t%(uploader)s\t%(thumbnail)s\t%(duration)s"
        clients = Supporting.get_ytdlp_clients()
        for client in clients:
            extractor_args = []
            if client != "default":
                extractor_args.append(f"youtube:player_client={client}")
                if po_token := os.environ.get("YTDLP_PO_TOKEN"):
                    extractor_args.append(f"youtube:po_token=mweb.gvs+{po_token}")
            command = ["yt-dlp", "--no-playlist", "--quiet", "--no-warnings",
                       "--remote-components", "ejs:github",
                       "--print", fmt]
            if extractor_args:
                command.extend(["--extractor-args", ",".join(extractor_args)])
            cookies = os.environ.get("YTDLP_COOKIES")
            if cookies:
                command += ["--cookies", cookies]
            command += ["--", video_id]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=25)
                if result.returncode == 0:
                    break
            except (subprocess.SubprocessError, OSError):
                pass
        else:
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
        extractor_args = []
        if client != "default":
            extractor_args.append(f"youtube:player_client={client}")
            if po_token := os.environ.get("YTDLP_PO_TOKEN"):
                extractor_args.append(f"youtube:po_token=mweb.gvs+{po_token}")
        
        command = ["yt-dlp", "--no-playlist", "--quiet",
                   "-f", "140/bestaudio[ext=m4a]/bestaudio",
                   "--remote-components", "ejs:github"]
        if extractor_args:
            command.extend(["--extractor-args", ",".join(extractor_args)])
        cookies = os.environ.get("YTDLP_COOKIES")
        if cookies:
            command += ["--cookies", cookies]
        command += ["-o", output, "--", video_id]
        return command

    def ensure_downloaded(video_id: str):
        if not _valid_video_id(video_id):
            return None
        now = time.time()
        if now - _last_prune[0] > 60:
            Supporting.prune_audio_cache()
            _last_prune[0] = now
        with _download_semaphore:
            with _locks_guard:
                lock = _download_locks.setdefault(video_id, threading.Lock())
            with lock:
                path = Supporting.cached_audio_path(video_id)
                if path:
                    return path
                output = os.path.join(AUDIO_CACHE_DIR, f"{video_id}.%(ext)s")
                clients = Supporting.get_ytdlp_clients()
                for client in clients:
                    result = subprocess.run(
                        Supporting.ytdlp_download_command(video_id, output, client=client),
                        capture_output=True, text=True)
                    if result.returncode == 0:
                        break
                    logger.error("yt-dlp download failed (%s client): %s", client, result.stderr.strip())
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


    @staticmethod
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

    @staticmethod
    def playlist_url_to_encoded_id(url):
        match = re.match(r"^[\w-]+", url.split('list=')[-1])
        if not match:
            return None
        return Supporting.encode_to_hex(match.group())
    
    @staticmethod
    def encode_to_hex(string):
        return ''.join([hex(ord(c))[2:].zfill(2) for c in string])

    async def get_playlist_info(playlist_id: str):
        ytmusic = YTMusic()
        try:
            playlist_raw = await asyncio.to_thread(ytmusic.get_playlist, playlist_id)
        except Exception:
            logger.exception("")
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
    logger.info('Completed request in %.2f seconds.', time.time() - start_time)
    if response is None:
        return error_response('playlist not found', 404)
    return jsonify(response)

def _parse_limit_arg():
    try:
        return max(0, int(request.args.get("limit", 0) or 0))
    except (TypeError, ValueError):
        return 0


async def _stream_playlist_payload(playlist_id, limit):
    """Resolve a playlist into the skill's SongInfoList payload, mirroring the
    full track list into the web remote's queue. Returns None when the playlist
    is unknown/empty. Shared by /stream_playlist/ and /play_genre/."""
    response = await Supporting.stream_playlist(playlist_id)
    if response is None:
        return None
    # Mirror the full playlist into the web remote's queue so a voice-started
    # playlist shows the real track list (and so /queue_tracks/ can serve the
    # skill continuation batches beyond its window below).
    try:
        queue = []
        for item in response['playlist']:
            t = item.get('thumbnail')
            thumb = t.get('url', '') if isinstance(t, dict) else (t if isinstance(t, str) else '')
            queue.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'thumbnail': thumb,
                'video_id': item.get('video_id', ''),
                'duration_ms': item.get('duration_ms', 0),
            })
        _update_now_playing(queue=queue, queue_index=0)
    except Exception:
        logger.exception("stream_playlist: queue mirror failed")
    # The skill persists its playlist copy to DynamoDB, where a 400KB item cap
    # makes a 1000-track list fatal — it asks for a window (limit) and pages
    # the rest through /queue_tracks/ as playback approaches the window's end.
    if limit:
        response = dict(response, playlist=response['playlist'][:limit])
    return response


@app.route("/stream_playlist/", methods=["GET"])
async def stream_playlist():
    start_time = time.time()
    playlist_id = request.args.get("id")
    if not playlist_id:
        return error_response('missing required parameter "id"', 400)
    limit = _parse_limit_arg()
    response = await _stream_playlist_payload(playlist_id, limit)
    logger.info('Completed request in %.2f seconds.', time.time() - start_time)
    if response is None:
        return error_response('playlist not found or empty', 404)
    return jsonify(response)


@app.route("/play_genre/", methods=["GET"])
async def play_genre():
    """Genre playback for the skill: search YT Music for a '<genre> music'
    playlist and stream the best match (same payload shape as
    /stream_playlist/). 404 when no usable playlist is found — the skill then
    falls back to a plain song search."""
    start_time = time.time()
    genre = (request.args.get("genre") or '').strip()
    if not genre:
        return error_response('missing required parameter "genre"', 400)
    limit = _parse_limit_arg()
    ytmusic = YTMusic()
    try:
        results = await asyncio.to_thread(
            ytmusic.search, query=f'{genre} music', filter='playlists', ignore_spelling=True)
    except Exception:
        logger.exception("play_genre: playlist search failed")
        results = None
    # Only try the first few hits, and stop starting new attempts once the
    # time budget is spent: each attempt costs a full playlist fetch, and
    # Alexa abandons the skill's request after ~8 seconds — a response that
    # arrives later is thrown away, so 404-ing early lets the skill's plain
    # song-search fallback still fit inside the window.
    _GENRE_TIME_BUDGET = 5.0
    for result in (results or [])[:3]:
        if time.time() - start_time > _GENRE_TIME_BUDGET:
            logger.warning('play_genre (%s): time budget exhausted, giving up', genre)
            break
        playlist_id = result.get('playlistId') or result.get('browseId') or ''
        if playlist_id.startswith('VL'):
            playlist_id = playlist_id[2:]
        if not playlist_id:
            continue
        response = await _stream_playlist_payload(playlist_id, limit)
        if response:
            logger.info('Completed play_genre (%s) in %.2f seconds.', genre, time.time() - start_time)
            return jsonify(response)
    return error_response('no playlist found for that genre', 404)


@app.route("/get_stream/", methods=["GET"])
async def get_stream():
    start_time = time.time()
    video_id = request.args.get("video_id")
    if not video_id:
        return error_response('missing required parameter "video_id"', 400)
    if not _valid_video_id(video_id):
        return error_response('invalid "video_id"', 400)
    response = await Supporting.get_stream(video_id)
    logger.info('Completed request in %.2f seconds.', time.time() - start_time)
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
    # Window the skill's copy around the requested track (DynamoDB 400KB item
    # cap — see /stream_playlist/). The skill pages the rest via /queue_tracks/.
    if len(queue) > _SKILL_QUEUE_WINDOW:
        idx = next((i for i, item in enumerate(queue)
                    if item.get('video_id') == video_id), 0)
        start = max(0, idx - _SKILL_QUEUE_BEHIND)
        queue = queue[start:start + _SKILL_QUEUE_WINDOW]
    logger.info('Completed stream_video in %.2f seconds.', time.time() - start_time)
    return jsonify({'song_info': {'metadata': metadata, 'stream': stream}, 'playlist': queue})


# How much of a long queue the Alexa skill gets per response. The skill keeps a
# sliding window in DynamoDB (400KB item cap) and fetches continuation batches
# from /queue_tracks/ as playback nears the end of what it holds.
_SKILL_QUEUE_WINDOW = 100
_SKILL_QUEUE_BEHIND = 25


@app.route("/queue_tracks/", methods=["GET"])
def queue_tracks():
    """Continuation batch for the skill's sliding playlist window: the tracks
    that follow `after` (a video_id) in the web remote's current queue. Returns
    an empty list when the video isn't in the queue any more (queue replaced by
    a newer play) — the skill then falls back to radio continuation."""
    after = request.args.get("after") or ''
    try:
        limit = min(200, max(1, int(request.args.get("limit", 75) or 75)))
    except (TypeError, ValueError):
        limit = 75
    with _np_lock:
        queue = list(_now_playing.get('queue') or [])
    idx = next((i for i, item in enumerate(queue)
                if item.get('video_id') == after), -1)
    tracks = []
    if idx >= 0:
        for item in queue[idx + 1:idx + 1 + limit]:
            tracks.append({
                'title': item.get('title', ''),
                'artist': item.get('artist', ''),
                'video_id': item.get('video_id', ''),
                'thumbnail': _thumbnail_metadata(item.get('thumbnail')),
                'duration_ms': item.get('duration_ms', 0),
            })
    return jsonify({'tracks': tracks})


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
    logger.info('Completed get_radio in %.2f seconds.', time.time() - start_time)
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
    logger.info('Completed request in %.2f seconds.', time.time() - start_time)
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
        queue = _get_now_playing().get('queue', [])
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
    if not _get_now_playing().get('duration_ms'):
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
        logger.exception("")


def _refresh_radio_queue(video_id, force=False):
    """Populate recommendations for the web remote after playback is real.

    Lambda also expands the queue, but that update can arrive late or not at all
    if Alexa routing is flaky. The proxy/webhook path knows the current video id,
    so it can refresh the visible recommendations independently.

    force=True skips the "queue already looks fine" shortcut below and always
    rebuilds -- used by "Play Radio" on a track that's already sitting in the
    current queue, where the whole point is to replace that queue with a fresh
    one seeded from just this track (otherwise the shortcut kept the old queue
    unchanged, so "Play Radio" silently did nothing but play the same track in
    the same queue).
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
        if not force and len(cur_queue) > 1 and any(q.get('video_id') == video_id for q in cur_queue):
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
        if cur.get('video_id') == video_id and (force or len(cur_queue) <= 1 or queue_stale):
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
            # The radio API occasionally omits a thumbnail for a given
            # recommendation (e.g. an alternate upload of a song that has one
            # elsewhere in the same queue) -- patch those in the background so
            # "Up Next" doesn't permanently show a blank banner for them.
            missing = [q['video_id'] for q in queue if not q.get('thumbnail')][:5]
            for vid in missing:
                threading.Thread(target=_backfill_queue_thumbnail, args=(vid,), daemon=True).start()
            return True
    except Exception:
        logger.exception("")
    return False


def _backfill_queue_thumbnail(video_id):
    try:
        metadata = _lookup_video_metadata(video_id)
        thumb = _thumbnail_url((metadata or {}).get('thumbnail'))
        if not thumb:
            return
        with _np_lock:
            queue = list(_now_playing.get('queue') or [])
            changed = False
            for item in queue:
                if item.get('video_id') == video_id and not item.get('thumbnail'):
                    item['thumbnail'] = thumb
                    changed = True
            if changed:
                _now_playing['queue'] = queue
                _now_playing['updated_at'] = time.time()
        if changed:
            _notify_sse()
    except Exception:
        logger.exception("")


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
        logger.exception("")
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
        logger.warning("[playback-watchdog] no confirmation for %s in %ss, retrying once", video_id, PLAYBACK_CONFIRM_TIMEOUT)
        error = resend()
        if error:
            logger.error("[playback-watchdog] retry dispatch failed: %s", error)
            _update_now_playing(playback_error={'type': 'dispatch_error', 'message': error})
            return
        if _wait_once():
            return
        if not _still_relevant():
            return
        logger.warning("[playback-watchdog] retry for %s also unconfirmed", video_id)
        _update_now_playing(
            playback_error={'type': 'timeout', 'message': "Playback didn't start. Check the device and try again."})
    except Exception:
        logger.exception("")


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
# Server-side pending store (keyed by random token, NOT the session cookie)
# so concurrent requests from other tabs can't race and lose the state.
_pending_store = {}
_pending_lock = threading.Lock()


@app.route("/login/", methods=["GET", "POST"])
def login():
    if not _remote_login_enabled():
        # No credentials configured; fall back to the key-in-URL scheme.
        return redirect('/remote/')
    if request.method == "GET":
        if _logged_in():
            return redirect('/')
        return _no_store(app.make_response(render_template("login.html", totp=_totp_enabled())))

    body = request.get_json(silent=True) or request.form

    # Step 2: a password-verified session submitting its 2FA code.
    if body.get("step") == "totp":
        token = str(body.get("token") or "")
        with _pending_lock:
            pending = _pending_store.pop(token, None)
        if not pending or pending.get('user') != REMOTE_USER or (time.time() - pending.get('at', 0)) > _PENDING_TTL:
            return error_response('login timed out, start again', 401)
        # str(): a JSON body can carry the 6-digit code as a number, and
        # .strip() on an int would 500 instead of failing the check cleanly.
        if not _totp_verify(str(body.get("code") or "").strip()):
            return error_response('invalid authentication code', 401)
        session['remote_user'] = REMOTE_USER
        session['sid'] = _session_open()
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
        token = secrets.token_urlsafe(32)
        with _pending_lock:
            _pending_store[token] = {'user': REMOTE_USER, 'at': time.time()}
        return jsonify({'ok': True, 'totp_required': True, 'token': token})
    session['remote_user'] = REMOTE_USER
    session['sid'] = _session_open()
    session.permanent = True
    return jsonify({'ok': True})


@app.route("/logout/", methods=["POST", "GET"])
def logout():
    # Revoke the sid server-side so every copy of this cookie dies now, not at
    # its 30-day expiry. "everywhere" kills all sessions on all devices; it is
    # accepted only from a JSON body (a cross-site form can't send one without
    # failing CORS preflight) and only from a currently-valid session.
    body = request.get_json(silent=True) or {}
    owner_logged_in = _logged_in()
    if owner_logged_in:
        _jam_close_all()
    if body.get('everywhere') and owner_logged_in:
        _sessions_close_all()
    elif session.get('sid'):
        _session_close(session['sid'])
    # Clear the whole session (not just remote_user) so nothing — pending 2FA
    # state included — survives a sign-out.
    session.clear()
    if request.method == "GET":
        return redirect('/login/')
    return jsonify({'ok': True})


def _no_store(resp):
    """Auth-dependent HTML must never be served from any cache: a cached copy
    of the logged-in page replays a 'still signed in' UI after the session is
    gone (cleared site data, sign-out, expiry)."""
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route("/remote/", methods=["GET"])
def remote_page():
    # The canonical URL is now the bare domain; keep /remote/ alive for old
    # bookmarks and installed PWAs but bounce them to the clean URL. The
    # key-in-URL scheme still serves here directly (see root()).
    if _remote_login_enabled():
        return redirect('/')
    return _no_store(app.make_response(render_template(
        "remote.html", asset_v=_STATIC_VERSION,
        jam_guest=_jam_guest() and not _valid_key_supplied())))


# ---- Jam endpoints ----

@app.route("/alexa/jam/start/", methods=["POST"])
def jam_start():
    """Owner-only (session or API key — guests can't reach /alexa/jam/*).
    Mints a fresh share token; any previous jam is revoked in the same step."""
    token = _jam_open()
    return jsonify({'ok': True, 'active': True, 'url': _jam_url(token)})


@app.route("/alexa/jam/stop/", methods=["POST"])
def jam_stop():
    """Revoke the jam. Every guest's next request 401s and their SSE stream
    closes on its next tick — revocation is effectively immediate."""
    _jam_close_all()
    return jsonify({'ok': True, 'active': False})


@app.route("/alexa/jam/status/", methods=["GET"])
def jam_status():
    token = _jam_active_token()
    if not token:
        return jsonify({'active': False})
    return jsonify({'active': True, 'url': _jam_url(token)})


@app.route("/alexa/jam/qr/", methods=["GET"])
def jam_qr():
    token = _jam_active_token()
    if not token:
        return jsonify({'error': 'no active jam'}), 404
    try:
        import io
        import qrcode
        import qrcode.image.svg
    except Exception:
        logger.exception("qrcode package is unavailable")
        return jsonify({'error': 'QR generation is unavailable'}), 503

    img = qrcode.make(_jam_url(token), image_factory=qrcode.image.svg.SvgImage)
    out = io.BytesIO()
    img.save(out)
    resp = Response(out.getvalue(), mimetype='image/svg+xml')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


_JAM_ENDED_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jam ended</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0a0a0a; color:#eee; font-family:system-ui,-apple-system,sans-serif; }
  .card { text-align:center; padding:32px; }
  .card h1 { font-size:1.3rem; margin:0 0 8px; }
  .card p { color:#999; font-size:.9rem; margin:0; }
</style></head>
<body><div class="card"><h1>This jam has ended</h1>
<p>The link is no longer active. Ask the host for a new one.</p></div></body></html>"""


@app.route("/j/<token>", methods=["GET"])
@app.route("/jam/<token>", methods=["GET"])
def jam_join(token):
    """Guest entry point. A valid token becomes a guest session cookie and the
    browser lands on the remote (served in guest mode); a dead token gets a
    plain 'jam ended' page. The cookie is non-permanent, so it dies with the
    guest's browser session even if they never leave."""
    if _jam_token_valid(token):
        # Joining must not silently downgrade the owner's own session (login
        # mode: session; key-in-URL mode: a valid ?key= on the link).
        if not _logged_in() and not _valid_key_supplied():
            session['jam'] = token
            session.permanent = False
        return redirect('/remote/' if not _remote_login_enabled() else '/')
    if session.get('jam') == token or not _jam_guest():
        session.pop('jam', None)
    return _no_store(app.make_response((_JAM_ENDED_HTML, 410)))


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
        # Record right away so "Recently Played" updates immediately, instead
        # of waiting for the Lambda webhook (which may lag or not fire for
        # some playback flows) -- mirrors alexa_play_queue below.
        _record_listen(video_id, item.get('title', ''), item.get('artist', ''), thumb)
        _update_now_playing(playing=False,
                            title=item.get('title', ''),
                            artist=item.get('artist', ''),
                            thumbnail=thumb,
                            video_id=video_id,
                            duration_ms=item.get('duration_ms', 0),
                            position_ms=0,
                            playback_confirmed=False,
                            queue_index=target_idx)
        # Queued items added without a duration (e.g. synced playlist tracks
        # that never had one) would otherwise show --:-- forever once they
        # become current -- mirrors the same fallback in alexa_play_track.
        if not int(item.get('duration_ms') or 0):
            threading.Thread(target=_lookup_and_update_np, args=(video_id,), daemon=True).start()
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
        # position_ms is a stored anchor, not a live value -- while playing,
        # the real position is only ever computed on the fly (_computed_position_ms)
        # from that anchor + elapsed wall-clock time; it's never written back.
        # Freezing playing=False without capturing that computed position first
        # leaves the stored anchor stuck at wherever it was last *explicitly*
        # set (track start or last seek), so a voice "stop" a minute into a
        # song snapped the bar back to 0:00/track-start instead of freezing at
        # the actual paused position. Mirrors the app-button pause path below.
        with _np_lock:
            _reset_progress(_computed_position_ms())
            _now_playing['playing'] = False
            _now_playing['updated_at'] = time.time()
        _notify_sse()
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
        same_track = video_id and video_id == _get_now_playing().get('video_id')
        if video_id and not same_track:
            # New track: pull instant metadata from the queue if we have it.
            queue = _get_now_playing().get('queue', [])
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
                # Queue items added without a duration (e.g. synced playlist
                # tracks that never had one, or "play next"/"add to queue" from
                # a source that only carried title/artist) would otherwise show
                # --:-- forever once they become current -- look the real
                # duration up in the background, same fallback as the
                # not-in-queue branch below.
                if not int(matched.get('duration_ms') or 0):
                    threading.Thread(target=_lookup_and_update_np, args=(video_id,), daemon=True).start()
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
            # Same track re-starting (a seek) -- OR a redundant confirmation
            # for a track _confirm_stream_delivery already confirmed (that
            # fallback fires as soon as the audio file is handed to the Echo,
            # before it's actually decoding; this webhook then arrives a beat
            # later reporting the same track at essentially the same offset).
            # Re-anchoring unconditionally on the redundant case snapped the
            # stored position back to ~0 after the client had already ticked
            # forward a second or two against the first anchor, producing a
            # visible progress-bar dip right after a fresh play. Only re-anchor
            # when this genuinely moves the position (a real seek/replay) —
            # skip it when already confirmed+playing at essentially this offset.
            with _np_lock:
                already_tracking = (_now_playing.get('playing')
                                     and _now_playing.get('playback_confirmed'))
                position_matches = abs(_computed_position_ms() - offset_in_ms) < 3000
                if already_tracking and position_matches:
                    redundant_confirmation = True
                else:
                    redundant_confirmation = False
                    _reset_progress(offset_in_ms)
                    _now_playing['playing'] = True
                    _now_playing['playback_confirmed'] = True
                    _now_playing['updated_at'] = time.time()
            if not redundant_confirmation:
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



# ---- Home feed (categorized recommendation rows) ----
_HOME_CACHE_TTL = 3 * 60
_HOME_FORCE_MIN_INTERVAL = 60
_home_cache = {'built_at': 0, 'rows': []}
_home_lock = threading.Lock()


def _normalize_home_item(item):
    video_id = item.get('videoId')
    if not video_id:
        return None
    artists = item.get('artists') or []
    thumbnails = item.get('thumbnails') or []
    return {
        'videoId': str(video_id),
        'title': str(item.get('title') or ''),
        'artist': str((artists[0] or {}).get('name') or '') if artists else '',
        'thumbnail': str((thumbnails[0] or {}).get('url') or '') if thumbnails else '',
    }


async def _build_home():
    ytmusic = YTMusic()
    try:
        raw_rows = await asyncio.to_thread(ytmusic.get_home, limit=5)
    except TypeError:
        raw_rows = await asyncio.to_thread(ytmusic.get_home)
    rows = []
    for row in raw_rows or []:
        items = [
            normalized for normalized in (
                _normalize_home_item(item)
                for item in (row.get('contents') or [])
            )
            if normalized
        ]
        if not items:
            continue
        out = {'title': str(row.get('title') or ''), 'items': items}
        if row.get('subtitle'):
            out['subtitle'] = str(row.get('subtitle') or '')
        rows.append(out)
    return rows


@app.route("/api/home/", methods=["GET"])
def get_home():
    force = request.args.get('refresh') == '1'
    with _home_lock:
        age = time.time() - _home_cache['built_at']
        fresh_enough = age < _HOME_CACHE_TTL
        if fresh_enough and _home_cache['rows'] and (
                not force or age < _HOME_FORCE_MIN_INTERVAL):
            return jsonify({'rows': _home_cache['rows']})
        try:
            rows = asyncio.run(_build_home())
        except Exception:
            logger.exception("home feed failed")
            if _home_cache['rows']:
                return jsonify({'rows': _home_cache['rows']})
            return jsonify({
                'rows': [{
                    'title': 'Recommended',
                    'items': _recs_cache.get('items', []),
                }]
            })
        _home_cache['rows'] = rows
        _home_cache['built_at'] = time.time()
        return jsonify({'rows': rows})


# ---- Blank-state recommendations (web remote) ----
# Mixes radios seeded from a couple of randomly chosen recent history tracks,
# shuffled, so the idle screen varies between visits instead of showing the
# same deterministic YouTube radio every time. Cold start (no history) falls
# back to YT Music's charts. Short cache so a refresh a few minutes later gets
# a fresh mix, without re-hitting YouTube on every single page load.
_RECS_CACHE_TTL = 3 * 60
# Floor for forced rebuilds (?refresh=1): the web remote sends refresh=1 on
# every page load, so several tabs/devices loading within a minute would each
# fire their own 5-radio YT Music build (and stack up waitress threads behind
# _recs_lock). Within this window a forced refresh serves the cache instead.
_RECS_FORCE_MIN_INTERVAL = 60
_recs_cache = {'built_at': 0, 'items': []}
_recs_lock = threading.Lock()


async def _none():
    return None


# Country for the cold-start charts (ISO 3166 alpha-2). Defaults to India so a
# user with no history yet sees Indian trending music, not a US Top-40 list.
CHARTS_COUNTRY = os.environ.get("CHARTS_COUNTRY", "IN")

# Well-known, durable YouTube Music videos used purely as radio *seeds* for the
# cold-start fallback (only reached when there's no history AND charts failed).
# Diverse mix of timeless global hits so at least some seeds work regardless of
# region changes or takedowns. get_radio_queue() (get_watch_playlist) is the same
# stable call used for real playback everywhere else in this app.
#   - dQw4w9WgXcQ  Rick Astley - Never Gonna Give You Up (global evergreen)
#   - kXYiU_JCYtU  Nirvana - Smells Like Teen Spirit
#   - fJ9rUzIMcZQ  Queen - Bohemian Rhapsody
#   - OPf0YbXqDm0  Mark Ronson - Uptown Funk
#   - 60ItHLz5WEA  Adele - Rolling in the Deep
#   - JGwWNGJdvx8  Ed Sheeran - Shape of You
#   - rhwO7C6jea8  Tones and I - Dance Monkey (global #1 in 56 countries)
_FALLBACK_SEED_IDS = [
    'dQw4w9WgXcQ', 'kXYiU_JCYtU', 'fJ9rUzIMcZQ',
    'OPf0YbXqDm0', '60ItHLz5WEA', 'JGwWNGJdvx8',
    'rhwO7C6jea8',
]


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
        age = time.time() - _recs_cache['built_at']
        fresh_enough = age < _RECS_CACHE_TTL
        # A forced refresh still honors a short floor so page-load bursts
        # (every client sends refresh=1) collapse into one rebuild. An empty
        # cache is never served in place of a rebuild.
        if fresh_enough and _recs_cache['items'] and (
                not force or age < _RECS_FORCE_MIN_INTERVAL):
            return jsonify(_recs_cache['items'])
        try:
            items = asyncio.run(_build_recommendations())
        except Exception:
            logger.exception("")
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
        with get_db() as conn:
            conn.execute('DELETE FROM history')
            conn.commit()
    # Recs are seeded from history; a stale cache would keep suggesting the
    # same tracks derived from history the user just wiped.
    with _recs_lock:
        _recs_cache['built_at'] = 0
    return jsonify({'ok': True})


@app.route("/history/<video_id>", methods=["DELETE"])
def remove_history_item(video_id):
    with _history_lock:
        with get_db() as conn:
            conn.execute('DELETE FROM history WHERE video_id = ?', (video_id,))
            conn.commit()
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
    # A jam guest's stream must die when the jam is revoked, not at whenever
    # the browser next reconnects. Capture the token now (the request context
    # is gone once streaming starts) and re-check it on every tick — the
    # 25s keepalive bounds how long a revoked guest can keep this open.
    jam_token = None if _logged_in() else session.get('jam')
    def generate():
        q = _queue_mod.Queue()
        sub = {'serial': serial, 'qv': None}
        with _sse_lock:
            _sse_subscribers[q] = sub
        _ensure_heartbeat()  # start the periodic re-sync (idempotent)
        if serial:
            threading.Thread(target=_refresh_volume, args=(serial, True), daemon=True).start()
        try:
            # Send current state immediately on connect (always with the queue)
            with _np_lock:
                snap = _np_snapshot(serial)
            sub['qv'] = snap['queue_version']
            data = json.dumps(snap)
            yield f"data: {data}\n\n"
            while True:
                if jam_token is not None and not _jam_token_valid(jam_token):
                    break  # jam revoked: close this guest's stream now
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
            logger.warning("[resolve] get_song(%s) failed, trying fallback", video_id)
            logger.exception("")
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
            logger.warning("[resolve] get_watch_playlist(%s) failed, trying search", video_id)
            logger.exception("")
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
            logger.exception("")
        return None, "Couldn't resolve that YouTube link."
    if list_match:
        try:
            playlist = ytmusic.get_playlist(list_match.group(1), 1)
        except Exception:
            logger.exception("")
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
        logger.exception("")
    return {'title': query, 'artist': '', 'thumbnail': ''}


@app.route("/alexa/play/", methods=["POST"])
async def alexa_play():
    body = request.get_json(silent=True) or {}
    # str(): a non-string JSON "query" would 500 on .strip() instead of 400.
    serial, query = body.get("serial"), str(body.get("query") or "").strip()
    if not serial or not query:
        return error_response('missing "serial" or "query"', 400)
    logger.info("[alexa/play] query=%r serial=%s", query, serial)
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
                        logger.exception("")
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
                    logger.error("[alexa/play] direct link play failed: %s", error)
                    _update_now_playing(playing=False)
                elif not _ensure_audio_ready_for_play(direct_video_id, wait=True):
                    logger.error("[alexa/play] direct link download failed video_id=%s", direct_video_id)
                    _update_now_playing(playing=False)
                else:
                    logger.info("[alexa/play] direct link sent successfully video_id=%s", direct_video_id)
                    _record_listen(direct_video_id, queue_item.get('title', ''),
                                   queue_item.get('artist', ''), thumb)
                    _prewarm_queue_audio(queue, queue_index)
            except Exception:
                logger.exception("")
                _update_now_playing(playing=False)

        threading.Thread(target=_bg_play_direct_link, daemon=True).start()
        logger.info("[alexa/play] dispatched direct link video_id=%s", direct_video_id)
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
                        logger.error("[alexa/play] playlist play failed: %s", error)
                        _update_now_playing(playing=False)
                    elif not _ensure_audio_ready_for_play(first['video_id'], wait=True):
                        logger.error("[alexa/play] playlist download failed video_id=%s", first['video_id'])
                        _update_now_playing(playing=False)
                    else:
                        logger.info("[alexa/play] playlist sent successfully (%d tracks)", len(queue))
                        _record_listen(first['video_id'], first.get('title', ''),
                                       first.get('artist', ''),
                                       first.get('thumbnail', ''))
                        _prewarm_queue_audio(queue, 0)
                except Exception:
                    logger.exception("")
                    _update_now_playing(playing=False)

            threading.Thread(target=_bg_play_playlist, daemon=True).start()
            logger.info("[alexa/play] dispatched playlist (%d tracks) video_id=%s", len(queue), first['video_id'])
            return jsonify({'ok': True, 'now_playing': first})
        # Unreadable playlist (private, radio-only id, etc.): fall through to
        # the older resolver so the user still gets a sensible error/first song.

    if is_link:
        spoken, error = await asyncio.to_thread(resolve_play_query, query)
        if error:
            logger.error("[alexa/play] resolve_play_query failed: %s", error)
            return error_response(error, 502)
        logger.info("[alexa/play] resolved link to: %r", spoken)
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
                    logger.error("[alexa/play] play_query failed: %s", error)
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
                _ensure_audio_ready_for_play(video_id)
                error = _dispatch_play_with_retry(serial, video_id)
                if not error and not _ensure_audio_ready_for_play(video_id, wait=True):
                    logger.error("[alexa/play] download failed video_id=%s", video_id)
                    _update_now_playing(playing=False)
                    return
            else:
                error = alexa_remote.remote.play_query(serial, spoken)
            if error:
                logger.error("[alexa/play] play failed: %s", error)
                _update_now_playing(playing=False)
            else:
                logger.info("[alexa/play] sent successfully")
                _record_listen(video_id, fields.get('title', ''),
                               fields.get('artist', ''), thumb)
        except Exception:
            logger.exception("")
            _update_now_playing(playing=False)

    threading.Thread(target=_bg_prepare_and_play, daemon=True).start()

    logger.info("[alexa/play] dispatched to background")
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
    # Set by callers that are about to append more tracks right after this call
    # (e.g. "Play All"/shuffle-play on a saved playlist) -- suppresses the
    # auto radio-queue build below, which otherwise races the client's
    # subsequent queue_add calls: seeing a still-single-item queue, it mistakes
    # the playlist-in-progress for a bare single-track play and overwrites it
    # with generated recommendations instead of the rest of the playlist.
    suppress_radio = bool(body.get("suppress_radio"))
    # "Play Radio" on a track already sitting in the current queue: the whole
    # point is to replace that queue with a fresh one seeded from just this
    # track, so it must skip the "already in queue, reuse as-is" branch below
    # (which otherwise left the old queue untouched and _refresh_radio_queue's
    # own "queue already looks fine" shortcut then made the rebuild a no-op).
    force_radio = bool(body.get("force_radio"))
    if not serial or not video_id:
        return error_response('missing "serial" or "video_id"', 400)
    if not _valid_video_id(video_id):
        return error_response('invalid "video_id"', 400)

    cur = _get_now_playing()
    queue = cur.get('queue') or []
    # A song can appear more than once in the queue (added twice, or a repeat
    # elsewhere in a playlist). Matching by video_id alone always resolves to
    # the *first* occurrence, so clicking a later duplicate would silently
    # play/highlight the earlier one instead. When the client knows exactly
    # which row was clicked, it passes queue_index -- use that (after
    # confirming it still points at this video_id; the queue can shift
    # between the client's last snapshot and this request) and only fall back
    # to the by-id search otherwise.
    item = None
    if not force_radio:
        try:
            queue_index = int(body.get("queue_index"))
        except (TypeError, ValueError):
            queue_index = None
        if queue_index is not None and 0 <= queue_index < len(queue) and queue[queue_index].get('video_id') == video_id:
            item = queue[queue_index]
        if item is None:
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
        # queue.index(item) would resolve to the *first* dict-equal entry,
        # which is exactly wrong for a repeated song -- reuse queue_index
        # directly when that's how `item` was actually found above.
        if queue_index is not None and 0 <= queue_index < len(queue) and queue[queue_index] is item:
            target_idx = queue_index
        else:
            target_idx = queue.index(item)

    _ensure_audio_ready_for_play(video_id, wait=False)
    error = _dispatch_play_with_retry(serial, video_id)
    if error:
        return _device_dispatch_failed(error)

    thumb = _thumbnail_url(item.get('thumbnail'))
    # Record the listen right away so "Recently Played" updates immediately,
    # instead of waiting for the Lambda webhook (which may lag or not fire for
    # some playback flows).  _record_listen uses INSERT OR REPLACE, so a
    # duplicate call from the webhook is harmless.
    _record_listen(video_id, item.get('title', ''), item.get('artist', ''), thumb)
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
    # skill's started webhook happens to rebuild it. Skipped when the caller
    # is about to build a real queue itself (see suppress_radio above).
    if not suppress_radio:
        threading.Thread(target=_refresh_radio_queue, args=(video_id, force_radio), daemon=True).start()
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
    """Categorized search results for the web remote's filter tabs."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return error_response('missing required parameter "q"', 400)

    search_filters = ('songs', 'videos', 'artists', 'albums', 'playlists')

    def _last_thumbnail(item):
        thumbs = item.get('thumbnails') or []
        return thumbs[-1].get('url', '') if thumbs else ''

    def _collect_songs(raw_groups):
        results, seen = [], set()
        for tracks in raw_groups:
            if isinstance(tracks, BaseException) or not tracks:
                continue
            for track in tracks:
                video_id = track.get('videoId')
                if not _valid_video_id(video_id) or video_id in seen:
                    continue
                seen.add(video_id)
                results.append({
                    'title': track.get('title') or '',
                    'artist': " and ".join(a.get('name') or '' for a in track.get('artists') or []),
                    'video_id': video_id,
                    'thumbnail': _last_thumbnail(track),
                    'duration_ms': Supporting.duration_ms(track),
                })
                if len(results) >= 50:
                    break
            if len(results) >= 50:
                break
        return results

    def _collect_artists(raw):
        results = []
        if isinstance(raw, BaseException) or not raw:
            return results
        for item in raw:
            results.append({
                'name': item.get('artist') or '',
                'thumbnail': _last_thumbnail(item),
                'browse_id': item.get('browseId') or '',
            })
            if len(results) >= 50:
                break
        return results

    def _collect_albums(raw):
        results = []
        if isinstance(raw, BaseException) or not raw:
            return results
        for item in raw:
            results.append({
                'title': item.get('title') or '',
                'artist': item.get('artist') or '',
                'year': item.get('year') or '',
                'thumbnail': _last_thumbnail(item),
                'browse_id': item.get('browseId') or '',
                'playlist_id': item.get('playlistId') or '',
            })
            if len(results) >= 50:
                break
        return results

    def _collect_playlists(raw):
        results = []
        if isinstance(raw, BaseException) or not raw:
            return results
        for item in raw:
            results.append({
                'title': item.get('title') or '',
                'track_count': item.get('itemCount') or '',
                'owner': item.get('author') or '',
                'thumbnail': _last_thumbnail(item),
                'browse_id': item.get('browseId') or '',
            })
            if len(results) >= 50:
                break
        return results

    def _categorize(raw):
        by_filter = dict(zip(search_filters, raw))
        return {
            'songs': _collect_songs([by_filter.get('songs'), by_filter.get('videos')]),
            'artists': _collect_artists(by_filter.get('artists')),
            'albums': _collect_albums(by_filter.get('albums')),
            'playlists': _collect_playlists(by_filter.get('playlists')),
        }

    def _has_results(results):
        return any(results[category] for category in ('songs', 'artists', 'albums', 'playlists'))

    ytmusic = YTMusic()

    # First try with spelling correction (ignore_spelling=False) so typos
    # like "dhdadak" get auto-corrected to "dhadak" by YouTube Music.
    raw = await asyncio.gather(
        *[asyncio.to_thread(ytmusic.search, query=query, filter=f,
                            ignore_spelling=False, limit=30)
          for f in search_filters],
        return_exceptions=True)
    results = _categorize(raw)

    # If spelling-corrected search found nothing, retry with exact spelling
    # in case the user intentionally typed an unusual query.
    if not _has_results(results):
        raw = await asyncio.gather(
            *[asyncio.to_thread(ytmusic.search, query=query, filter=f,
                                ignore_spelling=True, limit=30)
              for f in search_filters],
            return_exceptions=True)
        results = _categorize(raw)

    if not _has_results(results):
        return error_response('no results found', 404)
    return jsonify(results)



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


# ---------- Playlists & Liked Songs ----------

_last_thumbnail_backfill_sweep = 0.0

def _sweep_missing_thumbnails():
    """Older tracks can be stuck with no thumbnail (added back when the
    caller's item had a blank one, before /tracks/ started backfilling new
    additions itself). Patch a few of them up per sweep so the UI stops
    showing the placeholder note icon for them, without hammering ytmusic on
    every single page load."""
    global _last_thumbnail_backfill_sweep
    now = time.time()
    if now - _last_thumbnail_backfill_sweep < 3600:
        return
    _last_thumbnail_backfill_sweep = now
    with get_db() as conn:
        rows = conn.execute(
            "SELECT uuid, playlist_id, video_id FROM playlist_tracks "
            "WHERE thumbnail_url IS NULL OR thumbnail_url = '' LIMIT 20"
        ).fetchall()
    for r in rows:
        threading.Thread(
            target=_backfill_track_thumbnail,
            args=(r['playlist_id'], r['uuid'], r['video_id']),
            daemon=True,
        ).start()

@app.route("/api/playlists/", methods=["GET"])
def api_get_playlists():
    threading.Thread(target=_sweep_missing_thumbnails, daemon=True).start()
    return jsonify(_load_playlists())

@app.route("/api/playlists/", methods=["POST"])
def api_create_playlist():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    source_url = body.get("source_url")
    if not name and source_url:
        # Import flow doesn't ask the user to type a name — use the real
        # YouTube playlist title instead. A URL with no "list=" param (a plain
        # video/song link, not a playlist link) is rejected outright rather
        # than silently importing an empty "Imported Playlist".
        if "list=" not in source_url:
            return jsonify({'error': "That doesn't look like a YouTube playlist link (no playlist in the URL)."}), 400
        q = parse_qs(urlparse(source_url).query)
        list_id = q.get("list", [None])[0]
        if not list_id:
            return jsonify({'error': "That doesn't look like a YouTube playlist link (no playlist in the URL)."}), 400
        try:
            info = asyncio.run(Supporting.get_playlist_info(list_id))
        except Exception:
            info = None
        if not info:
            return jsonify({'error': "Couldn't find that playlist. Check the link and try again."}), 400
        name = info.get("title") or "Imported Playlist"
    if not name:
        return jsonify({'error': 'Name required'}), 400

    pl_id = f"pl_{uuid.uuid4().hex}"
    now = time.time()
    
    new_pl = {
        "id": pl_id,
        "name": name,
        "source_url": source_url,
        "updated_at": now,
        "tracks": []
    }
    
    with get_db() as conn:
        conn.execute('''
            INSERT INTO playlists (id, name, source_url, updated_at)
            VALUES (?, ?, ?, ?)
        ''', (pl_id, name, source_url, now))
        conn.commit()
        
    return jsonify(new_pl)

@app.route("/api/playlists/<pl_id>", methods=["DELETE"])
def api_delete_playlist(pl_id):
    if pl_id == "liked":
        return jsonify({'error': 'Cannot delete liked songs'}), 400
    with get_db() as conn:
        conn.execute('DELETE FROM playlists WHERE id = ?', (pl_id,))
        conn.commit()
    return jsonify({'ok': True})

@app.route("/api/playlists/<pl_id>", methods=["PATCH"])
def api_rename_playlist(pl_id):
    if pl_id == "liked":
        return jsonify({'error': 'Cannot rename Liked Songs'}), 400
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    with get_db() as conn:
        conn.execute('UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?', (name, time.time(), pl_id))
        conn.commit()
    return jsonify({'ok': True, 'name': name})

def _backfill_track_thumbnail(pl_id, track_uuid, video_id):
    """The caller's item can carry a blank thumbnail (e.g. a queue entry whose
    art hadn't loaded client-side yet when it was liked/saved) -- look the
    real one up and patch the stored row so it isn't stuck showing the
    placeholder note icon forever."""
    try:
        metadata = _lookup_video_metadata(video_id)
        thumb = _thumbnail_url((metadata or {}).get('thumbnail'))
        if not thumb:
            return
        with get_db() as conn:
            conn.execute(
                "UPDATE playlist_tracks SET thumbnail_url = ? WHERE uuid = ? AND (thumbnail_url IS NULL OR thumbnail_url = '')",
                (thumb, track_uuid),
            )
            conn.commit()
    except Exception:
        logger.exception("")


# Un-liking a song from the Liked Songs view is deliberately reversible in
# the UI (tap the heart again to undo) without the row disappearing until the
# modal is closed. But un-like deletes the DB row outright, so a same-song
# re-like right after would normally get a fresh added_at and jump to the
# bottom of the list -- surprising for what's meant to feel like an undo, not
# a brand new "like". Remember the original added_at briefly so a quick
# undo-relike restores the song to its old spot instead of the newest one.
_recent_unlike_added_at = {}   # video_id -> (added_at, expires_at)
_recent_unlike_lock = threading.Lock()
_RECENT_UNLIKE_TTL = 5 * 60

def _pop_recent_unlike_added_at(video_id):
    with _recent_unlike_lock:
        now = time.time()
        for vid in [v for v, (_, exp) in list(_recent_unlike_added_at.items()) if exp < now]:
            del _recent_unlike_added_at[vid]
        entry = _recent_unlike_added_at.pop(video_id, None)
        return entry[0] if entry else None


@app.route("/alexa/like/", methods=["GET"])
def alexa_like():
    """Voice "like this song": add a track to the local Liked Songs playlist
    (the same one the website's heart button uses). Called by the Alexa skill
    with the current track's metadata; falls back to the server's own
    now-playing track when no video_id is supplied. GET because the skill's
    API client only speaks key-authenticated GETs."""
    video_id = (request.args.get('video_id') or '').strip()
    title = request.args.get('title') or ''
    artist = request.args.get('artist') or ''
    thumbnail = request.args.get('thumbnail') or ''
    try:
        duration_ms = int(request.args.get('duration_ms') or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    if not video_id:
        np = _get_now_playing()
        video_id = np.get('video_id') or ''
        title = title or np.get('title') or ''
        artist = artist or np.get('artist') or ''
        thumbnail = thumbnail or np.get('thumbnail') or ''
        duration_ms = duration_ms or int(np.get('duration_ms') or 0)
    if not video_id:
        return error_response('nothing playing to like', 404)

    now = time.time()
    track_uuid = uuid.uuid4().hex
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO playlists (id, name, updated_at) VALUES ('liked', 'Liked Songs', ?)", (now,))
        existing = conn.execute(
            "SELECT uuid FROM playlist_tracks WHERE playlist_id = 'liked' AND video_id = ?", (video_id,)).fetchone()
        if not existing:
            conn.execute('''
                INSERT INTO playlist_tracks (uuid, playlist_id, video_id, title, artist, thumbnail_url, duration_ms, added_at)
                VALUES (?, 'liked', ?, ?, ?, ?, ?, ?)
            ''', (track_uuid, video_id, title, artist, thumbnail, duration_ms, now))
            conn.execute("UPDATE playlists SET updated_at = ? WHERE id = 'liked'", (now,))
        conn.commit()
    if not thumbnail:
        stored_uuid = existing['uuid'] if existing else track_uuid
        threading.Thread(target=_backfill_track_thumbnail, args=('liked', stored_uuid, video_id), daemon=True).start()
    if not existing:
        _bump_liked_version()
    return jsonify({'ok': True, 'already_liked': bool(existing), 'video_id': video_id})


@app.route("/api/playlists/<pl_id>/tracks/", methods=["POST"])
def api_add_track(pl_id):
    body = request.get_json(silent=True) or {}
    video_id = body.get("video_id")
    if not video_id:
        return jsonify({'error': 'video_id required'}), 400

    track_uuid = uuid.uuid4().hex
    title = body.get("title", "")
    artist = body.get("artist", "")
    thumbnail = body.get("thumbnail") or body.get("thumbnail_url", "")
    duration_ms = body.get("duration_ms", 0)
    now = time.time()
    if pl_id == "liked":
        now = _pop_recent_unlike_added_at(video_id) or now

    track = {
        "uuid": track_uuid,
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "thumbnail": thumbnail,
        "duration_ms": duration_ms,
        "added_at": now
    }
    
    with get_db() as conn:
        if pl_id == "liked":
            conn.execute("INSERT OR IGNORE INTO playlists (id, name, updated_at) VALUES ('liked', 'Liked Songs', ?)", (now,))
            existing = conn.execute("SELECT uuid FROM playlist_tracks WHERE playlist_id = 'liked' AND video_id = ?", (video_id,)).fetchone()
            if not existing:
                conn.execute('''
                    INSERT INTO playlist_tracks (uuid, playlist_id, video_id, title, artist, thumbnail_url, duration_ms, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (track_uuid, pl_id, video_id, title, artist, thumbnail, duration_ms, now))
                conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (now, pl_id))
            conn.commit()
            if not thumbnail:
                stored_uuid = existing['uuid'] if existing else track_uuid
                threading.Thread(target=_backfill_track_thumbnail, args=(pl_id, stored_uuid, video_id), daemon=True).start()
            if not existing:
                _bump_liked_version()
            data = _load_playlists()
            return jsonify({'ok': True, 'track': track, 'liked_songs': data["liked_songs"]})

        existing = conn.execute(
            "SELECT uuid FROM playlist_tracks WHERE playlist_id = ? AND video_id = ?", (pl_id, video_id)
        ).fetchone()
        if existing:
            return jsonify({'error': 'Song is already in this playlist'}), 409
        conn.execute('''
            INSERT INTO playlist_tracks (uuid, playlist_id, video_id, title, artist, thumbnail_url, duration_ms, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (track_uuid, pl_id, video_id, title, artist, thumbnail, duration_ms, now))
        conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (now, pl_id))
        if not thumbnail:
            threading.Thread(target=_backfill_track_thumbnail, args=(pl_id, track_uuid, video_id), daemon=True).start()
        conn.commit()

    return jsonify({'ok': True, 'track': track})

@app.route("/api/playlists/<pl_id>/tracks/<track_uuid>", methods=["DELETE"])
def api_remove_track(pl_id, track_uuid):
    with get_db() as conn:
        if pl_id == "liked":
            row = conn.execute(
                "SELECT added_at FROM playlist_tracks WHERE playlist_id = 'liked' AND video_id = ?", (track_uuid,)
            ).fetchone()
            if row:
                with _recent_unlike_lock:
                    _recent_unlike_added_at[track_uuid] = (row['added_at'], time.time() + _RECENT_UNLIKE_TTL)
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = 'liked' AND video_id = ?", (track_uuid,))
            conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (time.time(), pl_id))
            conn.commit()
            _bump_liked_version()
            data = _load_playlists()
            return jsonify({'ok': True, 'liked_songs': data["liked_songs"]})
            
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ? AND uuid = ?", (pl_id, track_uuid))
        conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (time.time(), pl_id))
        conn.commit()
    return jsonify({'ok': True})

@app.route("/api/playlists/<pl_id>/sync/", methods=["POST"])
def api_sync_playlist(pl_id):
    def sync_bg():
        with get_db() as conn:
            pl_row = conn.execute("SELECT * FROM playlists WHERE id = ?", (pl_id,)).fetchone()
            if not pl_row: return
            source_url = pl_row['source_url']
            
        if not source_url: return
        
        list_id = source_url
        if "list=" in source_url:
            q = parse_qs(urlparse(source_url).query)
            list_id = q.get("list", [None])[0] or source_url
            
        try:
            new_tracks_raw = asyncio.run(Supporting.get_playlist_tracks(list_id))
        except Exception as e:
            logger.error("[sync] Failed to sync %s: %s", list_id, e)
            return
        if new_tracks_raw is None:
            # Unreadable this time (private/deleted/transient API failure) —
            # bail out entirely rather than treating "couldn't fetch" as "the
            # source playlist is now empty" and wiping every stored track.
            logger.warning("[sync] %s returned no data; skipping to avoid wiping tracks", list_id)
            return
            
        now = time.time()
        new_tracks = []
        for track in new_tracks_raw:
            if not track.get("video_id"): continue
            
            new_tracks.append({
                "uuid": uuid.uuid4().hex,
                "video_id": track["video_id"],
                "title": track.get("title", ""),
                "artist": track.get("artist", ""),
                "thumbnail": track.get("thumbnail")["url"] if isinstance(track.get("thumbnail"), dict) else (track.get("thumbnail") or ""),
                "duration_ms": track.get("duration_ms", 0),
                "added_at": now
            })
            
        with get_db() as conn:
            existing_rows = conn.execute("SELECT video_id FROM playlist_tracks WHERE playlist_id = ?", (pl_id,)).fetchall()
            existing_vids = {r['video_id'] for r in existing_rows}
            source_vids = {t["video_id"] for t in new_tracks}

            added = 0
            for t in new_tracks:
                if t["video_id"] not in existing_vids:
                    conn.execute('''
                        INSERT INTO playlist_tracks (uuid, playlist_id, video_id, title, artist, thumbnail_url, duration_ms, added_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (t['uuid'], pl_id, t['video_id'], t['title'], t['artist'], t['thumbnail'], t['duration_ms'], t['added_at']))
                    added += 1

            # Mirror deletions too: a track no longer in the source YouTube
            # playlist is removed from our copy, so sync stays a true mirror
            # instead of only ever growing.
            removed_vids = existing_vids - source_vids
            removed = 0
            for vid in removed_vids:
                conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ? AND video_id = ?", (pl_id, vid))
                removed += 1

            if added > 0 or removed > 0:
                conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (now, pl_id))
            conn.commit()

    threading.Thread(target=sync_bg, daemon=True).start()
    return jsonify({'ok': True, 'status': 'syncing'})


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
        logger.error("[alexa/suggest] failed: %s", e)
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
        return _no_store(app.make_response(render_template("remote.html", asset_v=_STATIC_VERSION)))
    if _jam_guest():
        return _no_store(app.make_response(render_template(
            "remote.html", asset_v=_STATIC_VERSION, jam_guest=True)))
    if session.get('jam'):
        session.pop('jam', None)
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
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    # A tap on the icon should focus the already-running remote, not spawn a
    # second window with its own SSE stream.
    "launch_handler": {"client_mode": "navigate-existing"},
    "orientation": "portrait",
    "background_color": "#0a0a0a",
    "theme_color": "#0a0a0a",
    "categories": ["music", "entertainment"],
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


# Service worker: precaches the app shell assets under a cache versioned by
# _STATIC_VERSION and serves them cache-first, so an installed PWA paints
# without touching the network. Page navigations stay network-first (auth
# redirects and fresh HTML must win) with the last good shell as an offline
# fallback — combined with the localStorage state cache in remote.js, opening
# the app offline still shows the last known player. API, SSE, and audio
# proxy requests are never intercepted. Staleness is handled by versioning:
# any static file change alters _STATIC_VERSION, byte-changing this source,
# which makes the browser re-install the worker and precache fresh copies
# (install fetches bypass the HTTP cache via cache: 'no-cache').
_SERVICE_WORKER_TEMPLATE = """\
const VERSION = '__VERSION__';
const STATIC_CACHE = 'mb-static-' + VERSION;
const PAGE_CACHE = 'mb-pages-v1';
const PRECACHE = [
  '/static/icons/icon-192-any.png',
  '/static/icons/icon-512-any.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/favicon.ico',
  '/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    await Promise.allSettled(PRECACHE.map(
      (u) => cache.add(new Request(u, { cache: 'no-cache' }))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('mb-static-') && key !== STATIC_CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

const OFFLINE_HTML = '<!doctype html><meta charset="utf-8">'
  + '<meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>Music Box</title>'
  + '<body style="background:#0a0a0a;color:#eee;font-family:system-ui;display:grid;place-items:center;height:100vh;margin:0">'
  + '<div style="text-align:center"><h1 style="font-weight:600">Offline</h1>'
  + '<p>Music Box needs a connection to reach your speakers.</p></div>';

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // App shell assets: cache-first from the versioned precache. ignoreSearch
  // lets the ?v= cache-busted URLs the page uses hit the precached copies.
  if (PRECACHE.includes(url.pathname)) {
    e.respondWith((async () => {
      const cached = await caches.match(req, { ignoreSearch: true });
      if (cached) return cached;
      const resp = await fetch(req);
      if (resp.ok) (await caches.open(STATIC_CACHE)).put(req, resp.clone());
      return resp;
    })());
    return;
  }

  // Page navigations: network-first, falling back to the last good shell.
  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      try {
        const resp = await fetch(req);
        // Auth redirect (302 to /login/ etc.) comes back opaque for
        // navigations — hand it straight to the browser to follow. Never
        // cache it, and never mask it with the cached logged-in shell.
        if (resp.type === 'opaqueredirect' || resp.redirected) return resp;
        if (resp.ok && resp.type === 'basic' && (url.pathname === '/' || url.pathname === '/remote/')) {
          (await caches.open(PAGE_CACHE)).put(req, resp.clone());
        }
        return resp;
      } catch (_) {
        const cached = await caches.match(req, { ignoreSearch: true });
        return cached || new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html' } });
      }
    })());
  }
  // Everything else (API, SSE stream, audio proxy) goes straight to the
  // network — no respondWith, so the browser handles it natively.
});
"""
_SERVICE_WORKER = _SERVICE_WORKER_TEMPLATE.replace('__VERSION__', _STATIC_VERSION)


@app.route("/service-worker.js")
def service_worker():
    # Served from the origin root so its scope covers the whole site.
    # no-cache: the browser revalidates this file on navigations, so a new
    # _STATIC_VERSION rolls out to installed PWAs promptly.
    return Response(_SERVICE_WORKER, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# Main entry point
if __name__ == "__main__":
    import waitress
    # Each connected web-remote client pins one thread on the long-lived SSE
    # stream (/alexa/now_playing/stream), so 8 threads starve quickly with a
    # few tabs open — 16 leaves headroom for normal requests.
    waitress.serve(app, host="0.0.0.0", port=5000, threads=16)
