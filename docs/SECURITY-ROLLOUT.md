# Security remediation and rollout

Implementation status for the findings in [the architecture audit](CURRENT-ARCHITECTURE-SECURITY-REVIEW.md). Changes are in the working tree; no production deployment, live provider login, or real Echo pairing was performed.

## Implemented

| Boundary | Changes |
| --- | --- |
| Account authentication | Security generations invalidate stale password proofs and sessions; login replaces identity cookies; rejected account sessions cannot fall back to legacy owner; temporary-password sessions have restricted access; login requires JSON. |
| Password, email, recovery | Shared service policies; recovery email changes are staged and verified; old mailbox receives a notice; invalid passwords do not consume reset/invitation proofs; generated passwords use direct no-store responses; recovery cookie state no longer reveals lookup results. |
| TOTP | Persistent guessing limits, actual matched-counter replay prevention, session-bound expiring setup, atomic confirmation; existing-factor changes require independent recovery proof plus password; factor changes retire other sessions. First setup requires a confirmed recovery address. Legacy TOTP also consumes counters and persists its guessing budget. |
| Account administration | Atomic last-owner and signup-cap invariants; magic links require CSRF POST confirmation and enrolled TOTP; security-version checks and identity replacement. |
| Audio and Alexa | Versioned, account/session-bound audio capabilities; global stream admission; explicit subject/device pairing and signed video-bound Echo URLs; key-only owner compatibility disabled unless explicitly enabled. |
| Google / YouTube | One physical-browser coordinator, owner and attempt capabilities, serialized commands, stale-result fencing, expiring leases, exact Google origin checks before secret submission. |
| Amazon | Attempt IDs checked at the HTTP boundary and inside its event loop; stale awaited results cannot promote a replacement attempt; bounded captcha destinations and bodies. |
| Resource lifecycle | Unique 0600 cookie files with child-lifetime cleanup; explicit stream/resolver retirement; busy resolvers protected from LRU eviction; end-to-end resolver deadline and bounded PO requests; explicit SQLite connection closure and negative tombstone caching. |
| Deployment | Non-root app/browser, private browser network, dropped capabilities, resource limits, digest-pinned images/actions, hashed Python locks, fail-closed configured authentication, readiness/liveness, sanitized sensitive route logs. |
| Maintenance | Adversarial tests, account/admin route inventory, dependency/container/secret CI scans, SBOM generation, encrypted whole-state backup/restore and transactional key rotation. |

Authentication helpers, browser coordination, provider command files, Alexa identity, and stream pools now have separate modules. `server.py` still composes playback and many routes. Continue extraction by domain when changing those areas; keep one authoritative backend process until playback state, locks, rate limits and browser leases have an explicit cross-process implementation.

## Before deployment

1. Stop the backend and browser and take an encrypted backup of **all three volumes**: database/data, Amazon cookies, Chromium profiles. Keep a separately tested copy of the encryption keys. Migration advances the account schema to **7**. Rollback requires the prior image and prior whole-state backup; do not run an older binary against the migrated database.
2. Configure an independent `MULTIUSER_ENCRYPTION_KEY` (or existing supported `ADMIN_ENCRYPTION_KEY`). HTTPS multi-user startup now refuses implicit derivation from the Flask signing secret. **Preserve the existing encryption key** or rotate the stored ciphertext before changing it. Merely replacing an environment variable makes existing encrypted credentials unreadable.
3. For a deployment that previously derived its key from `SECRET_KEY`, securely derive the old Fernet key using `multiuser.crypto._fernet_key('', old_secret)` in an isolated administrative process. Write it directly to a protected key file, never logs/shell arguments. Rotate the database to the new independent key while stopped, update configuration, then restart. Keep signing-secret rotation separate.
4. Retain strong independent browser-control and egress tokens. Compose's one-shot permissions service changes only the named application volumes to UID/GID **10001** before application startup. Confirm bind-mounted cookie files remain readable by that UID.
5. Validate Chromium sandbox support on the target host. The managed review host blocks the required sandbox setup under the hardened container profile. Install/permit appropriate unprivileged user-namespace support on the deployment host. `MUSICBOX_CHROMIUM_DISABLE_SANDBOX=1` is an explicit compatibility escape hatch with weaker isolation; it is **not** the default and was not enabled in production. A passing browser-test launch does not prove production sandbox compatibility.
6. Deploy the Lambda changes and updated interaction models together with the backend. Configure `ALEXA_SKILL_ID` in Lambda and retain the Alexa invocation permission restriction. The account page generates a one-time pairing code for **each Echo device**; use the new link-account intent on that device. Unknown subject/device combinations are rejected. `ALLOW_LEGACY_SKILL_OWNER=1` deliberately restores key-only owner behavior if migration compatibility is required.
7. Existing v1 audio capabilities are invalid after the salt change. Refresh clients and reissue playback URLs. Password/security changes invalidate pending proofs and capabilities; session changes can require another login. Email changes now display a pending address until verification.
8. Check `/health/live` and `/health/ready`, signup/invite, password reset, forced password completion, TOTP setup/change/remove, staged email verification, deletion/purge, two simultaneous provider tabs, provider disconnect/reconnect, and two accounts on separate Echo devices in staging. Use actual recovery mail delivery and device callbacks for the final acceptance check.

