# Tunnel primary and protected-origin fallback — 2026-11-13

`testalexa.synthora.in` now normally uses the existing **MusicBox** Cloudflare
Tunnel. If its end-to-end health check fails three times, an independent systemd
check switches that hostname's **proxied** DNS record to this server's real IPv4
address. Three healthy probes restore the Tunnel CNAME. The hostname and public
HTTPS URL remain the same in both modes.

Production `alexa.synthora.in` was not contacted or changed. An authenticated
before/after comparison confirmed every pre-existing DNS record other than the
test hostname was unchanged. No shared SSL/WAF/country setting changed. The only
additional DNS record is a health-only hostname for this deployment.

## Request paths and access controls

Normal path:

`visitor -> Cloudflare -> cloudflared -> 127.0.0.1:8080 -> Caddy -> application`

Fallback path:

`visitor -> Cloudflare -> real origin:443 -> Caddy -> application`

The private connector port is published **only on host loopback**. Its Caddy
site accepts the exact Docker host gateway peer (`172.18.0.1/32`), not arbitrary
private addresses. Public HTTP/HTTPS sites accept Cloudflare TCP peers only.
Explicit `route` blocks keep rejection ahead of catch-all application handlers.
Caddy verifies/canonicalizes the client-IP header for both paths. Existing Amazon
and YouTube forward-authentication checks remain in the shared route snippet.
HTTP requests through the tunnel are redirected to public HTTPS before application
handling. The HTTP ACME challenge path is routed to Caddy's existing challenge
listener so the fallback's public certificate can continue renewing.

A dedicated nftables table, `inet musicbox_origin`, drops incoming non-Cloudflare
traffic to TCP/UDP ports 80/443 on `enp0s6` **before Docker DNAT**. It covers IPv4
and IPv6. This avoids relying on UFW host-input rules for Docker-published ports.
It neither flushes other firewall tables nor changes SSH port 22, outbound traffic,
loopback, or cloudflared's outbound connector traffic. Caddy's application-level
restriction remains as a second layer. The filter is loaded before Docker and
cloudflared on boot by `musicbox-origin-filter.service`.

Both the Caddy configuration and nftables rule use Cloudflare's published IP-range
snapshot, verified against its API during this work. Update both if those ranges
change. Source allowlisting identifies the Cloudflare network; it is not a
per-zone client certificate and does not replace application authentication.

## Deployment persistence

Files added:

- `Caddyfile.tunnel`: connector and protected-fallback sites, shared auth routes.
- `docker-compose.tunnel.yml`: adds the loopback-only connector publication.
- `scripts/musicbox-tunnel-failover.py`: health checks, hysteresis, pinned DNS update.
- `scripts/tests/test_tunnel_failover.py`: failover/security regression tests.
- `deploy/systemd/musicbox-tunnel-failover.{service,timer}`.
- `deploy/musicbox-origin-filter.nft` and its systemd service.

The existing `.env.testalexa` now selects:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.tunnel.yml
CADDYFILE=Caddyfile.tunnel
CADDY_TUNNEL_PROXY_CIDRS=172.18.0.1/32
```

Standard deployment commands using `--env-file .env.testalexa -p musicbox-test`
therefore retain the override. If explicitly supplying `-f`, include **both**
Compose files. Recreating only Caddy was necessary to add its private port and
configuration mount. The application, browser, provider, and egress services were
not rebuilt or restarted.

`cloudflared.service` already existed and is enabled, with restart-on-failure after
five seconds. Its missing `/etc/cloudflared/token` was restored from the tunnel's
authorized API and verified by a successful restart. It is root-owned, mode 0600.
No tunnel credential appears in the repository or command-line arguments.

## Health and failover

The fixed health URL is
`https://testalexa-tunnel-health.synthora.in/health/ready`.
Only that path is published on the health hostname; other paths hit the tunnel's
404 catch-all. It stays on the tunnel while the primary hostname is in fallback,
allowing actual end-to-end recovery detection. The checker requires HTTP 200,
`X-MusicBox-Ingress: tunnel`, and readiness JSON with `status: ready`.

