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

Final workflow links, counts and deployment revision are added after verification.
