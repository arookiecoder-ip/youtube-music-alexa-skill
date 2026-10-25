# Multi-user progress log

Last updated: 2026-10-25

## Where the work lives

| Repository | Branch | Base | Purpose |
| --- | --- | --- | --- |
| `youtube-music-alexa-skill` | `feat/multi-user` | `origin/master` `e313008` | Backend multi-user layer, admin panel, email auth, deployment docs |
| `MusicBox-APP` | `feat/multi-user` | `origin/main` `f4729df` | Client-side roadmap and integration notes only |

Nothing is merged to `master`/`main`. The existing single-owner deployment and
the Alexa skill/Lambda are untouched.

## Completed

### Backend

- `flask-server/multiuser/` package:
  - `config.py` — feature flag and every TTL/limit validated from environment.
  - `security.py` — PBKDF2-SHA256 hashing, password policy, secure tokens,
    TOTP helpers and an in-process sliding-window rate limiter.
  - `crypto.py` — Fernet-encrypted provider credentials, with an HKDF-derived
    key fallback from `SECRET_KEY`.
  - `db.py` — additive schema v1 + v2, migrations, audit, counts, integrity
    check and online SQLite backup.
  - `accounts.py` — user CRUD, roles, statuses, last-owner protection, sessions,
    email tokens, invitations, self-service profile and audit.
  - `mailer.py` — SMTP delivery, local `.eml` outbox, branded text/HTML email.
  - `admin.py` — `/admin` blueprint: dashboard, users, audit, system, account,
    login/logout, verification, reset, magic link and invitations.
- 16 admin templates (`templates/admin/`) with no external assets.
- `server.py` integration:
  - guarded `init_multiuser(app)` at startup;
  - `/admin` bypasses the device API-key middleware only when the layer has
    registered successfully.
- `cryptography>=42.0` added to `flask-server/requirements.txt`.
- `scripts/generate-multiuser-key.py` for `MULTIUSER_ENCRYPTION_KEY`.
- `.env.example` documents every multi-user, SMTP and token-TTL setting.
- `tests/test_multiuser.py` — 24 tests, all passing:

  ```text
  $ /tmp/opencode/venv/bin/python -m pytest -q tests/test_multiuser.py
  24 passed in 19.16s
  ```

  Covered: CSRF, login throttling/reset on success, session revocation, user
  create/edit/delete, last-owner protection, invitation activation, email
  verification gate, password reset, magic link, expired/forged tokens,
  token invalidation after email change, provider-credential encryption,
  audit CSV, system page, backups, configuration defaults and the
  disabled-by-default route surface.

### Email authentication

- Email verification (24h default).
- Password reset (60 min default).
- Passwordless magic-link sign-in (15 min default, admin roles only).
- Invitations (72h default) that activate the account when a password is set.
- Resend throttling and one live token per purpose per user.
- Email change requires the current password, invalidates outstanding tokens
  and forces re-verification.
- Enumeration-safe responses on public reset/magic-link requests.
- SMTP test button and outbox counters in **System**.

### Operations

- `MULTI_USER_ENABLED=0` default keeps the old app unchanged.
- `docker-compose.yml` now accepts `COMPOSE_ENV_FILE` (default `.env`) so the
  test stack can use `.env.testalexa` without touching another deployment.
- Docs: `ROADMAP.md`, `PROGRESS.md`, `DEPLOYMENT-TESTALEXA.md`,
  `DATA-MODEL.md`.
- App repo carries `docs/MULTIUSER.md` so client work starts from the same
  contract.
- Test stack `musicbox-test` is running on this host at
  `https://testalexa.synthora.in` with a public Caddy certificate.
- `musicbox-firewall.service` persists the Oracle iptables accepts for TCP
  80/443 across reboot (IPv4 and IPv6).

## Live deployment verification (2026-10-25)

Environment: Compose project `musicbox-test`, env file `.env.testalexa`,
origin `https://testalexa.synthora.in` through Cloudflare Full (non-strict).

| Check | Result |
| --- | --- |
| Containers | `ytmusic`, `youtube-browser`, `musicbox-egress`, `caddy` running; sidecars healthy |
| `GET /admin/api/health/` | `200` with `ok=true`, `phase=1` |
| Admin signed out | `/admin/` renders the sign-in page |
| Admin sign-in | `302 → /admin/`, dashboard renders |
| Admin pages | dashboard, users, audit, system and account all `200` |
| User lifecycle via UI | create → detail → edit → audit event → delete, all verified |
| Owner app | `POST /login/` `200`; `/home` and `/api/status` reachable |
| Test email | **System → Send test email** delivered through the Brevo relay to a real external mailbox |
| Verify link | `GET /admin/verify-email/<token>` → `200 Email verified`, `email_verified=1` in `users` |
| Password reset | request endpoint sent the email; `POST /admin/reset-password/<token>` → sign-in with the new password reached `Dashboard` |
| Magic link | `GET /admin/magic/<token>` → `302 → /admin/`; dashboard and users pages `200` on a fresh cookie jar |
| Deleted account | sign-in rejected (`401`) after deletion; tokens and sessions purged |
| Player account login | `POST /login/` with a `user`-role account → `200 {"ok":true,"player":…}`; `/` and `/remote/` `200`; `/alexa/search/` `200` |
| Player restrictions | `/history/`, `/api/library/`, `/api/playlists/`, `/alexa/proxy_check/` → `401`; owner login unchanged |
| TLS | public ACME certificate issued over HTTP-01 |
| Firewall | `musicbox-firewall.service` enabled and active |

