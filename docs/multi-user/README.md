# Multi-user platform documentation

Start here depending on what you need:

| Document | Contents |
| --- | --- |
| [ECHO-PAIRING-REMOVED-2026-11-11.md](ECHO-PAIRING-REMOVED-2026-11-11.md) | Removal of the Echo pairing-code UI and public code generation/redemption |
| [DEPLOYMENT-AUDIT-2026-11-11.md](DEPLOYMENT-AUDIT-2026-11-11.md) | Current security/app release, UI and authentication fixes, verification, remaining limits |
| [ROADMAP.md](ROADMAP.md) | Phased plan, decision log, out-of-scope items |
| [PROGRESS.md](PROGRESS.md) | What is implemented, test evidence, known gaps, next actions |
| [DEPLOYMENT-TESTALEXA.md](DEPLOYMENT-TESTALEXA.md) | Runbook for `testalexa.synthora.in`, SMTP, TLS, rollback |
| [DATA-MODEL.md](DATA-MODEL.md) | Tables, email-token rules, encryption, backup and retention |

## Current private-skill access

See [private skill setup and cross-verification checklist](PRIVATE-SKILL-ACCESS-2026-11-11.md) for per-user credentials, the admin Amazon/Alexa toggle, email verification and GitHub Actions app checks.

## At a glance

- Feature flag: `MULTI_USER_ENABLED=0` by default. Set to `1` to register the
  `/admin` blueprint, the self-service doors (`/signup`, `/forgot-password`,
  `/account`, `/api/account*`) and the multi-user tables.
- Admin panel: `/admin/` (its own session and CSRF, separate from `/login/`).
- Email auth: verification, password reset, magic-link sign-in and invitations.
- Accounts run their own door: sign up (verified email required), recover a
  password, manage profile / sessions / TOTP, and delete themselves — which also
  purges everything that account left outside its row.
- Per-user isolation (Phase 2.0–2.3, 2.6): queue, output lease, durable
  snapshot, YouTube Music session, download jar, Chromium profile, Amazon
  session and Echo devices, all keyed by identity.
- Existing single-owner app, Alexa endpoints and the production
  `alexa.synthora.in` deployment are not modified.
- Private voice skills route through per-account Alexa tokens and independently verified Amazon emails. Administrators control access per user.

## Local checks

```bash
python scripts/run-regressions.py --results /tmp/musicbox-regressions
```

This runs backend and browser-logic tests in separate processes so provider test doubles do not leak between suites. Playwright browser checks run separately. Never run Android compilation on the server; use GitHub Actions in the native app repository.
