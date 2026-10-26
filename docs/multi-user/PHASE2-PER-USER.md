# Phase 2 — Per-user player isolation

Status: **Phase 2.0, 2.1, 2.1b, 2.2, 2.3 and 2.6 shipped: every account signs in at
`/login/`, keeps its own queue, output lease, durable snapshot, YouTube Music session,
Amazon session and Echo devices, runs its own sign-up / recovery / 2FA / self-delete
door, and leaves nothing behind when it deletes itself. What remains is 2.4
(history/likes/playlists) and 2.5 (frontend).** The former
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


### Upstream sync (2026-10-25) — single-architecture work adopted into per-identity — **implemented**

`origin/master` moved 11 commits ahead while Phase 2 was in flight (touch
sign-in surface, logged-out Echo read-only, Amazon attempt cancellation, stream
handoff rejection, artist shelf unification, viewport bounds, asset versioning).
The merge is resolved in `feat/multi-user`; every owner-scoped assumption those
commits carried is now per identity:

- **Touch sign-in** (`/accounts/youtube`, `/accounts/amazon`,
  `/api/youtube/browser-session/*`, `browser-auth/touch_browser.py`) keeps the
  VNC-free frame/input surface, with the per-identity profile key
  (`_browser_profile_key`) and per-identity lease token, and upstream's viewport
  bounds (320–1920 × 480–1080) validated in `YouTubeBrowserSessionManager.start`
  → `400` for a bad viewport, `409` when the sidecar cannot take another window.
  `/stop`, `/open-youtube`, `/capture`, `/choose-account` and `/surface` each
  fence on `authorize(_browser_owner_id(), lease_cookie)` so a late close from one
  device cannot stop another device's sign-in.
- **Amazon sign-in** (`alexa_remote._start_proxy`, `_finalize_proxy_login`,
  `_cancel_proxy_attempt`, `_logout`, `amazon_login_transport`) keeps the
  attempt-id fencing and the durable email pointer, promoted into
  `self.cookie_dir` — i.e. into the *calling identity's*
  `ALEXA_COOKIE_DIR/accounts/user_<uuid>`, never the owner's. The single
  `PROXY_PORT` is still claimed per identity (`409` busy), which is what makes
  cancellation and `_stop_proxy` release ordering matter.
- **Logged-out Echo** (`_active_logged_out_echo`,
  `guard_logged_out_echo_controls`, `templates/static/js/alexa-readonly.js`)
  reads `alexa_remote.remote.connection_state`, which is now a per-identity field
  on the proxy: one account's missing Amazon session no longer greys out another
  account's Echo, and `guard_...` returns its `409 amazon_login_required` only to
  the identity that is actually signed out.
- **Stream handoff** (`a5111b0`) re-binds `_PLAYER_IDENTITY` as the generator
  starts, so the "released during handoff" check compares the caller's own output
  lease.
- **Background work** — the port pushed 21 `threading.Thread(...)` sites onto
  `_bg_thread`, which runs the job under the requesting identity (`threading`
  does not copy contextvars). Metadata lookups, radio builds, prefetch, promotion
  and resume watching all follow the account that asked for them, and the
  dedupe/flight keys (`_np_lookup_inflight`, `_radio_refresh_*`) carry the
  identity alongside the video id.
- **Newly per-identity state found by the port:** `_alexa_queue_decisions` (upstream's
  stale-queue-transition fix) and `_playback_failover_budget`, both of which were
  still module dicts and would have let one account's queue decisions or failover
  exhaustion decide another's playback.

Test consequence worth remembering: several suites (`test_output_routes`,
`test_account_pages`, `test_metadata_recovery`, `test_amazon_account_switch`)
exec a *subset* of real functions into a hand-built namespace. Any module global
those functions touch has to be injected there, and fakes must carry the same
attributes the per-identity code reads (`cookie_dir`, `_storage_dir`,
`_email_file`, `connection_state`). After the port the suite was
`Ran 835 tests … 33 failing names`, every one of them pre-existing on
`feat/multi-user`; 11 previously failing names now pass. (Phase 2.6 added 170
more tests; the current figure is in that section.)

### Phase 2.6 — self-service accounts, second factor and the deletion purge (2026-10-25) — **implemented**

Everything an account can only ask an administrator to do, plus the two things that
make those doors worth opening: an address that identifies the *client* rather than
the proxy, and a deletion that removes what the account left behind.

