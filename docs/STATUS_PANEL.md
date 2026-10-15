# Operational status panel

Open `/status` while signed in as the web owner. The profile menu also links to it.
Jam guests cannot access it. The JSON snapshot is at `/api/status`; existing owner
API-key authentication also works. Prefer the owner session over a key in a URL.
If a key is supplied in the page URL, JavaScript removes it from browser history
and uses it in memory for API headers. Responses are private and not cached.
The layout uses the main site’s black/charcoal/orange theme, a shared-capacity
bar, a cache-hit ring, connection tiles and compact mobile inventory cards.
No cookie values, API keys, account identifiers, absolute storage paths or query
strings are included in snapshots or exports.

## What it measures

- Requests: totals, status classes, active handlers, route-pattern counts and
  average/max latency; p50/p95/p99 use the latest 1,024 requests. The chart retains
  60 one-minute buckets. Latency ends at response headers, not the end of a stream.
  Status polling and static assets are excluded from request counters.
- Cache: hits/misses for audio-path lookups, audio responses with `X-Cache`, audio
  search and public/account YouTube Music clients. These are different layers,
  not a single end-to-end cache rate. Client-cache capacity and entry counts are
  also shown. A ready file means a recognized file extension, not validated media. The music
  capacity meter counts completed audio; partials and metadata are separate.
  Prefetched and protected bytes are subsets, never added twice. Completed audio
  stays until capacity/disk pressure requires LRU eviction across both pools.
  There is no idle expiry or separate warmup cap. Current tracks, active fetches
  and just-written audio are protected; the budget can temporarily be exceeded.
  Only abandoned partials have a cleanup timeout.
- Inventory: ready/partial audio, requested/warmup pools, pinned bytes, idle ages,
  the shared 5 GiB capacity meter and reserve limits. It retains the newest 200 audio rows,
  scans at most 20,000 directory entries or 150 ms, and marks incomplete totals
  as lower bounds. Symlinks are excluded. Metadata files count in storage totals.
- Fetching: queued plus running downloads, concurrency limits, active yt-dlp
  attempts and streams, cooldown and known dead/flaky video counts. Completed
  yt-dlp success means exit code zero, not a media-integrity guarantee. Up to 64
  active attempts and 60 recent HTTP-error/fetch events are retained.
- Storage/runtime: disk capacity and free space, read-only SQLite availability,
  process RSS/CPU time, threads, host load and memory, and cgroup v2 limits where
  available. Host load is not container CPU utilization. Unknown values remain
  unknown. The database check tests readability, not full database integrity.
- Cookies: file presence and modification age, bounded Netscape-format parsing,
  active/expired/session-cookie counts, next YouTube cookie expiry and last
  explicit validation result/age. Session cookies have no known expiry. A valid
  format or an unexpired cookie does not prove YouTube will accept it. Amazon
  status indicates only a locally loaded session, not online device reachability.

Counters and history belong to **one server process** and reset on restart. They
are not persistent or aggregated across workers or replicas. With multiple
workers, snapshots can alternate between processes; use a shared metrics system
for fleet-wide history. Never interpret these counters as billing or audit logs.

## Refresh and cost

Default refresh is 15 seconds, selectable from 5–60 seconds. Requests never
overlap; errors back off up to 60 seconds. Hidden tabs stop refreshing. Manual
refresh remains available while paused. Failed refreshes preserve the last
snapshot, and expired authentication stops polling. Local filesystem/system
collection is single-flight and cached for 10 seconds; its timestamp is visible.
Polling does not fetch music, validate cookies, or contact YouTube/Amazon.

The **Test audio access** button explicitly runs the existing download-cookie
probe. It can take up to 50 seconds and does perform an upstream sample download.
The panel has no cache-delete, settings-edit or other operational mutation action.

## Tests

From the repository root, after installing server requirements:

```sh
python -m pip install -r flask-server/tests/requirements-status.txt
python -m playwright install chromium
PYTHONPATH=flask-server/tests:flask-server python -m unittest discover -s flask-server/tests -p 'test_status*.py' -q
node flask-server/tests/test_status_ui.js
python -m pytest -q flask-server/tests/browser/test_status_dashboard.py
```

