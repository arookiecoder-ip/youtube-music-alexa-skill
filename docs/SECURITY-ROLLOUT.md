# Security remediation and rollout

Current private-skill configuration uses a private `.env` beside `data.py`, copied from `lambda/.env.example`. `api_key.py` references below describe the earlier deployment and are superseded by the [current setup](multi-user/PRIVATE-SKILL-ACCESS-2026-11-11.md).


Implementation status for the findings in [the architecture audit](CURRENT-ARCHITECTURE-SECURITY-REVIEW.md). Changes are committed on `feat/multi-user` and deployed to the test stack at `https://testalexa.synthora.in`. The Alexa-hosted development skill and all five language models are also deployed; live provider login and real Echo pairing still require account/device verification.

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

1. Stop the backend and browser and take an encrypted backup of **all three volumes**: database/data, Amazon cookies, Chromium profiles. Keep a separately tested copy of the encryption keys. Migration advances the account schema to **8**. Rollback requires the prior image and prior whole-state backup; do not run an older binary against the migrated database.
2. Configure an independent `MULTIUSER_ENCRYPTION_KEY` (or existing supported `ADMIN_ENCRYPTION_KEY`). HTTPS multi-user startup now refuses implicit derivation from the Flask signing secret. **Preserve the existing encryption key** or rotate the stored ciphertext before changing it. Merely replacing an environment variable makes existing encrypted credentials unreadable.
3. For a deployment that previously derived its key from `SECRET_KEY`, securely derive the old Fernet key using `multiuser.crypto._fernet_key('', old_secret)` in an isolated administrative process. Write it directly to a protected key file, never logs/shell arguments. Rotate the database to the new independent key while stopped, update configuration, then restart. Keep signing-secret rotation separate.
4. Retain strong independent browser-control and egress tokens. Compose's one-shot permissions service changes only the named application volumes to UID/GID **10001** before application startup. Confirm bind-mounted cookie files remain readable by that UID.
5. Validate Chromium sandbox support on the target host. The managed review host blocks the required sandbox setup under the hardened container profile. Install/permit appropriate unprivileged user-namespace support on the deployment host. `MUSICBOX_CHROMIUM_DISABLE_SANDBOX=1` is an explicit compatibility escape hatch with weaker isolation; it is **not** the default and was not enabled in production. A passing browser-test launch does not prove production sandbox compatibility.
6. Deploy the Lambda changes and updated interaction models together with the backend. Configure `ALEXA_SKILL_ID` in Lambda (and `ALEXA_SKILL_NAMESPACE` for a second skill) and retain the Alexa invocation permission restriction. The account page generates a one-time pairing code for **each Echo device**; use the new link-account intent on that device. Unknown subject/device combinations are rejected. `ALLOW_LEGACY_SKILL_OWNER=1` deliberately restores key-only owner behavior if migration compatibility is required.
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

## Release verification (2026-11-04)

Application revision `50bbef2` fixes the remaining 320px overflow by wrapping long details-button labels while keeping their icons inside the button. A deterministic larger-font test reproduced the failure before the fix; all 21 dashboard checks pass locally after it.