**Public door** — `flask-server/multiuser/selfservice.py` (a blueprint, mounted by
`MultiUser.init_app`, so `MULTI_USER_ENABLED=0` leaves the surface absent):
`/signup`, `/signup/resend`, `/verify-email/<token>`, `/forgot-password`,
`/reset-password/<token>`, `/account` and the `/api/account*` JSON. Signup is open
and must verify email: `register_self_service` fixes role/status and the account is
locked out of `authenticate` until the address is confirmed. Anti-abuse per request,
in this order: an off-screen honeypot field and a minimum-fill-time nonce both answer
with the *identical* "check your inbox" page (a bot that appears to succeed gives the
attacker nothing to tune against, and the audit row names which tell fired), then
`signup-ip` 5/hour and `signup-id` 3/day per email bucket, then
`registration_open()` — a daily cap counted in the database, so it survives a restart
in a way none of the in-process buckets do. `/forgot-password` answers the same way
whether or not the address exists and shares one budget with the admin door's reset
request. One live token per purpose per user; a token is spent by the first use.

**The account's own page** — `/account` (HTML) plus `/api/account` GET and
`profile` / `password` / `email/verification` / `sessions/revoke` / `totp/begin` /
`totp/qr` / `totp/confirm` / `totp/cancel` / `totp/disable` / `delete` POSTs, all
CSRF-bound (`mu_ss_csrf` in the session, sent as `X-CSRF-Token` or `_csrf`). An
anonymous browser is redirected off the page rather than handed a shell, and the JSON
endpoints answer 401. Changing the email re-verifies it; changing the
password keeps *this* browser signed in and revokes every other session; the session
list reports device and approximate age and never a hash. Self-delete needs the
current password **and** the username typed back, refuses the last owner and the last
administrator, revokes sessions, and hard-deletes the row — a person who deleted
themselves must not be resurrectable, while the audit journal deliberately keeps the
record that the account existed and closed itself. `/api/profile_status/` answers from
the caller's own provider rows, so an account sees its own YouTube/Amazon state and
never the owner's.

**Second factor at both doors** — `multiuser/pending.py` holds the few seconds between
"password accepted" and "code proven": a 256-bit single-use token whose record stays
server-side, bound to `hmac(ip_bucket | user_agent)` and re-compared at step two, so a
token that leaks out of the browser is useless from another one. Records are consumed
on read (a replay and a two-tab race both land on `None` → "start again"), pruned on
write, and capped at 256 with oldest-first eviction so a fast attacker cannot pile up
pending state. `/login/` step 1 mints a record only when `totp_is_enabled` for that
account; step 2 consumes that single-use record **before** looking at the code — a
replay, a second tab and a wrong code all land on `None` and answer "login timed out,
start again", so there is no per-token guess rate to attack — then `verify_totp_code`
spends one 30-second window so the same code cannot be replayed, and **re-loads the
user and re-checks `status='active'`** before minting a session: an account demoted or
2FA-disabled between the steps cannot get in. Expired, wrong and disabled answer with
bodies a caller cannot tell apart. `templates/login.html` now renders the code step
into the document always and reveals it on `json.totp_required` — which accounts need a
code is a per-account fact the page cannot know before the password is typed — and the
"Create an account" / "Forgot password?" links render from the GET site's
`signup_open` and `forgot_url`, so a deployment without the layer gets neither. The
admin door
(`/admin/login/`) enforces the identical step — an admin's 2FA is decoration otherwise
— with its own `_csrf` plus the pending nonce, a wrong code consuming and re-issuing,
and an `admin_login_2fa` audit row.

**Which client is this** — `multiuser/netids.py` resolves the address every limit,
audit row and pending binding keys on. `X-Forwarded-For` is honoured only when the
immediate peer is itself a proxy (private/loopback, or listed in
`MULTIUSER_TRUSTED_PROXY_CIDRS`) and uses the **rightmost** entry, the one our own
proxy appended, so a client's forged list loses. `CF-Connecting-IP` additionally needs
`MULTIUSER_TRUST_CLOUDFLARE=1` — and still needs a proxy peer, so declaring Cloudflare
must not turn the origin port into somewhere a passer-by chooses their address at.
IPv6 folds to /64; every key is hashed to `sha256[:32]`. Both doors now use byte-
identical scope strings (`login-account` on the username bucket at
`MULTIUSER_LOGIN_MAX_ATTEMPTS`, `login-ip` on the address bucket at 10×), reset on
success — and fixing `admin.py`'s reset, which passed the raw username and
`remote_addr` where the limiter keys on buckets, was part of it: that reset was a
silent no-op, so the two doors did not actually share a cleared budget.

