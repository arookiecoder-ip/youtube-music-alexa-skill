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

Alexa-to-phone/browser handoffs and 2.5.5 Echo-to-Echo handoffs use Amazon's native
PauseCommand instead of injecting a spoken pause through NLU and Lambda. Native
errors fall back to the existing skill pause; authentication errors and failed
fallbacks deny the handoff. The destination is never granted audio before the
source pause request succeeds. Online handoffs no longer perform a preliminary
presence request. Offline status is verified separately when a pause fails.

The Android changes also retain post-call resume intent while audio drains,
recheck call state after asynchronous ownership recovery, tolerate up to three
late focus losses in one non-extending eight-second window, require deliberate
queue-removal swipes, keep row surfaces opaque, and make the entire player dock
own its touch area, including loading placeholders. These changes are tested by
JVM regressions and emulator scenarios through GitHub Actions.