Browser tests use a temporary local server and fixture data, never production
services. Set `STATUS_SCREENSHOT_DIR` to retain desktop/mobile screenshots. CI
runs the tests and uploads screenshots plus browser JUnit results. Existing
playback regressions remain in a separate job and isolated module processes.

Deploy the backend branch to expose the panel; no Android build is required.


## Live logs, errors and lazy cache details

Cache labels describe what is measured: saved audio checks, audio-response
caching, account/public API client objects, audio resolver results, playback
stream lists and playlist track lists. Normal `/alexa/search/` activity is shown
separately using request counters; it is not counted as a cached search.

Click a cache (or View lookup details) to fetch hit/miss events into a dialog.
No detail history is in the initial status snapshot. A spinner covers pending
requests; closed/replaced dialogs ignore late responses. Filters and stable
cursors support hits/misses and older entries. Each cache retains at most 200
process-local events for 24 hours; cumulative counters can therefore exceed
available details. Identifiers are hashed except valid video IDs. Expired audio
resolver entries count as misses. These are lookups, not unique plays.

The Current server logs section loads only when requested. Live mode polls every
5 seconds while the section and tab are visible. Search covers messages, routes, exception text and request IDs; it is literal,
debounced and bounded to 200 characters. Severity/source filters run on the server. Older
pages pause live mode and the browser retains at most 300 displayed entries.

The Errors button shows unseen ERROR/CRITICAL records, including HTTP 5xx,
Python logging exceptions and unhandled Python background-thread exceptions.
Opening an error does not acknowledge it. Mark individual errors, or only the
currently displayed errors, as seen. New arrivals are never silently marked by
a blanket acknowledgement. Seen state is stored in SQLite and survives restarts.
Details include exception type/frames and up to 10 retained entries on each side
in journal arrival order. Compare timestamps/request IDs: nearby entries may
belong to concurrent requests, and asynchronous arrival/retention can truncate
context. Refresh context to see logs written after opening.

A local watchdog checks every 60 seconds for critical storage/database health
and yt-dlp attempts older than 120 seconds. It records one error per active
condition, then allows a new alert after recovery and recurrence. It does not
restart services, interrupt playback or contact upstream systems. SIGKILL,
container termination and whole-host failure cannot reliably be recorded by an
in-process watchdog; use external monitoring for those cases.

`STATUS_LOG_DB` selects a dedicated journal database. By default it is
`status_logs.sqlite3` beside `DB_FILE`, so existing persistent database mounts
also persist the journal. Records are redacted before enqueue/storage; known
application secrets, authentication payloads, URLs, paths and common identity
fields are removed. HTTP entries retain registered route patterns, never query
arguments. Some authentication messages are deliberately omitted in full.
This is sanitized application logging, not raw container stdout/stderr or logs
from other containers. Status polls are excluded to prevent feedback noise.

Producers never wait for SQLite: separate bounded queues feed one background
writer. The journal retains up to 4,000 ordinary and 1,000 error records, with
an additional SQLite page cap of approximately 32 MiB plus a bounded WAL.
**Retention is up to 24 hours**: size caps may discard the oldest entries sooner;
queue saturation/storage failures report dropped counts. Info floods cannot
consume the error-row budget. Expired rows disappear from API results exactly
at the cutoff, even if seen, and background cleanup removes them within the next
minute. Incremental vacuum/checkpointing reclaims free space; allocated database
pages may remain reserved within the cap. This policy does not rotate Docker or
host logs maintained outside the application.

Log/error APIs are owner-only and `no-store`:
- `GET /api/status/logs?level=&source=&q=&before=&limit=50`
- `GET /api/status/errors?before=&limit=50`
- `GET /api/status/errors/<id>`
- `POST /api/status/errors/seen` with JSON `{"ids":["..."]}` (at most 100)
- `GET /api/status/cache/<name>?outcome=hit|miss&before=&limit=50`

Failures preserve playback and existing browser snapshots. Read-only/full/missing
journal storage produces an unavailable state and bounded retries, not a logging
exception on request threads. Per-process cache counters and multi-worker caveats
still apply; shared journal paths aggregate records from workers, while queue
loss counters and watchdog condition suppression are process-local.