The layer alone was not enough: waitress 3 **deletes** `X-Forwarded-For` from the WSGI
environ unless its own `trusted_proxy` option names the exact proxy address, and on a
docker network Caddy's address is ephemeral. `flask-server/serve.py` replaces the
`waitress-serve` CLI entry point (which exposes no such knob) with
`waitress.serve(server.app, clear_untrusted_proxy_headers=False)`, so the chain reaches
Flask and `netids` judges it. Two tests pin that: the launcher keeps the flag off, and
the image starts `serve.py` rather than the CLI.

**Deleting what the row cannot reach** — `flask-server/account_purge.py` plus
`server._purge_account_artifacts(user_id)`, registered as the account layer's purge
hook and run *after* `DELETE FROM users`, so it works from the id alone. The identity
is the literal `'user:' + user_id` and never `_player_identity_for_user_id`: that
helper maps the bootstrap owner's row onto `'owner'`, and with the row gone it could
hand the human's own artifacts to a purge. Order matters:

1. `PlaybackStore.tombstone(identity, 900)` first — an in-process dict *and* a
   `playback_dead` table, so it survives a restart. `save()` refuses while dead and
   `PlaybackPersistence.submit()` refuses to enqueue, which is what stops a heartbeat
   already in flight from re-creating the snapshot row seconds after it was deleted.
   `load()` returns `{}`; expired deadlines are pruned lazily.
