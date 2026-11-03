# Multi-user roadmap

**Branch:** `feat/multi-user`  
**Scope:** turn the single-owner Music Box backend into a multi-user platform
without changing the production Alexa skill, Lambda or the existing
`alexa.synthora.in` deployment.  
**Flag:** everything in this document is behind `MULTI_USER_ENABLED=1`. The
default (`0`) is byte-for-byte the existing single-owner behaviour.

## Principles

1. **Additive first.** New tables, a separate `/admin` blueprint and a separate
   sign-in flow. Existing playback, device, Alexa and `/remote/` code paths are
   not edited to make the new feature work.
2. **Feature-flagged.** If initialization fails, `server.py` logs the failure
   and keeps serving the single-owner application.
3. **One authoritative process in Phase 1.** SQLite, in-process rate limiting
   and the local mail outbox are valid on the current single-container
   deployment. Horizontal scale is a later phase with explicit replacements.
4. **No Alexa changes.** Per-user Alexa account linking changes the Lambda and
   skill contract. That is intentionally out of scope until the user asks for
   it; the production skill remains untouched.
5. **Reversible.** Every schema change is forward-only and additive. Backups are
   taken before migration. Disabling the flag immediately restores the old
   surface.

## Phase 0 — Foundation (complete)

- [x] `feat/multi-user` branches on backend and Android app repositories.
- [x] Feature flag, configuration validation and guarded startup.
- [x] Additive SQLite schema (`users`, `user_sessions`,
      `user_provider_credentials`, `user_preferences`, `user_invites`,
      `audit_log`, `multiuser_schema_migrations`).
- [x] PBKDF2-SHA256 password hashing (600k iterations), password policy,
      secure tokens and constant-time comparisons.
- [x] Server-side admin sessions (`user_sessions`), session revocation and
      absolute/sliding expiry.
- [x] CSRF protection, security headers, per-account and per-IP login limits.
- [x] Audit journal with retention and CSV export.
- [x] Router-level bypass so `/admin` does not require the device API key.

## Phase 1 — Admin panel and email identity (complete)

### Admin panel

- [x] Dashboard with counts, provider summary and runtime state.
- [x] User list with search, role/status filters, pagination and provider badges.
- [x] Create user with role, status, generated password, invitation and
      verification options.
- [x] Edit profile, role and status; last-active-owner protection.
- [x] Enable/disable, soft delete, restore, password reset, session revocation,
      TOTP reset.
- [x] Self-service account page: email, password, sessions. One name per account
      (`username`), so there is no display-name field to edit.
- [x] System page: database integrity/size/schema, disk, backups, SMTP state,
      test email, manual backup.

### Email authentication

- [x] SMTP delivery using the Python standard library (`starttls`, `ssl` or
      `none`), with reply-to and a test-only redirect.
- [x] When SMTP is absent, write `.eml` files to a private local outbox instead
      of logging one-time tokens.
- [x] Email verification with configurable TTL and resend throttling.
- [x] Password reset by email with single-use, hashed tokens.
- [x] Passwordless magic-link sign-in for administrator roles.
- [x] Invitations that let the user choose their own password and activate the
      account.
- [x] Changing an email address invalidates outstanding tokens and requires
      re-verification.
- [x] Enumeration-safe “forgot password” and magic-link responses.

### Quality

- [x] `tests/test_multiuser.py`: 24 tests covering login/CSRF, roles, CRUD,
      last-owner protection, invitations, verification, reset, magic links,
      token invalidation, provider encryption, audit, backup and rate limiting.
      Phase 2.6 added `test_netids.py`, `test_selfservice.py`,
      `test_login_second_factor.py`, `test_account_purge.py` and
      `test_player_state.py`; the container suite is `Ran 1005 tests`.
- [x] Admin templates are responsive and use only first-party CSS/JS.
- [x] Deployment documentation and progress log.

## Phase 2 — Per-user playback data and provider linking — **2.0–2.3, 2.6–2.9 done**

- [ ] Scope `playlist_tracks`, history, likes, queues and preferences by
      `users.id`; migrate the existing single-owner rows to the bootstrap owner.
- [x] Per-user YouTube Music authentication: browser/header capture stored in
      `user_provider_credentials` through `SecretBox`, with connect/disconnect
      and health checks in the admin panel — and one credential file, download
      jar, `yt-dlp` pool and Chromium profile per identity.
- [x] Per-user Amazon/Alexa session cookie storage and validation.
- [x] Device/account selection: the web remote resolves the caller's own session,
      output lease and Echo list (`/api/profile_status/` per account).
- [x] Per-user rate limits for sign-in, self-service and the general API, keyed on
      the client address rather than the proxy. Quotas for search and stream
      resolution are still open.
