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
| Test email | **System → Send test email** wrote one `.eml` to the outbox; file removed after inspection |
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

## In flight / pending

- [ ] Configure SMTP for `synthora.in` (`MULTIUSER_SMTP_*`,
      `MULTIUSER_MAIL_FROM`). Until then, links go to the local outbox.
- [ ] Set the owner's real email from **My account → Profile** (or change the
      bootstrap `MULTIUSER_ADMIN_EMAIL`) and verify it before signing out.
- [ ] Run invite → verify → reset → magic-link once against a real mailbox.
- [ ] Commit and push both `feat/multi-user` branches.
- [ ] Phase 2: scope playback data and add per-user YouTube/Amazon linking.

## Known limitations (by design in Phase 1)

1. **No per-user playback isolation.** All users still drive the same
   single-owner playback state. The admin panel manages identities only.
2. **SQLite and in-process state.** One authoritative container. Do not scale
   replicas until Phase 5 (PostgreSQL + Redis) is done.
3. **Email delivery is not configured.** The outbox is private (`0700`/`0600`)
   but still contains plaintext one-time links. Configure SMTP or delete old
   files; do not expose the data volume.
4. **No admin 2FA enrollment UI yet.** Schema and login model support TOTP;
   enrollment arrives in Phase 4.
5. **Existing `/login/` logout clears the whole Flask session**, which also
   signs the same browser out of `/admin`. The reverse is not true. This is a
   known, accepted cross-interaction while the two flows coexist.
6. **No password-change enforcement.** `must_change_password` is stored but not
   yet enforced on the first admin session.
7. **Rate limits are per process.** Correct for the current one-container
   deployment, insufficient after scale-out.
8. **Email delivery is outbox-only on this host.** The live checks cover pages,
   identity CRUD and the existing app, not delivery to a real mailbox.

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

1. Configure SMTP for `synthora.in` and send the **System → Send test email**
   check.
2. Set and verify the owner email **before** signing out; keep a second owner.
3. Run through invite → verify → reset → magic-link once with real email.
4. Begin Phase 2 data scoping with a production-copy migration rehearsal.