[GitHub regression run 41760029881](https://github.com/arookiecoder-ip/youtube-music-alexa-skill/actions/runs/41760029881) passed both the playback and browser jobs. The release is deployed; live source hashes match, public health/login checks return 200, unauthorized mutations/browser access return 401, schema 7 integrity is `ok`, and all 17 accounts plus the stored provider credential remain intact. The approved Chromium compatibility mode is retained. ASK authentication was pending at that verification point; the completed Alexa-hosted release is recorded below.

## Alexa-hosted development release (2026-11-04)

Amazon developer authentication succeeded without linking an AWS account or creating IAM access keys. The private hosted skill was backed up before changes. Amazon reports deployment commit `363b66f` as **SUCCEEDED**; all five locales (`en-AU`, `en-CA`, `en-GB`, `en-IN`, `en-US`) exactly match the prepared models and include `LinkAccountIntent`. The original skill manifest/endpoints and invocation name were retained.

The approved backend API key is stored only in the private Amazon-hosted configuration. That configuration also activates the application-ID check and points to `https://testalexa.synthora.in`. Help and invalid-code pairing simulations both completed successfully; the latter reached the new pairing handler and rejected the invalid code. Actual Echo pairing requires the account page's current code on each device.

**Hosted runtime limitation:** Amazon reports Python 3.8 for this skill and manages that runtime. The new `lambda/requirements-hosted.lock` is compiled for Python 3.8, and all 44 Lambda tests pass with those exact hashed dependencies. The hosted requirements entry point uses `--require-hashes` and includes that compatibility lock. The existing Python 3.12 lock remains appropriate for newer self-managed runtimes.

The Python 3.8 compatibility audit reports known advisories in `requests==2.32.4` and `urllib3==1.26.20`. Available fixed versions require newer Python/dependency support, so this hosted compatibility lock is **not advisory-free**. Reproduce with `pip-audit --disable-pip --no-deps -r lambda/requirements-hosted.lock`. Resolving that remaining platform constraint requires a supported runtime/hosting migration; obtaining IAM keys alone does not upgrade this Alexa-managed runtime. See [Amazon's hosted runtime policy](https://developer.amazon.com/en-US/docs/alexa/hosted-skills/build-a-skill-end-to-end-using-an-alexa-hosted-skill.html).

## Separate multi-user Alexa skill (2026-11-09)

A second, independent Alexa-hosted custom skill now runs against the same backend without touching the original skill (`amzn1.ask.skill.27cadb23-7c3c-48c9-b01d-1cdfa56636b6`) or its `skill-package/` assets.

| Item | Value |
| --- | --- |
| Skill name / invocation | Music Box Multi / `music box multi` |
| Skill id | `amzn1.ask.skill.8cd77e6f-497a-40db-8057-d6aefcf8fc09` |
| Hosting | Alexa-hosted Python 3.8, its own Lambda, S3, DynamoDB and CodeCommit repository |
| Locales | `en-AU`, `en-CA`, `en-GB`, `en-IN`, `en-US` |
| Backend origin | `https://testalexa.synthora.in` |

### Isolation model

- **Bindings are skill-scoped.** `alexa_bindings` now has `PRIMARY KEY(skill_id, subject_hash, device_hash)`. The skill id arrives in `X-Alexa-Skill-Id`; the legacy skill sends no header and maps to the empty namespace. The same Echo can therefore hold one binding for each skill, even for different accounts, and a pairing made by one skill never authorizes the other.
- **Pairing codes stay unscoped but single-use.** The account page's existing code flow is unchanged. Whichever skill redeems a code first creates a binding only in its own namespace; replay into the other skill hashes to the same row and is rejected as already consumed.
- **Legacy compatibility is byte-compatible.** The empty namespace hashes values exactly as schema 7 did (`HMAC(secret, value)`); only a real skill id adds the `skill\0` prefix. The v8 migration copies existing rows into the empty namespace unchanged, and pairing codes issued just before the upgrade still redeem. A regression test covers both.
- **Pairing is budgeted per Echo.** The old shared-Lambda-egress IP budget (5/300 s for every customer) was replaced by 5 attempts per 300 s per subject/device plus a 100/300 s IP ceiling. One Echo guessing codes cannot lock out other households, while device-id rotation remains bounded.
- Malformed `X-Alexa-Skill-Id` values return `400 invalid_skill`; a well-formed unknown skill returns `403 link_account_required`; empty or missing user/device values cannot resolve a binding.
- The hosted Lambda has `ALEXA_SKILL_ID` and `ALEXA_SKILL_NAMESPACE` set to the new skill id, so the handler rejects requests whose `context.System.application.applicationId` differs. The API key and origin are stored only in that private hosted configuration.

### Test evidence

- Backend: `test_alexa_skill_isolation.py` **11 passed** (cross-skill isolation, independent per-skill pairing, code replay, malformed headers, expiry/security generation, disabled account, per-Echo rate limit, legacy-row and pre-upgrade code compatibility).
- Existing regressions: `test_auth_route_contract.py` **5 passed**, `test_multiuser.py` **33 passed**, `test_security_storage.py` **4 passed**, `test_alexa_logged_out.py` **7 passed**.
- Lambda: all **50** tests pass, including new skill-header and application-id enforcement tests.
- Live SMAPI `invoke-skill` against the deployed skill: launch and help returned normally; an invalid code was rejected; a valid account-page code linked the Echo; the next play returned `Playing Blinding Lights by The Weeknd`.
- After enabling the skill for development testing, live NLU `simulate-skill` routed "open music box multi" to the launch handler and "ask music box multi to link account 00000000" to the pairing handler, which rejected the code.
- Live backend checks: the new skill resolved while the legacy namespace did not inherit it; pairing the same Echo under the new skill created a second row in the new namespace; replaying that code with no skill header returned 403; malformed skill header returned 400; five wrong codes returned 403 and the sixth returned 429, while a different Echo was still allowed.
- The v8 migration was exercised on the live test database after a snapshot. It retained existing accounts and exposed the pre-v8 legacy hash incompatibility described above, which was fixed before the final verification.

### Remaining limits

- No physical Echo was available. SMAPI simulation invokes the deployed Lambda and the real backend, but cannot prove device audio output, callbacks, or Amazon-managed pairing UX.
- Both skills share one backend and one Lambda egress address. Isolation rests on the skill id + Alexa subject/device binding and the per-Echo rate budget, not on separate infrastructure for the application server.
- The new hosted repository's private `api_key.py` is not in this repository. Redeploying requires regenerating CodeCommit credentials with `ask smapi generate-credentials-for-alexa-hosted-skill` and recreating that private file.