- [ ] Migration command and rollback rehearsal against a copy of production
      data (never against production directly).

## Phase 3 — End-user authentication for the app and web remote

- [ ] Public login/refresh/logout endpoints for the Android app (short-lived
      access tokens plus rotating refresh tokens, or secure cookie sessions).
      The cookie half exists for the web remote; the app still authenticates with
      the device API key.
- [x] Account registration or invitation-only onboarding (product decision):
      **open sign-up, gated on a verified email address**, plus invitations from
      the admin panel. An owner can close public sign-up from the panel; the
      environment remains the ceiling.
- [x] TOTP enrollment and disable, with the code demanded at both sign-in doors
      (player `/login/` and `/admin/login/`). Recovery codes are deliberately not
      in this pass: an account that locks itself out is an administrator's job
      until there is a self-service reset worth trusting.
- [x] Per-device session list and “sign out everywhere”.
- [x] Self-delete, with the artifacts the account held outside its row.
- [ ] Optional OAuth/Google identity if it simplifies YouTube linking.
- [x] Keep the existing `/login/` owner session working during the transition —
      the owner signs in there and keeps the legacy `owner` identity slot.

## Phase 4 — Hardening and operations

- [ ] Argon2id migration for password hashes.
- [ ] Redis-backed rate limits and distributed login lockouts. Every limit today
      is in-process, which is correct for one container and cleared by a restart.
- [ ] Structured logs with request IDs, metrics and alerting for failed logins,
      email failures, backup failures and database integrity.
- [ ] Automated retention for the outbox and audit log.
- [ ] **Enforcing** a second factor (a deployment-wide or per-role requirement,
      and step-up re-auth for destructive actions). Enrolment, disable and the
      code step at both doors already exist from Phase 2.6.
- [ ] Dependency and container scanning in CI.
- [ ] Signed backup manifests and encryption at rest for backup files.

## Phase 5 — Scale-out architecture

Required only when one process/one SQLite file is no longer sufficient:

- [ ] PostgreSQL with versioned migrations (Alembic) and connection pooling.
- [ ] Redis for sessions, rate limits, queues, locks and playback authority.
- [ ] Stateless Flask replicas behind Caddy/another load balancer.
- [ ] Object storage for audio cache, cookies and exports.
- [ ] Worker service for email, previews, provider refreshes and backups.
- [ ] Idempotent job model and retry/dead-letter handling.
- [ ] Load tests for concurrent streams, searches and admin traffic.

## Phase 6 — Disaster recovery

- [ ] Automated encrypted off-site backups (database + encryption key + mail
      outbox metadata), with a documented retention policy.
- [ ] Point-in-time recovery for PostgreSQL once migrated.
- [ ] Quarterly restore drill on a clean host, with a timed RTO/RPO report.
- [ ] Runbooks for lost `SECRET_KEY`, lost `MULTIUSER_ENCRYPTION_KEY`, lost
      SMTP credentials and compromised admin sessions.
- [ ] Health checks and external uptime monitoring for `testalexa` and the
      production host.

## Phase 7 — Launch and cutover

- [ ] Product sign-off on onboarding, roles and email templates.
- [ ] Load and security review on `testalexa.synthora.in`.
- [ ] Backup, migration rehearsal and rollback plan.
- [ ] DNS/certificate cutover window with the production app still running.
- [ ] Post-launch monitoring and a 7-day hypercare window.
- [ ] Per-user Alexa support only if explicitly requested (requires skill and
      Lambda changes outside this branch).

## Explicitly out of scope

- Any change to the Alexa skill, Lambda function, interaction model or
  production `alexa.synthora.in` server.
- Sharing one Google/Amazon account across users.
- Billing, subscriptions or multi-tenant branding.
- Migrating existing playback rows in this phase; that is Phase 2 work with a
  rehearsal against a copy.

## Decision log

