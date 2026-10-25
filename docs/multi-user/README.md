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
  `/admin` blueprint and initialize the multi-user tables.
- Admin panel: `/admin/` (its own session and CSRF, separate from `/login/`).
- Email auth: verification, password reset, magic-link sign-in and invitations.
- Existing single-owner app, Alexa endpoints and the production
  `alexa.synthora.in` deployment are not modified.
- Phase 1 manages identities only. Per-user playback scoping and provider
  linking are Phase 2.

## Local check

```bash
cd flask-server
python -m pytest -q tests/test_multiuser.py
```

No SMTP or network is required; email is written to a temporary outbox.
