# Alexa security fixes — 2026-11-11

Current private-skill configuration uses a private `.env` beside `data.py`, copied from `lambda/.env.example`. `api_key.py` references below describe the earlier deployment and are superseded by the [current setup](PRIVATE-SKILL-ACCESS-2026-11-11.md).


Historical review/deployment record. For the current per-account token architecture and setup, see [Private skill access](PRIVATE-SKILL-ACCESS-2026-11-11.md).


Status: **deployed to the test backend and Music Box Multi skill**. See the [deployment and app audit](DEPLOYMENT-AUDIT-2026-11-11.md) for release identifiers, additional fixes, live verification, and remaining limits. This document follows the [implementation review](ALEXA-REVIEW-2026-11-11.md); that review records the pre-fix behavior.

## Changes

- Disabled voice changes to the backend URL. Authenticated requests ignore old saved URL overrides and use the administrator-configured HTTPS URL. URLs containing credentials, query strings, fragments, invalid ports, or whitespace are rejected. Backend GETs, pairing POSTs, and playback-event POSTs never follow redirects.
- Added `/api/alexa/identity/`, which requires a valid skill key and an active subject/device/skill binding and returns an opaque, non-cacheable binding version. Incomplete Alexa identity headers cannot fall back to the owner account or rely on a browser session.
- Added database schema version 9: each Echo binding has a random generation. Re-pairing rotates it, including re-pairing to the same account. Existing bindings survive migration.
- The multi-user Lambda verifies the current binding before loading any saved personal state. DynamoDB partition keys include the configured skill namespace, Alexa user, Echo device, and binding version. A different device or binding does not load the previous account's state or overwrite its DynamoDB item.
- Binding versions also incorporate the account security version, so account proof revocation changes the accessible Lambda state partition. In-flight requests retain their authorized version and cannot mint a stream capability at a newer version after revocation.
- Paired Echo stream capabilities include the originating binding generation. Re-pairing or deleting that binding revokes its capabilities without revoking a different Echo's streams. The capability signing salt changed to invalidate pre-fix URLs that lacked this boundary.
- Playback tokens identify their binding. Late callbacks from a previous binding are rejected before they can read or save the new user's state. Queue reconciliation understands the suffix while retaining queue-decision validation.
- Successful pairing clears the Echo's previous stream and buffered queue. Pairing itself does not load or save the previous account's Lambda attributes.
- Multi-user resume and seek fetch newly authorized stream URLs instead of replaying expired or previous-account capabilities. Explicit authorization failures never fall back to cached audio.
- Stopped logging the full playback-info dictionary, which contained signed stream URLs.
- Added the multi-user skill package to the regression workflow's path filters.

The existing custom skill, account-page pairing workflow, and `alexapy` command transport remain the architecture. No OAuth account-linking or casting integration was added.

## Verification

- All 51 Lambda regression tests passed. The new isolated real-SDK regression includes 14 security cases exercising actual Lambda dispatch, distinct DynamoDB partitions, metadata isolation after reassignment, rejected authorization before persistence access, current and stale callbacks, safe pairing, disabled URL overrides, refreshed stream capabilities, and redirect/configuration restrictions.
- 253 backend tests passed across 16 relevant files, plus five reported subtests. These cover the existing identity, authentication, account, provider, durable playback, purge, and queue contracts together with 13 new Alexa security tests and a binding-suffix queue-reconciliation test.
- The schema-upgrade test recreates a version-8 binding table in a temporary database, upgrades it, verifies that the binding survives, and repeats initialization without rotating its generation again.
- Python compilation and `git diff --check` passed.
- No live account, playback stream, Amazon session, or deployed configuration was modified. Tests used temporary databases, synthetic credentials, and mocked provider/HTTP calls.
- Wider backend regression logs: `/tmp/musicbox-security-fix-results-20261111/`. The security and queue files were rerun after their final additions: 13 and 40 tests passed respectively.

## Rollout requirements

1. Back up the backend database and current private Alexa-hosted source before deployment. Verify the target explicitly: **Music Box Multi**, `amzn1.ask.skill.8cd77e6f-497a-40db-8057-d6aefcf8fc09`. The workspace's default `ask-resources.json` still points to a different skill and must not be used as an assumed deployment target.
2. Deploy the backend changes first so schema 9 and the identity endpoint are available. Preserve the existing secrets and provider credentials.
3. Deploy the updated Lambda source, including the new `skill_security.py`, to the new skill. Retain that hosted repository's private `api_key.py` and runtime-compatible requirements. Its configured HTTPS origin, application ID, and namespace must identify the intended backend and skill.
4. Existing pairings remain valid. Old stream URLs are retired, and the new skill starts fresh device/binding-specific playback records. Old Lambda queues, resume positions, and saved playlist aliases are deliberately not copied from the ambiguous shared records. Start a new selection after deployment; old callbacks are rejected.
5. Verify two Echos on one Amazon account paired to different Music Box users, account reassignment while playing, unlink/revocation, delayed callbacks, resume/seek after an old pause, and web queue edits. Physical Echo audio was not tested locally.

Separate operational findings from the review remain: configure the test backend's invocation name to `music box multi` before testing its web controls, investigate the en-GB model-fetch discrepancy, and establish a dedicated ASK deployment target. The loop-setting bug is functional rather than a security boundary and is outside this security patch. Hosted Python 3.8 dependency advisories documented in `docs/SECURITY-ROLLOUT.md` require a separate runtime/dependency migration.
