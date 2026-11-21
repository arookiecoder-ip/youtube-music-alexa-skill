# Playback, downloads, account and security audit — 2026-11-21

Scope: production backend based on `d7b018b`, Android based on `8280da5`, Lambda/private skill and persistent Alexa endpoint. All Android compilation and container builds run in GitHub Actions. Local verification executes source tests only.

## Findings fixed

| Finding | Fix | Verification |
| --- | --- | --- |
| A localized AI dub could win default audio selection | Prefer original AAC audio and expose exact language selection | Resolver fixtures plus authenticated inspection of `M8ICGx4p0lA`: `hi.4` original; `en-US.10` auto dubbed |
| Byte-range/cache identities did not distinguish languages | Include selected track in cache keys and ETags; never serve original bytes for an explicit dub | Relay/cache/range isolation tests |
| Timed-out initialization or provider work could retain poisoned state/capacity | Discard hung handshakes, release slots on deadline, reject late cache publication | Hung initialization and eight concurrent hung-provider tests |
| Each login added another device session | Signed installation identity and atomic per-account session rotation | Same browser, independent same-UA/IP browser, cleared auth cookie, tampered identity, legacy adoption and cross-account tests |
| App logout erased installation identity | Preserve only the device cookie; do not treat it as authentication | Cookie persistence/routing tests |
| Download cancel/retry reused paths and old completion could remove the new attempt | Unique OS transfer destinations and explicit attempt ownership | Cancel/requeue/restoration tests |
| Cancellation during completion validation could resurrect a removed item | Recheck pending membership after file IO | Production path review, existing transfer/manifest tests |
| Download mutations could run before Room restore | Defer them until manifest initialization finishes | Restore/manifest regressions |
| Invalid completed files could remain on disk | Remove failed OS transfer and damaged destination | Audio validation regressions |
| Server errors had no administrator mail path | Redacted bounded asynchronous digests with retry, duplicate counts and overflow accounting | Error/critical/context/redaction/SMTP failure/queue-pressure tests |
| Library ERROR logs were discarded with noisy INFO logs | Retain ERROR/CRITICAL library records | Journal/alert integration test |
| Account screen hierarchy and device labels were weak | Stronger section headings, restrained surface contrast, current-device badge and app-friendly labels | Android UI regression and screenshot artifacts |
| Supported-links configuration was exposed as an app setting | Remove row, preserve manifest filters and routing | Existing app-link tests |
| Request stdout logging could retain new/unknown query credentials or unmatched path proofs | Log route templates and allowlisted nonsecret query values; omit error-body text | Secret-hygiene regressions |
| Documentation named old branches/releases and schema constant lagged migrations | Update production docs and schema version to 16 | Migration and source review |

## Verification and cleanup

Use `python scripts/run-regressions.py --results <directory>` for the full backend suite: legacy files install conflicting import-time provider stubs and must run in separate processes. A combined pytest collection is not a valid full-suite runner. This audit uses the existing isolated runner and keeps physical/browser integration checks separate.

Checks include the account endpoint policy inventory, route exposure/auth tests, account/resource isolation, CSRF, app audio scopes, signed media host validation, single-range contracts, playback ownership, queue revision races, signup/recovery/TOTP, purge/retention, Alexa SDK serialization and persistent endpoint transport. Supply-chain checks use locked Python requirements, npm advisory checks, repository secret scanning and GitHub image scanning.

Safe cleanup in this change removes invalid download destinations, avoids obsolete session accumulation for known installations, bounds alert groups and request lifetimes, and updates obsolete documentation. Existing sessions without installation proof are not mass-revoked or merged by browser name/IP. Unrelated working directories, credentials, cookies, backups and deployment data are preserved.

## Explicit limits

- This is a bounded source/test audit, not proof that every endpoint and dependency is free of vulnerabilities.
- Podcast language switching in the native menu applies to phone streaming; Echo/browser default to original audio. Selecting another renderer or an offline file shows an explanatory state.
- Original and dub availability is provider-dependent. The exact sample was inspected with the existing server cookie jar; its signed URLs/cookies were neither printed nor returned.
- Pending error email is memory bounded and may be lost on process termination. SMTP delivery uses the configured transport; an SMTP acceptance does not prove mailbox arrival.
- Existing unidentified session duplicates cannot safely be consolidated automatically. Sign out old devices explicitly; future logins acquire stable identities.
- Real-device network transitions, storage exhaustion, battery restrictions and OEM DownloadManager behavior still require physical-device testing in addition to emulator checks.

