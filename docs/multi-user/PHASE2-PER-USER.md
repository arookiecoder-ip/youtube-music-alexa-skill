# Phase 2 — Per-user player isolation

Status: **Phase 2.0, 2.1, 2.1b, 2.2 and 2.3 shipped: every account signs in at
`/login/` and keeps its own queue, output lease, durable snapshot, YouTube Music
session, Amazon session and Echo devices. What remains is the deletion purge,
2.4 (history/likes/playlists) and 2.5 (frontend).** The former
`MULTIUSER_PLAYER_LOGIN` owner/player split was removed by owner decision.
Owner decision: per-user isolation (own queue, own YouTube auth, own Echo devices)
rather than a shared-player login.

The multi-user layer built in Phase 1 manages **identities only**. The player is
still single-owner: every request that reaches playback runs against one set of
module-level globals. This document is the inventory and the staged plan for
changing that without breaking the working app or the Alexa path.

## Why it is not a small change

`flask-server/server.py` is ~10,900 lines. Playback authority lives in module
globals, not in a store that could be keyed by user:

| Global | Line | What it holds |
| --- | --- | --- |
| `_now_playing` | 1808 | queue, `queue_index`, current track, playing flag, thumbnails |
| `_volume_by_serial` | 1850 | volume per Echo device serial |
| `_alexa_volume_grace` | 1852 | volume grace windows per device |
| `_alexa_queue_decisions` | 1906 | ordered queue decisions shared with the skill |
| `_sse_subscribers` | 1895 | live update streams |
| `_home_cache`, `_recs_cache`, `_jam_home_cache` | 1326, 7218, 5791 | per-account recommendation payloads |
| `_YT_CACHE`, `_YT_HOME_CACHE` | 1308, 1329 | `ytmusicapi` clients (public and authenticated) |
| `_stream_list_cache`, `_playlist_tracks_cache`, `_artist_resolve_cache` | 1790, 1799, 9152 | provider caches |
| `_ARMED_PLAYS`, `_PLAY_INTENT_SEQS`, `_PENDING_DISPATCH`, `_TRIGGER_INFLIGHT` | 281, 299, 353, 368 | in-flight play intent and dispatch |
| `_sids`, `_jams_lock`, `_valid_jams` | 897, 986, 985 | session and Jam bookkeeping |

Singletons that are also owned by exactly one identity today:

- `remote = AlexaRemote()` (`alexa_remote.py:892`) — **one** Amazon session drives
  every Echo device.
- `_youtube_stream = YouTubeStream(...)` (`server.py:29`) — one streaming client.
- `_youtube_browser_controller` / `_youtube_browser_sessions` (1414, 1419) — one
  Google sign-in sidecar session.
- `YTMUSIC_AUTH_FILE` — one browser-auth file for personalized YouTube data.

Persisted state is single-owner too: `web_sessions`, `kv`, `jam_tokens`,
`playlist_tracks` have no `user_id`. History, likes and playlists are written by
the owner path with no owner column.

## Target model

1. **Identity** — a request resolves to a `user_id` (Phase 1 account) or to the
   legacy owner identity when no account session is present. Owner keeps the
   current behaviour forever: `REMOTE_USER` + the existing `/login/` credential.
2. **State container** — `PlayerState` per user, created lazily and held in a
   registry keyed by `user_id`, replacing the playback globals above. The owner
   identity maps to a fixed key (`owner`) so existing state/migration is a
   straight move, not a rewrite.
3. **Provider credentials** — reuse `multiuser.user_provider_credentials`
   (already encrypted with `MULTIUSER_ENCRYPTION_KEY`, currently 0 rows). One
   `ytmusicapi` client (and one browser-auth capture) per user, cached in that
   user's `PlayerState`.
4. **Devices** — per-user Amazon session. This is the hardest part: `AlexaPy`
   login is stateful and binds to an event loop, so N users means N long-lived
   sessions/workers. Needs a spike before committing to a design.
5. **Persistence** — add `user_id` to history/likes/playlist tables with a
   backfill that assigns existing rows to the owner; keep the owner path
   working during and after migration.

