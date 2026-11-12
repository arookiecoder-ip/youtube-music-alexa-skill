# Playback isolation and startup recovery

The shared backend previously kept armed songs, pending dispatch timers, outstanding
triggers and click sequence numbers in process-wide dictionaries. In particular,
`/armed_play/` without a serial selected the latest arm across accounts. These
stores and their locks now resolve through the authenticated playback identity.
Disconnect and account purge cancel pending timers and discard those stores.

Dispatch timers and startup watchdogs retain the intent timestamp and output lease
that created them. A newer pause, replay or output handoff retires the old work,
including when the video ID is unchanged. Queue-row playback stages its selection
before scheduling its command, and explicitly sets the pending flag on replays.
The watchdog captures its intent before the network send rather than after it.

Device confirmation timeouts and Alexa service failures preserve the current song
and queue. They stop pending playback and report an error. Automatic failover is
reserved for songs already identified as unavailable by the backend.

Streaming bodies iterate and close inside a separate context for their account.
They no longer leave a user's identity bound in the worker consuming the response.
An old Echo stream also stops when its play intent or output lease changes, so a
late first byte cannot confirm a paused or replayed attempt.

This reduces unwanted commands and interference; it does not establish a measured
improvement in Amazon's network latency or YouTube cold-start time. No Alexa
credentials or Lambda configuration changes are required.

## Verification

Automated coverage includes per-account arm consumption without a serial, matching
serials across accounts, independent click sequence numbers and trigger markers,
superseded watchdogs, same-song pause before stream delivery, interleaved stream
contexts, cleanup on another thread, timeout queue preservation, and failover only
for confirmed unavailable media. Existing queue boundary, transport, streaming,
account and browser playback tests are also run.

## Cross-check with the online Echo

1. Use two accounts simultaneously with different songs and queues. Verify each
   private skill consumes only its account's selected song and device.
2. Rapidly select several songs, including the same song twice. Confirm only the
   final choice starts and the UI settles on it.
3. Pause during startup, then pause/resume several times. Confirm playback stays
   paused after the last pause and resumes the current song at its saved position.
4. Switch browser/phone/Echo outputs during startup and during playback. Confirm
   the previous output stops and late callbacks do not reclaim it.
5. Test next/previous, duplicate songs, shuffle and repeat near track boundaries.
6. Disconnect the Echo or interrupt the network while starting a song. Confirm
   an error appears and the queue is preserved; reconnect and retry that song.
7. Test a genuinely unavailable song and verify bounded failover to the next song.
8. Turn Alexa access off, rotate/revoke the token, restrict the user and unlink
   Amazon separately. Verify old requests fail and other accounts remain usable.
9. Compare cached and uncached startup times and check that canceled playback
   does not start duplicate downloads or keep retrying in the background.

No Android compilation is performed on the server. These changes are server-side;
the original single-user deployment and GitHub master are outside their scope.

## Recorded checks

- Isolated full suite: 92 Python files (1,586 cases) and 30 JavaScript files.
  The first full run found one lock-contract violation in timeout notification;
  notification now occurs after releasing the playback lock. All 19 affected
  lock, intent-fencing and identity tests passed after the correction.
- Download/dispatch regression rerun: 55 tests plus 3 subtests passed.
- Browser collection playback and shared feedback: 14 tests passed.
- Stream route regressions: 12 tests passed, including same-song pause fencing.
- Test backend image built successfully. No account schema changes or credential
  rotations are part of this update.

Physical Echo playback and startup timing still require the cross-checks above;
synthetic tests do not establish real-device behavior or latency.
