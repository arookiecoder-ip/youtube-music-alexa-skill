# Deploying the multi-user test build on `testalexa.synthora.in`

This runbook deploys the additive multi-user backend to **this** server only.
It does not touch the production `alexa.synthora.in` host, the Alexa skill, the
Lambda or `lambda/api_key.py`.

- Compose project: `musicbox-test`
- Deployment env file: `/home/ubuntu/youtube-music-alexa-skill/.env.testalexa`
  (mode `0600`, gitignored)
- Public origin: `https://testalexa.synthora.in`
- Admin panel: `https://testalexa.synthora.in/admin/`
- Existing owner remote: `https://testalexa.synthora.in/login/` → `/remote/`

## 1. Preconditions

- DNS for `testalexa.synthora.in` resolves through Cloudflare to this host.
- Cloudflare SSL/TLS mode is **Full** (non-strict). Strict mode rejects Caddy's
  internal fallback certificate.
- Host firewall allows inbound TCP 80/443. On this Oracle host the cloud image
  installs a blanket `REJECT` before UFW's rules, so both layers must allow the
  ports:

  ```bash
  sudo iptables -I INPUT 4 -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
  sudo ufw allow 80/tcp
  sudo ufw allow 443/tcp
  ```

  The runtime rules above are persisted across reboot by
  `/etc/systemd/system/musicbox-firewall.service`, which idempotently inserts
  the same accepts for IPv4 and IPv6:

  ```bash
  sudo systemctl enable --now musicbox-firewall.service
  sudo systemctl status musicbox-firewall.service --no-pager
  ```

- Docker Engine and Compose v2.24+ are installed.

## 2. Generate the deployment env file

Never reuse production secrets. Generate fresh values:

```bash
cd /home/ubuntu/youtube-music-alexa-skill

# Fernet key for per-user provider credentials
python3 scripts/generate-multiuser-key.py

# Other secrets
python3 - <<'PY'
import secrets
for name in ("API_KEY", "SECRET_KEY", "YT_BROWSER_CONTROL_TOKEN",
             "MUSICBOX_EGRESS_TOKEN", "REMOTE_PASSWORD",
             "MULTIUSER_ADMIN_PASSWORD"):
    print(name, secrets.token_urlsafe(32))
PY
```

Create `.env.testalexa` from `.env.example` and set at minimum:

```dotenv
COMPOSE_ENV_FILE=.env.testalexa
SITE_ADDRESS=testalexa.synthora.in
PUBLIC_BASE_URL=https://testalexa.synthora.in
ALEXA_PROXY_BASE_URL=https://testalexa.synthora.in
API_KEY=...
SECRET_KEY=...
YT_BROWSER_CONTROL_TOKEN=...
MUSICBOX_EGRESS_TOKEN=...
REMOTE_USER=testowner
REMOTE_PASSWORD=...

MULTI_USER_ENABLED=1
MULTIUSER_ADMIN_USER=owner
MULTIUSER_ADMIN_PASSWORD=...
MULTIUSER_ADMIN_EMAIL=
MULTIUSER_PUBLIC_BASE_URL=https://testalexa.synthora.in
MULTIUSER_TEST_ENVIRONMENT=1
MULTIUSER_ENCRYPTION_KEY=...
MULTIUSER_BACKUP_DIR=/data/backups
MULTIUSER_MAIL_OUTBOX=/data/mail-outbox
MULTIUSER_MAIL_FROM=noreply@synthora.in
MULTIUSER_SMTP_HOST=smtp-relay.brevo.com
MULTIUSER_SMTP_PORT=587
MULTIUSER_SMTP_SECURITY=starttls
MULTIUSER_SMTP_USERNAME=<brevo-smtp-login>
MULTIUSER_SMTP_PASSWORD=<brevo-smtp-key>
```

`COMPOSE_ENV_FILE` makes Compose load this file into the `ytmusic` container
instead of the default `.env`; it is what keeps the test stack isolated.

Protect it:

```bash
chmod 600 .env.testalexa
```

`cookies.txt` must exist (an empty file is accepted) because Compose bind-mounts
it read-only:

```bash
touch cookies.txt
```

## 3. Build and start

Always pass both the env file and the project name. Omitting `-p` can attach to
another stack:

```bash
cd /home/ubuntu/youtube-music-alexa-skill
docker compose --env-file .env.testalexa -p musicbox-test up -d --build
```

First build pulls Node for the YouTube streaming client, installs Python
dependencies (including `cryptography`) and builds the browser-auth sidecar.
Expect several minutes. Follow progress with:

```bash
docker compose --env-file .env.testalexa -p musicbox-test ps
docker compose --env-file .env.testalexa -p musicbox-test logs -f ytmusic caddy
```

Healthy state: `ytmusic`, `youtube-browser`, `musicbox-egress` and `caddy` are
running; `caddy` publishes `0.0.0.0:80` and `0.0.0.0:443`.

## 4. Verified deployment (2026-10-25)

