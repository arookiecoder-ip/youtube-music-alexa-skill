# Music Box backend changelog

Reviewed 2026-10-25 against `master` through `08139f16aac382d80703d59104838c4ecd1fb40a`. The backend is tracked by date/commit, independently of Android source/build version numbers. [Current architecture](ARCHITECTURE.md) · [Android changelog](https://github.com/arookiecoder-ip/MusicBox-APP/blob/feat/alexa-backend/CHANGELOG.md).

This file summarizes behavior changes, explicitly records superseded work and lists all 50 repository PRs plus the complete 606-commit reachable history. A merged historical PR is not proof that its old implementation is still active; direct commits after PR #50 are included below.

## Unreleased

- Fix cookie-authenticated access to the YouTube sign-in surface and account chooser, with a regression through the real authorization middleware. Share the original owner sign-in theme, reveal only decoded browser frames, stop on expired authorization, and retain natural page scrolling to the confirmation action.

- Replace the inline account form and desktop VNC sign-in UI with dedicated responsive account pages and a leased touch/keyboard browser surface. Add explicit YouTube account selection and preserve existing Amazon credentials until a replacement verifies.
- Show a red profile outline for disconnected Amazon sessions. Keep voice-started Alexa playback visible as read-only, block transport/queue mutations with an actionable message, and retain a logged-out device entry until handoff.
- Release signed-out Echo ownership without requiring an unavailable Amazon pause and fence open Echo streams by output token, including same-song handoffs. Already buffered Echo audio still requires Amazon access to stop immediately.

- Preserve saved Amazon remote-control credentials when AlexaPy resets a failed restore attempt; explicit logout still removes them. Voice-skill progress is independent of this remote-control session. Remove the empty-device discovery toast.

- Atomically validate and commit Alexa buffered starts against the live queue, retain deletions across duplicate events and backend restarts, and prevent stale corrections from cancelling newer manual selections. Cover deletion at the final-second boundary followed by a new song selection.

- Fix playback-lock re-entry in stale Alexa buffered-track correction, queue artwork backfill and implicit-current-track likes. Add production-lock regressions and a guard against lock-taking helpers inside playback commits.

- Match artist playlist/featured/additional shelves to the existing cover-card layout, preserve clickable artist credits, align shelf arrows beside the existing View all button style, and replace manual collection pagination with cover skeletons and automatic loading.

- Expand native Home fetching from six to forty shelves, preserve one-item shelves and distinct recommendations with the same heading, and retain additional artist shelves such as live performances and library items.

- Add optional artist playlists and featured-on shelves, full website collection navigation and paging, with empty sections hidden.
- Keep the device selector arrow fixed for long names, disable desktop song-row swipe gestures, and give Jam its full now-playing artwork canvas.
- Decouple Music Box ingress/startup from VPN networking, prefer authenticated VPN egress with direct fallback, and restore VPN endpoint attachment after restarts. Update Caddy deployment example and network setup documentation.

- Fixed a server deadlock when switching paused or playing mobile audio to Alexa. Ownership and the frozen cursor now commit without re-entering the playback lock; stale-owner and stale-track checks remain enforced. Added a regression using a non-reentrant production-style lock.

## 2026-10-25 — Alexa final-second transitions and dispatch

Commits: [`faa30f1`](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/faa30f14d1443a519e8661b3b348f81918956a26), [`08139f1`](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/08139f16aac382d80703d59104838c4ecd1fb40a).

- Ignore an old finish during staged Next and an old same-song stop during seek before it can pause the new intent or poison the event watermark. Retain legacy timestamp/pause behavior and exact-token checks.
- After a genuine finish without a next start, recover against the live queue after a 1.5-second grace. Recheck song, token, intent, renderer/output token and pause/new selection before dispatch.
- Reject removed/stale prefetched successors, including without a protocol-2 decision; retain valid loop prefetch and finite-queue-end behavior.
- Keep explicit Pause, newer commands and phone ownership authoritative over delayed recovery.
- Reduce default selection debounce from 700 to 150 ms while retaining burst coalescing, in-flight guards and bounded confirmation recovery. This does not eliminate Alexa startup/seek buffering.

Evidence: [final playback/browser workflow](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/actions/runs/40127521237) passed 841 Python tests across 53 files, 31 JavaScript regression files, 44 skill/controller SDK tests and 35 browser tests, plus resolver tests/compilation. Three new boundary failures were reproduced before correction. The valid-loop regression was added before final verification.

One live seek probe returned HTTP acknowledgment in about 90 ms and server confirmation after 3.7 seconds. Its longer boundary attempt was interrupted by a separate Pause request; the original queue/paused cursor was restored. No audible-latency distribution or successful live boundary transition is claimed by that attempt.

## 2026-10-24 — Progressive streaming and current-track-only retention

- [`324f6e4`](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/324f6e4) introduces the private persistent YouTube.js YTMUSIC resolver and progressive AAC/MP4 relay, replacing download-first/per-song process startup for normal playback.
- [`be75170`](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/be75170) removes persistent multi-song server storage, cache budget/sweeper and saved-song cache status UI. Retain only received current-track bytes in an anonymous temporary file; URL prewarm and next-track prebuffer do not retire it prematurely.
- Keep authorized ranges, HEAD, validators, pinned representation, bounded resolver/stream slots, cancellation, credential reset and sanitized retryable errors. Explicit downloads stream to clients; `wait=1` does not mean full-file staging.
- Preserve separate metadata/artwork/auth caches and Android offline storage. Historical PR #1/#37 audio LRU settings are retired, not a second active song-cache mode.

See [streaming implementation and checks](docs/STREAMING_AUDIO.md). Recorded first-byte stream measurements exclude Alexa invocation/device startup.

## 2026-10-24 — Shared feedback, metadata, refresh and failover

Commits `8cefb14`, `7d50f25`, `9b5ed35`, `111b61d`, `9ff1ddb`, `cc2296d`, `1a6080b`:

- Release confirmed transport presentation promptly so remote controls repaint rather than waiting on stale local intent.
- Wake handoffs and concurrent browser ownership watchers, accept a frozen phone-to-Echo cursor atomically and preserve presentation during transfer.
- Recover YouTube titles/durations through metadata fallbacks; bind remote seek/remove to exact songs/occurrences and distinguish resume loading from ordinary seek presentation.
- Retain browser identity during refresh/reconnect grace without a false disconnect error or arbitrary switch to another device.
- Fail over a genuinely closed/disconnected active renderer only to a server-validated eligible foreground target, preserving cursor/play intent; lost unload delivery relies on presence expiry.

## 2026-10-22 to 2026-10-23 — Browser playback, controls and deliberate UI reversions

PRs [#43](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/43)–[#50](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/50):

- Add “This browser” as a renderer under the phone-style acknowledged output protocol, with lease expiry, staged seeks, pause during loading and upcoming URL warmup.
- Improve offline Echo/default-device resolution, source pause acknowledgment, device handoff presentation and shared loading/error delivery.
- Keep background presence/media reports out of page-loading feedback; avoid one controller consuming another controller's playback error.
- Bind remote browser seeking to the requested song/queue occurrence and avoid seek-to-end accidentally triggering auto-next when catalog/file durations differ.
- **Reversions:** #47 restores pre-#45 collection controls; #49 restores playlist presentation from before #43. The browser renderer, seek/output/status fixes remain; the reverted floating/header/description experiments must not be described as the current web playlist UI.

## 2026-10-18 — Bounded preparation and monotonic state

PR [#42](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/42):

- Replace unbounded duplicate preparation threads with a keyed, bounded priority pool and speculative-work limits.
- Fence web responses by output/intent generation and publish failed-command rollback with a newer playback revision.
- Expose bounded preparation telemetry and update isolated/state-ordering regression coverage. The pool later prepares streaming URLs rather than persistent audio downloads.

## 2026-10-15 — Operational diagnostics

PRs #36–#41 add the owner-only `/status` dashboard, resource/request/cookie diagnostics, bounded live journal/error inbox, lazy details, child-route session authorization and responsive/top navigation corrections. Refresh does not itself contact providers to certify credentials or prove audible playback.

PR #37 introduced a shared persistent 5 GiB LRU and related inventory telemetry at this point in history. That audio-storage implementation and its saved-song UI were removed on 2026-10-24. Resource, streaming, credential and journal diagnostics remain.

## 2026-10-07 to 2026-10-11 — Multiple devices, recovery and Alexa queue decisions

PRs #21–#35:

- Add acknowledged online mobile devices, 15-second presence expiry, exclusive phone-to-phone/browser/Echo handoffs and direct native source-pause acknowledgment.
- Preserve exact paused cursors, target volume steps and current queue metadata; reject stale/expired handoff acknowledgments and wrong-owner writes.
- Keep disconnected receivers paused in the historical policy; later guarded foreground close-failover refines active-disconnect behavior without auto-playing saved idle queues.
- Negotiate up to eight-second presence waits, send atomic handoff snapshots and avoid renewing stale devices when a wait returns.
- Route custom-skill pause through the correct transport; bound consecutive stream-failure skipping and cancel stale delayed recovery.
- Validate live queue decisions before Alexa ENQUEUE, replace buffered successors and apply one fenced boundary correction. Preserve cached resume and ordering of same-song callbacks.
- Stage seek/volume changes before dispatch, preserve earlier-arriving confirmations and keep available paused outputs selected.
- Verify subscription mutations without weakening JSON/session security guards.

## 2026-10-04 to 2026-10-06 — Native-app integration, catalog and persistence

PRs #1–#20:

- Introduce a side-effect-free app `/audio/` endpoint separately from the Echo `/proxy/` path, with ranges and authenticated metadata/audio errors.
- Add account collections/saved albums, artist metadata/durations, resilient renderer normalization and bounded album/single/song pagination.
- Validate audio cookies independently from account browsing, replace them atomically and avoid an expensive audio probe in every ordinary account startup check.
- Preserve exact duplicate-song cursor/occurrence identity, canonical queue-route session authorization and atomic full-playlist installation.
- Add output leases, token-fenced source pause acknowledgment, shared queue ownership and authenticated/idempotent revisions.
- Persist shared metadata/cursor in SQLite while restoring paused content rather than active leases; issue audio-only session credentials.
- Read complete playback playlists without trusting inflated provider track counts; start promptly and backfill while separating foreground/audio/polling budgets.
- Resolve Alexa voice/automatic Next from the live server queue and fence event timestamps at their supplied precision.
- **Reversion:** #3 reverts the early #2 staging/shared-queue integration. App queue support is subsequently reintroduced by #7 and follow-ups; current APIs are verified against source rather than inferred from #2 alone.

## 2026-07 to 2026-09 — Foundation and earlier playback/UI work

The earlier reachable history builds the personal Alexa custom skill and recommendation queue, Flask remote, SQLite history/playlists/likes, imported YouTube playlists, account browsing, mobile/desktop player and queue interactions, animations, lyrics/search/layout behavior, range/resume handling and YouTube authentication/streaming work. Detailed commit subjects and original dates are preserved in the full index below. These foundational entries are not a current latency, storage or hosting-cost guarantee.

## Pull request register

All 50 PRs returned for this repository were reviewed as of 2026-10-25; none was open. A merged PR is historical provenance, not proof every part of its implementation remains current. PR numbers mentioned in inherited commit subjects can belong to an upstream repository.

| PR | Title | State | Current relevance |
| --- | --- | --- | --- |
| [#1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/1) | Add /audio/ endpoint for app clients and make the audio cache a real LRU | Merged | Audio API survives; persistent LRU storage was retired on 2026-10-24. |
| [#2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/2) | Use HTTPS staging and add shared queue operations for the app | Merged | Reverted by #3; later app routes were reintroduced in #7 and follow-ups. |
| [#3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/3) | Revert "Use HTTPS staging and add shared queue operations for the app" | Merged | Reverts #2; not the final app/backend interface. |
| [#4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/4) | Expose account collections, artist metadata and device listening reports | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/5) | Fix artist collection arrows across YouTube renderer layouts | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/6) | Update the PRs from master to backend to master | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/7) | Restore device queue sync and complete artist release browsing | Merged | Reintroduces app queue support after the earlier revert. |
| [#8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/8) | Fix artist release pagination and shorten native account startup checks | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/9) | Validate playback cookies with an audio download and allow replacement | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#10](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/10) | Fix shared queue cursor for repeated playlist songs on phone | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#11](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/11) | Restore song results when mixed music search contains only albums or videos | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#12](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/12) | Fix session authorization for canonical phone queue requests | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#13](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/13) | Support full playlist installation on the phone queue backend | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#14](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/14) | Coordinate one shared queue and exclusive phone/Alexa playback | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#15](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/15) | Preserve shared playback intent and cached resume position during output handoffs | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#16](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/16) | Unblock phone-to-Alexa handoffs with direct pause acknowledgement | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#17](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/17) | Optimize full playlist playback and prevent audio/polling rate-limit contention | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#18](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/18) | Load complete playback playlists without trusting inflated track counts | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#19](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/19) | Persist playback state and secure audio with scoped session credentials | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#20](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/20) | Keep Alexa next on the shared queue and fence delayed handoff events | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#21](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/21) | Fix playback regressions and add exclusive mobile device handoffs | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#22](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/22) | Connect web and phone playback controls and fix audio handoffs | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#23](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/23) | Feat/backend for app | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#24](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/24) | Fix custom-skill Alexa pause transport | Merged | Uses the custom-skill pause transport, not a generic provider pause. |
| [#25](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/25) | Feat/backend for app | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#26](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/26) | Bound Echo failure recovery and refresh device presence promptly | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#27](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/27) | Merge pull request #26 from arookiecoder-ip/fix/echo-stream-tablet-controls Bound Echo failure recovery and refresh device presence promptly | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#28](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/28) | Keep disconnected phone playback paused and select silent foreground defaults | Merged | Historical paused-disconnect policy; later foreground close-failover refines it. |
| [#29](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/29) | Merge pull request #28 from arookiecoder-ip/fix/remote-phone-stability Keep disconnected phone playback paused and select silent foreground defaults | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#30](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/30) | Recover closed phone playback and reject stale handoff acknowledgments | Merged | Historical explicit-resume fallback; later close-failover adds guarded continuation. |
| [#31](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/31) | Merge pull request #30 from arookiecoder-ip/fix/orphaned-phone-resume Recover closed phone playback and reject stale handoff acknowledgments | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#32](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/32) | Verify unsubscribe requests without weakening the session security guard | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#33](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/33) | Reduce background traffic and remove redundant device handoff requests | Merged | Presence waits, atomic handoff snapshots and lower background traffic. |
| [#34](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/34) | Fix Alexa buffered queue races and cached resume state | Merged | Buffered decision/token protocol retained and extended by 2026-10-25 fixes. |
| [#35](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/35) | Fix Alexa seek/volume races and preserve paused playback output | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#36](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/36) | Add advanced operational status dashboard at /status | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#37](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/37) | Retain songs in a shared 5 GiB cache and redesign /status | Merged | Historical shared 5 GiB cache; superseded by current-track-only streaming. |
| [#38](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/38) | Add live logs, unseen error alerts and lazy cache diagnostics | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#39](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/39) | Fix status detail dialogs for owner login sessions | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#40](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/40) | Fix status dashboard spacing and responsive navigation | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#41](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/41) | Fix status navigation, controls and activity reporting | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#42](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/42) | Bound audio preparation and harden shared playback state | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#43](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/43) | Add PC playback and fix playlist, search, and offline playback | Merged | Browser playback retained; playlist presentation was later rolled back by #49. |
| [#44](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/44) | Fix offline Alexa fallback and browser playback seeking | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#45](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/45) | Fix browser seeking, device handoff, collection controls and audio search heroes | Merged | Seeking/handoff fixes retained; collection styling was rolled back by #47/#49. |
| [#46](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/46) | Prevent paused-state flashes during website device switching | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#47](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/47) | Restore previous web collection controls | Merged | Reverts later collection styling while retaining playback fixes. |
| [#48](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/48) | Fix shared playback status latency and loading feedback | Merged | History/reference; verify current behavior in ARCHITECTURE.md. |
| [#49](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/49) | Restore original playlist UI from before PR #43 | Merged | Restores playlist UI from before #43; later playback fixes remain. |
| [#50](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/50) | Prevent remote browser seeks from changing or targeting another song | Merged | Binds remote browser seeking to the intended song/occurrence. |

## Complete reachable commit index

This index covers all **606 commits reachable from `08139f1`** on the documented branch, including imported/merged history. It is a provenance ledger, not a claim that each historical implementation is active. Documentation-only changes after this functional snapshot are outside the index.

<details>
<summary>Expand the complete commit history</summary>

| Commit | Commit date | Subject |
| --- | --- | --- |
| [08139f1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/08139f16aac382d80703d59104838c4ecd1fb40a) | 2026-10-25 | Retain valid loop prefetch while rejecting removed Alexa successors |
| [faa30f1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/faa30f14d1443a519e8661b3b348f81918956a26) | 2026-10-25 | Fence stale Alexa boundary events and recover the live queue successor |
| [1a6080b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1a6080bbd49fceb905903e59dab1238b691c174d) | 2026-10-24 | Keep browser refresh local while allowing safe close failover after a reconnect grace |
| [cc2296d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cc2296d18afcbab5a001ecc514079c6a2d33328b) | 2026-10-24 | Fail over closed playback devices to the current foreground device without losing cursor or play intent |
| [9ff1ddb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9ff1ddba111eece86a1f1367376bfd3371a3a8d4) | 2026-10-24 | Recover YouTube metadata and fence refresh, resume and remote queue races |
| [111b61d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/111b61d8fcde2d01fa02c39b944b3fe9491773db) | 2026-10-24 | Allow concurrent browser ownership watchers behind a shared connection |
| [9b5ed35](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9b5ed35c8f25685364879cec119e3c246d37890d) | 2026-10-24 | Wake browser handoffs immediately and accept frozen phone-to-Echo transfers atomically |
| [7d50f25](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7d50f256886d3d1e258025053703cb59160f2883) | 2026-10-24 | Release confirmed transport intent so remote changes repaint immediately |
| [8cefb14](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8cefb1445a78e04ed1839cda13b1ebd5a08f45fd) | 2026-10-24 | Speed up remote playback feedback and fix output presence edge cases |
| [be75170](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/be75170fa782b013f6fe3ac0364e332f21eea544) | 2026-10-24 | Replace persistent song storage with temporary current-track audio |
| [324f6e4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/324f6e4321fc7dc2263211555280b94661701bcd) | 2026-10-24 | Stream YouTube audio with a persistent music client |
| [5ffb5bd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5ffb5bdabfde1688f59ac34bbe60b7b488ffaddc) | 2026-10-23 | Merge pull request #50 from arookiecoder-ip/fix/seek-track-identity |
| [102a91f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/102a91fa6c21fac045b0cd4d76e210213c4aa0ba) | 2026-10-23 | Keep remote browser seeks bound to the intended track |
| [99c8949](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/99c89497c0e5786b50f8a1603ba8acb45fafaf85) | 2026-10-23 | Merge pull request #49 from arookiecoder-ip/restore/pre43-playlist-ui |
| [57a24a5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/57a24a5b9d0fb8e30bedfdb563c1a125aaca0059) | 2026-10-23 | Merge pull request #48 from arookiecoder-ip/fix/responsive-shared-playback |
| [eb609ae](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eb609ae65365c805b436711233693ea92ae56d5a) | 2026-10-23 | Restore playlist page UI exactly to pre-PR43 source |
| [19651d2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/19651d2e639299f2afe6e0647c86119ef6680114) | 2026-10-23 | Avoid redundant Ubuntu upgrades in browser CI |
| [2bce03d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2bce03dff15b85f7752a09ed37b5d6b89cff3c5a) | 2026-10-23 | Fix shared playback status latency and misleading loading feedback |
| [8f20a4c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8f20a4c1dd5308e7a701b47c0a356b02681c0260) | 2026-10-23 | Merge pull request #47 from arookiecoder-ip/revert/web-collection-ui |
| [50c8767](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/50c876703888786b4a1b7bdafd3485ca37c65fd3) | 2026-10-23 | Restore previous web collection controls |
| [8ea45e1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8ea45e1563c5939ccd913de74579752259dfa947) | 2026-10-23 | Merge pull request #46 from arookiecoder-ip/fix/handoff-playback-feedback |
| [603fe37](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/603fe37e97e1024aa2e917a55b597e282b8b9ca3) | 2026-10-22 | Preserve website playing feedback while switching audio outputs |
| [0d1c607](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0d1c60767c9608aa6e2997672d47c1f1ba5b0155) | 2026-10-22 | Merge pull request #45 from arookiecoder-ip/fix/seek-and-output-handoff |
| [0a2e2f2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0a2e2f23b26c745bf21a7ccf1149207dc8a2b597) | 2026-10-22 | Keep collection scroll header outside hidden and transformed page containers |
| [c56d2c9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c56d2c99929b89cb2bf6c011ae58d9d1e66ce254) | 2026-10-22 | Polish collection navigation and restore matching audio search heroes |
| [6fd3ae3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6fd3ae3154e988d7f51cfa984048ef27823b3b52) | 2026-10-22 | Keep browser seeks smooth and preserve playback through device handoffs |
| [7ea1bd9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7ea1bd9087ef1712736a44dbf4b969b8c327edb4) | 2026-10-22 | Merge pull request #44 from arookiecoder-ip/fix/browser-playback-reliability |
| [0729ff1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0729ff19c91607c937386ed1a073d97811fa494d) | 2026-10-22 | Release browser presence on navigation and refresh confirmed media controls |
| [9e323d4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9e323d4b8fc7667830ac8ab774f88af1de8f01fe) | 2026-10-22 | Fix browser output fallback and responsive seeking during audio loading |
| [c2cb2c8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c2cb2c8a03589ab211f520f6db1f691e3788571d) | 2026-10-22 | Merge pull request #43 from arookiecoder-ip/fix/playback-and-playlist-experience |
| [e1e7483](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e1e7483c8d5f3c499e6ca9cfda05fcb902644d0b) | 2026-10-22 | Allow browser playback without an Amazon session |
| [e2b2c0d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e2b2c0dd3398261b6eab419bac2e136532e33082) | 2026-10-22 | Add browser playback and fix playlist, search and offline playback behavior |
| [1ca23c4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1ca23c41101c41a32662173849118bf3761c9aea) | 2026-10-18 | Merge pull request #42 from arookiecoder-ip/fix/playback-architecture |
| [fc5733e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fc5733e1f7f4e5cef04ead3eb1a17de8fc378edc) | 2026-10-18 | Preserve playback revisions and confirmation across PWA tabs |
| [75f6fb5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/75f6fb59be824d890014e3859d9fd0ec4e1ce800) | 2026-10-18 | Update isolated fixtures and cover monotonic rollback behavior |
| [6dd10b9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6dd10b95721b8148edede0d038131fee46e12451) | 2026-10-18 | Bound audio preparation and enforce ordered playback snapshots |
| [5c3c619](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5c3c619bf075e0b7aa4dbfc34f4cccf934de68b1) | 2026-10-15 | Merge pull request #41 from arookiecoder-ip/fix/status-navigation-and-activity |
| [4c56d4c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4c56d4cf7da6331252b98a83f4933e517b6ff16f) | 2026-10-15 | fix: restore status top navigation and clarify cache and fetch activity |
| [9b631a5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9b631a5bd19662351a3bd6efcad9de818f9d700e) | 2026-10-15 | Merge pull request #40 from arookiecoder-ip/fix/status-responsive-layout |
| [7a85204](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7a85204b747a8d10a5bec38dfb864fca12ff6ed4) | 2026-10-15 | test: verify live dialog refresh and reserve log pagination space |
| [2c5e94e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2c5e94ec836b7dea72b1e2d91fb5410b1e6b3fa3) | 2026-10-15 | fix: organize status dashboard and prevent panel layout shifts |
| [40cf0ba](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/40cf0baaa0105abde831dbf1e9080afd6d6c22f9) | 2026-10-15 | Merge pull request #39 from arookiecoder-ip/fix/status-session-details |
| [451f09f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/451f09f2a33ca817ec40453fef0607f93db515b3) | 2026-10-15 | fix: authorize status detail APIs for signed-in owners |
| [bef41a3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bef41a3dd0db9dd7742ccbf66a034ae346be4838) | 2026-10-15 | Merge pull request #38 from arookiecoder-ip/feat/status-live-logs |
| [8069681](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8069681be4b60ce9dead43393527ceb9f138b539) | 2026-10-15 | fix: bound journal payloads by UTF-8 bytes |
| [31be9c6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/31be9c6cf8b077c9b993908b2b412d6cc8f95fe9) | 2026-10-15 | fix: reject malformed status credentials and protect exception frame metadata |
| [11311c1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/11311c1da7a9d5d9b541d59966c26edcd1055f44) | 2026-10-15 | feat: add bounded live logs, error inbox and lazy cache diagnostics |
| [176b4d3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/176b4d302bc3725e9ef3b267d82cf31e731de664) | 2026-10-15 | Merge pull request #37 from arookiecoder-ip/fix/shared-cache-status-redesign |
| [8e80cda](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8e80cda8f0d246ed57c1e2fe1ae76e71bc8ee375) | 2026-10-15 | fix: clarify status resource meters and section selection |
| [9248603](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9248603214a3eea81568a172e5605929503dc618) | 2026-10-15 | fix: retain music in a shared LRU cache and redesign status panel |
| [3714071](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/37140714d50cabaa1eba9fd9d4bc7b3828eef194) | 2026-10-15 | Merge pull request #36 from arookiecoder-ip/feat/advanced-status-panel |
| [76a3abf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/76a3abf45924c1841a95749cd784bc30da7cb74c) | 2026-10-15 | feat: add advanced owner-only operational status dashboard |
| [cb30827](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cb30827a7fa4976072d121a7cb9f59b534229d94) | 2026-10-11 | Merge pull request #35 from arookiecoder-ip/fix/alexa-seek-volume-paused-output |
| [673972a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/673972a232b0429f3c50c110a2eb050265cc71a0) | 2026-10-11 | Coordinate Alexa seek and volume updates and preserve paused output |
| [db29e7e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/db29e7ec7218a8fac44204487b899fba9adaa7f7) | 2026-10-09 | Merge pull request #34 from arookiecoder-ip/fix/alexa-queue-reconciliation |
| [801697e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/801697e2da7ca287ae260a1aff6ada5c0359153c) | 2026-10-09 | Reconcile Alexa buffered queue revisions and preserve cached resume state |
| [803337c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/803337c0520e2cca7b0acd0022209a656ffd9bff) | 2026-10-08 | Merge pull request #33 from arookiecoder-ip/fix/battery-presence-wait |
| [1df1636](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1df163627a42d1b5f1579fae6bddba6a66e38a6c) | 2026-10-08 | Merge remote-tracking branch 'origin/master' into fix/battery-presence-wait |
| [b92ddb6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b92ddb6dad6c65c70d9defc1c15d38463898f5c2) | 2026-10-08 | Merge backend updates and preserve handoff and orphaned resume fixes |
| [e3b9657](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e3b9657d1750fb8cebc52e05ca4eaa8236923b49) | 2026-10-08 | Fence ownership changes that race a handoff snapshot copy |
| [630af92](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/630af9254a66b7937fd680bb7bb8db12430b578e) | 2026-10-08 | Reject expired source pause acknowledgments before they can reset destination progress |
| [6a80349](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6a80349b6b8c3e22aa94ba7161fdbbf014fe60f3) | 2026-10-08 | Deliver atomic queue and cursor state in device handoff responses |
| [bd78b4f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bd78b4f23948c25f1152266a187585e8404f51da) | 2026-10-08 | Do not renew device presence from a completed server wait |
| [c60eec1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c60eec176589e253ff403590bf6153ad35d7109b) | 2026-10-08 | Advertise negotiated presence wait support for safe app rollout |
| [f9a5792](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f9a5792148902b7ea2bbbb9b6631caa7a77e9bf7) | 2026-10-08 | Negotiate longer mobile presence waits without extending stale device lifetime |
| [2b84d95](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2b84d95f7e5da78949b08ee83a0bf859ca11653a) | 2026-10-08 | Merge pull request #32 from arookiecoder-ip/fix/subscription-request-tests |
| [2fc4c50](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2fc4c50ce01b0b72b0d701ef6975980d05c5a959) | 2026-10-07 | test: verify unsubscribe JSON requests and preserve CSRF protection |
| [9245b66](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9245b666db26531b4c962c0b4744fb33e5ffbd0d) | 2026-10-07 | Merge pull request #31 from arookiecoder-ip/feat/backend-for-app |
| [a49c47c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a49c47c03d184e459dcd1d9d11d3323c6bd830fd) | 2026-10-07 | Merge pull request #30 from arookiecoder-ip/fix/orphaned-phone-resume |
| [b5ae799](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b5ae79962b6510f17d7c0d79e11373f7bef2a772) | 2026-10-07 | fix: reject handoff acknowledgments after the source lease expires |
| [a6af911](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a6af9112a9fa88f2c284089e628b55a0c48061fa) | 2026-10-07 | fix: recover orphaned phone playback on explicit resume |
| [29fa784](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/29fa78493f8943ad41364cd8286972b43b8d10b6) | 2026-10-07 | Merge pull request #29 from arookiecoder-ip/feat/backend-for-app |
| [c3dafbf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c3dafbfeddca2b15bb0b1963dc3b9f9ec25f1763) | 2026-10-07 | Merge pull request #28 from arookiecoder-ip/fix/remote-phone-stability |
| [6f3f713](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6f3f713e1292da3eb2310db0baea9516b1c69336) | 2026-10-07 | Verify target volume levels through presence routes |
| [6ed1ae9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6ed1ae91ade37fb498d79cb5e891f8c0c4fee0fb) | 2026-10-07 | Expose validated target volume levels for remote hardware controls |
| [a98cc99](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a98cc99381ddbd246956c0d5fc14dadce0dd05fa) | 2026-10-07 | Capture idle queue metadata under the state lock |
| [7e70632](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7e70632995e28fd461b78d5c29602a90287e2027) | 2026-10-07 | Select current phone silently on foreground when shared playback is idle |
| [56fc415](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/56fc41539a3246b4af53cdd01e7c2ca92b3f3cb5) | 2026-10-07 | Report a disconnected playback target once per session |
| [f7b52c4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f7b52c4241840c9a0ec52b91e3635f3c1d6e366a) | 2026-10-07 | Merge remote-tracking branch 'origin/feat/backend-for-app' into fix/device-control-handoffs |
| [cc31403](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cc314037be0026f433cc48a8714ca9d6d831a100) | 2026-10-07 | Pause disconnected remote phones without auto-playing the controller |
| [81e5e5b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/81e5e5b721ab904a93ea658200b0e132fdf687d2) | 2026-10-07 | Merge pull request #27 from arookiecoder-ip/feat/backend-for-app |
| [b2ba20f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b2ba20fcd078cab62ab1c156da220a6a83c06910) | 2026-10-07 | Merge pull request #26 from arookiecoder-ip/fix/echo-stream-tablet-controls |
| [af01d72](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/af01d7260eb8a0340adfe4e7370682178d4f144d) | 2026-10-07 | Isolate resume identity checks from live audio downloads |
| [a09d0ab](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a09d0ab98f4f45d4d43f0d594d87dd0ffa7e05c7) | 2026-10-07 | Fence deferred failure recovery against concurrent transport changes |
| [75e85df](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/75e85df99d271ede95be98a0b59ceb51716e6219) | 2026-10-07 | Bound Alexa song failure recovery and expire stale device presence |
| [ef10840](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ef108403fed2ae63cf8b723643b3fcbbc787e703) | 2026-10-07 | Fence stream failures by playback revision and retain queue on transport outages |
| [36bae15](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/36bae1590ebe20a703074abdb9e5d4b73c5e2e3f) | 2026-10-07 | Keep stream watchdog active for nonfatal buffering notices |
| [0ea9d5b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0ea9d5b3703ae0f557ecc03e08e224a4a9a3938a) | 2026-10-07 | Normalize Alexa stream origins and report scoped playback failures without endless skips |
| [1329889](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/132988959ca33d17b1ce11329a00385fd2a73c58) | 2026-10-07 | Merge pull request #25 from arookiecoder-ip/feat/backend-for-app |
| [bf8c32a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bf8c32aed7a7c48fb552ad0e0f67ba233e9879fa) | 2026-10-07 | Merge pull request #24 from arookiecoder-ip/fix/device-control-handoffs |
| [223666e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/223666ee13262210957228b89cd916bd9058cf88) | 2026-10-07 | Route Echo pause through Music Box custom skill instead of provider transport |
| [1c79a21](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1c79a21b0792bee38409853c00447094a14f400b) | 2026-10-07 | Merge pull request #23 from arookiecoder-ip/feat/backend-for-app |
| [51d8864](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/51d88644755ec814e051a4cd4d81980f425f5522) | 2026-10-07 | Merge pull request #22 from arookiecoder-ip/fix/device-control-handoffs |
| [d31e4df](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d31e4dfd7960c326b7ac1fe1a7cd0318e76f5e5f) | 2026-10-07 | Connect web and phone playback controls with fast native pauses and offline fallback |
| [b63d896](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b63d89659870906c6d3e7fb8046ee4dcaab33a05) | 2026-10-07 | Merge pull request #21 from arookiecoder-ip/fix/connect-playback |
| [0b4a8d2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0b4a8d2ef7ab639004ceef6514fadb2694a266a9) | 2026-10-07 | Isolate resume tests from prior playback state |
| [eadb0a3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eadb0a3f40e8964246237e1cc3c55a212bd46401) | 2026-10-07 | Restore nested playback scenario mocks in reverse order |
| [703a455](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/703a4550c083c1f7fec7978eb0af28845e170ec7) | 2026-10-07 | Publish repeated audio confirmations outside playback lock |
| [fea035a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fea035a6af0db72cb060de7c3d47c14bf6239552) | 2026-10-07 | Isolate warm-cache race fixture from background prefetch |
| [dc151b6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dc151b668ca5c51820d3bd252e07ec7998a35024) | 2026-10-07 | Fence pending phone claims and superseded handoff acknowledgements |
| [eaecdaf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eaecdaf14490510bb37c75e66c7834b6d4e58cc6) | 2026-10-07 | Allow authenticated remote queue tools on the active mobile device |
| [38f60e2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/38f60e264bbbebaa3fdd64e2f3e28843640301bd) | 2026-10-07 | Exclude SQLite test journals from source control |
| [e67c413](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e67c4139953f8594e30d2ac475bb8ae4777233ad) | 2026-10-07 | Preserve source position and play state in device pause acknowledgements |
| [700ff11](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/700ff112aff52a4ac559d3eaf6566af4663da0ef) | 2026-10-07 | Preserve latest app backend playlist fast-start changes |
| [453809e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/453809ea08d9b4f589c469fc6069809532072b4f) | 2026-10-07 | Add online mobile playback ownership and repair queue and streaming regressions |
| [1df6630](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1df66309c127f358625b1e7509921d7b5220f03b) | 2026-10-06 | Fast playlist starts: brief play_queue responses for bulk installs |
| [2c41d97](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2c41d97ff619295c7bfd24fdec33e150d6cebe3b) | 2026-10-06 | Implement comprehensive testing suite for audio server improvements |
| [1339dce](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1339dce9032eae49f0f8b1d4bf92ec654b3de8cd) | 2026-10-06 | Merge pull request #20 from arookiecoder-ip/feat/shared-output-ownership |
| [432936b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/432936b079fa4fae875a95d1e6c249574437eede) | 2026-10-06 | Fence delayed Alexa starts after confirmed phone ownership handoff |
| [761952d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/761952dfc61c5832f746f260abfd022afc8d201c) | 2026-10-06 | Compare Alexa event watermarks at the precision supplied by the skill |
| [30128ea](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/30128ea15b36d5ed7c4744f6788270d4122894ca) | 2026-10-06 | Resolve loop wrap from authoritative queue instead of stale skill window |
| [bcc4e03](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bcc4e0367db032570964c17124a6accb2b8c99b9) | 2026-10-06 | Handle whole-second Alexa event timestamps without rejecting immediate controls |
| [4f82927](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4f8292768736dc8e7ce8aea72fc548cf71af1613) | 2026-10-06 | Resolve Alexa voice next from shared queue and ignore delayed playback events |
| [afdafaa](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/afdafaa749979fb2e1d57b591ed3af40c032c40e) | 2026-10-06 | Merge pull request #19 from arookiecoder-ip/feat/shared-output-ownership |
| [1003948](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/10039482b8298f3116aee748923c6546b2dc4367) | 2026-10-06 | Resolve duplicate-song continuation from the authoritative queue cursor |
| [d5aeb02](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d5aeb02dca80fedac8d94719f7f0188874f08eb9) | 2026-10-06 | Retain queue occurrence identity through legacy skill metadata refreshes |
| [22f8d4e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/22f8d4e5efb5009ce2a3fee81ef27193fff30e12) | 2026-10-06 | Persist shared playback metadata and add scoped audio sessions and idempotent queue edits |
| [e6dfb6b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e6dfb6b1d1cdbb7094850428be394b26aee0745d) | 2026-10-06 | Merge pull request #18 from arookiecoder-ip/feat/shared-output-ownership |
| [737248b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/737248b0ac5c620faf1bbfaf76f266b07d04f9ec) | 2026-10-06 | Use complete web playlist fetches and ignore inflated counts for playback pagination |
| [06a682c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/06a682cc0741489e2aed65edc8c47ae9f7627be4) | 2026-10-06 | Merge pull request #17 from arookiecoder-ip/feat/shared-output-ownership |
| [2d35390](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2d353905461488895c09202d26297a166bbb3b0f) | 2026-10-06 | Keep bulk cached downloads from exhausting foreground audio budget |
| [a903c59](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a903c5951439e6f11a964fed2ebabd3a9223fdf9) | 2026-10-06 | Separate cached audio bursts from expensive cold download limits |
| [cb80a36](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cb80a3695adc6498154fcbc100b4e2ceeb0c70bc) | 2026-10-06 | Fetch bounded playlist playback queues once and isolate ownership polling rate limits |
| [ebbeec0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ebbeec000b8bc12b872595b4a09de43d797915a7) | 2026-10-06 | Merge pull request #16 from arookiecoder-ip/feat/shared-output-ownership |
| [f25d5dd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f25d5ddf09506d68ebaa42e18a4881ae6f485833) | 2026-10-06 | Accept guarded source pause acknowledgement without waiting for phone polling |
| [a87c68b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a87c68bf278ab7bb123e1270dd0417bd52fdcefd) | 2026-10-06 | Merge pull request #15 from arookiecoder-ip/feat/shared-output-ownership |
| [1ce329a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1ce329a3cf7dbdcec890558fadcfeede30cc45d2) | 2026-10-06 | Reject superseded phone handoffs without disturbing current playback |
| [6380072](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6380072ae00349830413680ed9080910e20dc9e5) | 2026-10-05 | Honor shared resume positions on cached Alexa songs and cover seek handoff contract |
| [4a9b244](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4a9b244f7236893fdcea34752fab651d9f5712d5) | 2026-10-05 | Reject delayed phone reports after a newer phone play intent |
| [65c2b73](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/65c2b733cdc5d8d061cfd86054457034584f61cf) | 2026-10-06 | Merge pull request #14 from arookiecoder-ip/feat/shared-output-ownership |
| [a0bd2a9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a0bd2a9084670a58c60881634d7ee261c32273c2) | 2026-10-05 | Keep phone progress independent of Echo online status |
| [ad1827d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ad1827de0d29ba89667e1cbd3eb711281c5bdeaa) | 2026-10-05 | Exercise real web play handoff and failed dispatch loading |
| [31bb3eb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/31bb3ebb2a14d0b67af93ac2a9e1630e61157733) | 2026-10-05 | Keep delayed radio fetches from taking phone playback ownership |
| [1fb92ae](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1fb92aeb271ec4095388f44967a1ed01a0cd96a3) | 2026-10-05 | Share queue ownership across phone and Alexa without parallel playback |
| [c9989f9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c9989f9f89762eaec0f078312349f5e22cbd5219) | 2026-10-05 | Merge staging backend features into production |
| [d425189](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d425189ccceccd2703c766e89b3837f4a951825e) | 2026-10-06 | Merge pull request #13 from arookiecoder-ip/fix/atomic-phone-queue |
| [0b1bfdd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0b1bfddc5fefd7017e9d1df3f8c38f0ff382a598) | 2026-10-05 | Allow atomic phone installation of full collection queues |
| [f648f23](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f648f23a0106279d33d0d23b221f56fd62d8bd81) | 2026-10-06 | Merge pull request #12 from arookiecoder-ip/fix/shared-queue-session-auth |
| [f857839](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f85783923f474a3271cdec86dbaf3009e6f1dd6b) | 2026-10-05 | Check canonical queue URL updates without a redirect |
| [5052e74](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5052e74f088c6f00d02d558a1adeb54887fc9573) | 2026-10-05 | Normalize session authorization for phone queue endpoint |
| [2286853](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2286853a80396499eec200c62d8accdea6b59ca0) | 2026-10-05 | Merge pull request #11 from arookiecoder-ip/fix/app-search-song-fallback |
| [a5da941](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a5da9415c19b14113a2c7b5dd07ce453c1e2204e) | 2026-10-05 | Return audio songs when mixed search only contains albums artists or videos |
| [e114a99](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e114a997dc2d85599fd1e79a940d2bf4a450d8c9) | 2026-10-05 | Merge pull request #10 from arookiecoder-ip/fix/phone-queue-cursor |
| [dfba442](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dfba442359f72cffb7670d6afa99a32f1f3898a1) | 2026-10-05 | Preserve exact phone queue cursor for repeated playlist songs |
| [b027ba8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b027ba87bc0f53ae77c9fa705f7b70421e705c09) | 2026-10-05 | Merge pull request #8 from arookiecoder-ip/fix/artist-release-continuations |
| [47f62e9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/47f62e965a3693db226599cfcada4780c3cbe993) | 2026-10-05 | Keep native account startup checks free of audio download probes |
| [0a76249](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0a762490736fc1db8a57a49e1a2aef1ddd863463) | 2026-10-05 | Merge remote-tracking branch 'origin/feat/backend-for-app' into fix/artist-release-continuations |
| [5d23c4b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5d23c4bc23cac26115c492b8cb71855c9bd00daa) | 2026-10-05 | Merge pull request #9 from arookiecoder-ip/fix/download-cookie-validation |
| [eec4e8b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eec4e8bb863c77283bfaa866fb1502427ad20ca0) | 2026-10-05 | Validate download cookies with audio and allow atomic replacement |
| [f6ea057](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f6ea0571b6a0e06fa8629ccfe925f147c4be180e) | 2026-10-05 | Validate download cookies with audio and allow atomic replacement |
| [70df090](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/70df090f5317c3d70b545ebd87d91347c2ff2b71) | 2026-10-05 | Follow modern artist release footer tokens and appended continuation pages |
| [f553635](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f553635755c44353c077bc99ae789d1f7bd1fc33) | 2026-10-05 | Merge pull request #7 from arookiecoder-ip/fix/device-queue-and-playback |
| [7a40a3e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7a40a3ee75cf149656fd962dfa53854f73bfd277) | 2026-10-05 | Only treat an actual empty release grid as an empty collection |
| [78c3940](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/78c3940eb6f77b205d5e6d1871770c1199df2508) | 2026-10-05 | Restore device queue synchronization and handle remaining artist release layouts |
| [88a6e15](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/88a6e156c4278c58c71460f62efd34e48d55b46b) | 2026-10-05 | Merge pull request #6 from arookiecoder-ip/master |
| [14c3067](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/14c30677ffd3f7196cb7f368b4b5e9eb76d77f09) | 2026-10-05 | Merge pull request #5 from arookiecoder-ip/feat/library-collections |
| [88b44e6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/88b44e696756a6082c020d1b63ae0bcba4aa7496) | 2026-10-05 | Preserve artist account errors and exercise release continuation loading |
| [5e96494](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5e964942d85e5c8b91a4ef81596f1fb0b605f407) | 2026-10-05 | Use resilient artist parsing and normalize full release collection layouts |
| [1b5481b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1b5481b0c25164a6c840771aa0124a4400a0dbab) | 2026-10-05 | Merge pull request #4 from arookiecoder-ip/feat/library-collections |
| [8000722](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8000722fe3b31f19ce55943a5d50d80c066f57d7) | 2026-10-05 | Record confirmed device playback in authenticated YouTube listening history |
| [894c0c7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/894c0c7ebc637b6e89d9178c3f447a7733413edd) | 2026-10-05 | Resolve preview durations when queued metadata has no length |
| [1cde0d8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1cde0d84f0e697b5bdfcbd872ee611cdeaeebaf8) | 2026-10-05 | Expose bounded artist release pages and read-only song metadata |
| [88cb405](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/88cb405c00a85e6aca7af4e66b03cb365602bba1) | 2026-10-05 | Include account saved albums in library collections response |
| [cc4d90c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cc4d90c7c7f1a2320e762a22ea33d5d7f092899c) | 2026-10-04 | fix: enhance healthcheck and ensure virtual display readiness in entrypoint |
| [27bdee9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/27bdee94916852bfaa8236a0b96099c28c06d443) | 2026-10-04 | Merge pull request #3 from arookiecoder-ip/revert-2-feat/backend-for-app |
| [cef6015](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cef6015846b9bf818a722565fe2586d712e61950) | 2026-10-04 | Revert "Use HTTPS staging and add shared queue operations for the app" |
| [d53f3c0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d53f3c038be7f7fca8a89c327853f562179b0e4f) | 2026-10-04 | Merge pull request #2 from arookiecoder-ip/feat/backend-for-app |
| [687986a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/687986aac8641599be0aef58bfd8071ed4e31a76) | 2026-10-04 | fix: preserve shared queue during phone radio fetches and report playback |
| [8713168](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8713168ceaea2115b4f94089d9954791c4eba0cc) | 2026-10-04 | fix: use HTTPS staging and support the app shared queue |
| [a0548d4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a0548d4afdf807c49f34dd076c7d5fe4394f221e) | 2026-10-04 | Merge pull request #1 from rajeet-04/feat/audio-endpoint-lru-cache |
| [279395f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/279395fd66c257925c0a6142bcfabd6e73b8f5be) | 2026-10-04 | Add /audio/ endpoint for app clients and make the audio cache a real LRU |
| [4acb810](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4acb8103b8d65e4f32cadbfda19b9a3c4da5587c) | 2026-10-04 | feat: add standalone staging compose (direct IP:port, no Caddy/hostname change) |
| [e7af0b8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e7af0b8153dfce6484cf86bb4f5e8dd7c8d33b6c) | 2026-09-19 | feat: enhance track promotion logic to prevent incorrect playback state |
| [0eeb830](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0eeb830f54f86106db192b9816788a8e96b27d5e) | 2026-09-19 | Implement tap-and-hold release guard for context menus |
| [96f1d70](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/96f1d704ce900f972bb96f8ae4b59eb4d80fa405) | 2026-09-18 | feat: add album context menu and enhance playlist options |
| [d013a4c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d013a4cdb64d77ffaa6bf9e114b8b30efcbf6f1b) | 2026-09-18 | feat: enhance page title synchronization for playback and background states |
| [2591201](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2591201b3d0a20ed945f5112fd8fd3aa4e83f09c) | 2026-09-18 | Enhance mobile context sheets and player interactions |
| [11a4030](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/11a403022d389dfe1bf1289fad265ef408f4d917) | 2026-09-18 | feat: improve range resume handling and cache management for audio streaming |
| [5d9b5d0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5d9b5d0a77267243718f3bcaa895c5527c2553d6) | 2026-09-18 | feat: enhance animation durations and transition handling for artwork and volume controls |
| [3d37071](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3d3707162fa955b28001ceaf5f7055752a64e4b5) | 2026-09-18 | feat: improve animation timings and user scroll handling in player and queue components |
| [5042090](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/50420909cf8f4189324897a43877ed7aa6c0b69f) | 2026-09-18 | feat: enhance play/pause state management and snapshot confirmation handling |
| [bc6a4f7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bc6a4f7e63a1d51e26d962ff35990bc34f9c0932) | 2026-09-14 | Merge branch 'master' of https://github.com/arookiecoder-ip/youtube-music-alexa-skill |
| [c9be320](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c9be32058387e90c024832f260b6a1e539e0f937) | 2026-09-11 | feat: implement resume handling improvements with armed play and state management |
| [6c82f57](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6c82f57dd253c02b8006f49cb45cb8b58f1c5eb1) | 2026-09-11 | feat: implement resume confirmation handling with timeout and retry logic |
| [a7ffaad](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a7ffaad29f1a3f432ba317f7f99b05e61f1c3248) | 2026-09-11 | feat: enhance playback state handling with confirmation and processing flags |
| [58ed225](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/58ed225121b95023c44ed66fed81cb20e28192a4) | 2026-09-11 | feat: improve dynamic page title and background tab handling for now playing updates |
| [30a3e34](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/30a3e3425078fdf294b926e8e3d48e34360f86a8) | 2026-09-11 | Add regression test for queue reordering without auto-scroll |
| [3a705dd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3a705dd99e695d1cdc91a15f92a5b3e47ebbe0c4) | 2026-09-11 | feat: enhance header behavior and now-playing overlay interactions for smoother transitions |
| [75e53dd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/75e53dd84bdf21a913bde43001f081e61567c289) | 2026-09-11 | Add mobile now-playing overlay functionality and regression tests for queue reordering |
| [36c205e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/36c205e5c918d5883d1accf1ad06d00091e5677f) | 2026-09-03 | feat: externalize CSS/JS assets to versioned static files and update service worker for PWA support feat: implement explore page refresh logic to prevent stale data display test: add regression tests for explore freshness and static asset loading |
| [0b57aee](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0b57aeede34f3314fc51d55f6716718534370d86) | 2026-09-02 | feat: enhance artwork loading and display logic for now playing section |
| [14a0b23](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/14a0b23662c549006b3de1bf659bd6f89c342a5b) | 2026-09-02 | Add mobile swipe-to-skip functionality and tests |
| [b1717fd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b1717fdc7718f5145bcf30c4c43d0518ea76534c) | 2026-09-02 | feat: enhance mobile context sheet and queue modal history management |
| [c536aaf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c536aaf13fd344ca27de00d594fc1febf8deab07) | 2026-09-01 | refactor: simplify search result transition effects and enhance scroll state management |
| [d739117](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d739117e20f5e3a486528327429bd2d6bc72dc9f) | 2026-08-30 | refactor: update modal headers and bodies to routed full-page components for consistency |
| [3669f25](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3669f25f65b9eb461ad246e85106e0f9a1fb0976) | 2026-08-30 | feat: implement full-page routed views for Explore, Library, and Mood/Genre sections |
| [d09845d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d09845d912974b767380f7ddb2774325ce58d8a0) | 2026-08-30 | refactor: remove unnecessary visual noise and improve mobile interaction for track titles |
| [f644b44](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f644b440675476b20f241822df1bc8be24245f1b) | 2026-08-30 | feat(mobile): bottom-sheet context menus with slide animation, drag-to-dismiss, and scrim |
| [d828eca](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d828ecaafb307ef23b2259fbe9f19d0f9f98ebe5) | 2026-08-30 | fix(mobile): stable mini player with fade-out close, consolidated rules, and swipe over title |
| [8f3ecb6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8f3ecb68259225597901d72fba1e131844624fb0) | 2026-08-30 | chore: gitignore flask-server secret_key.txt |
| [334f2fb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/334f2fb91577e934c212a0104e58baf2a389fc75) | 2026-08-30 | feat(mobile): redesign now-playing controls with action grid, swipe-to-change music, and volume styling |
| [1fc75f0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1fc75f043fa69f55d1a58d90e08228bab1b4726d) | 2026-08-29 | feat(volume): implement gradient scooped track for volume sliders and update volume state handling |
| [3c439d6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3c439d6be4c591f5712f0cc93d3c8d2d4209dfe5) | 2026-08-29 | feat(artist): refine song playback logic for "All songs" and Top songs shelf |
| [50d3357](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/50d3357b2f0ddbc4700cfb99bc885bd3d8af7e32) | 2026-08-29 | feat(home): update context menu to treat non-track cards as playlists to prevent empty video_id errors |
| [76cbfec](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/76cbfece685b39929610beed8f97de3662327de7) | 2026-08-29 | feat(next-track): implement /next_track/ endpoint to resolve the next track from the server's live queue feat(tests): add unit tests for next-track enqueue logic and authoritative track resolution |
| [6ab5342](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6ab5342d7ef48c3962e35de8c6f0cbce280f6177) | 2026-08-29 | feat(artist): adjust padding and layout for edge-to-edge display on mobile artist page |
| [daa46cd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/daa46cd5a8ae7a0b2eb2d77c3e62855b62b970e8) | 2026-08-29 | feat(search): enhance mobile search results layout and improve like button functionality |
| [5be043e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5be043e972134d6624e969e7c450994ccf94463d) | 2026-08-29 | feat(ui): enhance ripple effects and loading states for improved user experience |
| [6a15c65](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6a15c65877779d11202ddb99f4d89cd54f26d4da) | 2026-08-29 | feat(playlists): implement virtualized rendering for long playlists to improve performance |
| [010b804](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/010b804abdaa76a8987c7bb01c2001f5ff4292db) | 2026-08-29 | feat(ui): implement ripple effect for interactive elements and add click-effects.js |
| [6b9911d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6b9911d1819b3c716bf5d04931e48b654882b93c) | 2026-08-20 | feat(skeleton): implement content-aware skeleton loaders for Explore and History pages |
| [a0cd655](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a0cd65580199d5e21107c2128d3424a22dde3fb9) | 2026-08-20 | feat(play): improve handling of next/previous commands to prevent playback errors during transitions |
| [3e592f6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3e592f69f86cb76ea6fcb031bbcd4dafabb09d50) | 2026-08-20 | feat(ui): adjust layout and padding for improved consistency in home and player sections |
| [22c5e4b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/22c5e4b77d0eba59700e5f960a0ee3ebba9536f2) | 2026-08-19 | feat(ui): update card dimensions and layout for improved consistency across artist, home, and search pages |
| [f555b21](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f555b217885a39b116403a506946763679273018) | 2026-08-19 | feat(play): enhance direct link playback handling and improve search UI dismissal |
| [5d5c183](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5d5c183aa93a9cc8220b2c1a97137eb382f4615f) | 2026-08-19 | feat(player): enhance token management for playback and add exception handling |
| [8be3e34](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8be3e3438ef3c3edd719bbeefccf9d8ef575a53c) | 2026-08-19 | feat(ui): enhance mobile library layout and adjust search bar visibility |
| [eb95fcc](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eb95fcc4474d844f5dd902d05e09edaa677c9c8e) | 2026-08-19 | feat(ui): update styles for context menus and improve search functionality |
| [9abfed8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9abfed817649d9060f18da6fffe54be3d6a8fdcc) | 2026-08-19 | feat(benchmark): add script to measure yt-dlp download time for videos fix(tests): add feasibility tests for download latency and streaming performance refactor(server): implement prewarm cache handling for in-flight downloads update(.gitignore): include cookies_premium.txt in ignored files |
| [43af388](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/43af3889e276bc109352287d2a43e49ed75ab8a3) | 2026-08-19 | refactor: standardize page title formatting and improve swipe row opacity handling |
| [d7938dd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d7938dd04da8af8ba3317760303230fd670a9613) | 2026-08-19 | feat(jam): implement fallback to public home when anonymous charts fail |
| [a775208](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a77520865d0619a79f85a3feff519ae03744549c) | 2026-08-19 | feat(api): add track artwork endpoint authorization test for web session |
| [8acc68d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8acc68dc528739b51b39c7bb475c8236fb52de42) | 2026-08-19 | feat(playback): enqueue next track early for seamless playback transition |
| [ba9506b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ba9506ba0171a49915cbf776b755f909f468914b) | 2026-08-19 | fix(playback): watchdog the newest song when a trigger is reused in flight |
| [299675e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/299675edfc568e47c5152971ef7bc8a8d0481552) | 2026-08-19 | fix(sse): reconnect playback poller when returning to the app on mobile |
| [93d6390](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/93d63902314d955c89ced296fb8406440138ce0f) | 2026-08-10 | feat(swipe): enhance swipe gesture feedback with underlay opacity adjustments |
| [3e9cce3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3e9cce31c814d3c213af7a3acc5e4c5771c0226f) | 2026-08-10 | feat(history): improve history modal close behavior to enhance user experience |
| [2360853](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2360853d6b71b403c3363b31ce41392f654539b4) | 2026-08-10 | feat(playlist): enhance layout stability during player interactions to prevent flicker and clipping |
| [ded4a2e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ded4a2edb7089a04da131ec72d385dd63375dad0) | 2026-08-10 | feat(header): integrate search bar into header for improved accessibility and layout |
| [9bd7bf7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9bd7bf72c5dd2b6cc0e9fbec2694587eccb64bec) | 2026-08-10 | feat: add swipe actions across music rows |
| [030b763](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/030b763b1df86d9ebb055357f4018b345593f1e6) | 2026-08-09 | feat(artist): implement artist name resolution and improve credit handling |
| [c8f230d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c8f230d2a355d6873bcfc8a79b614c21ba7416de) | 2026-08-09 | feat(sidebar): update rail collapse behavior and improve transition effects |
| [8505330](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/85053305bbdba6a57976a60c5b04e8244c6cc59b) | 2026-08-09 | feat(search): implement skeleton loading state for search results to prevent layout shift |
| [b9062fb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b9062fb32fa46cc5a04e424a0feae4b5a2cb0763) | 2026-08-09 | feat(css): establish definite-height scroll chain for collection detail page |
| [4e6e9b8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4e6e9b8e791f33f9c550baefa6a120fdfa6f129a) | 2026-08-09 | feat(router): enhance routing logic for artist, album, and playlist paths |
| [c8185b3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c8185b34dc1721842de4b5ac69dc5ec0f3fee8ef) | 2026-08-09 | Refactor collection detail page implementation |
| [1da5b06](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1da5b0649a8aa1d3d66529d72c64a63e67812f09) | 2026-08-09 | feat(ui): enhance route handling for artist and playlist sections in home and UI state |
| [8c5c74b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8c5c74b09e1a48e8c45aa90b6d7efee03a8f58b2) | 2026-08-09 | Refactor CSS for improved readability and navigation affordance |
| [3c038ef](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3c038ef170d24c788cd01f7f8557aaabb7d4b9fb) | 2026-08-09 | Add profile menu styles to sidebar.css for improved user experience |
| [5a9b74e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5a9b74eaa1d61467dbb5bf90bf371dfd849714e3) | 2026-08-09 | feat(collection): implement loading skeletons for collection and search results |
| [215ca6b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/215ca6be99bf941ac955c458e59256205da862e4) | 2026-08-09 | feat(subscriptions): implement YouTube Music artist subscription management and error handling |
| [0441443](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/04414434377d1243cf89fd140500a4fd7165967c) | 2026-08-08 | feat(player): improve mobile playback scroll behavior and visibility of progress elements |
| [c437bce](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c437bce00bb89af44c09da60b13466895a39cb64) | 2026-08-08 | feat(playlists): filter out hidden personal playlists from library and sidebar |
| [ac213b3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ac213b3be04d264b78bdf3af5d25b8fd0408d057) | 2026-08-08 | feat(playback): implement playback processing state and visual feedback |
| [bc5dca3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bc5dca31a370ba4ee2745488106ae85f1427e203) | 2026-08-08 | chore(ui): remove noisy progress console logs |
| [ceda349](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ceda349513607f86c0cf48cc5fbf972380be8c3c) | 2026-08-08 | Add regression tests for search and YouTube icon pause functionality |
| [6095aed](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6095aedc489c769ecf869cfb1e19a7668968afe2) | 2026-08-08 | feat(pwa): update manifest to allow any orientation and add regression tests |
| [0732eed](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0732eed631d448ee5f26e36e768deee020c51d1e) | 2026-08-08 | feat(streaming): enhance ytdlp command for streaming playback with retries and timeout adjustments |
| [7046e5c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7046e5cf013d2495c73b2758dc7a2eda7f16810e) | 2026-08-08 | Enhance artist metadata handling and link generation |
| [ad85f41](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ad85f41d5eaf86e7464fa1af1d600815e7e8cfbd) | 2026-08-08 | fix(web): sync toggle after Alexa pause |
| [08b2dfa](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/08b2dfa18c941990150b2c3d2afeee387e538fa6) | 2026-08-08 | fix(web): route resume through Alexa playback path |
| [5e9dabf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5e9dabf18837414f04d1889a91059737c66f2d12) | 2026-08-08 | fix(search): defer route handoff until results are ready |
| [78093bd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/78093bd9a69fb0852f029a99d56f39a653a68437) | 2026-08-08 | fix(navigation): prevent route transition ui shift |
| [87fac96](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/87fac96fc7b0789445df18208e246f022a30824d) | 2026-08-08 | fix(search): clear stale results after navigation |
| [a26bb0f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a26bb0f9e3e66fc2e3ee31e41ce73a5356115d39) | 2026-08-08 | fix(search): prevent homepage transition shift |
| [d73dc1a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d73dc1a2725346a718be1c26e35d0c15a14a9ee2) | 2026-08-08 | fix(search): preserve page during enter search |
| [c8d5ada](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c8d5ada2bfe5644dc136ff206a63b13eddfd939d) | 2026-08-08 | style(search): use square category tabs |
| [02f7469](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/02f74696ce718b338ac1f7e5e789ba7eb70627cb) | 2026-08-08 | fix(search): restore visible result tab styling |
| [2f05bf7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2f05bf73e2135d957a77cd6ac406869fbd177b9d) | 2026-08-08 | fix(ui): make playback shortcut and playlist hover visible |
| [2dcf4f2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2dcf4f288f3643f5149f9525fd8cc511d84b9a6a) | 2026-08-08 | feat(ui): improve playlist modal interactions |
| [8f0b92e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8f0b92e59177231143b210b3f5c990a3128e9761) | 2026-08-05 | fix(playback): fast-fail retries, flaky-video cache, and PO token sidecar |
| [ffb270b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ffb270ba393d1ee7c64ac4d8844d244e03db36f0) | 2026-08-08 | feat(search): improve UX by keeping current view visible during search requests |
| [986e959](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/986e959e68fd055ef3027003f11c7018c7fefa48) | 2026-08-08 | feat(artist): clean artist credits to filter generic labels and topic channels |
| [1d13e83](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1d13e83b34287080ac765a12c5d3d27b6b0151f6) | 2026-08-05 | feat(playback): add audio file validation to prevent truncated cache corruption |
| [bc5524c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bc5524c96ea455a78559a213fc1b5e4a4d9b3a5f) | 2026-08-05 | style(sidebar): prevent new playlist container from flexing |
| [553a0c0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/553a0c0b4f85f2bce4218447ee3783d836eded08) | 2026-08-05 | feat(pwa): add desktop window sizing and improve play intent sequencing |
| [4d22ea6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4d22ea64cdb6e65672145f098b44e8670d3e950a) | 2026-08-05 | feat(ui): improve artist link behavior and sidebar playlist container sizing |
| [1b4662a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1b4662a8b878045fef075ddd41d3f899cb71b973) | 2026-08-05 | style(sidebar): improve new playlist container collapse animation |
| [e7c3a9e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e7c3a9ecf265c39d3cd083e4193427b7ed5286d0) | 2026-08-05 | feat(playback): implement adaptive confirmation timeout and auto-advance on failure |
| [81a56a5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/81a56a50e7e64fe05edfa36e24c30610bdb5036b) | 2026-08-05 | feat(queue): implement auto-scroll to active track with smart scroll preservation |
| [579e60e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/579e60e4951c81152c45bc74489c9898e24caddf) | 2026-08-05 | Add regression test for play intent ordering and improve module stubbing in routing tests |
| [eba2a52](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eba2a52c54c43b52510458d8078c69fba6a9ddd9) | 2026-07-28 | feat: implement start over functionality in playback handler |
| [ce47f71](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ce47f713b75cb3cd3a55ed3582e8775a0eb351fb) | 2026-07-28 | feat: improve search results handling and enhance suggestion button interactions |
| [3432420](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/34324209c4f8a5e218a6efe468a0f5fd23806851) | 2026-07-28 | feat: enhance playback confirmation logic and improve progress bar responsiveness |
| [a6bf7c4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a6bf7c41c566409406526a9b59527f69cb86c26c) | 2026-07-27 | refactor: enhance suggestion button interactions and improve SVG sizes in search component |
| [c68d27c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c68d27ce4d510a9298120137a6fa9d0accac7b74) | 2026-07-26 | feat: implement cold-cache streaming for yt-dlp to improve response time on Echo devices |
| [ca4b347](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ca4b34711108d9ba519b8fa8e5a0e095afd8ff00) | 2026-07-26 | fix: update docker-compose.yml for VPN usage and network configurations |
| [21e34ff](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/21e34ff28ab34b9b136746bcd6d32e8d40b67ee5) | 2026-07-25 | Add regression tests for trailing-slash paste-URL bug in Flask server |
| [b903ed0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b903ed015ba30e2f9b2b79252355f171790e8c16) | 2026-07-25 | feat: implement body scroll lock for mobile full player to enhance user experience |
| [9b9191c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9b9191c5143ef33ca6c1598009aaddce93c69cdb) | 2026-07-25 | feat: implement mobile swipe-to-skip functionality for artwork in full player |
| [220742f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/220742ff3984f414ec4dae10eac3c9b44627b0c6) | 2026-07-25 | feat: implement artwork preloading for improved playback experience |
| [9e96cbb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9e96cbbbc31f79d0ec4840986c429769a2956dd1) | 2026-07-25 | fix: adjust mobile player dimensions and spacing for improved layout |
| [546b4bf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/546b4bf0b0ef9427119eb700fe75e7bfe503bb45) | 2026-07-25 | feat: redesign mobile queue access with new button and improved interaction |
| [700a331](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/700a331dad5d5dddb06a3192eec3a54fe8e85395) | 2026-07-25 | fix: adjust mobile player dimensions and disable dragging for improved usability |
| [3908628](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3908628bfacbc1c4228a287499620578ee17350c) | 2026-07-25 | fix: improve player stability by optimizing fingerprint handling and CSS animations |
| [f29d374](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f29d374074188e5a797709ad6db0040943f6a508) | 2026-07-24 | fix: update mobile now-playing controls to use persistent play/pause button for better interaction |
| [5e9b533](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5e9b533cee26a4fe186ce8103f97021994bce61d) | 2026-07-24 | feat: enhance playback handling for collections and playlists, allowing seamless track selection and improved user experience |
| [766d374](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/766d37420c7ecca1d3bab0c9d06903361dca4c61) | 2026-07-24 | feat: add suggestion usage button with SVG icon for improved search interaction |
| [af36ee3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/af36ee33ae8aed6fd3cba73467157de04582fd02) | 2026-07-19 | fix: optimize now-playing route state cleanup for smoother transitions |
| [6a7540e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6a7540e2bd8537a24eb1aa88179b01cfb35d5dc6) | 2026-07-19 | feat: implement audio cache management with configurable TTL and sweep interval |
| [e9eff0e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e9eff0e8719d740d893832ba9d65f0c67ae9984d) | 2026-07-19 | feat: enhance navigation handling for now-playing overlay and browser history management |
| [cb738ea](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cb738eaeaac6693698c3092c520cad35b061a6bc) | 2026-07-19 | feat: implement fallback for empty YouTube Music artist shelves and enhance canonical channel ID resolution |
| [ba27366](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ba2736648790fa544981520613dc1b0cd19807bc) | 2026-07-19 | refactor: update README and setup guides for Docker deployment; remove VPS setup instructions |
| [b154b18](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b154b180cddd87ba1af21b495364dc3318507428) | 2026-07-19 | feat: enhance YouTube Music session management with capture overlay and improved state handling |
| [8bd116a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8bd116a87d704a2285ce9ece629922ed1d007c96) | 2026-07-19 | fix: improve mobile search experience by managing overflow and scroll behavior |
| [9fd6fd5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9fd6fd5eb072e5dd0eab2cc2d68b84f8210ab637) | 2026-07-19 | feat(quick-01): add persistent Chromium auth sidecar |
| [3960f6d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3960f6dfd6e7640afde8079d0e69ecd182e7c720) | 2026-07-17 | fix: improve error handling in post request for better feedback |
| [720db39](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/720db39044f292dc98a660942baf69350ae7e9f3) | 2026-07-17 | fix: enhance guest experience by restricting access to account features and improving UI elements |
| [7e82a1d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7e82a1d90d430d38b2b92a549d7c1dc62fe114ef) | 2026-07-17 | fix: extend header bleed to genre pages |
| [41ffa1c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/41ffa1c9af2c1e5aba2c815f3983e99b98e82790) | 2026-07-17 | fix: limit Explore underline to title hover |
| [fe4a562](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fe4a562a17c70a563ed71197d164da12cf7eaf30) | 2026-07-17 | fix: keep Explore available anonymously |
| [dec3f42](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dec3f42de584edc5d504f2286dd6393869a5711f) | 2026-07-17 | fix: verify YouTube account before showing personal UI |
| [d655daf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d655daffad0d54bc66a76f5389d3949d294e1c12) | 2026-07-17 | fix: hide personal controls from anonymous users |
| [2a7ff73](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2a7ff735d6f5a55f3242d20c98a2cf1f3a5de143) | 2026-07-17 | fix: adjust artist hero section positioning and background gradient for improved visual consistency |
| [1d99286](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1d99286bbeb44f422ec34385adb34b3772f70b07) | 2026-07-17 | feat: enhance SPA routing and search functionality with improved state management |
| [05bd96a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/05bd96a1dbaf723d8edbab0f3ccc28ff6cdb31f1) | 2026-07-17 | feat: add canonical SPA routing and deep-link search |
| [5c4c415](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5c4c415fa452b8e3f5e405c75557c0269b949c47) | 2026-07-17 | feat: show YouTube cookie validity in profile |
| [ca4b9f7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ca4b9f718088f7a23ba9b88d2ee14c12ea7759a5) | 2026-07-16 | fix: prevent image dragging to enhance user experience on mobile and desktop |
| [1f0c248](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1f0c248712977625848d2a6a479713982b8b395f) | 2026-07-16 | fix: remove mobile shelf drag functionality to improve performance and user experience |
| [706a09a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/706a09a0dcd8ba1e07b7a2a477d7c27e87021faa) | 2026-07-16 | feat: implement collection playback and queue management enhancements |
| [262f621](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/262f6216144fd1eddcbb1fd851da1e03b176e100) | 2026-07-16 | fix: guard progress loop visibility callback |
| [ebab4e5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ebab4e5889a1bebd81b944194989c5aebf9321ac) | 2026-07-16 | feat(playlists): implement add track to playlist functionality and enhance playlist rendering |
| [7b861d0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7b861d07b297819eb8303cdeba74cecc68d17949) | 2026-07-16 | Fix playlist playback ids and device checks |
| [d06d0ae](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d06d0aea3796e00e58578112f250a8c90a5abee8) | 2026-07-16 | fix(queue): synchronize queue identity and update rendering logic for active tracks |
| [fa6ee12](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fa6ee1215120a63d24ab3014b331488e408520ea) | 2026-07-16 | feat: add explicit package markers for media utilities and data models |
| [8e33ae6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8e33ae62351236cfb1de41bc873919f34176652e) | 2026-07-16 | chore: update dependencies and clean up unused imports |
| [8ee977d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8ee977d35f6378712a2abb4329ef5e217cb8cbf2) | 2026-07-16 | fix(mobile): close sidebar from home shortcut |
| [61d6e58](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/61d6e586f4083184b005f377f76829eed743a035) | 2026-07-16 | fix(mobile): close queue before detail navigation |
| [e83e16b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e83e16bdc7eab4d07a3f8e8f334e5d10399ed67a) | 2026-07-16 | fix(mobile): prevent sticky home title underline |
| [abf2eda](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/abf2eda86ee4b6483da82bd579a5acbea7b39cc3) | 2026-07-16 | fix(mobile): resolve F-01 — remove mobile tap underlines |
| [dca6dac](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dca6dac83956de84ebc1c7a268a42de1ffccb86f) | 2026-07-16 | Refine home and explore album navigation |
| [0362166](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0362166c98b3649becce7626b45ae6a654d96ee0) | 2026-07-16 | Refine shared search and hover styling across surfaces |
| [c815578](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c815578ce9317766ed47b4226af1c327a56be27d) | 2026-07-15 | Refactor music skill playback flow |
| [2032091](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/203209101870585519492994d946b37e706523d1) | 2026-07-15 | Refactor Alexa YT Music skill implementation |
| [6a63892](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6a638922e6526ae9c22c570db5d20a36cb16644c) | 2026-07-15 | Refine mobile top results and route chrome behavior |
| [6217b98](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6217b98aac6f1dd26edcd6da9bc4a7ebe4498e26) | 2026-07-15 | Refactor player and UI components for improved mobile experience and remove legacy mini player functionality |
| [b0a08dc](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b0a08dc7355ef0e03e96d39312582e73caf13cdc) | 2026-07-15 | Refine mobile now-playing controls and queue icons |
| [aa58eba](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/aa58eba878f096f08b6f95cfb7b029aa965c305b) | 2026-07-15 | Refine mobile player flow and artwork fallback |
| [fd1f1ec](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fd1f1ecfd87ab3264acb179f603b713d4fc9c22a) | 2026-07-15 | Improve artwork fallback handling and queue state sync |
| [2d9027b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2d9027ba30d4b791d0b6acb7ca19e6884751d9a0) | 2026-07-15 | Refine audio fallback and player UI behavior |
| [f3866a8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f3866a8a53e623fa5a47d095566513a368a2fc10) | 2026-07-14 | Stop auto-skipping unavailable tracks and tighten yt-dlp fallback |
| [d0e363e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d0e363eb541a4d8e0576b9f8c922e4eb32c2ccad) | 2026-07-14 | Fix mobile now-playing tap duplication and playback toggle reentry |
| [bfe0bd8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bfe0bd867934f6bee2d8c0dc6412d9cffe10ecbb) | 2026-07-14 | Add mobile volume control and improve playback handling for mobile devices |
| [0d37f11](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0d37f112678c41b6f5dc09fc1950ecca61edd89e) | 2026-07-14 | ytdlp client bug fix |
| [782cece](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/782cece086e3c7575bac1941ea892c5c1ec762de) | 2026-07-14 | Refactor Alexa music skill playback and request handling |
| [eacaa98](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eacaa987b3b30fb43fc91ccb6be811dee430c319) | 2026-07-13 | Harden mobile now-playing layout and safe-area handling |
| [7254122](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/725412208792c6357cd5795d5ee83b854f2fda57) | 2026-07-13 | Guard playlist overlay against stale route updates |
| [e3087ce](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e3087cee1ea7bbbeb20a50410089f61aa15e5f99) | 2026-07-13 | Refine now playing artwork loading behavior |
| [80b478d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/80b478dce973733073275303551aa40974275abb) | 2026-07-13 | Refresh artist subscription cache and match IDs more robustly |
| [e94da0a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e94da0af12f4936d6a7b577a637305f53b9d300a) | 2026-07-13 | Adjust artist route player clearance for fixed playbar |
| [2ff8017](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2ff801728f197038a228f0782a1afe7dfda35164) | 2026-07-13 | Stabilize browsing views and playback handoff |
| [7fa3a8f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7fa3a8fb967a83a3017ba425a765778b95b9c781) | 2026-07-13 | Improve library playlist pagination and route handling |
| [730d677](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/730d677f9bd36ebe7890f617fa2887b02e75d464) | 2026-07-13 | Improve now playing artwork and playlist loading behavior |
| [f82a580](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f82a5802ed79d799117c219aa925f1762ad2b251) | 2026-07-13 | Align artist route and search playlist cards with shared playback UI |
| [1021538](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/10215383279bf7cdbf1cef8b036b0d34305e6bf9) | 2026-07-13 | Fix playlist paging and align media interactions |
| [4127625](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/41276254d633f711dcba843fa749400cc27421f8) | 2026-07-13 | Refine explore and player route styling |
| [eaf493d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eaf493da5e5120c050b56dccff9806622997aad6) | 2026-07-13 | Fix playlist overlay routing and hover transitions |
| [9a270f7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9a270f73f34a4a4851912717212aa5d3e4015cc9) | 2026-07-13 | Improve artist parsing and remote library polish |
| [325bb23](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/325bb2354f2175adac500de1abc862e8999d63ed) | 2026-07-13 | Fix artist and explore fallbacks, and polish remote UI |
| [ccef55c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ccef55cd6bf89dc019eae29643a0639092099f30) | 2026-07-13 | Improve explore mood fallback and align library/explore cards |
| [ea3c03c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ea3c03c406158ce86c2dee777cf136d1124cf5ad) | 2026-07-13 | Improve explore mood results and shelf layouts |
| [511c4b8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/511c4b828c6960e2e6541c64c638e2759eaaf134) | 2026-07-13 | Refactor music skill playback flow and response handling |
| [4441137](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/44411374fd02c6a8a46f0fb96f0ed1a59f08fad7) | 2026-07-13 | Render initial home shelves synchronously |
| [e51431c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e51431c222dff1cd5c09ee59ab086c3479101ff0) | 2026-07-13 | Expand artist top songs and refresh explore rendering |
| [7c53eb1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7c53eb19fbdc1433f79516a922b6da9f9a1f1e82) | 2026-07-13 | Improve playlist metadata and playback indicator behavior |
| [8e369cd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8e369cd1b4ab8f81002646e63676ee55182b92f7) | 2026-07-13 | Resolve missing album links and sync playback indicators |
| [d31618c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d31618cbfa298f91eb2580e98e4ec0afa182ecd7) | 2026-07-13 | Adjust rail and play section positioning |
| [b4702f3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b4702f35f75678cc4c7dc8b06d630f4d4ab12e6b) | 2026-07-13 | Adjust fixed play rail below the header |
| [be62c10](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/be62c109c9c9fb8d912411ebd26593db59e99777) | 2026-07-13 | Fix album id normalization and now-playing overlay behavior |
| [01d7115](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/01d71151e5e86422068f1dd77a445883cd1e5904) | 2026-07-13 | Move playback indicator to now-playing artwork and add debug tracing |
| [4a983b7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4a983b71600974022415bfa638cff6969ddf5da0) | 2026-07-13 | Stabilize home playback and now-playing routing |
| [6f1baaa](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6f1baaa03068b3df1a7be2fd17c8b324ffd831f7) | 2026-07-13 | Add player tracing and normalize home feed IDs |
| [6858754](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/68587542da96bac3505c125197d1bc49556f529d) | 2026-07-13 | Add subscribed artists storage and artist UI refinements |
| [556b831](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/556b8317aef94b95024862d66f9a67a23ed5f176) | 2026-07-13 | Refine music UI interactions and playback affordances |
| [e9649c9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e9649c90d4ae292b2879f06d3a77f916a68af72a) | 2026-07-13 | Add album IDs and improve playlist hero handling |
| [868ff85](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/868ff856b71e63789a60304cb109e29e0697fab6) | 2026-07-13 | feat: playlist management, dead-video recovery, and hero polish |
| [02695a3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/02695a395e3fc42c97c6cd55dad90c46b2eb1566) | 2026-07-12 | Player animation fix |
| [54a7bf3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/54a7bf30af4847b09881b0f375afda33b69a39cb) | 2026-07-12 | feat: Enhance image handling and navigation in explore and home sections |
| [17f88a9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/17f88a9acb819e2aa2683bbb6ab8188f4367a8ef) | 2026-07-12 | centralized right click feature |
| [91f8ab8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/91f8ab87a22c1a4d351ab7ce11c8c48e3a681b45) | 2026-07-12 | Refactor Alexa YouTube Music skill flow and supporting handlers |
| [80316c5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/80316c5bf8c1ed2547d6660202e340a0e07cf66e) | 2026-07-12 | fix: backend wiring, mini-popup animation, and jam UI polish |
| [8fcbc6c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8fcbc6cceab35c632c3995ae7ffd3ea37ae708c6) | 2026-07-12 | feat: enhance jam functionality and UI improvements |
| [99b3718](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/99b3718076d4824868caec5bc006023910643373) | 2026-07-12 | refactor: remove YouTube OAuth implementation and update related documentation |
| [ce448dd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ce448dd217ef12b79b59ccdc059b555dbf22591b) | 2026-07-12 | fix: add OAuth fallback for history endpoint |
| [5474f38](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5474f385936cd1d1e257b46474228bc37268031e) | 2026-07-12 | fix: upgrade history failure log to WARNING for debugging |
| [a0240c8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a0240c84043935e370671b4c17ccf0af7ef03912) | 2026-07-12 | fix: improve error handling for YouTube OAuth and fallback to anonymous access |
| [d6b03e4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d6b03e4f2b4bfb6a4cfa3fcb54fff36d2479ac69) | 2026-07-12 | fix: use playlist artwork and reveal track thumbnails |
| [790bd77](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/790bd77e660dedfdd5d9cad647a34152be7cda36) | 2026-07-12 | fix: remove playlist detail panel background |
| [8eeb169](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8eeb169abac13792b85234f56d8eb2a32b1b1738) | 2026-07-12 | fix: restore playlist detail layout structure |
| [c5cb6cd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c5cb6cd889b967ac605ce074c9eca3c60498e389) | 2026-07-12 | fix: open Liked Music card as playlist |
| [14668ee](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/14668eed99f22e462f6c6727df9e37056056cf9d) | 2026-07-12 | fix: accept Chrome cURL browser authentication |
| [778a0a5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/778a0a5dff8431c073243a09b42a2ff57b9e1dbe) | 2026-07-12 | feat: add browser auth recovery for personalized data |
| [5d4e013](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5d4e013f39f0452a1ebbfd8a7a4330c1d613be3d) | 2026-07-12 | fix: degrade unavailable Liked Music browse |
| [1a50fa1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1a50fa1b29a19c0bfac8fe463f55b4bc5b41a2dd) | 2026-07-12 | fix: fall back to public Explore data |
| [c4490e8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c4490e870b36279376b206bf5507613d7e452da4) | 2026-07-12 | fix: degrade OAuth library browse gracefully |
| [9b1054d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9b1054d0115cc045acbca9ab96d3086f0b38523b) | 2026-07-12 | fix: invalidate cached UI bundles on deploy |
| [bf0c800](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bf0c800b1704c500bd197791dcde3d1d95dd094d) | 2026-07-12 | fix: match account menu to device theme |
| [f3f4a13](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f3f4a13ebd72c0820836d208793ad62dc8d2998c) | 2026-07-12 | fix: complete YouTube OAuth modal flow |
| [09fd55f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/09fd55f3a120288fa5e8f7df14b30fb3d8b6eb78) | 2026-07-12 | chore: add test_auth.json to .gitignore |
| [86764ab](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/86764ab53b7c765350f024826ebf3699e825d545) | 2026-07-12 | feat: add authentication credentials and Flask server implementation for Alexa YT Music skill |
| [6d9290a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6d9290a7508adb7e08947628015e98ec1225eee7) | 2026-07-12 | feat: add server entry point and CSS layout modules for home and artist pages |
| [554f802](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/554f802c8249967d47b3717731797fa103dd0129) | 2026-07-12 | feat: implement flask server, add browser-based cookie extraction for yt-dlp, and update authentication documentation |
| [616aba5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/616aba5e166a4ce26f31a594d873bd4296eb5444) | 2026-07-12 | feat: initialize web remote flask server with auth, sqlite database, and armed-play functionality |
| [84d98a3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/84d98a3578020ee6d55910a4a958d326bbd6d865) | 2026-07-12 | feat: implement sidebar and profile menu interaction logic in sidebar.js |
| [227039b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/227039b0e1ee3abed91a840174844f156968e511) | 2026-07-12 | feat: implement flask server foundation for Alexa YT Music skill with SQLite persistence and web remote auth support |
| [5a04883](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5a0488320fd9de42c421e13b950ff912dafbf390) | 2026-07-12 | feat: add flask-based web remote server with authentication and playback control |
| [64e78c7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/64e78c7118991b4def4ae5995c34cb0cdb83669a) | 2026-07-12 | feat: initialize Flask server with database, authentication, and remote playback support |
| [3bb9ec7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3bb9ec7f3256c95edaa92afc7be38e60acf475be) | 2026-07-12 | feat: initialize Flask server with authentication, session management, and SQLite database for Alexa YT Music skill |
| [b6515f3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b6515f30b52e69cdf1a6146460ce640310856ad1) | 2026-07-12 | feat: implement frontend home page rendering logic and responsive sidebar components for the Alexa YT Music interface |
| [58c4430](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/58c443077ef93af9524a67613177dcfbaf6984e7) | 2026-07-12 | feat: implement centralized API utility with fetch timeouts, error handling, and URL normalization |
| [e94e2b4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e94e2b46620badd10f9267cc97dfe9e5c701f58d) | 2026-07-12 | feat: add PWA support with broadcast channel state synchronization and implement playlist library management |
| [87498cc](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/87498cc43c4fd46a91d3805a3aad6b08e64e9e19) | 2026-07-12 | feat: implement modern music-app shell with sidebar, top search bar, and persistent playbar |
| [485f4b4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/485f4b49d34fc5a87604d4cfddc556274a5015eb) | 2026-07-12 | feat: implement artist page UI, related script modules, and backend routing for enhanced music discovery. |
| [79a70b5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/79a70b5f04dfd4c5918b50349437a8a01c8cd41f) | 2026-07-12 | feat: implement Flask server with web-remote, SQLite persistence, and authentication support |
| [7c1b20c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7c1b20cb75e77b9f9d49e2e3baf08a199b48cd0d) | 2026-07-12 | feat: initialize Flask server with database and session management for Alexa YT Music skill |
| [8867c3e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8867c3e48ce3226ac97fc57761273dfee2376993) | 2026-07-12 | feat: initialize Flask server with authentication, database, and playback management logic |
| [30d796d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/30d796d4248a196544ec4b97ed15e2f35366bfee) | 2026-07-12 | feat: initialize Flask server with authentication, session management, and SQLite database for Alexa YT Music integration |
| [f738fcb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f738fcb391dc33d341bce30eecbd7ba89ce23807) | 2026-07-12 | feat: implement modular frontend architecture and library playlist integration for music remote |
| [73c53d3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/73c53d32a6fb84ee3a91307b05093cc2fcde7951) | 2026-07-12 | docs: update authentication documentation and add headers_auth volume mapping to docker-compose |
| [7404c45](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7404c459dc4da60fa01770cf9b020cc23708708a) | 2026-07-12 | feat: implement home feed UI with recommendation shelves and responsive rendering logic |
| [11d6187](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/11d6187c980c8e2fe3623d800c42808d324c32ca) | 2026-07-12 | feat: add Flask web remote with auth, armed-play support, and SQLite history tracking |
| [631ef98](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/631ef98d1dff12ad21bf8e078693f3aad122584c) | 2026-07-11 | Refine playback routing and search progress behavior |
| [8323c72](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8323c72674be0af60c0103fb9fe8657d79f49869) | 2026-07-11 | Improve search personalization and route UI polish |
| [c4532d3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c4532d3b62bf0e60e91057b99e39d5a78f08deca) | 2026-07-11 | feat: enhance player UI and functionality with responsive design and improved seek handling |
| [fc28ac2](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fc28ac25271b96ad16dbfb1d7d5ddb728b5808c4) | 2026-07-11 | feat: implement modern music-app shell with left nav rail, persistent playbar, and centered search pill |
| [6d2eb5f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6d2eb5f567062b70097d2714473fac20a83dca59) | 2026-07-11 | feat: implement modern music application UI shell with navigation rail and search bar |
| [75b8d54](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/75b8d54b09c060caa5ad1ba9790053b4a0598304) | 2026-07-11 | feat: implement frontend architecture and core UI components for web interface |
| [512af14](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/512af14375ba1a36464dc245248b74647a56d576) | 2026-07-11 | feat: add UI components and styles for search, home, and player interfaces |
| [ddaf8c4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ddaf8c42a2d53b453f34409bbb87b15b2239dc8b) | 2026-07-11 | feat: implement modular frontend architecture with new router, navigation, and artist/album/search views. |
| [ddcb302](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ddcb302620ead79773d2abd3d2b068c87bc6d030) | 2026-07-11 | feat: implement home feed UI with lazy-loading skeletons and interactive carousels |
| [ce8a7db](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ce8a7db6735d6b598b85cbfa70cd1186c976758e) | 2026-07-11 | feat: implement modular frontend components with dedicated JS and CSS assets |
| [484b106](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/484b106647d67e5c71a50981ef20c2ab3d1513c6) | 2026-07-11 | feat: implement Flask web server and remote control interface for Alexa YT Music skill |
| [bfdefd9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bfdefd996b1919a9626cba5b6e8abfd9af6afb06) | 2026-07-11 | feat: implement audio player UI with progress tracking and mini-player popup functionality |
| [19f7a1a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/19f7a1aca3797e249629a1ebd5ae6761ff48e220) | 2026-07-11 | feat: implement modern music application shell with persistent playbar, navigation rail, and centered search bar |
| [9f75f50](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9f75f5069918602d0bac64ce294ae7b2133098bc) | 2026-07-11 | feat: implement interactive music queue management with drag-to-reorder and swipe actions |
| [099950f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/099950f9a09c5459049ab1558730a42707773075) | 2026-07-11 | feat: implement initial web interface with CSS base styles, layout components, and service API integration |
| [9bb485c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9bb485c3cf6a04115d5a876802f1b50654660f35) | 2026-07-11 | Refine sidebar playlist and header layout |
| [e1514ef](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e1514ef9aadc1acb80bde1ec85f4e360a698f198) | 2026-07-11 | Refine remote player and playlist navigation UI |
| [d581e3f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d581e3f163f99bac15c392d7dcb118345af477ae) | 2026-07-11 | Polish music UI and add song album lookup fallback |
| [0ae71e9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0ae71e9ff437b18b710c144ea475a725c0892df9) | 2026-07-11 | Refine player and playlist UI interactions |
| [4be634a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4be634aa45e9283a4b11390d5d529e02554ca45b) | 2026-07-11 | Refactor artist link handling and update like button SVGs |
| [954a22c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/954a22c5e3142509b45cc02111ef8ed0cf6a2066) | 2026-07-11 | feat: enhance player and routing behavior for improved user experience |
| [0d3e46e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0d3e46e5e9f1e9fd38b317fd427159c249047cf4) | 2026-07-11 | feat: refactor navigation handling to use window.navigateTo for improved routing consistency |
| [76062ab](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/76062abdf2dbeae5b0d840cbde2ced764e352f3a) | 2026-07-11 | feat: implement modern music app shell with redesigned navigation, persistent playbar, and centered search bar |
| [474bb27](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/474bb27f5c6dd086c4adff5d48318709fa0ce550) | 2026-07-11 | feat: implement remote interface redesign with modular JS components and updated styling |
| [e536250](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e53625003cc1ab9e8155d66e3cbfc79a00a44bb3) | 2026-07-11 | feat: implement new remote dashboard interface with modular CSS and JS components |
| [488d4ef](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/488d4ef1c32e17b4e37f31d1416f8758f738f031) | 2026-07-11 | feat: redesign application shell with a modern music-app layout including a persistent playbar, navigation rail, and centered search bar. |
| [ab4d235](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ab4d235b0f509a82df960d59481a1f3f9d1c6a7b) | 2026-07-11 | Fix playbar restore and compact sidebar animation |
| [076efb8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/076efb82d20f6648b3ce63db9f2c9a9deb34e1bb) | 2026-07-11 | Fix desktop playlist, sidebar, and now-playing layout |
| [7abb516](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7abb516e1b3f6994d59a9498308c4ae72e2a633d) | 2026-07-11 | Refine player and playlist desktop interactions |
| [cb81f59](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cb81f59205a208bad460d18105d4d915dc59c7c1) | 2026-07-11 | Add album details and account menu to the remote UI |
| [93eea67](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/93eea67b6af8f6c239185128c00093344c0265e2) | 2026-07-11 | Add desktop routes for history and now-playing views |
| [d26fae5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d26fae50ae813706a4cd97fb43713f3e2fa6fb0f) | 2026-07-11 | feat: adjust playlist card play button size and improve modal overlay visibility handling |
| [37e9df1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/37e9df19fdae61bef6218ec4804b554d75de21dd) | 2026-07-11 | feat: update sidebar navigation with titles and labels for better accessibility |
| [3fad357](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3fad357d0f88d455213b8737045b85751141b79b) | 2026-07-11 | feat: enhance player UI with compact actions and queue modal functionality |
| [e5559ab](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e5559ab7ec75883bc433ae6be5f153884e344148) | 2026-07-11 | feat: implement sidebar toggle and enhance home feed with personalized recommendations |
| [c505708](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c5057080f3e43a13e491bea71ee7fe82b4e5946a) | 2026-07-11 | feat: enhance home feed with fallback handling and empty state message |
| [eb43d2e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eb43d2e175ba7fa12e89e8f11836732b2d4f1dba) | 2026-07-11 | feat: implement redesign with new sidebar navigation and home greeting |
| [49d95ea](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/49d95ea634f5322c9a8ece6f55bfd43d834047e1) | 2026-07-11 | refactor: remove dead code and update home view visibility logic |
| [404bb95](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/404bb9527e3cc18154401ccb925e9b1b86208cdd) | 2026-07-11 | feat: add PWA support with install prompt and update notifications |
| [d27e4ec](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d27e4ec0afdda1e54406c0ffcb213c9fa7c0fd2c) | 2026-07-10 | feat(06-01): return categorized Alexa search results |
| [0a0e494](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0a0e49498e708aaf6e6a45b81771b6a5df32a4e6) | 2026-07-10 | feat(06-02): render categorized search results |
| [9584d2e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9584d2e594801faf1fca672b914e69b2f27452fe) | 2026-07-10 | feat(06-02): add search result category tabs |
| [fd1a9cd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fd1a9cd66776e2b4c11ab4e4127316c79ff73f98) | 2026-07-10 | feat(05-03): load home feed on idle |
| [dacbb62](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dacbb625ab01070b337d7f6641f37fa2efaf6e40) | 2026-07-10 | feat(05-03): route home view to home section |
| [1b3a5b5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1b3a5b5eb8d27939312d222bbf95c8ddbe1627a8) | 2026-07-10 | feat(05-03): add home feed client module |
| [255dbe0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/255dbe077dd0d646093d6b14fe08b7983d07acdb) | 2026-07-10 | feat(05-02): wire home feed template |
| [4b8510e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4b8510ea806b80b89077a18c0a0899395ba37a4a) | 2026-07-10 | feat(05-02): add home feed row styles |
| [224b0fe](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/224b0fec7256379c60938046124b8419b44c99fe) | 2026-07-10 | feat(05-01): add home feed endpoint |
| [0fa3ae8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0fa3ae86cfe144682ed350170eb7609f01c9d6d6) | 2026-07-10 | test(05-01): add home endpoint tests |
| [664595e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/664595eb26fdc3dc5f348f94f0495d3810293106) | 2026-07-10 | feat(remote): enhance page initialization and preload handling |
| [60ff9b0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/60ff9b0bc00efa76db5f238bbd2bc33e1761ea5a) | 2026-07-10 | fix(04-GAP): add hash route handlers |
| [b2369cc](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b2369ccbe877ed480a5fc45c24b80e514237efef) | 2026-07-10 | fix(04-GAP): consume design tokens across CSS modules |
| [80ad230](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/80ad230db8cd7481aafc7eefae3e2805078fc36d) | 2026-07-10 | chore(04-04): ignore Flask runtime smoke artifacts |
| [5a72bc8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5a72bc8d31c331c58902d98a1e6742a8228a6f0a) | 2026-07-10 | feat(04-04): remove remote monolith assets |
| [9b66ef9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9b66ef910a65a8ecdc68add21fc548d1a24dd1d9) | 2026-07-10 | feat(04-04): wire remote template modules |
| [adaf2f9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/adaf2f93e06170165b9cf37a5943a91199607422) | 2026-07-10 | feat(04-03): extract sidebar history recs and sw modules |
| [689d7a7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/689d7a7aec719328b42b60cb37f7cc7cbce6fe16) | 2026-07-10 | feat(04-03): extract queue and search modules |
| [a5ecb95](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a5ecb95ff4ec972429f05c46b44969a0bd99a1d1) | 2026-07-10 | feat(04-03): extract player and volume modules |
| [5ce197e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5ce197ec8a6eb345db8c693704e515a32381d928) | 2026-07-10 | feat(04-02): add toast device and SSE modules |
| [4d2a18d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4d2a18dc451c4d0fe81ef509b44a8e23870107b0) | 2026-07-10 | feat(04-01): split remote CSS modules |
| [8a65cbe](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8a65cbe073a1ad59246b0ae31171031fba048d3d) | 2026-07-10 | feat(04-02): add cache and hash router modules |
| [c62ffca](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c62ffca101d068562db9ea597b7211fa3778456e) | 2026-07-10 | feat(04-02): add API and UI state modules |
| [3297f35](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3297f35abd6995aa1271b1ff80ae0e1e25f33cd8) | 2026-07-10 | feat(jam): rotate links and share recommendations |
| [bef058b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bef058bc94f79dce719aca04a9a39f1b15c0a1f8) | 2026-07-10 | feat(jam): show live owner indicator |
| [4ab0475](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4ab047524fc4cd5ab9feb9c3e71908c0d04f33e7) | 2026-07-10 | fix(jam): resolve F-01 stale root ended page |
| [027c84d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/027c84d7658ab8c0035fa45155075687c9014402) | 2026-07-10 | fix(remote): resolve F-01 poll and SSE lifecycle edge cases |
| [720566a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/720566ae1050ee1ed7398fa2c959fd10631be218) | 2026-07-10 | fix(remote): reduce request queue buildup |
| [b91399e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b91399ec1d746acfd0573e6c1d06aa1bff836cc4) | 2026-07-10 | fix(jam): skip owner-only preload calls for guests |
| [ee7734e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ee7734efd40e05706c1a7fc239903a199eb301eb) | 2026-07-10 | fix(jam): improve share links and QR |
| [b4a6df6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b4a6df642533e8745f5f2e6747d684b907ca7779) | 2026-07-10 | fix(jam): resolve F-01 guest revocation and privacy |
| [db5cfa5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/db5cfa5b26e7dfa687eeb194b130ed6edd77ad39) | 2026-07-10 | Add support for genre playback and song liking functionality |
| [51877dd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/51877dd30c95c50454fe98fd12dbea51a8cb6eb8) | 2026-07-10 | perf: lazy chunked playlist rendering with IDB cache (IntersectionObserver) |
| [d52bd26](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d52bd268d3f9ede0f2b9dcce9599ee0f0782515f) | 2026-07-10 | feat: implement server-side pending store for 2FA login and enhance login flow with token |
| [04065de](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/04065de15a705e0ee4bd0d1464a2e3da6c63b6f9) | 2026-07-10 | Enhance playlist management with sliding window and server queue continuation |
| [bcc9971](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/bcc99719fedcf887ca416a85ffa62708cc12838d) | 2026-07-10 | feat: implement history table migration and enhance history display with date buckets |
| [61ab575](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/61ab575811803c89df9a8f3b40c6543daeb9b721) | 2026-07-10 | docs(03.2-01): confirm AUTH-01 — logout flow already sufficient |
| [a2261d0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a2261d05b5f934a0eed443d17070c16e0b67032b) | 2026-07-10 | feat(03.2-01): extended toast with close/detail/retry + 401 redirect |
| [5921e40](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5921e4033820d7eb5f6a38ad24b4933f9fcb8cb0) | 2026-07-10 | feat(03.2-01): enrich playback_error with type field |
| [3ed133b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3ed133bb974aa2b81def3fd85b48bb96b6b695b7) | 2026-07-10 | feat(03.1-02): inactivity timeout in progress syncLoop |
| [0e137a9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0e137a987540ac53c0386852a505f3757a252542) | 2026-07-10 | feat(03.1-02): scrollTop save/restore in showQueue |
| [8c8a844](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8c8a844628b03213a5e57c65ddd54ab784d8fa97) | 2026-07-10 | feat(03.1-02): RAF-coalesced queue updates in handleNpUpdate |
| [cd40da1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cd40da1fb5912690d5fd1f37cb230b3dd92b70df) | 2026-07-10 | feat(03.1-01): add content-visibility: auto to .queue-item |
| [b3b3f3e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b3b3f3e9d29079f1c09a70aa41def04f3d624f55) | 2026-07-10 | feat(03.1-01): add diagnostic logging for 107-cap investigation |
| [1476e28](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1476e2867859c239b9d89445b767df959e5f5324) | 2026-07-10 | fix(02): IN-04 log warning on missing api_key.py, IN-06 periodic prune_audio_cache |
| [82bbdf4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/82bbdf412361e12a28f705c99beb21f8d47b047c) | 2026-07-10 | fix(02): WR-06 — guard _recent_unlike_added_at dict with dedicated lock |
| [d4054bb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d4054bb659c108b2c05e4752912528dd9df35e51) | 2026-07-10 | fix(02): WR-03 — defer init_db() to first request instead of module import time |
| [518f923](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/518f92301215cc8899671605bc2f119916e8c795) | 2026-07-10 | fix(02): WR-01 — add missing @staticmethod decorators to 8 Supporting class methods |
| [51424f4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/51424f4d1328551a2a84c183dce7316aeb0fa269) | 2026-07-10 | fix(02): CR-03 — use _download_semaphore in ensure_downloaded to cap concurrent yt-dlp subprocesses |
| [21d3931](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/21d39315516feca599f15de8ddd1263e7e1f40ad) | 2026-07-10 | fix(02): CR-02 — guard remaining direct _now_playing reads with _get_now_playing() lock wrapper |
| [d3c727b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d3c727b8b8fcc4ff36b7e35254da78b623ba4913) | 2026-07-10 | fix(02): CR-01 — split CancelIntent from StopIntent so both handlers are reachable |
| [9876be5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9876be5694750175c5a2d752e849fde2f6422365) | 2026-07-09 | fix(codebase): resolve F-01 — fix undefined filtered variable in handleNpUpdate after _pendingRemovals removal |
| [a1deab3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a1deab36bb947a9504c22950834fab89b3119057) | 2026-07-09 | feat: initialize flask server with authentication, sqlite database, and playback management logic |
| [f8c3e66](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f8c3e66d349d1df1f090fdf852292830550d647e) | 2026-07-09 | feat: initialize flask server with authentication, sqlite database, and playback orchestration logic |
| [08cde40](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/08cde4082d61eb898690ad61ebf935a614b93815) | 2026-07-09 | feat: add Alexa remote control bridge for music playback and session management |
| [4dcec9c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4dcec9c0d9ec363610ced41536353b219b87dd00) | 2026-07-09 | feat: implement flask server and alexa remote control logic with persistent sqlite history storage |
| [5e7fd2f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5e7fd2f57f40f1288c4581377242f5e8e6970c78) | 2026-07-09 | feat: add YTDLP_COOKIES environment variable for yt-dlp integration |
| [e443c88](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e443c88afabef1377cec63f386ae4ad352207e18) | 2026-07-09 | fix: adjust cookie handling to ensure writable path for yt-dlp cookies |
| [e0a730e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e0a730e7941377a512e23b3be898f00999421282) | 2026-07-09 | feat: update README and server logic for improved yt-dlp client handling |
| [9ce564d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/9ce564d8480b008da89e0e16831466e2002dcdcf) | 2026-07-09 | feat: update requirements and server logic for improved yt-dlp client handling |
| [a7b109a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a7b109a4b14b37e00d7f1e0c575a29804320039d) | 2026-07-09 | feat: update .gitignore, README, and server logic for improved yt-dlp integration and client handling |
| [7d16ce1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7d16ce1ba0c885f2847d3cc4efd83218b0506491) | 2026-07-09 | feat: update .gitignore to include new file patterns |
| [5cd2b0c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5cd2b0c583d38d2a66e7cdf4e4a5af44e91884e1) | 2026-07-07 | feat: enhance queue item interaction and optimize rendering for active states |
| [28525b8](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/28525b855c094f9ec01d3203918bf1584a28d50c) | 2026-07-07 | feat: add remote.js and remote.html to provide a controller interface for the music player |
| [4800f41](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4800f415ae5cdbdc491989b8e123dc6a7a4a3008) | 2026-07-07 | feat: add client-side UI controller and layout for remote player interface |
| [da51d29](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/da51d2922c96daa1a0cbf17ab160e90fdb2e9ed7) | 2026-07-07 | feat: improve playlist menu interaction and update YouTube Music link with playback position |
| [66382a5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/66382a5109215a88f08a247ad8cd177975f51e0b) | 2026-07-07 | feat: enhance session management by showing controls on session expiration |
| [42fe448](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/42fe448bb3d2d5cfccaa2fb52645b47fca00dbbf) | 2026-07-07 | feat: update Dockerfile and server to use waitress for improved performance and logging enhancements |
| [19c113c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/19c113c897cf1102381d903101d7c90c1b99043e) | 2026-07-07 | feat: enhance radio playback functionality to allow fresh queue creation and improve user experience |
| [6039926](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6039926f2c79f0c634f6e24f3c902d426eaccd1e) | 2026-07-07 | feat: enhance playback state management to prevent unintended track restarts and improve volume synchronization |
| [0846773](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/084677307735e682bbccc80cc22670e713873a4c) | 2026-07-07 | feat: implement writable path for YTDLP_COOKIES to prevent read-only errors |
| [c28a6a6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c28a6a60bf28cef65771f02a7a6dfc2bfd13b3db) | 2026-07-07 | feat: add reposition handling for playback to differentiate between genuine replays and repositioning |
| [be58839](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/be58839791064afbb4a7616aa4787fa5ddc43258) | 2026-07-07 | feat: improve playlist detail menu handling and ensure proper state management on stop |
| [3c5f588](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3c5f588c6ae5c1ddda08893633f6e0601cb1896c) | 2026-07-07 | feat: add GitHub credit link in sidebar footer for improved attribution |
| [5d4fe2a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5d4fe2a6c0814339e2cbc3641a9d3acd0569015c) | 2026-07-07 | feat: improve handling of queued items without duration and adjust CSS for button spacing |
| [0eeed0e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0eeed0e5abc6688f43f96635c3734d096fbc8fb0) | 2026-07-07 | feat: enhance track-like button styling to include background color for liked state |
| [999bfe0](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/999bfe0e7314824f087edae4ce057a46a8e695ca) | 2026-07-07 | feat: enhance playFromQueue functionality to handle duplicate songs in the queue |
| [e341a55](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e341a5574e77c5e5f5b602aca6699e03b053cdb4) | 2026-07-07 | feat: enhance playlist track styling with z-index adjustments for swipe functionality |
| [0dddce1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0dddce167ae1a91b59dac4734715f8173362fb45) | 2026-07-07 | feat: adjust playlist button sizes for improved UI consistency |
| [893ab68](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/893ab68268b9a2015a0ae4383d54d84b4252783a) | 2026-07-07 | feat: update playlist menu styles for improved consistency and user experience |
| [dc37518](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dc3751863e6e605db9814ed25019a308754a4425) | 2026-07-07 | feat: adjust z-index for playlist more menu to ensure proper layering with modal overlays |
| [e5ca829](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e5ca829cb595a480f73ed3d04d9fcaa217909d6b) | 2026-07-07 | feat: enhance liked songs functionality with undo-like feature and improved UI interactions |
| [97c7e23](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/97c7e233bcbb5265d3280ea0d56bd6b7aad3b63b) | 2026-07-07 | feat: improve playback tracking and modal behavior for better user experience |
| [cdc5172](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cdc51728ae1e761fd3bfd64fcfed7cf7bb571da2) | 2026-07-07 | feat: enhance playlist and queue menus with improved animations and functionality |
| [4cdde49](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4cdde49e758b9b62d22ea7244cd834cf8da0096c) | 2026-07-07 | feat: add functionality to prevent duplicate tracks in playlists and enhance track removal process |
| [8de1585](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8de1585f8a33d3ab519f8db558d1194f986ffe37) | 2026-07-07 | feat: implement thumbnail backfilling for radio queue and enhance button visibility in results |
| [1c7adc1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1c7adc15af3afd8a738b4d4a90718ffe8c0df2a6) | 2026-07-07 | feat: add thumbnail backfilling and suppress radio queue for playlist playback |
| [65bb8f5](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/65bb8f59757975d592324871370ea55a38f84f0f) | 2026-07-07 | feat: add shuffle play button and functionality to playlist detail modal |
| [4937bc3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/4937bc31c329701531707f7e12b1e7ff536a70ee) | 2026-07-07 | feat: enhance playlist functionality with improved track details and UI updates |
| [a910bb6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a910bb6ef37c98c5a6a6d3c7fe74772c5f834cbe) | 2026-07-07 | feat: enhance session restoration by persisting last used email for cookie retrieval |
| [a45b99c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a45b99c5137a32b865af8d0f022b66946157ff59) | 2026-07-07 | fix: improve session restoration logging for better debugging |
| [5d1feb6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5d1feb68b5b6935f06bc31379a84e1b74e6dfa3f) | 2026-07-07 | Add confirm dialog for delete actions and update play all button class |
| [eed36bd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/eed36bd5377a27e2251cf67c3e6dbfdfa5dece65) | 2026-07-07 | refactor: streamline playlist rendering and queueing logic for improved user experience |
| [aa73815](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/aa7381543aeb3f2c9cbcef75613aa38ec44ea434) | 2026-07-07 | feat: add playlist renaming and play all functionality with UI updates |
| [fa8ea93](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fa8ea93e9726ff0b36304bb16a840b66ddbfecf4) | 2026-07-07 | fix: correct request path check in require_api_key function |
| [7c4643b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7c4643bc0df169c8b2dcec9d8e254772537c38d6) | 2026-07-07 | fix: prevent browser from caching API GET requests (fixes disappearing liked songs on refresh) |
| [44c3270](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/44c32705093333fa145fc06e623e8d1844f61164) | 2026-07-07 | feat: replace native prompt with on-screen modal for importing playlists and optimize playlist modal load time |
| [cb85bad](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cb85bad4137d2d901e3e3a6696959a1fcb9a92f2) | 2026-07-07 | chore: ignore local data.db database file |
| [e6a4925](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e6a4925bbf6e10bf781d373ce7284cbdc6b20f53) | 2026-07-07 | feat: add inline sync button to imported playlists in modal |
| [89ea575](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/89ea575db786626ddfa114c3519dfb39f1bb8192) | 2026-07-07 | feat: add Import YouTube Playlist button to Playlists modal |
| [f57df48](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f57df483672c8cc6b3b080726a8a8cce353330a9) | 2026-07-07 | fix: resolve race condition leaving recommendation skeleton stuck after clear |
| [1c755ed](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/1c755ed485ecfec358654257105c224bdd69e80d) | 2026-07-07 | style: change Save to Playlist icon to folder-plus to differentiate from Play next |
| [393170a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/393170a2dcd704133741c723a40f30f22684de37) | 2026-07-07 | fix: add playlist_tracks migrations for duration_ms and added_at |
| [6cbb5f9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6cbb5f95b6d32d612344ee08262fe65f81c0f9db) | 2026-07-07 | fix: playlist ui rendering issues including missing thumbnails, clipping, and layout |
| [a6986bf](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a6986bfc640737fc836c2ffa5ff2309e14e3d950) | 2026-07-07 | style: update Save to Playlist modal to match Playlists modal UI |
| [d420ab7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d420ab7905f4cdef92572560f99f458e9d1df679) | 2026-07-07 | fix: add DB schema migration to ensure source_url exists |
| [c86b0bb](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c86b0bbaae5c07928d4211e81e9c5b2c4903afa9) | 2026-07-07 | feat: hide remove from playlist button behind 3-dot kebab menu |
| [a0aae10](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a0aae1032a44fee0107b29d7374c193425e95566) | 2026-07-07 | feat: ensure Liked Songs is always visible, use heart icon thumbnail, and remove raw button from playlist list |
| [fee7051](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fee70517bb0a8ec88b572f431fbfa7dc4a4112b0) | 2026-07-07 | fix: ensure fresh data is fetched when opening playlists modal to fix empty tracks issue |
| [d56c26f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d56c26f9097422a33d991868e1a8605f1748be0b) | 2026-07-07 | Merge branch 'master' of https://github.com/arookiecoder-ip/youtube-music-alexa-skill |
| [8b1360c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/8b1360c41be9b173901ce8e29a99a1dd9a58eafe) | 2026-07-07 | fix: use correct CSS class for playlist thumbnails |
| [b956c9e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b956c9ef880281291e4a844ccb5e034725a9ee56) | 2026-07-07 | fix: populate Recently Played from all web remote play flows and fix thumbnail field mismatch |
| [08e7149](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/08e7149d2c3b5e50912a458be8f2c05d755be205) | 2026-07-07 | fix: populate Recently Played from all web remote play flows and fix thumbnail field mismatch |
| [34f75b9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/34f75b9673848b2e64f2874af970361757551dad) | 2026-07-07 | feat: implement flask server with SQLite persistence and web remote functionality |
| [5eb0895](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5eb0895fb79dd959071cb9fa08771c6f1ded6f01) | 2026-07-07 | feat: add persistent SQLite-based history and playlist management to the web remote server |
| [dc6b2a1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/dc6b2a18d9f55ed8fd56ce0bff4e04da64b062d7) | 2026-07-07 | chore: remove deprecated legacy JSON migration paths from docker-compose configuration |
| [a97c7fd](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a97c7fd7de892e9e77ca98ce8714f77fac50d159) | 2026-07-07 | feat: implement flask-based remote control server with web-based UI for media playback management |
| [f6a3186](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f6a31861dc2d25dab71442d91ee1b5c5319d1631) | 2026-07-07 | feat: add playlists.js to manage user playlist CRUD operations and UI interactions |
| [a932590](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a93259028c4f80124e621d77d10cae6afcf9d8e0) | 2026-07-07 | Fix likedSongs reference and CSS encoding |
| [174b50f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/174b50f13b02a7f2989657f37c59c2d83b855932) | 2026-07-06 | Implement full Playlist and Liked Songs functionality |
| [da496e9](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/da496e97aaa6336f49b9e456476d51ec1b9bc44c) | 2026-07-06 | Add YouTube Music button to mobile mini-player popup |
| [0e3157a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0e3157af8f389e16f084d0f84edcf29eb2626ec9) | 2026-07-06 | Preload all UI thumbnails to prevent step-by-step progressive rendering |
| [caa468b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/caa468be03eac47087fbd205bbe9ce883f3351e9) | 2026-07-06 | feat: implement Flask-based Alexa YT Music skill backend with remote web control and authentication |
| [fab8de6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fab8de64de7d5bdeefa52d240779072d0ca8530e) | 2026-07-06 | feat: add initial CSS layout and styling for remote interface |
| [253639d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/253639d83e21660eec261248265cd5dbec98534c) | 2026-07-06 | feat: add initial CSS layout and styling for remote interface |
| [c9a093e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/c9a093e5562df7a4d57d702f2b8b09f7243f4e81) | 2026-07-06 | feat: add stylesheet and script for custom remote UI controls |
| [fcfa9ac](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fcfa9ac5c9572102fd33d16f8254306b45c5c6b3) | 2026-07-06 | feat: add frontend remote controller logic with state management and progress tracking |
| [db6c2a3](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/db6c2a363e1adb05bfc68278c12db6bbfe66af5c) | 2026-07-06 | feat: add base responsive CSS styling for remote interface |
| [5ff56a1](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/5ff56a1c81c6da159935f2911fc9537fd4b7d900) | 2026-07-06 | Merge branch 'master' of https://github.com/arookiecoder-ip/youtube-music-alexa-skill |
| [222842e](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/222842e895905b5844e0a7c97a9b8c947a76cc62) | 2026-07-06 | feat: add remote.css stylesheet to style the application interface and responsive layout |
| [ee4676f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ee4676f16e462dfd762f7645958339b3247a74bf) | 2026-07-06 | feat: add responsive UI styling and layout logic in new CSS and JS files |
| [fbff80d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/fbff80da7d16bf7c26a0e85eac4b71ac1be790e2) | 2026-07-06 | feat: implement remote controller UI and state management logic |
| [e16526c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/e16526c02f89e766b1e855da9953dfbb8d9a7920) | 2026-07-06 | feat: add remote.css and remote.js for custom web interface styling and functionality |
| [a3afd8c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a3afd8c0e6ecc3ade11cb08a194f157ba7a79963) | 2026-07-06 | feat: add custom CSS and JS for remote interface layout and device control functionality |
| [f25cb37](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f25cb37c19a56f5e8ddbb87378c08b3dfcec958a) | 2026-07-06 | Enhance radio queue handling: fill duration for now-playing track from radio queue and build "Up Next" queue immediately for recommendations |
| [3c13b64](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/3c13b6468b857b07cb105e1160c4bc13e056799e) | 2026-07-06 | Refactor recommendations layout: implement full-width grid with fixed 2 rows and adjust skeleton loading for improved display |
| [69a5997](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/69a5997af454950bb594b359581b3b27c2d16d5a) | 2026-07-06 | Enhance recommendations handling: implement background metadata lookup for duration and adjust caching strategy for session persistence |
| [622fa0b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/622fa0be637986c0af5398dff6139c2d3856d7e6) | 2026-07-06 | Refactor recommendations display: implement skeleton loading for smoother transitions and enhance grid structure for better tile visibility. |
| [2256a54](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/2256a542bf16ef2b9cecc55de3a5f53c70b47582) | 2026-07-06 | Refactor recommendations feature: enhance chart retrieval logic, improve fallback handling, and update UI layout for better display of recommended tracks. |
| [50289f4](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/50289f4bd19d5739b4dc4661f5ddb6066d4f028d) | 2026-07-06 | Enhance recommendations and UI layout: improve seed selection for varied playback, adjust song name display for readability, and refine layout behavior for better content fitting. |
| [ef03a9b](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ef03a9b8260ad7b4894929848152fa804fa5233c) | 2026-07-06 | Enhance recommendations feature: randomize seed tracks for varied playback and improve skeleton loading in UI |
| [f6070c7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/f6070c786b9e090f6c0cec70df010a4285a76146) | 2026-07-06 | Refactor Recently Listened feature: update UI components, enhance history handling, and improve modal transitions |
| [b1c1117](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b1c1117f05b5ab3b5819433be6894fecd0102e3c) | 2026-07-06 | Add Recently Listened feature: implement history tracking, UI components, and modal for playback history |
| [b3e95a6](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/b3e95a67a05bbe5d725acd2eccc1ac909ed23857) | 2026-07-06 | Implement recently listened history and recommendations feature: add UI components, API endpoints, and data handling for playback history and recommendations. |
| [a31372a](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/a31372af5fdb2d332a0ba52c5ed5c69710b8ea3d) | 2026-07-06 | Enhance playback handling and queue management: implement recovery for lost webhooks, optimize track promotion, and manage pending removals in the UI. |
| [7a6c287](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7a6c2878efa61e0f6a9d1f9730722a65c4a2eaaa) | 2026-07-06 | Update cron job command in Docker setup to ensure correct directory context |
| [ce33860](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/ce33860425f401e0d4379c6f4dae29e675e80c64) | 2026-07-06 | Update .env.example and api_key.py with clearer instructions and default values |
| [564153f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/564153ff5b4b7ae8640fa61d925c0e5f3e182b61) | 2026-07-06 | Add Docker Compose deployment notes for Flask and Amazon login proxy |
| [780245c](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/780245cafa36af43634853397e2206a799b905d0) | 2026-07-06 | Bind Alexa login proxy to all interfaces for Docker network accessibility |
| [6c4ab29](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/6c4ab29100c62cddd2ab79d91a9c4cbdec0ec663) | 2026-07-06 | Update server to run on all interfaces by changing host to "0.0.0.0" |
| [43d9fd7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/43d9fd7c2a2680a9d38a8450578f12812b29d7ed) | 2026-07-06 | Refactor setup instructions in README for clarity and organization |
| [26a5f6f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/26a5f6f26f1b70ba1f8b559b54d41378e4969506) | 2026-07-06 | Update environment configuration and documentation for Docker setup |
| [0610bb7](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/0610bb71ed3143e697960d3b532b58519156672d) | 2026-07-06 | Add Docker Compose instructions for cache management in README |
| [7e63707](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/7e637072f97ec6e955d1528c3ac2ab569927cf09) | 2026-07-06 | Add initial Docker setup with Caddy and Flask server, including environment configuration |
| [d33e37f](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/d33e37fb7a295458a9ba9c8bdbc8ecb80ac396f2) | 2026-07-06 | Refactor API key handling and update README for clarity |
| [cbf596d](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/commit/cbf596d2de52cf8bd6dbce84d9b0e904a451cfc2) | 2026-07-06 | Initial commit |

</details>
