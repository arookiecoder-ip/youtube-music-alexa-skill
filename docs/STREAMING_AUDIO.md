# Persistent YouTube audio streaming

Docker enables `YOUTUBE_STREAMING=1`. `/audio/` (web/phone) and `/proxy/`
(Echo) resolve AAC audio with a persistent YouTube.js YTMUSIC client and relay
upstream bytes immediately. Only received current-track bytes are retained in
one anonymous temporary file. A confirmed next track replaces it; pause/resume
and same-track handoffs keep it. URL prefetch and Echo's next-track prebuffer do
not evict the song still playing. HEAD/info requests never change the current
track. Sparse byte ranges are tracked so holes are never served as audio.

There is no persistent song cache, audio volume, LRU budget or sweeper. A 64 MiB
per-current-track safety ceiling and disk-error handling let streaming continue
without retaining additional bytes. The file is anonymous and released by the OS
on process exit. Old generation writers cannot resurrect removed songs.
Explicit download requests stream to the client with an attachment filename;
they do not build a saved-song collection on the server.

Flask still authorizes every media request and applies the existing Echo/phone
ownership and generation checks. No signed upstream URL is returned to a device.
The Node worker uses private stdin/stdout, a reduced environment, a memory limit,
and filesystem read permissions limited to its code and selected cookie file.
It cannot spawn subprocesses or write files under Node's permission model.

The session/player is initialized once in the background at server startup.
Cookie file replacement resets the session and URL cache. URLs are coalesced,
bounded to 128 entries and cached for at most five minutes (less near signed
expiry). Queue preparation resolves URLs rather than downloading audio.
Four resolver calls and eight active audio responses are allowed; excess work
gets a retryable error instead of unbounded processes or queues.

Audio is pinned to AAC/MP4 itag 140. Range requests remain attached to the same
encoding. HEAD, suffix/open-ended ranges and stable ETags/If-Range are supported.
An upstream 403/410 refreshes the signed URL once before sending any audio.
Disconnects close the upstream socket and release capacity, including responses
closed before their first iteration. A hung/dead worker can restart; a player
decipher failure invalidates the session with a bounded refresh frequency.
Upstream failures are sanitized and never expose cookies, URLs or signatures.

Streams are not silently spliced to a new encoding or restarted from byte zero
after audio has been sent. An interrupted stream closes; the player can retry
using ranges. Unavailable tracks and unsupported encodings return 404/422;
temporary failures return retryable 502/503. Some restricted tracks may still
require new login cookies or remain unavailable on this server's IP.

## Validation

```sh
npm ci --prefix flask-server/youtube-stream --ignore-scripts
npm test --prefix flask-server/youtube-stream
python scripts/run-regressions.py --results /tmp/musicbox-tests
python -m unittest discover -s lambda/tests -p 'test_*.py' -q
```

Offline regression files disable automatic client warmup; the streaming-specific
route tests verify auth, ownership, no persistent downloads,
Range/HEAD, failure contracts and connection cleanup. Worker tests cover timeout,
death and concurrent replies. Resolver tests cover credential rotation (including
rotation during initialization), expiry, bounds, format pinning and decipher
recovery without contacting YouTube.

Live validation used isolated containers with production credentials mounted
read-only. Five songs, three fresh resolutions each, returned valid initial MP4
audio in 140–354 ms. Full transfers of two tracks matched Content-Length; range
bytes matched the corresponding full-stream bytes. HEAD, If-Range, invalid
ranges, eight concurrent requests and worker restart were also tested.
This first-byte measurement excludes Alexa command dispatch and device buffering;
it is not a physical Echo click-to-audible guarantee.

## Deployment and data

Only `ytmusic` needs rebuilding/recreation. There is no cache-mode switch or
legacy file-download playback path. Existing database and authentication volumes
are unchanged. The removed audio-cache volume is no longer mounted; an unused
old cache volume may be deleted after verifying no container references it.
