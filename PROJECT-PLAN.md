# Alexa YouTube Music Skill — Project Plan

Personal Alexa skill that plays YouTube Music on Echo devices, with YT Music's
recommendation engine, at **zero cost**. Based on
original Alexa music skill project (MIT),
with architecture ideas from
[akhilerm/youtube-music-alexa-skill](https://github.com/akhilerm/youtube-music-alexa-skill).

## Architecture

```
Echo ── Alexa cloud ── AWS Lambda (skill logic + queue, free tier + DynamoDB)
                          │ HTTP
                          ▼
                    Flask server (ytmusicapi + yt-dlp) on our own machine,
                    exposed via free ngrok tunnel
```

Why the split: yt-dlp stream extraction is far more reliable from a home IP —
YouTube blocks AWS datacenter IPs. The Lambda handles Alexa intents and queue
state (DynamoDB); the Flask server does YT Music search/recommendations and
stream-URL extraction.

## Zero-cost checklist

- Alexa developer account — free
- AWS Lambda + DynamoDB — free tier (or use an **Alexa-hosted skill**, free forever,
  provides the `DYNAMODB_PERSISTENCE_*` env vars this code already expects)
- Flask server — runs on our PC (or an old Android phone via Termux)
- ngrok free tier — URL changes on restart; the skill has a
  "set api url" voice command to update it

## Validated so far (2026-07-03)

- Python 3.13 venv in `.venv` with flask, ytmusicapi 1.12.1, yt-dlp 2026.6.9
- ytmusicapi search ✔, radio/recommendations (50 tracks) ✔
- yt-dlp stream URL extraction ✔ — but the upstream repo's
  `--extractor-args youtube:player_client=ios` is broken (needs PO token since 2025);
  removed in our copy, default clients work
- yt-dlp warns a JS runtime (deno) is recommended — works without, install deno if
  formats start going missing

## Roadmap

1. **Run the Flask server locally** (`flask-server/server.py`) and exercise its
   endpoints by hand.
2. **Create the Alexa skill**: Alexa developer console → Alexa-hosted (Python) skill,
   upload `skill-package/interactionModels/custom/en-IN.json`, deploy `lambda/` code.
3. **Wire them together**: start ngrok, set the API URL via the voice command,
   play a song end-to-end on the Echo.
4. **Personal library**: authenticate ytmusicapi (browser OAuth) so "my playlists",
   likes, and personalized home feed work — upstream only supports public playlists.
5. **Phone remote**: extend the Flask server with a control API + simple PWA page;
   use an Alexa remote-control library to push playback to the Echo from anywhere
   (no same-WiFi/Bluetooth needed — everything goes through the cloud).
6. Quality of life: auto-update ngrok URL, better error speech, multi-device.
