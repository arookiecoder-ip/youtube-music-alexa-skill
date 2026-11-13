# Queue selection and Alexa transport latency

Music Box 2.5.5 sends `new_queue: true` for selections from search, library and
other non-queue surfaces. The server skips the historical video-ID lookup for
these requests, even if the same video is already queued. Explicit queue taps
continue to send their index and retain surrounding tracks. Older clients keep
their existing contract.

Known-online transport commands refresh stale Amazon presence in parallel rather
than waiting for another request before sending Resume. Known-offline devices
still require a synchronous refresh and cannot silently pass the online check.
Only one background refresh runs per account at a time, and refresh failures do
not invalidate an accepted transport command.

The initial 2.5.5 native PauseCommand optimization was rolled back: Amazon can
acknowledge it without stopping this custom-skill stream. Handoffs now send the
skill pause and retain source ownership until an authenticated PlaybackStopped
callback confirms the same video and playback token. Command acceptance,
optimistic paused UI state, cached stop records, and stale callbacks do not
release audio. A missing confirmation times out and refuses the switch. Active
signed-out/unreachable Echo playback cannot be silently handed off. The existing
2.5.5 `native_handoff` hint now requests this verified path for compatibility; no
APK update is required for the correction. Stale presence refresh still runs in
parallel, avoiding an unnecessary preflight without sacrificing confirmation.

The Android changes also retain post-call resume intent while audio drains,
recheck call state after asynchronous ownership recovery, tolerate up to three
late focus losses in one non-extending eight-second window, require deliberate
queue-removal swipes, keep row surfaces opaque, and make the entire player dock
own its touch area, including loading placeholders. These changes are tested by
JVM regressions and emulator scenarios through GitHub Actions.