The stack is running as project `musicbox-test` on this host:

| Check | Result |
| --- | --- |
| `ytmusic`, `youtube-browser`, `musicbox-egress`, `caddy` | running; egress and browser sidecar healthy |
| `https://testalexa.synthora.in/admin/api/health/` | `200`, `{"ok":true,"phase":1,...}` |
| Caddy ACME | public certificate issued over HTTP-01 |
| Admin sign-in (`owner`) | `302 → /admin/`, dashboard renders |
| Admin pages | dashboard, users, audit, system, account all `200` |
| User lifecycle | create → detail → edit → audit event → delete all verified through the UI |
| Existing owner login | `POST /login/` JSON `200`, `/home` `200`, `/api/status` `200` |
| Firewall persistence | `musicbox-firewall.service` enabled and active |

The smoke test used disposable users and removed them afterwards. SMTP delivery
is configured against the Brevo relay and a test message reached a real external
mailbox. The owner account still has no email address, so verification, reset and
magic-link flows have not yet been exercised end-to-end against a real inbox.

## 5. TLS

Caddy requests a public certificate automatically using HTTP-01. If Cloudflare
or the origin firewall prevents ACME, the edge still connects in Full
(non-strict) mode, but browsers may see a certificate warning on direct origin
access. Fall back to the internal certificate:

```bash
printf '\nCADDYFILE=Caddyfile.internal\n' >> .env.testalexa
docker compose --env-file .env.testalexa -p musicbox-test up -d caddy
```

`Caddyfile.internal` adds `tls internal`; Cloudflare still presents its own
public edge certificate. Set Cloudflare to **Full** (not Full Strict) for this
fallback. Prefer the public certificate once ACME succeeds.

Check certificate state:

```bash
docker compose --env-file .env.testalexa -p musicbox-test logs caddy | grep -Ei 'certificate|acme|error'
curl -sS -o /dev/null -w '%{http_code}\n' https://testalexa.synthora.in/admin/api/health/
```

## 6. First sign-in

1. Open `https://testalexa.synthora.in/admin/login/`.
2. Sign in as `owner` with `MULTIUSER_ADMIN_PASSWORD` from
   `.env.testalexa`.
3. Open **My account → Profile**, set your real `@synthora.in` address and a
   display name. Changing the email requires the current password and sends a
   verification link by email.
4. **Before signing out**, retrieve the verification link from the outbox and
   open it. Email verification is enforced at the next sign-in when an address
   is set. If you sign out first, an owner can still resend verification from
   **Users → (your account) → Send verification email** while signed in.
5. Open **System** and confirm the database integrity is `ok`.
6. Create a second owner before relying on this account, so the last-owner guard
   cannot lock you out.

Retrieve the newest verification link without printing other messages:

```bash
docker compose --env-file .env.testalexa -p musicbox-test exec ytmusic \
  sh -c 'ls -t /data/mail-outbox/*.eml | head -1 | xargs sed -n "/verify-email/p"'
```

If the only owner signs out while their email is unverified, they cannot sign
back in. Recover without touching the database by disabling the gate for one
boot, marking the address verified, then re-enabling it:

```bash
sed -i 's/^MULTIUSER_REQUIRE_EMAIL_VERIFICATION=1/MULTIUSER_REQUIRE_EMAIL_VERIFICATION=0/' .env.testalexa
docker compose --env-file .env.testalexa -p musicbox-test up -d ytmusic
# sign in, open Users -> owner -> Mark verified manually
sed -i 's/^MULTIUSER_REQUIRE_EMAIL_VERIFICATION=0/MULTIUSER_REQUIRE_EMAIL_VERIFICATION=1/' .env.testalexa
docker compose --env-file .env.testalexa -p musicbox-test up -d ytmusic
```

If the bootstrap password was left blank, the generated password was written to
`multiuser_admin_password.txt` beside `DB_FILE` (`/data` inside the container,
`ytmusic_data` volume). Retrieve it and delete it after the first login:

```bash
docker compose --env-file .env.testalexa -p musicbox-test exec ytmusic \
  cat /data/multiuser_admin_password.txt
```

## 7. Email delivery (`synthora.in` via Brevo)

Delivery runs through Brevo's SMTP relay. The domain is authenticated with
Brevo by DKIM, so any `@synthora.in` sender is allowed;
`MULTIUSER_MAIL_FROM` is `noreply@synthora.in`.

```dotenv
MULTIUSER_SMTP_HOST=smtp-relay.brevo.com
MULTIUSER_SMTP_PORT=587
MULTIUSER_SMTP_SECURITY=starttls   # 465 + ssl also works
MULTIUSER_SMTP_USERNAME=<login from Brevo → SMTP & API → SMTP>
MULTIUSER_SMTP_PASSWORD=<SMTP key; not the account password or a v3 API key>
MULTIUSER_MAIL_FROM=noreply@synthora.in
MULTIUSER_MAIL_FROM_NAME="Music Box"
MULTIUSER_MAIL_REPLY_TO=
```

