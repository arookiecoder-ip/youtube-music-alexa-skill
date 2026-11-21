# Persistent Alexa HTTPS endpoint

The optional `alexa-endpoint` service loads the existing Python ASK handlers once
at startup. It removes managed Lambda initialization from subsequent requests;
Amazon command routing, authorization, stream resolution and Echo buffering still
contribute latency. No synthetic playback or periodic skill invocation is used.

Only explicitly configured skills move. Other users' hosted skill copies keep
using DynamoDB. Never share this service's account token across accounts.

## Request and state boundaries

The HTTPS endpoint verifies `Signature-256` over the exact request body, validates
the signing certificate chain against trusted roots, checks its Amazon SAN and
validity, rejects unsafe certificate URLs and redirects, checks the 150-second
request timestamp window, and pins both context and session application IDs.
Every dispatched playback request still undergoes the existing account/token/
Amazon profile authorization. No cached authorization result crosses requests.

Each process has one skill ID and account ID. SQLite rows additionally retain the
existing skill/user/device/binding-derived persistence key. The state volume is
private, writable by UID10001, and independent of the main application's queue
DB. Missing migration metadata makes readiness fail, including after volume loss.
Never create an empty readiness marker to recover an existing skill.

Requests from the same Amazon user/device are serialized with a bounded wait;
different actors can proceed concurrently. A durable request-ID/body-hash ledger
suppresses duplicate dispatch across restarts for ten minutes. Reusing an ID with
a different body fails. A crash after claiming a request may suppress that request;
this is at-most-once dispatch, not an exactly-once delivery guarantee. Existing
control watchdogs and a new user action recover with a new request ID.

## Deployment and migration

1. Run existing server/Lambda regressions and
   `python -m unittest discover -s alexa-endpoint/tests -v` using the endpoint lock.
   Build from the repository root with `docker build -f alexa-endpoint/Dockerfile`.
2. Provision a private state directory owned by UID10001. Put the existing skill's
   four `MUSICBOX_*` account settings in a private mode0600 env file. Add
   `MUSICBOX_SKILL_ID`, `MUSICBOX_MIGRATION_ID`, a random 48-byte URL-safe
   `MUSICBOX_MIGRATION_TOKEN`, and `MUSICBOX_MIGRATION_EXPIRES` (Unix time,
   less than one hour). Start the optional Compose service and add the documented
   Caddy route. Do not expose its port directly. Readiness must initially be503.
3. Back up the actual development manifest and each regional endpoint privately.
   Save the selected track, full queue, output token, position and paused/playing
   state. Quiesce controls and confirm the Echo is stopped before scanning state.
4. Deploy `lambda/state_migration.py` and the reviewed runtime interceptor changes
   to the existing hosted skill, retaining its private `.env`. Deploy the backend
   instruction support. No AWS credentials leave the existing Lambda.
5. Operator creates `/data/alexa-skill-export.json` in the backend, mode0600, with
   `account_id`, `skill_id`, `transfer_id`, `token`, `expires`, and `url` pointing
   to the same backend origin's `/alexa/skill/migration/`. For Alexa-hosted roles,
   also set `mode: device` and `expected_devices` to the full number of Echos
   being retained. Hosted roles deny Scan; device mode consistently reads only
   the current authorized skill/user/device/binding key with GetItem. Invoke
   each retained Echo once. Offline devices must come online before cutover;
   do not lower the count unless the operator confirms a device is retired.
   Only an authenticated,
   matching LaunchRequest receives the instruction. Invoke one silent launch.
   Remove the instruction immediately after the export completes.
6. The old Lambda consistently scans its own table and posts one bounded complete
   snapshot. Partial scans, excess size/time, duplicates/conflicts or invalid rows
   cannot activate a partial import. Verify row count and snapshot digest. Imported
   unknown/legacy rows remain archived; current device binding keys are unchanged.
   A scan is not a transactional snapshot across concurrent writers: quiescence
   in step3 is mandatory. Keep the source table intact.
7. Disable migration by removing the migration env values and recreate the worker
   with the same state directory. Verify state survives the restart, unsigned public
   requests fail and readiness passes. Switch all existing development regional
   endpoints to `https://YOUR_HOST/alexa/skill/`, trusted certificate. Test a real
   Amazon-signed request, resume, seek, pause, continuity, rapid controls, error
   handling and state restoration. Roll back immediately if verification fails.
8. Restore the user's exact queue/selection/cursor/output and original playing
   state, revoke the temporary verification session, and retain private backups.
   Live-stage changes, when applicable, require Amazon's publishing process.

## Rollback

Keep the original hosted Lambda and manifest. Before any new persistent-service
state is written, restore the manifest directly. After new state is written,
quiesce the Echo and controls first; otherwise a direct manifest rollback loses
new saved playlist/offset changes. Enable a fresh short-lived migration token on
the worker, configure the same scoped operator instruction with `action: restore`,
restore the old Lambda manifest, and launch once while controls remain quiescent.
The hosted migration handler fetches the authenticated HTTPS snapshot, validates
all rows before writing, then upserts only its own configured DynamoDB table.
An interrupted restore is retried idempotently; do not resume playback until all
rows are acknowledged. It does not delete archived DynamoDB rows. Remove the
instruction and disable migration again. Restore the original playback state and
verify it with a fresh durable read. Never log the instruction, state payload,
account token, signature, certificate credentials or federated AWS log links.

The migration interface returns404 unless explicitly enabled with an unexpired
secret. It is not a general public state management API. Size bounds deliberately
fail closed; large tables require a separately reviewed batched migration.
