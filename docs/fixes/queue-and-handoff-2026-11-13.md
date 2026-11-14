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

Handoffs use the skill pause because Amazon can acknowledge a native PauseCommand
without stopping a custom-skill stream. Target selection now takes priority: a
known invalid Amazon session skips remote commands, and a missing stop callback
waits at most 400 ms after command acceptance. The Amazon source-pause call
is capped at one second and cancels its coroutine on timeout. An unreachable Echo cannot block
the selected phone or browser. Concurrent song/output changes still reject stale
requests, and ownership tokens fence late callbacks. If Amazon cannot authenticate,
the source cannot be guaranteed silent; no repeated remote commands are issued
just to obtain confirmation.

The Android changes also retain post-call resume intent while audio drains,
recheck call state after asynchronous ownership recovery, tolerate up to three
late focus losses in one non-extending eight-second window, require deliberate
queue-removal swipes, keep row surfaces opaque, and make the entire player dock
own its touch area, including loading placeholders. These changes are tested by
JVM regressions and emulator scenarios through GitHub Actions.