The smoke test created disposable users and removed them afterwards. It did not
change the owner account or playback state.

Existing Python regressions: **58/58 files passed**, run with the repository's
per-file isolation pattern (`scripts/run-regressions.py` logic) using Python
3.12 and a local venv containing the application dependencies. The production
image intentionally does not ship `pytest`, so the suite was not executed
inside the running container. Frontend Node tests were not run; no frontend
code changed on this branch.

## Upstream port verification (2026-10-25)

Same stack, rebuilt with `docker compose --env-file .env.testalexa -p
musicbox-test up -d --build`. A disposable `user`-role account was created
through **Users → New**, used for the checks and deleted afterwards; `test123`
and the owner were not touched.

| Check | Result |
| --- | --- |
| Player sign-in | `POST /login/` → `200 {"ok":true,"next":"/"}` |
| Account sign-in pages | `/accounts/amazon` and `/accounts/youtube` → `200`, responsive, no `vnc.html`; both entry buttons (`#youtube-browser-auth`, `#amazon-signin`) render in the player page |
| YouTube touch sign-in | `POST /api/youtube/browser-session/start` (412×915) → `200`, `url="/accounts/youtube"`, `state="waiting_for_login"`; `GET …/surface` returned a live JPEG frame (the sidecar clamped the viewport to its bounds); `POST …/stop` → `200 {"success":true}` |
| Per-identity output | `/api/app/output/` epoch/token differ per account (`ae9907e2…` vs owner `eef5d08c…`); the account never saw the owner's `output_serial` |
| Per-identity credentials | `/api/status/` for the account reported `downloads.present=false`, `youtube_headers.present=false`, `amazon_session_in_memory=false`; the owner still reported its cookie jar `present=true` |
| Amazon sign-in endpoints | `POST /alexa/proxy_login/` validated input (`400`), `GET /alexa/proxy_check/` → `{"logged_in":false,"running":false}` for the account, `POST /alexa/proxy_cancel/` with an unknown `attempt_id` → `{"cancelled":false}` (attempt fencing holds) |
| Logged-out Echo UI | `alexa-readonly.js` served to both identities |
| Test suite | `Ran 835 tests` inside the container (`python3 -m unittest discover -s tests`): 33 failing names, all pre-existing on this branch, and 11 previously failing names now green |

## In flight / pending

- [x] Configure SMTP for `synthora.in` — Brevo relay, `587`/STARTTLS,
      sender `noreply@synthora.in`; login and delivery verified from the container.
- [ ] Set the owner's real email from **My account → Profile** (or change the
      bootstrap `MULTIUSER_ADMIN_EMAIL`) and verify it before signing out.
- [x] Run verification → reset → magic-link against a real mailbox (disposable
      account, then deleted). Invitations are still unexercised.
- [ ] Commit and push both `feat/multi-user` branches.
- [x] Phase 2.0 (egalitarian rewrite): every account signs in to the player at
      `/login/` like the owner; the owner/player split and
      `MULTIUSER_PLAYER_LOGIN` are gone. See `PHASE2-PER-USER.md`.
- [x] Phase 2.1 (core): `_bind_player_identity()` resolves a request to
      `owner`, `user:<id>` (account) or the jam host (guest); `_now_playing`
      and `_np_lock` are `player_state.py` proxies over that identity, so each
      account has its own queue. Verified live on testalexa (user queue vs
      owner queue diverge; jam guest adds land in the host's queue).
- [x] Phase 2.1 (jam): `jam_tokens` carries `user_id`; opening/stopping a jam
      only touches the caller's identity, so accounts run their own jams in
      parallel with the owner's.
- [x] Phase 2.1b: `playback_snapshot` is keyed by identity (the legacy single row
      migrates to `owner`), the persistence writer queues one coalesced save per
      identity and captures the caller's identity in `submit()` (its worker thread
      never inherits the request context), and `_playback_output`,
      `_volume_by_serial` and `_alexa_volume_grace` resolve per identity.
      Verified live: an account claimed phone output while the owner stayed on
      Alexa, each wrote its own queue, and both came back through a container
      restart without overwriting each other.
- [x] Phase 2.2: per-user YouTube Music. `YouTubeCredentialStore` keeps the
      legacy auth file for `owner` and one JSON per account; `YTMusic` clients,
      the home feed cache, listen recording, the download-cookie jar, the
      `yt-dlp` node pool and the background audio pool are all keyed by
      identity; the capture sidecar gets a per-account Chromium profile and
      refuses a second concurrent sign-in with `409`. An account without linked
      YouTube gets the public catalog instead of the owner's session. Verified
      live on testalexa (owner captured into base `/profile`, a disposable
      account into `/profile/<uuid>`, second start refused `409`, jar state
      differed per account; account and profile removed afterwards).
