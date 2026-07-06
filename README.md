# Music Box — YouTube Music on Alexa (self-hosted, zero cost)

A personal Alexa skill that plays YouTube Music on Echo devices with YT Music's
recommendation engine (endless radio from any song — the queue keeps extending
itself with more recommendations as you approach the end), at zero monthly cost.

Based on the original Alexa music skill project (MIT), heavily modified:
permanent VPS backend, server-side audio proxy, PO-token/cookie handling for
datacenter IPs, API-key auth, SSML fixes, relevance-scored search, rebuilt
interaction models, and a browser **web remote** — protected by a
username/password (+ optional 2FA) login — that starts and controls playback
on the Echo from any device.

---

## Architecture

```
Echo / Alexa app
      │  voice
      ▼
Alexa cloud ──► Alexa-hosted Lambda (Python, free tier, EU-Ireland)
                     │  HTTPS + API key (urllib3)
                     ▼
              Caddy (auto Let's Encrypt TLS)          Oracle Cloud
              https://<ip-dashes>.sslip.io            free-tier VPS
                     │  reverse_proxy                 (Ubuntu 24.04 arm64)
                     ▼
              Flask server (port 5000, systemd)
              ├── ytmusicapi ── search / radio / playlists
              ├── yt-dlp ────── downloads audio into a local cache
              │        (tv client, mweb fallback + cookies + bgutil PO token + deno)
              ├── /proxy/ ───── serves the cached audio file to the Echo
              └── web remote ── /remote/ UI + /alexa/* API (AlexaPy)
                       └── Amazon login proxy (aiohttp, port 5001,
                           mounted publicly at /alexa/proxy/ via Caddy)
```

**Why an audio proxy?** googlevideo stream URLs are IP-locked to the machine
that resolved them — an Echo can never fetch them directly. Worse, on
datacenter IPs even the resolving machine gets 403 on raw fetches; only
yt-dlp's own download path (tv client, falls back to mweb + GVS PO token)
works. So the server downloads each track (~3 MB m4a) into a cache and serves
the file itself, with Range support.

### Components on the VPS

| Piece                              | Role                                                                                |
| ---------------------------------- | ----------------------------------------------------------------------------------- |
| `~/ytm` venv                       | flask[async], ytmusicapi, yt-dlp, alexapy, bgutil-ytdlp-pot-provider                |
| deno (`~/.deno`)                   | JS runtime yt-dlp needs to solve YouTube's challenges                               |
| `bgutil-provider` Docker container | generates PO tokens (port 4416, auto-restarts)                                      |
| `~/cookies.txt`                    | YouTube account cookies (throwaway account) — gets past the datacenter-IP bot check |
| `ytmusic.service` (systemd)        | runs the Flask server with the env vars below                                       |
| Caddy                              | HTTPS via sslip.io hostname + automatic Let's Encrypt; also fronts the login proxy  |
| cron (`*/30` + daily)              | sweeps cached audio older than 2 h; trims yt-dlp info cache weekly                  |

### Environment variables

Core (`server.py`):

| Var               | Purpose                                                                                                                                                              |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PUBLIC_BASE_URL` | e.g. `https://<your-ip-with-dashes>.sslip.io`. When set, `audio_url` points to `/proxy/` and downloads are pre-warmed. Unset = dev mode (returns direct googlevideo URLs). |
| `YTDLP_COOKIES`   | path to cookies.txt; passed to every yt-dlp call                                                                                                                     |
| `API_KEY`         | shared secret; when set, all endpoints except privacy/terms and the login flow require `?key=` (or `X-Api-Key` header) **or** a valid web-remote session cookie. Must match `API_KEY` in `lambda/api_key.py`. |
| `AUDIO_CACHE_DIR` | audio cache location (default `/tmp/ytm_audio_cache`)                                                                                                               |
| `HISTORY_FILE`    | listening-history JSON file location for the web remote's Recently Listened / Recommended sections (default `/tmp/ytm_listen_history.json`) — see the Docker note below |
| `FLASK_DEBUG`     | set to `1` for auto-reload during local development                                                                                                                 |

