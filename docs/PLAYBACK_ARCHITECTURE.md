# Playback boundaries and validation

The supported deployment is one owner with multiple phone/Echo devices and Jam
guests, served by one authoritative Flask process. Keep the existing SQLite,
credentials and audio-cache volumes. Do not increase WSGI process count or add
replicas: ownership leases, queue locks and provider caches are process-local.
Multiple threads in the same process are supported.

Docker playback uses the persistent YouTube streaming client described in
[STREAMING_AUDIO.md](STREAMING_AUDIO.md). It replaces per-song yt-dlp startup
and file buffering for playback; explicit downloads and the disabled-mode
legacy cache path remain available. Queue preparation warms media URLs instead
of audio files while streaming is enabled.

## Components

- `server.py`: HTTP composition and existing catalog/device integrations.
- `youtube_stream.py` and `youtube-stream/`: private persistent resolver,
  bounded disk-free HTTP relay, byte ranges and session recovery.
- `audio_jobs.py`: fixed background cache-preparation pool, coalesced by video ID.
  Four workers admit at most sixteen queued/running unique jobs. Speculative work
  occupies at most two workers and two queued/running jobs. Requested jobs take
  priority; requested playback promotes queued prefetch and replaces its stale
  generation-dependent callable. Saturation drops preparation rather than creating
  another thread. Existing request deadlines and retryable audio errors remain.
- `playback_output.py`: output ownership, revision, epoch and expiring phone lease.
- `playback_contract.py`: failed-command rollback restores content with a new
  revision. Revisions must never go backwards inside an output generation.
- `queue_commands.py`: bounded command replay protection; callers serialize
  check/apply/record under the shared queue lock.
- `playback_store.py`: coalescing SQLite writer for durable single-owner metadata.
  Restarts restore paused content without restoring active ownership leases.
- `templates/static/js/ui-state.js`: web snapshot acceptance and queue-mirror
  updates. Epoch/output/playback revision checks reject stale snapshots.
- `templates/static/js/sse.js`: historical filename for **finite HTTP polling**.
  Page lifecycle, selected device and play-intent guards reject responses crossing
  a transition. Polling uses 3 seconds for active foreground playback/transitions,
  10 seconds for foreground paused/background playing, and 30 seconds for
  background paused, with failure backoff capped at 60 seconds.

Cached files retain authenticated byte-range delivery and stable validators.
Cold streaming retains its separate budget for compatibility with Echo deadlines.
The preparation pool isolates background extraction from request execution; it
is not a separate audio-serving process or a CDN. Do not expose the cache directly
through Caddy without preserving authorization, range semantics and cache pinning.

The owner-only status snapshot includes aggregate `fetching.preparation` counters,
active/queued work and total/max job execution time. Keys, callables, paths and
results are not retained in diagnostics. Existing HTTP/first-byte/handoff metrics
remain available. Physical-device latency still needs measurement on a live system.

## Validation and rollout

Install server and Lambda dependencies plus pytest, then run:

```sh
python scripts/run-regressions.py --results /tmp/musicbox-tests
python -m unittest discover -s lambda/tests -p 'test_*.py' -q
```

The runner executes every top-level Python and JavaScript regression file in a
fresh process, because legacy tests install different integration stubs. Each
file has a 120-second limit; logs and Python JUnit XML are retained. Playwright
checks run separately in the existing status-dashboard Actions job.

Deploy this backend before the matching APK: older servers restore old revisions
on failed commands, which clients with revision ordering correctly reject. No
account, queue or audio-cache migration is needed. Android CI uploads a review APK
and all JVM results; the existing signed workflow remains available for release
updates. Unit tests do not establish physical Echo/phone handoff or production
capacity. This change does not introduce multi-user sessions or distributed state.
