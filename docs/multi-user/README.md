# Multi-user platform documentation

Start here depending on what you need:

| Document | Contents |
| --- | --- |
| [ROADMAP.md](ROADMAP.md) | Phased plan, decision log, out-of-scope items |
| [PROGRESS.md](PROGRESS.md) | What is implemented, test evidence, known gaps, next actions |
| [DEPLOYMENT-TESTALEXA.md](DEPLOYMENT-TESTALEXA.md) | Runbook for `testalexa.synthora.in`, SMTP, TLS, rollback |
| [DATA-MODEL.md](DATA-MODEL.md) | Tables, email-token rules, encryption, backup and retention |

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
- Still open: history, likes and playlists (2.4), the frontend (2.5) and the
  voice skill's per-account routing (2.3b).

## Local check

```bash
cd flask-server
python -m unittest discover -s tests -p "test_*.py"
```

No SMTP or network is required; one-time links are written to a temporary
outbox. The reference run is inside the image (`docker exec … python3 -m
unittest discover -s tests -p "test_*.py" -q`), because that is the Python that
ships — the two failures it shows (`test_offline_dispatch_state`,
`test_home_shelf_variety`) are a missing `pytest` and a stale import, both
predating this work.