> **Docker Compose deployments:** `server.py` binds Flask to `0.0.0.0` (not
> `127.0.0.1`) and the Amazon login proxy in `alexa_remote.py` does the same for
> its port, since Caddy runs in a separate container and can only reach the
> `ytmusic` container over the Docker bridge network, not via loopback.
>
> The audio cache volume (`ytmusic_cache` → `/tmp/ytm_audio_cache`) is TTL-swept
> and `/tmp` itself is ephemeral, so listening history is kept on its own
> `ytmusic_data` volume mounted at `/data`, with `HISTORY_FILE` pointed there
> (`docker-compose.yml` sets this up already) — otherwise history would be
> wiped on every container recreate.

Web-remote login (see [Web remote](#web-remote-control-the-echo-from-any-browser)) — lets you open `/remote/` with a clean URL instead of `?key=<API_KEY>`:

| Var                   | Purpose                                                                                                                                    |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `REMOTE_USER`         | web-remote login username. **Login is enabled only when both this and `REMOTE_PASSWORD` are set**; otherwise `/remote/` falls back to `?key=`. |
| `REMOTE_PASSWORD`     | web-remote login password                                                                                                                   |
| `REMOTE_TOTP_SECRET`  | optional base32 secret adding a 6-digit 2FA code to the login (RFC 6238 TOTP, verified in-server, no extra dependency)                      |
| `SECRET_KEY`          | signs the session cookie. Set it so logins survive restarts; if unset a random key is generated (sessions reset on every restart)          |
| `COOKIE_INSECURE`     | set to `1` only for local HTTP testing (drops the cookie's `Secure` flag). Leave unset in production — the VPS serves HTTPS                 |

Amazon / Echo side (`alexa_remote.py`). **Amazon credentials are NOT
configured here** — you type them into the remote's login form and only the
resulting session cookie is stored (see
[Amazon login](#amazon-login-browser-driven-no-credentials-on-the-server)):

| Var                     | Purpose                                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------------- |
| `AMAZON_DOMAIN`         | Amazon site your account lives on: `amazon.in`, `amazon.com`, … (default `amazon.in`)                |
| `ALEXA_PROXY_BASE_URL`  | public HTTPS origin the browser reaches the login proxy at. Falls back to `PUBLIC_BASE_URL`, so you usually don't need to set it — only if the proxy is fronted by its own hostname |
| `ALEXA_PROXY_PORT`      | local-only port the login proxy listens on (default `5001`); Caddy forwards the public `/alexa/proxy/` path here |
| `ALEXA_COOKIE_DIR`      | where the Amazon session cookie is persisted (default `flask-server/alexa_cookies/`)                 |
| `SKILL_INVOCATION_NAME` | skill invocation name used in text commands (default `music box`)                                    |

---

## Repository layout

```
flask-server/         backend
  server.py           search, radio, audio proxy, web-remote routes
  alexa_remote.py     AlexaPy bridge + Amazon login proxy for the web remote
  templates/          setup page + /login/ + /remote/ UI
  static/             web-remote assets (PWA icons etc.)
  alexa_cookies/      persisted Amazon session (gitignored — never commit)
lambda/               Alexa skill code (paste into the Alexa-hosted code editor)
  lambda_function.py  intent + AudioPlayer event handlers
  data.py             spoken messages, imports DEFAULT_API_URL/API_KEY
  api_key.py          API_KEY + DEFAULT_API_URL placeholder template (tracked — never commit real values, fill them in only in the Alexa console copy)
  mediaUtils/player.py  API client, playback controller, SSML escaping
  models/player_models.py  dataclasses
skill-package/interactionModels/custom/   interaction model JSON (5 locales)
run-local.ps1         local dev launcher (Windows)
HANDOFF.md            detailed session-by-session project state
PROJECT-PLAN.md       roadmap
```

---

## Setup from scratch

Full step-by-step, beginner-friendly guides (server + Alexa skill, start to
finish) live in their own files — pick one based on how you want to run the
server:

- **[SETUP-DOCKER.md](SETUP-DOCKER.md)** — Docker Compose. Recommended if
  this VPS will host other projects too (keeps everything isolated,
  shares Caddy across projects).
- **[SETUP-VPS.md](SETUP-VPS.md)** — plain Python venv + systemd, no Docker.
  Simpler if this VPS is dedicated to just this project.

Both guides cover the same ground: installing dependencies, getting YouTube
cookies, setting up HTTPS with Caddy, and creating/configuring the Alexa
skill in the developer console. The rest of this README is reference
material — architecture, environment variables, voice commands, the web
remote, and troubleshooting.

### Shared secret: the API key

Both setup guides have you generate one secret and use it in **two places**
that must always match:

```bash
openssl rand -base64 32 | tr -d '\n'
```

- **Alexa console**: `lambda/api_key.py` → `API_KEY = "<secret>"` → Deploy.
- **Server**: `API_KEY` in `.env` (Docker) or the systemd unit (plain VPS),
  then restart the service.

A mismatch here is the #1 cause of "401 everywhere" — see
[Troubleshooting](#troubleshooting).

---

## Voice commands

- "Alexa, ask music box to **play** _song_" — starts an endless radio queue,
  auto-extending with more recommendations as you get near the end
- "... **play** _song_ **by** _artist_" — artist-aware (falls back to video results for mashups/covers)
- "... **play songs by** _artist_" / "... **play album** _album_"
- "Alexa, **next / previous / pause / resume**"
- "... **seek to** _N_ **seconds**" — jumps within the current track (Alexa has no
  native seek, so this re-issues playback at the new offset; a brief re-buffer is
  normal). Mainly driven by the web remote's scrubber.
- "... **shuffle on/off**", "... **loop on/off**"
- "... **what's playing**" (announce now playing)
- "... **start playlist** _name_", "... **what are my playlists**"
- Playlists/API URL are added via hex-encoded values from `<api_url>/setup/?key=<API_KEY>`

Note: the first song of a session takes ~7-8 s to start (server downloads it;
YouTube enforces an ad-skip gate of ~4-5 s on monetized videos — this fires
regardless of yt-dlp player client (mweb/tv/web_music all hit it) and isn't
bypassable: the googlevideo URL 403s if fetched before that window opens, so
it's a real server-side constraint, not just yt-dlp being polite). The `tv`
client (with `mweb` as automatic fallback) is used since it has slightly less
client-config/JS-challenge overhead than mweb, saving ~1 s. Track-to-track
transitions are instant — the next song is pre-downloaded while the current
one plays.

---

## Web remote (control the Echo from any browser)

`https://<PUBLIC_BASE_URL>/remote/` serves a phone-friendly page (installable
as a PWA) that can start any song, playlist, or pasted YouTube link on the
Echo, and pause / resume / skip / set volume / seek — from anywhere, without
speaking to the device.

Features:

- **Search with live suggestions** (proxied through `/alexa/suggest/`) and a
  clear button; results play on the selected Echo.
- **Live now-playing progress bar** you can drag to seek. The bar ticks
  locally for smoothness but is anchored to server state pushed over SSE, so
  opening the page partway through a song (or on a second device) lands on
  the right position, and multiple open pages — phone and laptop at once —
  stay in sync. Dragging the scrubber seeks on release (Alexa can only seek
  by restarting the stream at the new offset, so expect a brief re-buffer).
- **Queue view** — the upcoming radio queue, tap any entry to jump to it,
  plus a shuffle-queue button (keeps the current song in place).
- **Pasted YouTube links** — a watch link plays directly (bypassing search);
  a watch link with a `list=` id queues the rest of that playlist, like
  YouTube does.
- **Recently Listened** — a persistent, server-side history (survives page
  reloads and server restarts) of tracks actually played, recorded only once
  the skill confirms real playback (not on a mere play request, and not on a
  seek or resume-from-pause). Tap an entry to replay it instantly; remove
  individual entries or clear the whole list, with a themed confirmation
  dialog matching the rest of the UI (no browser `confirm()` popups). On
  mobile this section lives in the hamburger sidebar; on desktop it's in the
  main column.
- **Recommended for you** — shown only on the blank/idle screen, with a
  shimmering skeleton while it loads. Mixes a "for you" radio (seeded from
  your most-recent track) with a "discover" radio (seeded from an older
  track), preferring songs you haven't already played; falls back to YT
  Music's charts if you have no history yet, or if there isn't enough to mix.
  Cached for 30 minutes server-side and invalidated whenever you clear your
  history.

Access is gated by the **web-remote login**: with `REMOTE_USER` /
`REMOTE_PASSWORD` set, you sign in once at `/login/` and a session cookie
authorizes the page — the long API key never appears in the browser. Without
those env vars, it falls back to the legacy `?key=<API_KEY>` scheme.

**How it works:** Amazon offers no official API for "make my Echo play
something" (Spotify can only do it because its client is embedded in Echo
firmware under a commercial partnership). The remote instead impersonates the
Alexa phone app through Amazon's internal HTTP endpoints, via
[AlexaPy](https://gitlab.com/keatontaylor/alexapy) — the open-source library
the Home Assistant community has used for years:

- "Play something" sends a **text command** — literally the string
  `ask music box to play <query>` — as if you'd typed it to Alexa, so it flows
  through the skill and its search like a spoken request.
- The transport buttons send the same `PlaybackController` events as the Alexa
  app's now-playing card (the lambda handles these).
- The scrubber seeks by sending a text command (`ask music box to seek to N
  seconds`) that routes into the skill's `SeekIntent`, which re-issues playback
  at the new offset — Alexa exposes no seek directive, so this stream restart is
  the only way to reposition.

Because the API is unofficial, Amazon may occasionally change it and break
the library until it updates. Everything is scoped to your own account and
devices; the endpoints (`/remote/`, `/alexa/*`) sit behind the web-remote
login or the API key.

### Remote login (clean URL instead of a key)

The proxy `API_KEY` is a long secret that also rides inside the audio URLs the
Echo fetches, so it can't be shortened — putting it in the shareable remote URL
(`/remote/?key=<huge string>`) was clumsy. Instead, set a username and password
and log in once:

- Set `REMOTE_USER` and `REMOTE_PASSWORD` (both required to switch the login
  on), and ideally `SECRET_KEY` so sessions survive restarts.
- Optionally set `REMOTE_TOTP_SECRET` to add a 6-digit **2FA** code on top —
  generate one with
  `python3 -c "import base64,os;print(base64.b32encode(os.urandom(20)).decode())"`
  and add that string to an authenticator app.
- Opening `/remote/` unauthenticated redirects to `/login/`. On success the
  server sets a signed, HttpOnly, 30-day session cookie that authorizes the
  remote page and all `/alexa/*` calls — the API key never touches the browser.
  A **Sign out** button on the page clears it.

Scope: the cookie only unlocks the remote (`/remote/`, `/alexa/*`). The
YouTube-Music data-plane endpoints (`/find_stream_list/`, `/get_stream/`,
`/proxy/`, …) still require `API_KEY`, so the Echo/Lambda path is unaffected.
If `REMOTE_USER`/`REMOTE_PASSWORD` are unset, the login is disabled and
`/remote/` keeps working with `?key=<API_KEY>` exactly as before.

The env vars live in `/etc/systemd/system/ytmusic.service` (see the unit file
above); after editing, `sudo systemctl daemon-reload && sudo systemctl restart
ytmusic`.

> Note: this login protects the **web remote** (who may control your Echo from
> a browser). It is separate from the Amazon login below, which is how the
> server itself talks to Amazon's API.

### Amazon login (browser-driven — no credentials on the server)

The remote needs an Amazon session to control your Echos, but **no Amazon
credentials are stored on the server**. Login happens interactively, in your
own browser, through a proxied copy of Amazon's real login page:

1. Open the remote and click **Log in to Amazon**; enter the account's email
   and password in the form.
2. The server spins up an [AlexaPy](https://gitlab.com/keatontaylor/alexapy)
   `AlexaProxy` session (local port 5001, publicly reachable at
   `/alexa/proxy/` through Caddy) and hands your browser the login URL.
3. Your browser completes Amazon's actual login there — including any
   captcha, OTP, or "approve this device" push — so nothing has to be
   scripted blind.
4. On success only the resulting **session cookie** is persisted (to
   `ALEXA_COOKIE_DIR`, default `flask-server/alexa_cookies/`) and adopted as
   the live session. The credentials you typed are used once to seed the
   proxy login and are never written to disk.

Later restarts reuse the persisted cookie, so this is a one-time step until
Amazon invalidates the session. There is **one** Amazon session for the whole
server — logging in again replaces which account controls every Echo (the API
requires an explicit `force` flag to do that, so a stale tab can't swap
accounts silently).

Config: set `AMAZON_DOMAIN` to the Amazon site your account lives on
(`amazon.in`, `amazon.com`, …). `ALEXA_PROXY_BASE_URL` is only needed if the
login proxy is served from a different hostname than `PUBLIC_BASE_URL`.

**Security recommendation: log in with a throwaway second account, not your
main one.** The persisted session cookie is a logged-in Amazon session —
anyone with root on the VPS could use it.

1. Create a fresh Amazon account (new email, no payment methods).
2. Alexa app → Settings → Your Profile & Family → add it to your **Amazon
   Household** as a second adult (so it can see and control your Echos).
3. Log in to the remote with *that* account.

Worst-case leak then exposes an account that can only control your speaker.
`alexa_cookies/` is gitignored territory — never commit it.

### Endpoints

| Route                | Method   | Purpose                                                          |
| -------------------- | -------- | ---------------------------------------------------------------- |
| `/login/`            | GET/POST | web-remote login page; POST `{"username","password","code"}` sets the session cookie |
| `/logout/`           | POST/GET | clears the session cookie                                        |
| `/remote/`           | GET      | the web UI (redirects to `/login/` when unauthenticated)         |
| `/alexa/status/`     | GET      | login/config state (first stop when debugging)                   |
| `/alexa/proxy_login/` | POST    | `{"email","password"}` → starts the Amazon proxy login, returns the `login_url` to open (409 + `"force": true` required if already signed in) |
| `/alexa/proxy_check/` | GET     | poll whether the browser finished the Amazon login               |
| `/alexa/devices/`    | GET      | Echo devices (`?refresh=1` to re-fetch from Amazon)              |
| `/alexa/volume/`     | GET      | `?serial=…` → current volume (falls back to cached value)        |
| `/alexa/play/`       | POST     | `{"serial", "query"}` → search text, or a YouTube link (played directly; `list=` links queue the playlist) |
| `/alexa/play_queue/` | POST     | `{"serial", "video_id"}` → jump to a specific queue entry        |
| `/alexa/shuffle_queue/` | POST  | shuffles the upcoming queue, keeping the current song in place   |
| `/alexa/command/`    | POST     | `{"serial", "action"}` — play, pause, next, previous, volume (+`value` 0-100) |
| `/alexa/seek/`       | POST     | `{"serial", "position_ms"}` (or `position_seconds`)              |
| `/alexa/suggest/`    | GET      | `?q=…` → YT Music search suggestions for the search bar          |
| `/alexa/now_playing/` | GET     | current track + progress anchor (`position_ms`, `duration_ms`, `started_at`) + queue |
| `/alexa/now_playing/stream` | GET | Server-Sent Events stream of now-playing state; drives the live progress bar + queue across all open pages |
| `/alexa/state_event/` | POST    | webhook the Lambda posts on playback `started`/`stopped`/`finished` (with the offset) so state is event-driven, not polled |
| `/armed_play/`       | GET      | called by the skill to pick up a play the remote armed (API-key protected, not a session endpoint) |
| `/history/`          | GET      | `?limit=…` (default 20, max 100) → recently-listened tracks, newest first  |
| `/history/`          | DELETE   | clears all listening history                                     |
| `/history/<video_id>` | DELETE  | removes a single track from history                              |
| `/recommendations/`  | GET      | `?refresh=1` to bypass the 30-minute cache → mixed personalized + discovery track list for the blank-state screen |

---

## Local development (Windows)

`.\run-local.ps1` starts the server on `http://127.0.0.1:5000/remote/` using
the repo's `.venv`, with `FLASK_DEBUG=1` (edit → refresh) and no
API_KEY/login gates. Real Echo control works locally too, if
`flask-server\alexa_cookies\` holds a valid Amazon session — either copy it
once from the VPS (`scp -r ubuntu@<vps>:~/flask-server/alexa_cookies
flask-server/`) or log in via the page (the script sets
`ALEXA_PROXY_BASE_URL=http://127.0.0.1:5001` so the browser hits the login
proxy directly, without Caddy).

---

## Troubleshooting

| Symptom                                                       | Check                                                                                       |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Alexa: "there was a problem with the skill's response"        | CloudWatch logs (Code tab → Logs link)                                                      |
| Says "Playing X" but silence + `PlaybackFailed` in CloudWatch | `curl <PUBLIC_BASE_URL>/proxy/?video_id=J7p4bzqLvCw&key=<key>` from outside — should be 200 |
| Server errors                                                 | `sudo journalctl -u ytmusic -f` on the VPS                                                  |
| yt-dlp bot-check / "Sign in to confirm" errors return         | cookies expired → re-export + re-scp cookies.txt                                            |
| HTTPS dead                                                    | `journalctl -u caddy` — cert renewals need ports 80/443 open                                |
| 401 everywhere                                                | API_KEY mismatch between `lambda/api_key.py` and ytmusic.service                            |
| `/remote/` shows `{"error":"unauthorized"}` instead of a login | login not enabled — set **both** `REMOTE_USER` and `REMOTE_PASSWORD` in ytmusic.service, then daemon-reload + restart |
| Web-remote login: "invalid authentication code"               | TOTP mismatch — check the phone clock, that the authenticator secret equals `REMOTE_TOTP_SECRET`, and that the code is current |
| Signed in, but bounced back to `/login/` after a restart      | set `SECRET_KEY` (a random one is generated per boot, invalidating old cookies)             |
| Search/queue works but no audio plays on the Echo              | `PUBLIC_BASE_URL` (`.env`/systemd) and `DEFAULT_API_URL` (Lambda's `api_key.py`) must both point at the domain the Echo can actually reach — a leftover placeholder (e.g. an old `sslip.io` hostname) after migrating to a real domain sends Alexa `audio_url`s that resolve nowhere |
| 502 Bad Gateway, Caddy logs `connect: connection refused` (Docker) | Flask/aiohttp bound to `127.0.0.1` instead of `0.0.0.0` — Caddy can't reach a container's loopback address over the Docker network. See the Docker note under [Environment variables](#environment-variables) |
| Amazon login page won't load / times out                      | the Caddy `/alexa/proxy/*` → `localhost:5001` route is missing, or `ALEXA_PROXY_BASE_URL`/`PUBLIC_BASE_URL` doesn't match the origin the browser is on |
| Amazon login refused / loops                                  | `/alexa/status/` shows the state; check `AMAZON_DOMAIN` matches the account's Amazon site, then retry the in-page login (captcha/2FA happen in your browser) |
| Web remote worked, then broke after months                    | Amazon changed the internal API → `~/ytm/bin/pip install -U alexapy` and restart; if the session expired, just log in again from the page |

`HANDOFF.md` has the full debugging history and the reasoning behind every
piece of this setup.

---

## License

MIT — see LICENSE.
