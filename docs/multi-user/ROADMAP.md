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
- [x] Self-service account page: display name, email, password, sessions.
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
- [x] Admin templates are responsive and use only first-party CSS/JS.
- [x] Deployment documentation and progress log.

## Phase 2 — Per-user playback data and provider linking (next)

- [ ] Scope `playlist_tracks`, history, likes, queues and preferences by
      `users.id`; migrate the existing single-owner rows to the bootstrap owner.
- [ ] Per-user YouTube Music authentication: browser/header capture stored in
      `user_provider_credentials` through `SecretBox`, with connect/disconnect
      and health checks in the admin panel.
- [ ] Per-user Amazon/Alexa session cookie storage and validation.
- [ ] Device/account selection API so the Android app can choose a profile.
- [ ] Per-user rate limits and quotas for search, stream resolution and writes.
- [ ] Migration command and rollback rehearsal against a copy of production
      data (never against production directly).

## Phase 3 — End-user authentication for the app and web remote

- [ ] Public login/refresh/logout endpoints for the Android app (short-lived
      access tokens plus rotating refresh tokens, or secure cookie sessions).
- [ ] Account registration or invitation-only onboarding (product decision).
- [ ] TOTP enrollment and recovery codes (schema support already exists).
- [ ] Per-device session list and “sign out everywhere”.
- [ ] Optional OAuth/Google identity if it simplifies YouTube linking.
- [ ] Keep the existing `/login/` owner session working during the transition.

## Phase 4 — Hardening and operations

- [ ] Argon2id migration for password hashes.
- [ ] Redis-backed rate limits and distributed login lockouts.
- [ ] Structured logs with request IDs, metrics and alerting for failed logins,
      email failures, backup failures and database integrity.
- [ ] Automated retention for the outbox and audit log.
- [ ] Admin 2FA enforcement and step-up authentication for destructive actions.
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