## Offline backup, restore and key rotation

Use Python 3.12 with the locked cryptography dependency. Key files contain a URL-safe base64 32-byte key, mode 0600. Use a **separate backup key** from the provider database key. Pass actual stopped-volume mount directories for the placeholders below.

```sh
python scripts/security_storage.py backup --key-file /secure/backup.key --output /backups/state.enc --data /mnt/data --alexa /mnt/alexa --profiles /mnt/profiles
python scripts/security_storage.py restore --key-file /secure/backup.key --source /backups/state.enc --destination /mnt/empty-restore
python scripts/security_storage.py rotate --database /mnt/data/data.db --old-key-file /secure/provider-old.key --new-key-file /secure/provider-new.key
```

Backup uses a SQLite snapshot and authenticated streaming encryption. Restore authenticates the entire archive before extraction, rejects unsafe members/nonempty destinations, and revokes restored sessions, email proofs, pairing and stream generations. Restored accounts must log in and Echo devices must pair again. Database key rotation is all-or-nothing, clears pending proofs/sessions, and requires updating the runtime key before restart. It does not encrypt provider files or Chromium profiles individually; the whole-state backup covers those files.

Recommended initial retention: seven daily and four weekly encrypted backups, stored outside the application host with separate key custody. Adjust to the actual privacy/storage policy. Run an isolated whole-system restore drill before rollout and quarterly thereafter. The tool supplies mechanics; it does not schedule backups or provision external storage.

## Validation and remaining limits

See the task's final test results for execution counts. Automated coverage includes full Flask authentication routes, adversarial service tests, provider controllers, browser UI, Node resolver deadlines, Lambda SDK serialization, encrypted restore/tampering and key-rotation rollback. CI runs pinned dependency advisories, secret scanning, image scans and an SBOM job.

No automated suite proves live Google/Amazon acceptance, mailbox delivery, actual Echo attribution, production-host sandbox compatibility, multi-hour disconnect storms, or a production restore drill. Those checks require the staging environment and user-owned accounts/devices. SQLite, in-memory playback/rate budgets and the browser coordinator currently assume a single backend process. The route policy inventory covers account/admin blueprints; playback/provider routes retain their existing policies and focused tests. Upstream unfixed container advisories remain visible in scan output; CI blocks fixable HIGH/CRITICAL findings.

### Local validation record (2026-11-03)

- Isolated server/web runner: **113 regression files covered**. The final broad run passed 111 files; its two failing fixtures were corrected and passed in separate fresh-process reruns (account pages: 5 tests; sidecar: 27 tests). The added Amazon race test also passed after that broad run.
- Security CI selection: **32 passed**. Node 22 resolver: **20 passed**. Lambda SDK/controllers: **44 passed**. Backup/rotation: **4 passed**; Amazon state/attempt tests: **35 passed**.
- Browser UI: **98 passed** outside the dashboard; dashboard **19 passed**, with its remaining check passing on an isolated retry (**1 passed**). That check failed once under concurrent test/scan load; retain it in CI and investigate if it repeats.
- Both production images build; backend UID 10001 readiness/access smoke and Compose configuration validation pass. Production Chromium sandbox launch fails under this review host's namespace policy (see rollout requirement above).
- Nonignored source snapshot: Gitleaks reports **no leaks**. Backend image: **274** upstream advisories, **38 HIGH/CRITICAL**, **0 fixable HIGH/CRITICAL**. Browser image: **1018** advisories, **254 HIGH/CRITICAL**, **0 fixable HIGH/CRITICAL**. Counts include distribution scanner classifications, not demonstrated application exploits; an empty fixable gate is not a clean image.
- Runtime dependency locks were audited separately with no known advisories. Native OS/Chromium findings above remain; update pinned bases/packages when upstream fixes become available.

## Test stack rollout (2026-11-03)

Deployed the remediation to Compose project `musicbox-test` at `https://testalexa.synthora.in`. Schema 7 migration retained all 17 existing accounts; provider ciphertext decrypts and database integrity is `ok`. Backend/browser run as UID 10001. Public readiness, login and admin health return 200; the deployed browser launches successfully.

Before switching, saved local rollback filesystem images/configuration and an encrypted whole-state archive under `/home/ubuntu/musicbox-deployment-backups/20261103T165703Z/`. An isolated restore drill authenticated/extracted all three state directories, retained 17 accounts, and revoked restored sessions. These local backups still need external retention and separate key custody.

This host blocks Chromium user namespaces. The user explicitly approved preserving its existing unsandboxed compatibility mode in the ignored `.env.testalexa`; private networking, non-root execution and container limits remain enabled. Lambda/interaction-model publication is pending ASK authentication. Legacy key-only owner bypass remains disabled; updated skill requests must pair their Alexa subject/device before normal account playback.

Deployment also exposed a YAML flow-list parsing bug in the tmpfs options, now quoted correctly. Hosted CI exposed numeric TOTP leading-zero loss and a narrow-screen header overflow with wider system fonts; both have targeted fixes and regression coverage.
