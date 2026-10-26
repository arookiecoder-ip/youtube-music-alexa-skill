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
  - `db.py` — additive schema v1 + v2 + v3 (`totp_pending_*`,
    `idx_users_created_by`), migrations, audit, counts, integrity check and
    online SQLite backup.
  - `accounts.py` — user CRUD, roles, statuses, last-owner protection, sessions,
    email tokens, invitations, self-service profile and audit, plus the
    self-service half: `register_self_service`, the verification gate,
    `change_own_password`, `session_summaries`, `revoke_own_sessions`,
    `begin/confirm/cancel/disable_totp`, `verify_totp_code`,
    `delete_own_account` and the purge hook.
  - `mailer.py` — SMTP delivery, local `.eml` outbox, branded text/HTML email.
  - `emails.py` — the one copy each message is written in, shared by the admin
    panel and the self-service door so the two cannot drift.
  - `netids.py` — which client a limit or audit row keys on (proxy-peer rule,
    rightmost `X-Forwarded-For`, opt-in Cloudflare, IPv6 /64 folding, hashed
    buckets).
  - `pending.py` — the single-use, browser-bound record between "password
    accepted" and "second factor proven".
  - `selfservice.py` — the `/signup`, `/forgot-password`, `/reset-password`,
    `/verify-email`, `/account` and `/api/account*` blueprint, with its own
    limiter table, CSRF surface and security headers.
  - `admin.py` — `/admin` blueprint: dashboard, users, audit, system, account,
    login/logout, verification, reset, magic link and invitations.
- `flask-server/account_purge.py` — the retry queue and the path guards behind
  account deletion; the purge itself is `server._purge_account_artifacts`.
- 16 admin templates (`templates/admin/`) with no external assets, and the
  self-service set (`signup`, `selfservice_base`, `selfservice_form`,
  `selfservice_result`, `reset_password`, `account`, `_form_macros`) with
  `templates/static/css/account.css`, `selfservice.js` and `account.js`.
- `flask-server/serve.py` — starts waitress with
  `clear_untrusted_proxy_headers=False`, because the CLI cannot and the address
  rules above need the forwarded chain to reach Flask at all.
- `server.py` integration:
  - guarded `init_multiuser(app)` at startup;
  - `/admin` bypasses the device API-key middleware only when the layer has
    registered successfully;
  - the same middleware lets the self-service paths through (`/signup`,
    `/signup/resend`, `/forgot-password`, `/verify-email/`, `/api/account/`),
    while `/account` and `/api/account` stay 401 to an anonymous browser;
  - `POST /login/` grew the second step for accounts that enabled TOTP, sharing
    the administrator door's budgets and the same pending store;
  - `_client_ip()` resolves through `multiuser.netids`, so `_check_rate_limit()`,
    the audit trail and the login limits key on the visitor rather than on the
    proxy container;
  - the purge hook and its sweeper (`ACCOUNT_PURGE_SWEEP_SECONDS`) run beside the
    YouTube auth keepalive, because a delete's file work outlives the request.
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
- Five suites for the self-service work, all passing:
  `tests/test_netids.py` (address resolution and the launcher/Dockerfile pins),
  `tests/test_selfservice.py` (the public door end to end, its limits and its
  enumeration equivalence), `tests/test_login_second_factor.py` (both doors'
  two-step sign-in and the shared budget), `tests/test_account_purge.py` (the
  tombstone, the seven registries, the file guards, the retry queue and the
  sidecar endpoint) and `tests/test_player_state.py` (`discard`).
  `tests/test_playback_store.py` and `tests/test_multiuser.py` picked up the
  tombstone and mail-env cases.

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

### Self-service accounts (Phase 2.6)

- Open sign-up at `/signup`, gated on a verified email address before the account
  can sign in; a honeypot field and a minimum-fill-time nonce both answer with the
  same "check your inbox" page, so a bot gets nothing to tune against.
- Per-door limits: 5 sign-ups per hour per address, 3 per day per email, and a
  daily cap counted in the database (`MULTIUSER_MAX_SELF_SIGNUPS_PER_DAY`) because
  nothing in-process survives a restart.
- `/forgot-password` and `/reset-password/<token>` for accounts, sharing one
  budget with the equivalent admin requests and answering identically whether or
  not the address exists.
- `/account` and `/api/account`: profile (an email change re-verifies), password
  (this browser stays, every other session is revoked), the session list with no
  hash or token in it, resend verification, TOTP enrol/QR/confirm/cancel/disable,
  sign-out-everywhere, and self-delete.
