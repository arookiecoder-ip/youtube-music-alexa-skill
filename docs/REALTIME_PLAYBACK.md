# Playback event gateway

`python realtime_gateway.py` is the image entrypoint. A single process owns the existing Flask playback state, a loopback-only Waitress listener (16 workers) and an aiohttp listener on port 5000. aiohttp streams normal HTTP requests/responses, including audio ranges, to Waitress and handles `/api/app/events/` WebSockets asynchronously. Idle event connections consume no Waitress worker. Do not launch multiple processes/replicas until playback authority, ownership, revisions and notifications are moved to shared storage.

The channel is read-only and owner-session authenticated. It rejects foreign browser origins and unauthorized subscriptions, caps concurrent connections at 64, bounds outgoing sends, coalesces change signals and revalidates sessions at least every 15 seconds. Revocation wakes subscriptions immediately. Snapshot/auth requests use a separate bounded HTTP pool. Proxy clients never share cookies; upstream redirects, response cookies, compression and range headers are preserved. The original client address is passed in a private overwritten header for Flask rate limits; only loopback requests may use that header.

Frames contain `type=playback_state`, a process-random `event_epoch`, increasing `event_revision`, and `state`. State contains the same markers plus playback/output revisions, progress anchor, queue version, presence revision and retained error ID. A changed output token always gets the full queue even when its version is unchanged. A periodic snapshot repairs missing callbacks and checks authorization; it cannot invent receiver confirmation. Playback commands remain on the existing token/command-ID guarded HTTP API.

All presence clients now share four waiting slots and a maximum two-second wait. Capacity exhaustion returns `presence_retry_ms=750`, which clients must respect. Event-capable native clients use short presence heartbeats eight seconds apart and wake on command/device events. Phone playback leases remain 12 seconds and their existing renewal loop is not slowed.

Liked songs use one single-flight owner-account cache with a 60-second TTL. Stale responses refresh in the background; upstream errors preserve cached favourites and throttle retries. Successful likes update the cache immediately. Account changes and in-flight refresh generations are fenced; cold failures return retryable 503 rather than a false empty favourite list.

## Validation and rollout

GitHub Actions runs the isolated Python/JavaScript, SDK and browser regressions, then builds the Docker image and checks actual gateway startup, HTTP forwarding and unauthorized event access. No image is built on the production server. Tests cover real WebSockets, notification storms, recovery snapshots, logout, origin/access limits, command rejection, audio ranges, separate cookie forwarding, queue deltas, transfers and cache concurrency.

The external container port stays 5000. Existing Caddy reverse_proxy supports the upgrade without a new public port. Deploy only the CI-verified image in a separate authorized deployment. Existing clients retain HTTP fallback. Monitor endpoint latency, worker queue warnings, stream connection count, reconnects, 429s and lease renewals. Exercise real receiver feedback and background Android behavior before claiming device-level latency or battery improvements.