The timer runs about 20 seconds after each check finishes. Three consecutive bad
checks switch to the origin; three good checks restore the tunnel. Normal failure
detection takes roughly a minute, plus API/DNS/edge propagation. It is **not
instantaneous failover**. The controlled drill invoked the same automatic checker
more frequently to exercise its thresholds without a long test outage; that
shorter drill duration is not the normal failover SLA.

The host resolver initially negatively cached the new health hostname. The checker
uses Cloudflare DNS-over-HTTPS only on a DNS-resolution error, then connects with
normal TLS certificate and hostname verification. It does not treat general HTTP,
TLS, or connectivity failures as DNS problems, and never disables TLS validation.

The service runs as a sandboxed dynamic user. Its API token is DNS-write only for
the existing zone and restricted to requests from this server's current public IP.
Cloudflare grants DNS write at zone granularity; the program further pins the
**single record ID and hostname** and accepts only the known Tunnel CNAME or known
origin A target. It refuses unexpected operator changes and always writes
`proxied: true`. No Tunnel-management permission is needed by the monitor.
Root-only credentials/configuration are passed through systemd `LoadCredential`.
Temporary token copies were removed after installation.

The checker has no dependency ordering on cloudflared: it must still run when the
connector is stopped, fails to start, or is stuck starting. State is written
atomically under `/var/lib/musicbox-tunnel-failover/` with a lock.

This mechanism needs the server and Cloudflare API to remain available. It does not
recover a dead VM, a dead application shared by both paths, or a Cloudflare-wide
outage. DNS/edge propagation can temporarily mix tunnel/fallback observations;
both routes retain the same application authentication.

## Verification completed

- Eight failover regression tests passed: consecutive-failure/recovery thresholds,
  no flapping on intermittent probes, record/hostname/target validation, refusal to
  unproxy or overwrite unrelated DNS, correct readiness shape and route marker.
- Two live connector-stop drills passed, the second after installing the network
  filter. With cloudflared stopped, three failed automatic checks changed DNS to
  the origin and the public fallback returned 200. Restarting it and obtaining
  three healthy checks restored the Tunnel CNAME and public tunnel HTTP 200.
- Twelve final public HTTPS checks passed through the tunnel. Private account,
  status, admin, Amazon proxy, and YouTube login paths returned 401; sensitive files
  returned 404; login and readiness returned 200.
- Public HTTP login redirected to HTTPS; the health hostname's private API returned
  404. Another container, bypassing its outbound proxy, received 403 from the
  private connector port even with a forged Cloudflare IP header.
- Direct connections to this server's public IPv4 on 80 and 443 timed out; the
  nftables drop counter incremented during those checks. SSH listeners remained
  unchanged on IPv4 and IPv6.
- The network filter's configuration passed `nft --check` and reloaded idempotently.
  Caddy validation and `git diff --check` passed. Application/browser remain healthy.
- Tunnel connector, recurring failover timer, and network filter are enabled and
  active. Final state is **tunnel / healthy**.

## Operations and rollback

Useful commands:

```bash
sudo systemctl status cloudflared musicbox-tunnel-failover.timer musicbox-origin-filter
sudo journalctl -u musicbox-tunnel-failover.service -n 20
sudo cat /var/lib/musicbox-tunnel-failover/state.json
sudo nft list table inet musicbox_origin
```

Backup of the prior environment/base Caddy configuration:
`/home/ubuntu/musicbox-deployment-backups/tunnel-20261113T043934Z/`.
Evidence, DNS snapshots, validation and drill results:
`/home/ubuntu/security-reports/2026-11-13-tunnel-migration/`.

For a rollback, stop the failover timer first so it cannot undo the operator's DNS
choice. Restore the primary record to the known **proxied** A target while the
protected fallback is running; then restore deployment configuration as needed.
Keep the network filter unless intentionally reopening direct origin access.
Removing its systemd unit does not automatically delete its active nftables table;
explicit deletion of that one table is required if rolling back the filter itself.
Do not flush the system firewall. Never alter the production hostname for this
rollback, and do not delete the user's existing tunnel.

The real origin IP remains known to Cloudflare and may remain in historical DNS
records. This deployment hides the live origin from normal public DNS responses
and blocks direct web access; it does not erase previously exposed IP history.