- Self-delete demands the current password **and** the username typed back,
  refuses the last owner and the last administrator, hard-deletes the row and
  purges everything the account held outside it: durable snapshot (with a
  tombstone that outranks a heartbeat already in flight), in-process queues,
  output leases and volumes, the Amazon cookie directory, the YouTube auth file,
  the download jar, the Jam tokens and the Chromium profile. Failures retry with
  backoff for 24 hours rather than being forgotten.
- TOTP at sign-in for both doors, with the pending record single-use and bound to
  the address bucket and user agent that earned it.

Details, live results and the stated limitations: `PHASE2-PER-USER.md`.

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

Existing Python regressions: the container suite is the reference run, because the
image is what ships. `tests/test_offline_dispatch_state.py` still needs `pytest`,
which the production image deliberately does not carry, and
`tests/test_home_shelf_variety.py` still imports a symbol `home_feed` does not
export — both fail as loader errors and both predate this work. Frontend Node tests
were not run; no frontend code changed on this branch.

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
- [x] Phase 2.6: self-service accounts — open sign-up gated on email
      verification, `/forgot-password` + `/reset-password`, `/account` with the
      `/api/account` JSON (profile, password, sessions, TOTP, self-delete),
      TOTP at both sign-in doors, per-client address resolution
      (`multiuser/netids.py` + `serve.py`) and the deletion purge. Live
      end-to-end on testalexa with a throwaway address, including the negative
      paths; the owner's and `test123`'s artifacts were re-checked untouched after
      the delete. Details in `PHASE2-PER-USER.md`.
- [x] Purge per-identity artifacts on account deletion. The row is only the
      beginning: `playback_snapshot` (plus a tombstone so a heartbeat in flight
      cannot write it back), the seven in-process registries, the YouTube auth
      file, the download-cookie jar, `ALEXA_COOKIE_DIR/accounts/user_<id>`, the
      Jam tokens and the Chromium profile directory now all go, with anything that
      fails queued in `pending_purges` and retried by a daemon with 30s→2h backoff
      for 24 hours.
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
4. **Second factor is enrol-yourself, not enforce.** Every account (including an
   administrator) can turn TOTP on and off from `/account`, and there is no
   deployment-wide requirement that it be on. Enforcement and step-up
   authentication for destructive actions stay Phase 4 work.
5. **Existing `/login/` logout clears the whole Flask session**, which also
   signs the same browser out of `/admin`. The reverse is not true. This is a
   known, accepted cross-interaction while the two flows coexist.
6. **No password-change enforcement.** `must_change_password` is stored but not
   yet enforced on the first admin session.
7. **Rate limits are an annoyance-multiplier, not a control.** Every bucket is
   in-process, so a container restart clears all of them and scale-out would give
   each replica its own budget. The durable parts are deliberately in the database
   instead: the daily sign-up cap, the lockout columns and one live token per
   purpose. The flip side is that a legitimate user can be locked out of a
   self-service action by their own retries — `account-delete` allows 3 per 24
   hours, and the operator's only lever short of waiting is a restart, which resets
   every other budget too. Insufficient after scale-out; Phase 4 replaces it with
   Redis.
8. **Email flows verified on a disposable account only.** Verification, password
   reset and magic-link sign-in were exercised end to end against a real mailbox
   and the account was deleted afterwards. Invitations, and the same flows on a
   long-lived owner account, are still unexercised.
9. **Per-client addressing depends on the launcher.** `serve.py` must start
   waitress with `clear_untrusted_proxy_headers=False`; revert to the
   `waitress-serve` CLI and every visitor behind Caddy collapses into one address
   bucket, which quietly disables the login and self-service limits. Two tests in
   `tests/test_netids.py` pin both halves of that.
10. **`MULTIUSER_TRUST_CLOUDFLARE` is a claim about the topology.** It is correct
    only while the public origin really is proxied by Cloudflare. `netids` still
    requires the immediate peer to be a proxy, so a direct hit on the origin port
    is answered with the requester's own address — but if the origin is exposed and
    this flag is on, the forwarded chain is believed from whatever peer is listed in
    `MULTIUSER_TRUSTED_PROXY_CIDRS`.
11. **Self-delete is hard, admin delete is soft — but both purge.** An
    administrator's **Delete** keeps the row (`status='deleted'`, email and TOTP
    cleared, username reserved) so **Restore** can bring the account back; the
    snapshot, credential file, jar, cookie directory, Jam tokens and Chromium
    profile are purged either way, because the purge hook runs after both paths.
    What a restore therefore cannot give back is the connected providers — a
    restored account signs in and re-links. Self-delete removes the row outright,
    because a person who deleted themselves must not be resurrectable.
12. **Nine pre-existing failures in `tests/test_youtube_browser_session.py`.** They
    reproduce name-for-name and assertion-for-assertion on a clean `HEAD` checkout,
    belong to the browser-capture work rather than this phase, and are now visible
    in the full-suite run because that module became importable there.

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