2. Every in-process holder: the queue-sync `threading.Timer` is cancelled *before* its
   slot is discarded (a live Timer would fire seconds later under a rebound identity
   and drive someone else's Echo), then `discard` across
   `_player_registry`, `_playback_output_registry`, `_volume_registry`,
   `_alexa_volume_grace_registry`, `_alexa_queue_decisions_registry`,
   `_alexa_queue_sync_slot_registry`, `_playback_failover_registry` and
   `alexa_remote._remote_registry`, plus `_youtube_streams.discard` (closing the
   `YouTubeStream` before the cookie jar it holds open is deleted), the browser-session
   record (dropped, not revoked — `revoke` would re-create the entry and fire a network
   close), the auth caches and the home-cache mtime. `_ARMED_PLAYS` is deliberately
   left alone: it keys on an Echo serial, and revoking it would disarm a shared physical
   device.
3. Secrets at rest: `youtube_auth/<id>.json`, `download_cookies_<id>.txt`,
   `ALEXA_COOKIE_DIR/accounts/user_<id>/` (refusing empty and `OWNER`, requiring a
   non-symlink direct child of the accounts directory, and *not*
   `_clear_persisted_session`, which deletes its `cookie_dir`'s contents), the jam
   tokens, and the sidecar `POST /profile/delete {'profile': id}` — which checks the
   key before touching the filesystem (empty is the owner's base profile, a symlink or
   a non-child is not a profile this service may remove) and answers `409` while any
   capture is running.
4. Steps that remove secrets at rest must succeed or be retried: failures land in
   `pending_purges` and a daemon next to the YouTube auth keepalive
   (`ACCOUNT_PURGE_SWEEP_SECONDS`, default 60, minimum 15) drains it with 30s→2h
   backoff for 24 hours, then logs an operator problem rather than growing the queue.
   The in-process drops are best effort, because a restart clears them anyway.

**Tests** — `tests/test_netids.py` (peer trust, rightmost entry, Cloudflare refusal and
its two fall-throughs, /64 folding, junk never reaching the stored key, plus the
launcher/Dockerfile pins), `tests/test_selfservice.py` (signup → locked → verified →
signed in, honeypot and `signup_too_fast` fake success, the DB cap, enumeration
equivalence, both forgot doors sharing one budget, TOTP enroll/confirm/login/disable,
the pending secret never appearing in `has_totp`, the session list emitting no `sid_hash`,
self-delete with the wrong password / wrong typed username / last-owner refusal),
`tests/test_login_second_factor.py` (the pending store's single use and binding, both
doors' two-step flows, shared budgets, the single-owner door unchanged),
`tests/test_account_purge.py` (the tombstone beating `submit`, the cookie-directory
guards, all seven registries cleared, the retry queue's backoff and give-up, the
sidecar endpoint's path checks), `tests/test_player_state.py` and
`tests/test_playback_store.py`. Container suite: `Ran 1005 tests` with 33 failing
names, identical to HEAD's set — the browser-session module became importable inside
the full run (at HEAD the suite failed to load it at all), and its 9 failures reproduce
name-for-name and assertion-for-assertion on a clean HEAD checkout.

Test isolation learned the hard way, and now encoded in the suites: `mock.patch.dict(os.environ, …, clear=False)`
does not remove a deployed machine's `MULTIUSER_SMTP_*`, so an outbox test on a host
with real credentials silently stops writing to the outbox and every token assertion
fails — the mail keys are now blanked *inside* the env dict. And `import server` reads
`DB_FILE` at import and starts its background loops then, so both suites that need the
real module patch `DB_FILE` to a temp file around the import; unguarded, the status
watchdog issues real `yt-dlp` calls that land in whichever test happens to be patching
`subprocess.run`.

**Verified live** on `testalexa.synthora.in` (project `musicbox-test`, `.env.testalexa`,
throwaway address, `test123`'s password never touched):

| Check | Result |
| --- | --- |
| Address resolution | direct probe → the probe's own address; through Caddy → the appended true client (`161.118.217.36` once `MULTIUSER_TRUST_CLOUDFLARE=1`), a forged leftmost `X-Forwarded-For` ignored |
| Signup | `POST /signup` → the "check your inbox" page; the `.eml` read **inside** the container; sign-in before verification refused; `GET /verify-email/<token>` → account active |
| `/account` | `200` with the account's own username, email mask, sessions and provider state; `/api/account` JSON matched it; `/api/profile_status/` showed this account's providers, not the owner's |
| Password change | kept this browser, revoked the other session |
| 2FA | `totp/begin` → secret + `otpauth://` URI, QR endpoint served, `totp/confirm` activated it; re-login went through the code step; the same code twice → "start again"; `totp/disable` needed the current code |
| Self-delete refusals | wrong password and wrong typed username each `4xx` with the row intact, audited as `account_delete_failed` |
| Self-delete accepted | `users`, `user_sessions`, `user_provider_credentials` rows gone; `playback_snapshot` row gone and a `playback_dead` tombstone at ~896s; `youtube_auth/<id>.json`, `download_cookies_<id>.txt`, `ALEXA_COOKIE_DIR/accounts/user_<id>/` and `/profile/<id>` all removed |
| Owner unharmed | the owner's and `test123`'s identical artifacts still present after the delete |
| Retry queue | the sidecar's `UnboundLocalError` (fixed: `_profile_key` needs `global` when the endpoint clears it) left the profile in `pending_purges`; after rebuilding `youtube-browser`, the sweep finished it and the live call returned `{"deleted": true}` |
| Layer off | `/account`, `/api/account`, `/signup`, `/forgot-password` → `404`, and `/admin/login/` → `401`, with `MULTI_USER_ENABLED=0` |

**Limitations stated plainly.** The rate limits are in-process: a container restart
clears every bucket, which is why the durable controls are the database cap, the
lockout columns and one live token per purpose. The `account-delete` ceiling (3 per
24 hours) is real — three refused attempts by an ordinary account and the fourth is
answered "try later" for a day, with a restart the operator's only lever short of
waiting. Observed during this walk. Self-service delete is hard while the admin
panel's delete is soft (the row survives with its email and TOTP cleared and its
username reserved, so **Restore** works) — and both paths purge, which is why a
restored account comes back without its connected providers.
`MULTIUSER_TRUST_CLOUDFLARE=1` is only correct while the origin really is behind
Cloudflare; on a directly reachable origin it must stay off. One Chromium and one
Amazon login proxy port remain server-wide, so concurrent provider sign-ins still take
turns. History, likes and playlists are still Phase 2.4.

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

`2.0 → 2.1 → 2.2 → (spike) 2.3 → 2.6 → 2.4 → 2.5`

2.0 alone made `sanam12341` able to sign in and drive playback; 2.1 is what
actually stops accounts from sharing one queue; 2.6 is what lets an account open
its own door and close it again. Every phase keeps
`MULTI_USER_ENABLED=0` and the production deployment unaffected.
