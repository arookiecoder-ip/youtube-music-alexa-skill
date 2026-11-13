# Endpoint access audit — 2026-11-12

## Confirmed finding: Amazon login proxy bypassed website authentication

The Caddy `/alexa/proxy/*` handler forwarded traffic directly to port 5001,
bypassing Flask's authentication middleware. Flask also explicitly exempted
that prefix. The proxy represents a shared listener serving one account's
in-progress Amazon login. Provider-library flow credentials are not a substitute
for checking the website account that owns that attempt.

Implemented and deployed:

- Caddy forward authentication requires a live website login, enabled Amazon
  access, and an active proxy attempt belonging to that account.
- The backend requires the current attempt's internal authorization header when
  running behind Caddy, checks expiry, and removes the header before forwarding.
- Direct development mode binds the listener to loopback. Local processes remain
  inside that development trust boundary.
- Removed the Flask public-prefix exception and disabled caching on the tunnel
  and authorization response.
- Updated Caddyfile, Caddyfile.example, and Caddyfile.internal. The local Caddyfile
  has Git's skip-worktree flag. The committed default configuration includes
  the same protection while preserving the local deployment configuration.

Deployed on 2026-11-12 after explicit user authorization. Rebuilt and recreated
only the `ytmusic` service in the existing `musicbox-test` Compose project, using
its existing `.env.testalexa` environment, and reloaded Caddy. The resolved service
environment matched the previous container. Deployed server.py/alexa_remote.py
hashes match the reviewed workspace source. The container reports healthy.

Post-deployment HTTPS checks through both the local origin and the public hostname
returned 200 for `/health/ready` and 401 for `/alexa/proxy/` and `/api/account`.
Nested proxy paths and a POST with a forged internal authorization header also
returned 401. `/login/` returned 200. No real Amazon login was initiated.

## Review and validation scope

Reviewed the registered player API inventory and central auth hooks; account and
administrator blueprint guards; Alexa identity, scoped stream/audio capabilities,
Jam permissions, diagnostics, static-file delivery, browser-login control-token
authentication, and Compose port/network exposure. The network egress gateway is
private infrastructure; its unauthenticated internal mode relies on Docker network
isolation. Only Caddy publishes host ports in the inspected application stack.

The new anonymous endpoint sweep replaces private API handlers with sentinels and
verifies anonymous requests never reach them. It covers 110 route/method cases.
Account/admin blueprints have separate guards and existing security tests rather
than being included in that sentinel sweep. Website document routes such as
`/history` render through their own login guard; `/history/` is the private API.

Tests:

- Focused authentication, Alexa isolation, account/resource boundaries, account
  policy inventory, endpoint exposure, and proxy guards: **44 passed; 110 subtests passed**.
- Broader status, cookie upload, YouTube session, guided security, multiuser,
  and self-service selection: **164 passed; 2 failed** due to a shared test stub
  missing `call_with_one_refresh`. The affected YouTube session file passed
  **39/39** when run independently. No production change was made for this test
  isolation issue.
- Caddy configuration validation passed. `git diff --check` passed.

Read-only HTTPS checks against the running local origin, using its configured
hostname and bypassing outbound proxies:

| Path | Anonymous status |
| --- | --- |
| `/api/profile_status/` | 401 |
| `/api/status/`, `/api/status/logs` | 401 |
| `/alexa/devices/`, `/alexa/now_playing/` | 401 |
| `/youtube-login/`, `/api/youtube/browser-session/surface` | 401 |
| `/api/account`, `/admin/api/users/` | 401 |
| `/get_stream/`, `/proxy/`, `/audio/` | 401 |
| `/history/`, `/api/library/` | 401 |
| `/.env`, `/cookies.txt`, `/.git/config` | 404 |
| `/alexa/proxy/` | 502 before deployment; 401 after deployment |

## Limits and remaining work

No additional anonymous private API handler access was found in this scope. This
is not proof that every authenticated data flow is free of defects. The route
sweep uses stubbed collaborators, and the origin checks do not exercise a real
Amazon login, the public CDN, or every authenticated tenant/role combination.
Public login/recovery pages, policy pages, static assets, minimal health checks,
and credential-bearing invitation/Jam entry points are intentionally reachable.

An exposed route alone does not establish that credentials were stolen. Historical
abuse and credential compromise were not determined; access-log retention and
provider account-session history need separate incident review. No credentials
were printed, rotated, or revoked during this audit.
