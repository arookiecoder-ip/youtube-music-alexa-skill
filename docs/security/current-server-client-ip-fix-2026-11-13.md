# Current-server client-IP header fix — 2026-11-13

Applied the reviewed Caddy patch to the existing `musicbox-test` deployment at
`testalexa.synthora.in`. Production `alexa.synthora.in` was neither contacted nor
changed. No Cloudflare zone setting, DNS, firewall, credential, or account changed.

Caddy now derives the client IP from `CF-Connecting-IP` only when its TCP peer is
in Cloudflare's published IPv4/IPv6 ranges. It then replaces the forwarded header
with the derived client address before forward-auth and application proxying.
Direct clients cannot choose the application's rate-limit/audit address merely
by supplying that header. The origin remains directly reachable; sealing the
origin or migrating to Cloudflare Tunnel is separate work.

Updated `Caddyfile`, `Caddyfile.example`, and `Caddyfile.internal`. The active
Caddyfile's existing skip-worktree index flag was preserved; its change therefore
is not listed in ordinary Git diffs. All writes preserved the existing inode so
that the running Caddy bind mount received the new contents without recreating
the container. The host and mounted file hashes match:

`1f79fbfe880385de46291f6bbf488f48c3507d368d624e3c3e2f4da42105d37d`

The Cloudflare range snapshot was rechecked against the public API before
application. It contains 15 IPv4 and seven IPv6 ranges and requires maintenance
if Cloudflare changes its published list.

Validated the candidate inside the running Caddy v2.11.7 container before writing
the live configuration. Caddy reloaded successfully at 2026-11-13 04:20:58 UTC;
no application containers were rebuilt or restarted. The live admin configuration
confirms the 22 trusted ranges, strict client-IP parsing, selected CF header, and
canonicalizing request-header middleware. Caddy's adapter represents `{client_ip}`
as `{http.vars.client_ip}` in the active JSON configuration.

Post-reload verification:

- 20 direct-local-origin HTTP checks passed. Anonymous account/status/admin APIs,
  Amazon proxy (including forged-header/encoded-path variants), and YouTube
  browser/WebSocket surface returned 401; sensitive-file paths returned 404;
  login and readiness returned 200.
- Five HTTPS checks through Cloudflare passed: readiness/login 200 and account,
  Amazon proxy, and YouTube browser surface 401.
- Application and browser containers remain healthy.
- `git diff --check` passed.

The preceding isolated review had passed 24 existing address/auth/proxy tests,
six signup security tests, and 19 Caddy proxy integration checks (one reproducing
the old weakness and 18 validating the candidate across ordinary and forward-auth
paths). The live checks do not pretend to expose internal client-IP state through
an unauthenticated diagnostic endpoint; the trust behavior was tested in isolation
and the corresponding running configuration was verified after reload.

Rollback copies of all three files:
`/home/ubuntu/musicbox-deployment-backups/caddy-client-ip-20261113T042058Z/`.
To roll back, restore each file in place (preserving the bind-mounted inode),
validate the restored configuration, then reload Caddy. No rollback was needed.

Detailed application record and verification outputs:
`/home/ubuntu/security-reports/2026-11-13-current-server-fixes/application-record.json`.
The prior comparison report describes the pre-deployment state; this note records
the subsequent authorized deployment.
