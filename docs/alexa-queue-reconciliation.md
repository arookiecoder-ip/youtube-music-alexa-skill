# Alexa queue and resume reconciliation

Deploy the backend first, then the Lambda code and its normal ASK dependencies. No Android update or APK is needed. Older skills retain the original `/next_track/` response. An updated skill also understands an older server; failed live lookups never fall back to deleted songs.

The updated skill requests protocol 2. Its decision binds the current playback token, output epoch/owner, user intent and queue occurrences/order. It validates the decision again after resolving audio. Each replacement play has a unique token, including same-song resumes. Stop/finish/failure callbacks from an older token cannot alter the new playback. Queue metadata refreshes and volume polls do not cause additional queue replacement invocations.

After an enqueue decision, actual queue edits coalesce into one guarded app-selection invocation. The skill checks Echo's context token and sends `REPLACE_ENQUEUED`, preserving the current stream, offset and metadata. Removing all successors sends `ClearQueue(CLEAR_ENQUEUED)`. The production ASK SDK serialization test verifies that replacement does not include `expectedPreviousToken`, which Amazon allows only with `ENQUEUE`.

If a previously buffered song begins during the final delivery race, its decision is checked against the live queue. The backend stages at most one correction through the existing dispatcher. That correction is fenced against a later pause, selection or output switch. The started handler restores its prior state when a callback is rejected. Natural finish followed by started remains valid, and duplicate songs resolve to the authoritative queue occurrence.

Radio continuation remains available at the live tail. It appends at most 25 recommendations only if the queue and playback ownership still match the state captured before the lookup. A late recommendation response cannot replace a user edit or a phone queue.

Resume retains the current cached stream URL while preparing the next song. Actual Echo activity, rather than the persisted `in_playback_session` flag, determines whether an unarmed voice resume is a no-op. A fresh server resume arm overrides that guard. Control lookups have a three-second total timeout and no implicit GET retries. `[control-timing]` logs separate those lookups from Amazon's command delivery. Live audio pipe reads return available bytes without waiting to fill a 64 KiB block; `[stream-timing]` logs first-byte latency. Cache completion, truncation checks and range delivery remain intact.

## Limits

A custom skill cannot retract audio already consumed by Echo. `REPLACE_ENQUEUED` has no atomic previous-token check, so an edit at the exact boundary can briefly start the old song before correction. The correction depends on delivery of the started callback. Decisions are bounded to the latest 32 records in this server process; a backend restart loses their history and retains legacy callback handling. Multiple backend workers require shared ownership/decision storage; the existing architecture uses process-local playback state.

Cached audio avoids extraction/download latency. It does not eliminate Amazon's injected skill invocation, Lambda startup, HTTPS connection or Echo buffering. Native provider transport remains unchanged because it previously caused custom-skill playback failures. Cold range requests and an already-running file download still wait for a complete validated file; this change does not claim progressive M4A range delivery or Spotify-like instant resume.

## Verification

Focused regression tests cover queue revisions, duplicates, metadata-only updates, pause and output changes, delayed/reordered callbacks, bounded correction, natural finish ordering, recommendation races, live-pipe first-byte delivery, cached resume and actual ASK directive serialization. GitHub Actions runs the relevant server and skill suites on pull requests and master updates.

The broad local suite has two pre-existing test-fixture problems: `test_pwa_manifest.py` provides an incomplete browser-session import stub, and seven cases in `test_youtube_browser_session.py` resolve `browser-auth/service.py` relative to the wrong directory. These are separate from the playback changes; they are not reported as passing. Physical Echo timing and the near-end delivery race still require device validation after deployment.
