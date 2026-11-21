# Persistent Alexa SDK runtime

The optional `alexa-endpoint` service loads the existing Python ASK handlers once
at startup. The Alexa skill retains its original Lambda endpoint and DynamoDB
state. Both Echos keep their existing settings, saved playlists and playback
records, including devices currently offline. No account or state migration,
new AWS resource, periodic invocation or synthetic playback is required.

Normal successful Lambda requests import neither the ASK SDK nor boto3/botocore. A small relay:

1. Authorizes the current Alexa envelope through the existing backend identity
   endpoint, including fresh Amazon profile proof and current account policy.
2. Consistently reads only that request's existing skill/user/device/binding key
   through bounded SigV4-signed DynamoDB HTTP calls using the Lambda execution
   role. The signer is checked against AWS botocore test vectors, including
   temporary credentials. AWS credentials stay inside Lambda.
3. Sends the envelope, this request's authorization response and attributes to
   the persistent SDK service, authenticated with a separate private HMAC secret.
4. Verifies the signed response and conditionally saves changed attributes to
   the same DynamoDB item before returning any AudioPlayer directive to Alexa.

The relay removes ASK SDK initialization from Lambda's normal cold path. Lambda
itself can still initialize after idle, and Amazon routing, stream resolution,
network calls and Echo buffering still take time. Measure first audio bytes and
real device callbacks; do not equate either with acoustic output.

## Boundaries and failure handling

The worker pins the account and skill IDs. It checks HMAC-SHA256 over the exact
body, request time and nonce, plus the event timestamp, identity key, session
application and authorization parameters. Responses have a separate signed
message domain. Requests are bounded to1MiB and persisted attributes to400KiB.
The relay follows no redirects and contacts only the configured HTTPS backend
origin. Invalid or missing secrets fail closed.

Only the authenticated Lambda relay can supply an authorization response. It is
installed into the SDK's request-local prefetch once for that exact envelope and
is always cleared, including on exceptions. This prevents a second authorization
call from consuming or cancelling the same armed control. Backend calls after
that still run existing authorization, binding and playback revision checks.

Worker persistence is request-local. No music queue or saved playlist is moved
into its SQLite file; SQLite stores a bounded replay ledger only. Per-device
locks serialize SDK work; different devices can proceed concurrently. Request
IDs and body hashes suppress duplicate dispatch for ten minutes across worker
restarts. Conflicting request-ID reuse fails. This provides at-most-once dispatch,
not exactly-once delivery: a crash can suppress one request, recovered by the
existing app watchdog or a new user command with a new Alexa request ID.

DynamoDB responses are size bounded and CRC32 checked when AWS supplies a checksum.
DynamoDB conditional writes fence concurrent whole-item saves. A stale write or
failed durable save never returns its playback directive. Unknown item fields
and DynamoDB numeric types are retained. Authorization denials use the existing
SDK error handler before any state read. After an uncertain remote dispatch,
the Lambda does not dispatch the same command again through a local fallback.
No raw body, profile proof, state URL, HMAC secret or AWS credential is logged.

## Deploy

1. Run existing backend and Lambda suites plus
   `python -m unittest discover -s alexa-endpoint/tests -v`. Build from the repo
   root with `docker build -f alexa-endpoint/Dockerfile` using the hashed lock.
2. Provision a private state directory owned by UID10001. Configure the worker's
   private mode0600 env file with the skill's existing four `MUSICBOX_*` account
   settings, `MUSICBOX_SKILL_ID`, and a new random48-byte URL-safe
   `MUSICBOX_RUNTIME_SECRET`. Never commit it to the public repository. Start the
   service using `docker-compose.alexa-endpoint.yml`; expose no public host port.
3. Add this route before the generic backend handle in the existing Caddy site:

   ```caddyfile
   handle /alexa/skill/relay/ {
       header Cache-Control "no-store"
       reverse_proxy alexa-skill:8000
   }
   ```

4. Validate/reload Caddy, verify worker readiness and confirm unsigned requests
   receive403. Deploy the reviewed Lambda runtime files including
   `runtime_relay.py`; add the same private `MUSICBOX_RUNTIME_SECRET` to that
   hosted skill's `.env`. Other skill copies without a secret retain normal SDK
   execution. Keep the Alexa manifest and all regional Lambda endpoints intact.
5. Test actual resume/seek/pause callbacks, rapid controls, paused seeking,
   continuity, worker restart, error handling, original state restoration and
   an idle interval. Confirm worker timing logs show dispatch and the successful
   Lambda path leaves `lambda_function._runtime` unset. Never report idle latency
   as fixed solely from unit tests or a warm request.

## Rollback

Remove `MUSICBOX_RUNTIME_SECRET` from the hosted skill's private `.env` and deploy
the reviewed default SDK code (or the previous hosted commit). The manifest and
DynamoDB table have not moved, so both devices retain their latest saved state.
Keep the worker until that deployment succeeds, then remove its route/container
if desired. Rotate both copies of the secret together for a new rollout.

The public runtime route accepts the private Lambda relay protocol. Amazon's
existing skill trigger/resource policy authenticates the original invocation;
the worker additionally pins the configured skill and account IDs.