| Decision | Rationale |
| --- | --- |
| SQLite first | Current deployment is one process; avoids an infrastructure migration before product fit. All access goes through `Database`, so PostgreSQL can replace it. |
| Separate admin session | Avoids coupling the new role model to the legacy single-owner `/login/` session and lets either be disabled independently. |
| SMTP + outbox | The domain is owned, but credentials were not available at build time. The outbox keeps email flows testable without leaking tokens to logs. |
| No per-user playback yet | The playback authority is in-memory and single-owner. Scoping it correctly is a larger change than authentication and needs its own phase. |
| Email verification default on | Prevents typo/abandoned addresses from owning reset and magic-link flows. It can be disabled with `MULTIUSER_REQUIRE_EMAIL_VERIFICATION=0`. |
| Open sign-up, verified email required | Owner decision. An address the person actually controls is what makes self-service password recovery safe, so verification is the price of the open door, and the daily cap plus the database counter are what bound it. |
| Self-delete needs password **and** the username typed back | Re-authenticates the session that is asking to destroy data, and the typed name is the deliberate second act that stops a scripted or mis-clicked delete. It hard-deletes because a person who deleted themselves must not be resurrectable. |
| Self-service in the player, not the admin panel | `/account` + `/api/account` live where the music is; `/admin/account` keeps the administrative edit. One page for both would have made either weaker. |
| TOTP enroll/disable, no recovery codes yet | Recovery codes are a second secret-storage problem with its own loss and leak modes. Enrol/disable plus an administrator's TOTP reset is the smaller honest surface. |
| `serve.py` instead of the `waitress-serve` CLI | waitress deletes untrusted proxy headers before Flask exists, and its `trusted_proxy` option cannot name an ephemeral docker address. Per-client limits are worth owning the launch command; two tests pin it. |
| Purge is a queue with backoff | The row delete is transactional; the filesystem, the sidecar and the in-process state are not. A half-finished purge is a privacy failure, so it retries for 24 hours and then says so in the log. |
| Verification and reset use typed codes, not links | A link is a credential a mail reader can follow without anybody typing anything: it executes on render, survives forwarding, and turns address verification into a click. A six-digit code makes the person act, expires in 15 minutes, is single-use, and is spent after five wrong tries — without ever locking the account, which would let anyone who knows a username deny sign-in. Codes are Fernet-encrypted because a digest of one million values is precomputable from a stolen database. |
| Invitations and administrator sign-in keep links | Deliberately different shapes. An invitation is a first-contact handshake and the `magic` link *is* the sign-in; neither is a step the person asked to perform on an existing account. Both stay token-hash-only. |
| A code never mints a session | Every session at both doors requires the password. The verify form carries no password, so letting it sign the person in would open a second, weaker door into the same accounts. `/verify` answers with an "Email confirmed" page that points at sign-in and sets no session cookie. |
| Sign-up kill-switch is owner-only; the env var is the ceiling | Closing sign-up governs who may obtain an account, which is not an ordinary admin action. `MULTIUSER_SELF_SIGNUP_ENABLED=0` cannot be overridden from the panel, so a stolen owner session cannot reopen a deployment that said never. Closing stops new rows only: pending accounts can still verify, resend and reset. |
| Provider disconnect deletes the secrets, not just the row | The old disconnect removed the `user_provider_credentials` row while the auth file, jar, Chromium profile and Amazon cookies survived — the account kept streaming and controlling its Echo. `_forget_account_artifacts` now has `youtube`/`amazon`/`all` scopes, retries only the failing scope, and the flash names what survived — or says nothing was connected at all, because the service answers `(failures, was_linked)`. |
| Provider sign-in is typed fields, not a mirrored browser | Asking someone to type a password into a picture of a browser gives them no `type="password"`, no password manager, no way to verify what is being captured, and costs a viewport to guess at. The app asks the questions the provider actually needs answered (email, password, code, captcha, which device) and its own automation types into the provider's page. The captcha and the device choice stay interactive because those are genuinely questions only the person can answer. |
| One account may hold the sign-in browser at a time | Google keeps its page open between steps, so an unclaimed browser lets two accounts overwrite each other's half-finished sign-in. The claim is per identity and expires after 300 s, so an abandoned attempt stops blocking; another account is told to wait rather than being silently queued. |
| The raw mirror endpoints stay for now | `/api/youtube/browser-session/surface` and `…/choose-account` (and the sidecar's `/interactive/frame`/`input`) are still routed, authorised and tested, but nothing in the UI calls them since the doors became forms. Deleting them means changing the sidecar image too, so that is an explicit operator decision, not a side effect of this pass. |
| One name per account | `display_name` was a second name nothing enforced: it was not unique, not searchable as a handle, and not accepted at sign-in, so it read like a username that could not be used as one. Schema v5 drops the column and `username` is the only name. The alternative — making the display name authoritative — would have meant two identities per account forever. |
| Sign-in says "Email or username" | The service matched either since the first commit, but the field said "Username", so people typed an address into a box that looked like it would reject it. A label that understates what a form accepts is a bug the user finds alone. |
| Public paths avoid technical words | `/admin/invite/<token>` became `/invite/<token>`, `/signup/verify` became `/verify`, `/signup/resend` became `/resend-code`. An invited person has no business seeing an `admin` segment in a URL, and nobody should have to read a path to know which code goes where. The `/admin` twins keep their prefix because that door really is staff-only. |
