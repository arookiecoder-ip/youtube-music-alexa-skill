# Fix paced whole-file media delivery

## Measured bottleneck

The user reported approximately 3-second startup on the original single-user
server and 4.5 seconds on the multi-user server. Removing redundant account
lookups and a serial metadata request did not by itself establish parity.

A read-only media probe found that the relay's whole-file Googlevideo request
could deliver audio very slowly. One sample transferred 128 KiB in 12.8 seconds
of body time (about 10 KiB/s), below the approximate bitrate of itag 140 audio.

A controlled comparison used the same resolved media URL and the same first
64 KiB:

| Upstream request | Open time | Body time | Bytes |
| --- | ---: | ---: | ---: |
| Whole-file GET | 13 ms | 1,670 ms | 65,536 |
| Bounded HTTP range | 9 ms | 3 ms | 65,536 |

The roughly 1.7-second difference in obtaining this initial buffer is a plausible
contributor to the reported 1.5-second audible startup gap. This measures media
transport, not the complete Echo startup sequence. The observed response pacing
varied between samples; it is not a fixed speed for every song or CDN edge.

## Implementation

The relay now requests sequential upstream ranges of at most 512 KiB, rather
than a paced whole-file body. It still returns the original public contract:

- Full client GET: HTTP 200 with the complete representation's Content-Length.
- Client range: HTTP 206 with the exact requested Content-Range and length.
- HEAD and If-Range semantics are preserved.
- Every upstream range is checked for the exact bounds, length and media type.
- Normal range boundaries do not spend the two-attempt failure-recovery budget
  or re-resolve the signed URL. Genuine disconnects and expired URLs still use
  bounded, exact-byte recovery and representation validation.
- Client cancellation closes its current socket and releases its original stream
  slot. The bytes continue to use the requesting account's current-track cache.

There is no separate pre-download job or shared audio file. Data is fetched as
an existing client stream advances. One client's ranges never become another
account's queue or current-track state.

## Verification

The revised live relay delivered the complete 3,678,072-byte test file: 15 ms to
open and 129 ms to iterate its body. No Echo playback command was sent for these
transport probes. Only counts, durations and statuses were retained; signed
URLs, cookies and private audio were not published.

All 24 focused backend test files passed (307 reported cases including subtests).
The media relay file includes 39 cases. New coverage checks full and partial
responses spanning several upstream segments, exact byte continuity, current
cache contents, no budget consumption at normal boundaries, failures within a
segment, expiry at a normal boundary, and rejection when an edge ignores a
bounded range. The startup client remains the previously verified version with
65 passing Lambda tests; this final transport correction is backend-only.

The original single-user deployment remains unchanged. Confirm the audible
result on the same Echo with several song selections and a full untouched song;
synthetic and transport checks alone do not prove the complete latency gap is
eliminated.