## Verification evidence

- Isolated backend/web logic: **155/155 regression files passed**, including **2,004 Python cases**, zero failures/errors/skips, plus 32 JavaScript files.
- YouTube resolver: **23/23 tests passed**.
- Alexa Lambda/private SDK: **79/79 tests passed**. Persistent endpoint: **31/31 tests passed**.
- Locked Python dependency audits: server, sign-in browser, Lambda and persistent Alexa endpoint reported **no known vulnerabilities**. npm reported zero advisories for the resolver runtime dependencies.
- Additional alert/journal and request-secret regressions passed after logging hardening.
- Public read-only probes before deployment: readiness 200, login 200, unauthenticated audio 401, unauthenticated proxy authorization 401.
- [Backend change and live CI results](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/pull/70): security boundaries, dependency audit, gitleaks, Trivy, CodeQL, browser containers, full isolated regression runner, SDK and deployment image.
- [Android change and live CI results](https://github.com/arookiecoder-ip/MusicBox-APP/pull/32): JVM checks, account/device/player UI, signing, optimized release and upgrade/install checks. The initial signed build and JVM checks passed in [Actions](https://github.com/arookiecoder-ip/MusicBox-APP/actions/runs/44918340549); final source and publication use the subsequent main-branch run.

Production deployment is performed by the backend master-branch Actions workflow after merge. Stable APK publication is performed by the main-branch Android Actions workflow only after signing and upgrade checks pass. Workflow links expose final status; local test results are not a substitute for those gates.

## Production rollout verified

- Runtime image revision: `56714541eaecd54deac88d1d79b5dd356dead941`. [Verified-image deployment succeeded](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/actions/runs/44921049856). The subsequent master change only improves deployment workflow/runbook handling for slow links; no compilation ran on the server.
- Account database migrated successfully to schema 16 with the hashed installation column.
- The exact podcast defaults to `hi.4` (Hindi original); `en-US.10` is available and marked AI dubbed. Both returned HTTP 206 with correct 1,024-byte ranges and separate representation identities. English resume returned `bytes 1024-2047/79456025`.
- The public HTTPS route also returned valid 1,024-byte 206 responses for both languages. Public readiness returned 200; unauthenticated audio/proxy checks returned 401.
- A complete song download returned all **3,567,945 declared bytes** in 1.13 seconds. SHA-256: `0acbfca028e19bc129683d2564c597c5161a4384dcac28258288c13d562e7dd2`.
- A clearly labelled synthetic error traversed the redacted journal/alert mail pipeline and was **accepted by the configured SMTP server**. The recipient address and credentials were not printed. Mailbox delivery is not inferred from SMTP acceptance.
- [Android 2.5.16 is published](https://github.com/arookiecoder-ip/MusicBox-APP/releases/tag/v2.5.16): version code 134, pinned signing identity, optimized non-debuggable APK, checksums, and successful API 33/36 install/upgrade/cold-launch gates. [Publication workflow](https://github.com/arookiecoder-ip/MusicBox-APP/actions/runs/44921416692).
- Final app JVM checks: **576 tests**, zero failures/errors/skips. Functional device checks passed 39 account/player tests, plus 21 phone and 23 tablet cases, including the separate-UID media-session security probe. Diagnostic screenshot export is tested separately from the published APK.
- Android lint reported zero errors/fatal findings; low-priority warnings and reviewed security-related warnings are recorded in [the Android audit](https://github.com/arookiecoder-ip/MusicBox-APP/blob/main/docs/AUDIT-2026-11-21.md).

Older session records without installation proof remain explicit sign-out entries; merging them by a shared address or browser name would risk revoking independent devices. Existing downloaded files are preserved rather than overwritten during a server language-default change. Re-download an older podcast copy if it contains the previous dubbed audio.