## Phases

Each phase is independently shippable, additive, and behind a flag. Nothing in
Phase 1 (admin panel, email, accounts) changes.

### Phase 2.0 — player sign-in for accounts (~1 session) — **implemented**

Shipped in `flask-server/server.py`: `PLAYER_LOGIN_ENABLED` (`MULTIUSER_PLAYER_LOGIN`),
`_player_user()` / `_player_logged_in()` / `_player_sign_in()`, an account branch in
`POST /login/`, a player branch in `require_api_key()` ahead of the Jam guest
branch, `_viewer_restricted()` in place of `_jam_guest()` wherever the decision is
"public catalog vs the owner's personal data", session revocation on `/logout/`,
and the guest shell labelled with the account username.

Verified live on `testalexa.synthora.in` with a disposable `user`-role account:
sign-in returned `{"ok":true,"player":…}`; `/` and `/remote/` returned `200`;
`/alexa/search/` returned real results; while `/history/`, `/api/library/`,
`/api/playlists/` and `/alexa/proxy_check/` returned `401`. Owner sign-in still
returned `{"ok":true}`, an unknown user `401`, and a form-encoded mutation `401`
(JSON-only CSRF rule). Account deleted after the run.

Original Phase 2.0 description follows.

Prerequisite for everything else: a request must be able to say *who* is
playing. Currently `_logged_in()` requires `session['remote_user'] == REMOTE_USER`
exactly, so an account cannot even reach `/remote/`.

- Accept Phase 1 accounts on `POST /login/` behind `MULTIUSER_PLAYER_LOGIN=1`.
- Set `session['player_user_id']` and a server-side session id; add
  `_player_logged_in()` next to `_logged_in()`.
- Authorize player sessions with the **existing Jam guest allow-list**
  (`_JAM_PATHS`, `_JAM_PUBLIC_GET_PREFIXES`), so no owner surface (Amazon login,
  playlists, likes, history, `jam/*`, `/alexa/clear`) is newly exposed.
- Until Phase 2.1 lands, a player session drives the **same** queue as the owner.
  Document that clearly; it is a login, not isolation.

### Phase 2.1 — per-user queue and now-playing (~2–3 sessions) — **implemented**

`flask-server/player_state.py` adds `PlayerStateRegistry`, `PlayerStateView`,
`PlayerStateProxy` and `IdentityLock`, plus an `IDENTITY` contextvar. `server.py`
binds the identity in `_bind_player_identity()` (`before_request`), and the
module-level `_now_playing` / `_np_lock` are those proxies — so all ~439 existing
read and write sites resolve to the caller's own state without being rewritten.
The install's owner account, the Alexa skill and unbound background threads
resolve to `owner`.

Live updates already isolate correctly by accident of history: `/alexa/now_playing`
is a per-request poll that reads the caller's own `_now_playing`, and the
`_sse_subscribers` registry `_notify_sse()` walks has had no writer since the
endless stream became a single-shot response.

Shipped since:

- **Phase 2.1b** — `PlaybackStore` keys its snapshot by identity (the legacy single
  row migrates to `owner` on first boot), `PlaybackPersistence` holds one pending
  save per identity and captures the caller's identity in `submit()` because its
  worker thread never inherits the request context. `_playback_output` is now a
  `PlayerStateProxy` over one `PlaybackOutput` per identity, and `_volume_by_serial`
  / `_alexa_volume_grace` are per-identity views. Verified live: an account claimed
  phone output while the owner stayed on Alexa, both wrote different queues, both
  rows survived a container restart, and neither overwrote the other.
- Jam tokens carry a `user_id` (`_jam_open(user_id)`, and a guest binds to the host's
  identity through the token).
- **Phase 2.2** — the YouTube provider layer is per identity (see below).

Still owner-scoped: the voice skill's own requests, because the Lambda calls this
server as the install owner (see Phase 2.3 for what did move).

Original Phase 2.1 description follows.

