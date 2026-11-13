# Primary Music Box deployment

The multi-user server replaces the former single-owner service at
`https://alexa.synthora.in`. The native app uses this same origin for account
sessions, library access and playback. The Alexa skill is named **Music Box** and
all five English models use the invocation **music box**.

The retained skill ID is `amzn1.ask.skill.8cd77e6f-497a-40db-8057-d6aefcf8fc09`.
`ask-resources.json` and both skill packages point to that deployment. The older
Music box development skill is retired after the replacement is verified.
Private hosted credentials remain outside GitHub.

The existing server and volumes are reused. Keep Compose project `musicbox-test`
to preserve account, library, browser and provider data. Its name is an internal
resource identifier; it does not select a staging environment.

- Runtime configuration: `.env.production`, mode 0600, never committed.
- `SITE_ADDRESS=alexa.synthora.in`.
- `PUBLIC_BASE_URL`, `MULTIUSER_PUBLIC_BASE_URL`, and the Alexa proxy origin use
  `https://alexa.synthora.in`.
- `SKILL_INVOCATION_NAME=music box`.
- `COMPOSE_ENV_FILE=.env.production`.
- `COMPOSE_FILE=docker-compose.yml:docker-compose.tunnel.yml:/home/ubuntu/musicbox-ci/compose.image.json`.
- `CADDYFILE=Caddyfile.tunnel`; keep the exact connector peer CIDR already in use.

Both the production hostname and `testalexa.synthora.in` reach the same server.
The old hostname remains available for installed 2.5.3 clients; new links and
2.5.4 clients use the production hostname. Existing host-scoped browser sessions
must sign in on the new hostname. No account database is replaced or migrated
from the former server.

Cloudflare DNS for the production hostname uses the MusicBox tunnel, with a
protected origin fallback. The failover monitor pins the production record ID,
hostname, tunnel target and this server's IP. The existing test health hostname
continues to probe the same private connector. Other DNS records and applications
on the former server are unchanged. The old server is left running.

Backend images and Android APKs are compiled in GitHub Actions. Backend deployment
from the website default branch (`master`) loads the pinned, tested image via the
restricted SSH command; it does not build on the server. Android's default branch
is `main`; its production build verifies the established APK signing certificate,
JVM tests, app-module emulator tests and APK entry points.

Before cutover, save a SQLite backup, current environment, Caddy configuration,
DNS/tunnel configuration, existing image ID and hosted-skill Git bundle. Rollback
restores the prior DNS record and tunnel configuration plus the server's saved
environment and image. Do not delete database volumes. Deletion of the old Alexa
skill is permanent; its saved package is retained privately for reconstruction.

New installs without an explicit owner password write an encrypted
`multiuser_admin_password.enc`, created with mode 0600, instead of a plaintext
password file. An administrator can decrypt it inside the container with the
configured `SecretBox` and encryption key. Existing accounts and credentials are
not changed. Generated legacy API credentials derive from the persistent signing
key; explicitly configured keys and existing legacy key files remain supported.

This host retains its existing explicit
`MUSICBOX_CHROMIUM_DISABLE_SANDBOX=1` compatibility setting. CI's browser smoke
uses the same setting because the hardened container profile cannot create
Chromium's additional sandbox on the hosted runner. The non-root service,
container capability restrictions, authenticated attempt lease and private
network boundary remain enabled. This does not claim a working Chromium sandbox.
