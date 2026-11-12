# Reduce remaining Alexa startup round trips

## Baseline

The user reported about 3 seconds from a play click to audible audio on the
single-user deployment, compared with about 4.5 seconds on the multi-user one.
Recent multi-user server logs showed 3.3–3.5 seconds from warm play requests to
first audio delivery, with approximately 0.3 seconds spent opening the audio
upstream. Server first-byte timing is not the same as audible Echo startup.

The original and multi-user command senders both use the same Amazon custom
command, online-device checks and cache TTLs. The multi-user skill had an extra
serial API call: verify identity, consume the armed song, then request that song's
metadata and stream URL. The preceding fix removed redundant profile checks
inside those calls, but did not remove this additional round trip.

## Changes

The authenticated armed-song response can now include a startup payload containing
its normalized metadata, bounded queue window, exact selected queue position and
stream capability. The skill consumes that payload once within its request and
avoids a second song lookup. This makes the optimized direct-play path two API
calls: identity verification and armed-song startup, followed by the Echo's audio
fetch. No metadata network lookup is needed when the selected queue entry already
has its title. Older clients/servers and incomplete or malformed payloads retain
the existing fallback.

Queue-sync control arms and resume peeks keep their previous behavior. A repeated
song retains the selected occurrence, including inside a long queue's window.
The startup payload does not persist across requests or users.

Amazon profile requests also reuse a bounded HTTPS connection pool through separate
thread-local sessions. Bearer headers remain request-local and cookies are cleared
before and after each call. Token, account, Amazon-link and permission checks remain
live. This does not extend the existing request-scoped profile-proof cache.

A read-only transport probe without credentials returned HTTP 401 as expected.
Fresh warm connections took 222–230 ms in that sample; requests over an established
connection took 178–188 ms. These figures demonstrate connection setup overhead;
they are not a measurement of authenticated startup or a promise of a particular
Echo latency.

Backend logs now include command dispatch duration and intent-to-first-audio-byte
timing, to separate remote command delivery from stream opening on the next play.

## Verification

The focused backend suite passed all 24 files (302 reported cases, including
subtests). All 65 Lambda tests passed, including an isolated real-SDK case that
emits an AudioPlayer.Play directive from the bundled response without making a
stream_video request. The SDK case also checks a selected duplicate's token index.
The final startup payload regression cases were rerun after queue-position support
was added.

Additional checks cover legacy fallback, malformed payloads, different requested
videos, separate envelopes, one-time consumption, peeks, control arms, long queue
windows, per-worker session isolation, cookie clearing after errors, account
revocation, streaming recovery and transport races.

Deploy the updated Lambda player and model together. The private .env values are
unchanged. The original single-user deployment is a read-only reference; public
changes belong to feat/multi-user. No Android code or build is involved.

## Physical check still needed

Use the same online Echo and compare several repeated song selections after the
skill deployment finishes. Record click-to-audible time, then compare command and
first-byte log timings. The 3-second versus 4.5-second baseline has not yet been
re-measured on the device after this update. Do not claim the gap is eliminated
from synthetic tests alone.