- Extract `_now_playing` into `PlayerState`, registry keyed by identity.
- Rewrite the queue/now-playing/seek/volume routes to resolve state from the
  request identity instead of module globals.
- SSE streams subscribe per identity.
- Migrate the owner's in-memory state to the `owner` key; no user-visible change.
- **Acceptance:** two signed-in accounts can play different tracks without
  touching each other's queue, `/api/status` reflects the caller's identity, and
  the Alexa path still drives the owner's state.

### Phase 2.2 — per-user YouTube account (~2 sessions) — **implemented**

Shipped in `server.py`, `youtube_credentials.py`, `youtube_browser_session.py`,
`browser-auth/service.py`, `audio_jobs.py` and `download_cookies.py`:

- `YouTubeCredentialStore` keeps the legacy auth file for the `owner` identity and
  gives every `user:<id>` identity its own JSON under `/data/youtube_auth/<id>.json`.
- `_get_ytmusic(identity)` / `_get_ytmusic_home(identity)` and `_catalog_client()` /
  `_signed_in_catalog_client()` — an account with no linked YouTube talks to the
  public catalog and never borrows the owner's session.
- `_home_cache` is a `_IdentitySlotCache` (per-identity TTL value plus per-identity
  single-flight rebuild lock); `_record_listen` keys its dedupe by
  `(identity, video_id)` and pushes to that identity's library.
- The capture sidecar takes a `profile` key: the owner stays on the base
  `/profile`, an account gets `/profile/<id>`. One browser, so a second concurrent
  sign-in is refused with `409` instead of overwriting the first.
- Download cookies are per identity (`/data/download_cookies_<user_id>.txt`), and
  the `yt-dlp` node pool is per identity (`_youtube_stream_for(identity)`).
- `AudioJobs` captures `IDENTITY` at submit and rebinds it while the job runs, so a
  background prefetch can never fill another account's cache; keys are
  `(identity, video_id)`.
- The install's owner account keeps the legacy `owner` slot
  (`_player_identity_for_user_id`), so its auth file, jar, Chromium profile and
  persisted queue survive the switch to multi-user instead of asking it to reconnect.

Verified live on `testalexa.synthora.in` with a disposable account: the owner's
capture launched Chromium in the base `/profile`, the account's capture created
`/profile/<uuid>`, the account was refused with
*"Another account is connecting YouTube Music right now."* while the owner held the
browser, and each account saw its own download-cookie state. The account and its
profile directory were removed afterwards.

### Phase 2.3 — per-user Echo/Alexa (~3–5 sessions, needs a spike) — **implemented**

`AlexaRemote` now takes the directory its session lives in, and
`alexa_remote.remote` is a `PlayerStateProxy` over a `PlayerStateRegistry`: the
install owner keeps the session this module has always held (so the cookie
already in the `ytmusic_alexa_cookies` volume stays valid), and every other
account gets its own `AlexaRemote` under `ALEXA_COOKIE_DIR/accounts/user_<id>`
the first time that identity touches an Alexa call. None of the ~29
`alexa_remote.remote.<call>()` sites in `server.py` changed — identity resolution
happens behind the attribute access, the same way `_playback_output` does.

What that removed from the owner-only column:

- Device discovery, transport, volume, now-playing and play dispatch are per
  account. An account that has not connected Amazon gets
  *"Not signed in. Use the login button to sign in to Amazon."* — it can never be
  handed the owner's Echo list, mirroring the public-catalog rule for YouTube.
- Signing out deletes only the caller's session. Accounts live *inside* the
  owner's directory, so `_clear_persisted_session()` removes the files at its own
  level plus alexapy's `.storage` tree and leaves other directories alone; the old
  `shutil.rmtree(ALEXA_COOKIE_DIR)` signed everybody out.
- The "already signed in — resubmit with `force`" guard is gone: replacing a
  session can now only ever replace the caller's own. The frontend lost its
  confirm-and-retry branch with it.
- `status()` answers "not signed in" from disk for an identity that never
  connected, instead of starting its background loop for the answer.