- [x] Phase 2.3: per-user Amazon/Alexa session and Echo devices. `AlexaRemote`
      takes its own `cookie_dir`, and `alexa_remote.remote` is a per-identity
      proxy: the owner keeps the legacy directory (so the persisted cookie in the
      `ytmusic_alexa_cookies` volume stays valid) and each account gets
      `ALEXA_COOKIE_DIR/accounts/user_<id>`, with its own event loop, device list,
      volume and queue sync. Sign-out deletes only that identity's files. The
      interactive login proxy still binds one port server-wide, so a second
      concurrent sign-in is refused with `409` and the claim is reclaimed once an
      abandoned login stops polling. Verified live on testalexa (two disposable
      accounts each answered *"Not signed in"* on `/alexa/status/`, first login
      `200` / second `409` / retry after sign-out `200`, sign-out removed only its
      own `.storage`; accounts and directories removed afterwards).
- [x] Upstream sync: the 11 single-architecture commits on `origin/master`
      (touch sign-in surface, logged-out Echo read-only, Amazon attempt
      cancellation, stream handoff rejection, artist shelf unification,
      viewport bounds, asset versioning) are merged into `feat/multi-user` and
      re-expressed per identity — per-account browser profile and lease,
      `connection_state` on the per-identity remote, attempt and proxy-port
      claims scoped to the caller, `_bg_thread` for every background job so
      `threading` never loses the requester, and `_alexa_queue_decisions` plus
      `_playback_failover_budget` moved off shared module state. Details in
      `PHASE2-PER-USER.md`.
- [ ] Phase 2.3b: voice-skill routing per account. The Lambda still calls this
      server as the install owner, so "Alexa, play … on Music Box" reaches the
      owner's queue and devices; per-account Echo control happens from the web
      remote. Needs skill account linking, which stays a non-goal for Phase 2.
- [ ] Purge per-identity artifacts on account deletion. `delete_user` wipes the
      encrypted credential rows, but the YouTube auth file, download-cookie jar,
      Chromium profile directory, `ALEXA_COOKIE_DIR/accounts/user_<id>` and
      `playback_snapshot` row of a deleted account survive on the data volume.
- [ ] Phase 2.4: `user_id` on history, likes and playlist tables.

## Known limitations (by design in Phase 1)

1. **One Amazon login at a time.** Queue, now-playing, output lease, durable
   snapshot, the YouTube layer and (since 2.3) the Amazon session and Echo devices
   are per identity. What is still server-wide is the interactive login proxy's
   port: while one account is mid-sign-in, another gets `409` until it finishes or
   goes five minutes without polling. Voice commands still reach the owner's
   devices (see 2.3b).
2. **SQLite and in-process state.** One authoritative container. Do not scale
   replicas until Phase 5 (PostgreSQL + Redis) is done.
3. **Email delivery goes through one shared relay.** Mail is sent as
   `noreply@synthora.in` via Brevo; per-user sender identities and bounce
   handling are out of scope for Phase 1. The outbox remains the fallback when
   SMTP is unset, and it holds plaintext one-time links — never expose the data
   volume.
4. **No admin 2FA enrollment UI yet.** Schema and login model support TOTP;
   enrollment arrives in Phase 4.
5. **Existing `/login/` logout clears the whole Flask session**, which also
   signs the same browser out of `/admin`. The reverse is not true. This is a
   known, accepted cross-interaction while the two flows coexist.
6. **No password-change enforcement.** `must_change_password` is stored but not
   yet enforced on the first admin session.
7. **Rate limits are per process.** Correct for the current one-container
   deployment, insufficient after scale-out.
8. **Email flows verified on a disposable account only.** Verification, password
   reset and magic-link sign-in were exercised end to end against a real mailbox
   and the account was deleted afterwards. Invitations, and the same flows on a
   long-lived owner account, are still unexercised.

## Verification commands

```bash
# Backend unit tests
cd /home/ubuntu/youtube-music-alexa-skill/flask-server
/tmp/opencode/venv/bin/python -m pytest -q tests/test_multiuser.py

# Compile check
python3 -m compileall -q multiuser server.py

# Local admin smoke test (does not touch the running stack)
MULTI_USER_ENABLED=1 DB_FILE=/tmp/mu.db SECRET_KEY=test \
MULTIUSER_ADMIN_USER=admin MULTIUSER_ADMIN_PASSWORD='Test!Passw0rd' \
python3 - <<'PY'
from flask import Flask
from multiuser import init_multiuser
app = Flask(__name__); app.secret_key = 'test'
init_multiuser(app)
print(sorted(str(r) for r in app.url_map.iter_rules() if str(r).startswith('/admin')))
PY
```

## Next actions

1. Set and verify a real email address on the owner (and on the second admin)
   **before** signing out; keep a second owner.
2. Run through invite → verify → reset → magic-link once with real email.
3. Begin Phase 2 data scoping with a production-copy migration rehearsal.