Quote values that contain spaces: Compose strips the quotes, and it keeps the
file safe to `source` in a shell.

Recreate the app, then send a test from **System → Send test email** (owner
accounts only):

```bash
docker compose --env-file .env.testalexa -p musicbox-test up -d ytmusic
docker compose --env-file .env.testalexa -p musicbox-test logs --tail=20 ytmusic \
  | grep -E 'Email delivery configured|email sent|email delivery failed'
```

A successful send logs `[multiuser] email sent to …` and records
`test_email_sent` in the audit log. A failure logs `email delivery failed` with
the SMTP error class and records `test_email_failed`.

DNS state for `synthora.in` (verified):

| Record | Value | Purpose |
| --- | --- | --- |
| `brevo-code` TXT | `brevo-code:…` | domain ownership |
| `brevo1._domainkey` CNAME | `b1.synthora-in.dkim.brevo.com.` | DKIM |
| `brevo2._domainkey` CNAME | `b2.synthora-in.dkim.brevo.com.` | DKIM |
| `_dmarc` TXT | `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` | DMARC reporting |
| `MX` | `mx.zoho.in` et al. | Zoho stays the inbound mail host |

SPF is intentionally left as `v=spf1 include:zoho.in ~all`. Brevo sends with
its own Return-Path on its bounce domains, so DKIM alignment carries DMARC; an
SPF include is only needed for a Custom Return-Path or Dedicated IP. Merging it
into the single existing SPF record is what would risk breaking Zoho inbound
mail.

Provider checklist:

- Keep the Brevo SMTP login and sender on the same authenticated domain.
- DMARC starts at `p=none` while testing.
- Port 587 STARTTLS is usually the least restrictive; 465 SSL also works.
- If the provider blocks the host, check outbound TCP 587/465 from this server.

`/data/mail-outbox` inside the `ytmusic_data` volume is the fallback: when
`MULTIUSER_SMTP_HOST` is blank, every one-time link is written there as a `.eml`
file instead of being sent. Treat those files as secrets:

```bash
docker compose --env-file .env.testalexa -p musicbox-test exec ytmusic \
  ls -l /data/mail-outbox
```

Never paste an `.eml` into chat or logs; it contains a working one-time link.

## 8. Verify the existing app still works

- `https://testalexa.synthora.in/` serves the existing app shell.
- `https://testalexa.synthora.in/login/` accepts `REMOTE_USER` /
  `REMOTE_PASSWORD` from `.env.testalexa`.
- `/remote/` loads and playback controls behave as before.
- The Alexa/device API still requires `API_KEY`; nothing in the skill/Lambda
  was changed.
- `https://testalexa.synthora.in/admin/` redirects to the admin login when
  signed out and shows the dashboard when signed in.
- Running `MULTI_USER_ENABLED=0` and restarting removes the admin blueprint
  without affecting the existing app.

## 9. Backups

Manual backup (owner only): **System → Create backup now**, or:

```bash
docker compose --env-file .env.testalexa -p musicbox-test exec ytmusic \
  python - <<'PY'
import os
from multiuser import MultiUserConfig
from multiuser.db import Database
config = MultiUserConfig.from_env()
dest = os.path.join(config.backup_dir, 'manual.db')
Database(config.db_path).backup_to(dest)
print(dest)
PY
```

Copy backups and the encryption key off the host. A backup without
`MULTIUSER_ENCRYPTION_KEY` cannot restore provider credentials.

## 10. Stop, roll back and remove

Stop without deleting data:

```bash
docker compose --env-file .env.testalexa -p musicbox-test stop
```

Disable only the multi-user layer while keeping the existing app running:

```bash
sed -i 's/^MULTI_USER_ENABLED=1/MULTI_USER_ENABLED=0/' .env.testalexa
docker compose --env-file .env.testalexa -p musicbox-test up -d ytmusic
```

Remove the stack but keep named volumes:

```bash
docker compose --env-file .env.testalexa -p musicbox-test down
```

Remove everything including the database, outbox, Caddy certificates and the
YouTube browser profile:

```bash
docker compose --env-file .env.testalexa -p musicbox-test down -v
```

Only use `-v` after taking a backup; it is irreversible.

## 11. Security checklist

- [ ] `.env.testalexa` is `0600` and gitignored; no secrets in the repository.
- [ ] `MULTIUSER_ENCRYPTION_KEY` backed up separately from the database.
- [ ] Cloudflare origin certificate mode is Full or Full (Strict) with a public
      Caddy certificate; never Flexible.
- [ ] Ports 80/443 are the only new inbound ports; 5000/5001 are not published.
- [ ] SMTP credentials use a dedicated sender, not a personal mailbox password.
- [ ] The outbox volume is not exposed through Caddy, SFTP or backups shared
      with third parties.
- [ ] Admin accounts use unique passwords; owner count is at least two.
- [ ] Audit log reviewed after setup; unexpected logins investigated.