- The Alexa queue sync runs for every identity. Its coalescing timer and lock are
  per identity (a shared timer let one account's queue edit cancel another's
  sync), and its `threading.Timer` thread re-binds the scheduling identity — a
  Timer does not inherit the caller's context, so an account's sync would
  otherwise have dispatched into the owner's session.

Still shared, deliberately:

- **The login proxy's port.** AlexaPy's interactive login is a man-in-the-middle
  HTTP proxy bound to `PROXY_PORT`, and one process can bind it once. A claim
  (`_proxy_claim`) gives the port to the first account signing in and a second
  login is refused with `409` and *"Another account is connecting to Amazon right
  now. Try again in a minute."* — the same shape as the YouTube capture guard. The
  holder renews its claim on every `/alexa/proxy_check/` poll, so a login someone
  walked away from stops blocking the port after five minutes, and the next login
  shuts that abandoned proxy down before binding it.
- **The voice skill.** The Lambda still calls this server as the install owner
  (account linking remains a non-goal), so "Alexa, play … on Music Box" still
  reaches the owner's queue and devices. An account drives its own Echoes from its
  web remote; per-account skill enablement is its own spike.
- One `AlexaRemote` per identity means one event-loop thread per *active* account.
  Instances are created lazily and are not reclaimed within a process lifetime.

`tests/test_alexa_identity.py` covers the directory mapping (including that an
identity string cannot path-escape it), per-instance isolation, the
sign-out-deletes-only-its-own-session rule, and the proxy port claim/renew/expire
behaviour.

Verified live on `testalexa.synthora.in` with two disposable accounts: each got
its own `ALEXA_COOKIE_DIR/accounts/user_<uuid>` directory, `/alexa/status/` answered
*"Not signed in…"* for the owner and for both accounts instead of sharing one
session, the first `POST /alexa/proxy_login/` took the port (`200`) and the second
account was refused with `409` *"Another account is connecting to Amazon right now.
Try again in a minute."*, and signing the first out freed the port for the second
(`200`). Both sign-outs removed their own `.storage` tree and left the sibling
account directory and the owner's directory in place; the accounts and their
directories were deleted afterwards.


### Phase 2.4 — history, likes, playlists (~2 sessions)

- Schema migration adding `user_id` to the playlist/history/likes tables.
- Backfill existing rows to the owner.
- Per-user queries and endpoints; Jam and owner paths preserved.

### Phase 2.5 — frontend (~2–3 sessions, MusicBox-APP repo)

- Account sign-in, session expiry, account switcher, per-user empty states.
- Client contract is already sketched in `MusicBox-APP/docs/MULTIUSER.md`.

## Decisions still open

1. **Scope of Phase 2.3.** Decided: per-user Amazon sessions for the web remote
   (Phase 2.3 above). Account linking for the voice skill — so that a non-owner's
   Echo can be told "play X on Music Box" by voice — stays out of scope; it needs
   a per-account skill enablement, not a session change.
2. **Storage.** Keep SQLite with `user_id` columns for Phase 2 (single
   authoritative process), or start PostgreSQL now. SQLite is consistent with
   Phase 1 and does not block any phase above.
3. **Owner fallback.** Decided: the install's owner account keeps the legacy `owner`
   identity slot, because the auth file, download jar, base Chromium profile and
   persisted queue already built there belong to it. Panel role and player identity
   remain separate concepts — no *other* account inherits owner privileges, and the
   legacy `/login/` credential still resolves to the same `owner` slot.

## Non-goals

- Horizontal scaling, Redis, PostgreSQL migration (Phase 5 in `ROADMAP.md`).
- Account linking for the Alexa skill / Lambda.
- Per-user billing, quotas or storage limits.

## Order of work

`2.0 → 2.1 → 2.2 → (spike) 2.3 → 2.4 → 2.5`

2.0 alone makes `sanam12341` able to sign in and drive playback; 2.1 is what
actually stops accounts from sharing one queue. Every phase keeps
`MULTI_USER_ENABLED=0` and the production deployment unaffected.
