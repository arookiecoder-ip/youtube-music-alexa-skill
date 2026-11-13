# Alexa stream recovery and startup latency

## Observations

Recent logs showed HTTP responses ending short of their advertised length
immediately after track changes and pause/resume commands. The previous intent
check ran for every audio chunk, so even a same-song transport change terminated
an already-started response. Recent logs did not include an explicit Alexa
PlaybackFailed event proving the cause of every reported untouched-song stop.

The relay also had no recovery after an upstream read failed or ended early.
Such a failure happened after HTTP headers were sent and broke the player's
connection directly.

The original single-user command sender and the multi-user sender both use the
same Alexa custom-command mechanism. The multi-user skill additionally verifies
account binding before its handler and authenticates subsequent backend calls.
Previously every one of those HTTP calls independently fetched the Amazon email
profile, adding repeated external network round trips to the critical path.

## Changes

- Snapshot stream intent and output ownership before opening the upstream.
  Reject a superseded first byte, but keep a same-song response open after it
  starts. Output handoffs and superseded tracks still cancel their old streams.
- Recover upstream disconnects and premature EOF from the exact unread offset,
  with at most two reconnect attempts per response. The original client range
  and Content-Length remain unchanged. Recovery verifies the representation,
  returned range, media type and length before accepting more bytes.
- Share a positive Amazon profile proof only among backend calls carrying the
  same Alexa request ID, Music Box user/token, linked-account generation, Amazon
  subject/device, endpoint and profile-token hash. The bounded 512-entry store
  has a fixed 10-second deadline and no sliding expiry. Missing request IDs use
  the previous fresh-verification behavior. Failures are never cached.
- Always recheck live token validity, account eligibility and linked Amazon
  generation before and after every request, including proof-cache hits.
- Include authentication time in request-duration logs and redact stream
  capabilities and other credentials from logged query parameters.
- Update the private skill client and downloadable package to send the request
  ID. Existing private .env values require no changes.

This removes redundant profile lookups within a request. Actual improvement in
Echo startup time must still be measured on the device; Amazon command delivery,
Lambda startup, and uncached YouTube resolution remain external costs.

## Verification and device checks

Automated checks cover exact-byte reconnection, partial-read exceptions, bounded
retries, original client ranges, changed representations, malformed recovery
responses, cache continuity, socket/slot cleanup, stale first bytes, pauses during
upstream opening, same-song response continuity, per-user isolation, immediate
local revocation, request separation, proof expiry and credential redaction.
The Lambda tests check that one envelope forwards the same request ID throughout.

On the online Echo, cross-check:

1. Play an untouched song through its end, then allow the next track to start.
2. Pause/resume and seek repeatedly; confirm no resets, loops or stale UI state.
3. Change songs rapidly and switch outputs while a song is starting.
4. Compare cold and repeated startup times for the same songs against the
   single-user deployment. Record command time and first audible audio.
5. Confirm recovery after a brief upstream interruption, with no repeated audio.
6. Disable Alexa access, revoke/rotate a token, restrict a user and unlink Amazon
   independently. The old configuration must fail even during a cached proof.
7. Run two accounts concurrently and verify independent songs, queues and devices.

The original single-user skill/server is used only as a read-only reference.
Public changes belong to feat/multi-user. Android sources are unchanged and no
Android compilation runs on the server.

For Alexa-hosted skills, Amazon documents that pushing the skill's private
CodeCommit development deployment deploys its Lambda code; this is independent
of the public GitHub branch. See [Amazon's hosted deployment instructions](https://developer.amazon.com/en-US/docs/alexa/hosted-skills/alexa-hosted-skills-ask-cli.html).

Recorded verification: 22 focused backend files passed (294 reported cases, including subtests); 58 Lambda tests passed. Deployed backend readiness returned HTTP 200 and source hashes matched all three changed runtime modules. Physical Echo playback and startup measurements remain to be checked.
