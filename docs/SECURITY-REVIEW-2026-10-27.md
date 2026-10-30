# Authentication security review — 27 October 2026

Reviewed the current working tree, including uncommitted changes. This is a code and offline behavior review, not a production penetration test. No live Google/Amazon login, real email delivery, or production account mutation was performed. Application code was not changed.

## Flow map

| Flow | Entry points | Main security boundary |
| --- | --- | --- |
| Signup/verification | `/signup`, `/verify`, `/resend-code` | CSRF, signup nonce, fixed user role, email codes |
| Account/owner login | `/login/` | Account password or legacy environment password, optional pending TOTP, two session systems |
| Admin login | `/admin/login/`, `/admin/magic/<token>` | Password/TOTP or email link |
| Password recovery/set/change | Public reset, account reset/change, admin reset/change, invitations | Multiple routes call different account-service methods |
| Email change | `/api/account/profile`, `/api/account/email/change`, `/admin/account/profile/` | Inconsistent password/mailbox requirements |
| TOTP setup/replacement/removal | Player and admin account endpoints | Pending encrypted secret, password/email step-up, TOTP confirmation |
| Account deletion | Self-delete and admin delete | Password/email proof, last-owner checks, DB deletion, asynchronous artifact cleanup |
| YouTube | Interactive browser, scripted form, automatic server renewal | Shared Chromium and control token; separate ownership mechanisms |
| Amazon | Scripted form and mirrored AlexaPy proxy | Per-account remote object, shared proxy claim, staged credential promotion |

Severity depends on prerequisites. “Reproduced” below means deterministic temporary test accounts or fake controllers, not exploitation of the deployed service.

## High-priority findings

### 1. Email-change proof can be bypassed through another route — High, reproduced

`multiuser/selfservice.py:1174` (`api_profile`) calls `update_own_profile` with only the current password. `/api/account/email/change` additionally requires the old mailbox's code. `multiuser/admin.py:1460` also uses the weaker path.

An authenticated session plus the password can replace the recovery mailbox without the old mailbox proof promised by the newer flow. A direct POST to `/api/account/profile` returned 200 and changed the email without any code. Existing TOTP does not gate this route.

Fix: enforce one email-change policy in the service, remove/bind the legacy routes, stage the new email until verified, notify the old address, and decide session revocation centrally.

### 2. Admin email-link sign-in skips enrolled TOTP — High, reproduced

`multiuser/admin.py:1872` consumes a magic token and immediately creates an authenticated admin session. It never checks `has_totp`. Magic links are enabled by default.

A mailbox compromise or stolen sign-in link therefore bypasses the account's password and authenticator. A TOTP-enabled test owner received a valid admin session from a magic link with no TOTP submission. This is an alternate authentication policy, not a cryptographic break of TOTP.

Fix: require TOTP after magic-link verification for enrolled accounts, or explicitly define and restrict a separate recovery procedure. Use a confirmation POST rather than a session-creating GET; current GET behavior also permits login CSRF and link-scanner consumption.

### 3. TOTP guessing budgets reset before second-factor success — High, reproduced

`server.py:7011` vicinity and `multiuser/admin.py:1681` reset password/IP rate limits as soon as the password matches. Each correct-password submission creates another pending token. Wrong second-factor submissions consume that token but have no durable per-user TOTP failure budget.

An attacker who knows a password can repeatedly obtain fresh tokens and try codes. Six guesses were accepted for processing with a configured login limit of three. Coarse HTTP limits still exist, but they are not a second-factor account budget and distributed sources weaken them further. The admin public-login route returns before its blueprint's general authenticated-request limiter.

Fix: separate password and TOTP budgets, key second-factor attempts by immutable user ID, and reset failure state only after the complete login succeeds.

### 4. Pending password proof survives password changes/resets — High, reproduced

Pending records store user ID and destination but no credential/security version. Neither password changes nor session revocation invalidate all pending records. Both login completion routes only recheck current status/TOTP.

Reproduction: obtain a pending admin token, change the password, then submit the pending token with a valid authenticator code. Login still returned 302 and created a session. Recovery therefore does not fully retire previously accepted password proofs.

Fix: increment a security version on credential changes and compare it at second-factor completion and session creation. Include TOTP replacement, disablement, account disablement, and session-wide revocation in that policy.

### 5. Account revocation does not revoke scoped audio access — High, reproduced

`server.py:1420` vicinity (`_audio_session_valid`) validates audio credentials solely against `web_sessions`. Account password changes, account-session revocation and deletion act on `user_sessions`. `audio_credentials.py` embeds only the legacy SID, scope and TTL, with no account ID/security version.

A scoped token issued to a player remained valid for `/next_track` after `revoke_all_sessions` revoked that player's account sessions. A token-only request also lacks an authenticated account identity and defaults to the owner context. The allow-list includes queue-sensitive `/queue_tracks` and `/next_track`, not just audio bytes.

Fix: bind token subject and credential version to the real account/session, validate both, establish that identity for the request, and narrow streaming capability to the endpoints it actually needs.

### 6. Login preserves legacy owner identity when switching accounts — High, reproduced

`server.py:6923` writes `mu_sid`/`mu_user_id` without clearing `remote_user`. Admin login likewise does not replace the whole session. `_logged_in` falls back to the retained legacy owner if account-session validation fails.

Reproduction: a browser carrying legacy owner fields signs into a player account; revoke the player's sessions. The same cookie still passes `_logged_in` as the legacy owner. This requires an existing owner cookie, but breaks account-switching and revocation boundaries on shared browsers.

Fix: replace authentication state atomically on login; retain only explicitly approved navigation/preferences. Never fall back to another identity after an explicit account credential is rejected.

### 7. Removing/replacing TOTP requires no independent factor — High with stolen-session/password prerequisites; reproduced

`multiuser/accounts.py:1123` and `:1246` use `require_email_code=False`. A valid session plus password can disable TOTP or start replacement without proving the existing authenticator or recovery mailbox. Admin replacement/removal accepts password OR an emailed code through `reauthenticate`.

Password-only removal succeeded in the offline probe. This weakens protection after session/password theft. Notifications occur after the change and do not prevent it. This is an explicit current policy, not an accidentally omitted frontend field.

Fix: require existing TOTP or a deliberate recovery factor, protect replacement/removal with a short-lived action-specific proof, and revoke other sessions when factors change.

### 8. YouTube operations can affect another account's shared browser — High for potential credential misbinding; disruption reproduced

`youtube_browser_session.py:219` closes the global sidecar even when revoking an unrelated identity. `/logout/` invokes this operation. A fake-controller test confirmed revoking account B calls `/interactive/close` while A still owns the browser.

`server.py:7850` sends `/google/stop` even when the caller did not own the scripted claim. `browser-auth/service.py` does not bind stop/capture/candidate-take to an attempt ID. Interactive leases, scripted `_google_form_holder`, and automatic-renewal locking are separate authorities for one physical browser. `poll` checks ownership, performs network work, then promotes without an immutable attempt/generation check. These are race windows; cross-account credential capture was not demonstrated end-to-end.

Fix: one shared browser coordinator; require owner/profile/attempt ID on every sidecar command, candidate and promotion. Revalidate immediately before committing credentials. A stale close must never close a later attempt.

## Additional confirmed and source-level findings

### 9. TOTP replay tracking spends the wrong counter — Medium, reproduced

`security.py:totp_verify` accepts counter ±1 but returns a boolean. `accounts.py:1291` records the server's current counter instead of the matched counter. The same code was accepted at time T and again at T+30 seconds. The legacy verifier has no replay ledger at all.

Fix: return the matched counter and atomically consume that exact counter against the same secret/security generation.

### 10. Expired pending TOTP enrollment can still activate — Medium, reproduced

`accounts.py:confirm_totp_enrollment` reads the row before `_sweep_totp_enrollment`, then verifies and promotes the secret from the stale row. A pending secret 99,999 seconds old was accepted after the sweep removed it from the database. Confirmation is also not a conditional update against the exact pending secret/version, leaving concurrent replacement races.

Fix: check expiry and promote the matching pending generation atomically. Bind pending setup to the initiating session and clear it on password/security changes.

### 11. Amazon scripted login lacks attempt fencing after awaits — Medium, source verified

`alexa_remote.py:845` captures `_form_login`, awaits network work, then can promote it without checking it is still the active attempt. `_form_login_answer`/cancel operate by account, not attempt. A concurrent start, cancel, signout, or old-tab answer can act on another attempt of the same account. The mirrored proxy finalizer already has an identity check; the form path needs the equivalent.

Fix: generation-bound start/answer/cancel/promotion and recheck after every await. Keep prior verified credentials until atomic promotion succeeds. No live Amazon race was performed.

### 12. Signup/recovery account enumeration — Medium, reproduced for recovery

`selfservice.py:887` sets `mu_ss_pending_verify` only when a recovery request matches an account. Flask's default signed cookie is readable, not encrypted. Even with identical page text, the caller can inspect whether the pending field/user ID appeared. The reset page subsequently exposes the remembered username. Resend has similar conditional state; signup returns distinct collision results. Unknown-password authentication also skips expensive password verification, creating a timing difference.

Fix: opaque server-side pending state with indistinguishable responses/state shape; normalize collision behavior where privacy is intended; use a dummy hash verification for nonexistent accounts. Usernames may be intentionally public, but mailbox registration should have an explicit policy.

### 13. “Must change password” is only a flag — Medium, reproduced

`accounts.py` sets `must_change_password`, but login/session validation and global request guards do not enforce a restricted session. Account API access still returned 200 with the flag set. Temporary/admin-reset passwords can therefore remain usable for normal operation indefinitely.

Fix: issue restricted sessions until the required password change completes; allow only password-change, necessary factor verification, and logout routes.

### 14. New/reset passwords are placed in the client-side session — Medium, source verified

`multiuser/admin.py:791` flashes the reset/generated plaintext password. Flask stores flashes in its signed client-side session cookie; the repository does not configure a server-side session implementation. This duplicates the credential into a browser cookie. User creation also deserves the same treatment for generated-password flashes.

Fix: a short-lived, single-use server-side display record or direct no-store response; never put a password in session/flash data. HttpOnly and signing do not encrypt its contents.

### 15. Last-owner and signup limits are check-then-write races — Medium, source verified

`accounts.py:340` counts remaining owners before the later update/delete transaction. Two simultaneous owner-removal operations can each see another owner and both proceed. `delete_own_account` also checks last-administrator state before its eventual deletion. Signup daily-cap checks similarly precede account insertion.

Fix: serialize these invariant checks and writes in the same transaction (for example, SQLite `BEGIN IMMEDIATE`), with concurrency tests. These were not exercised against a live database.

### 16. Invitation/reset credentials can be burned by invalid new passwords — Low, source verified

`selfservice.py:775` consumes the invitation before service-level password-strength validation; a weak matching password spends the link without activating the account. The public admin reset similarly consumes its code before validating password strength.

Fix: validate first; consume and commit the credential change together. Return retryable validation feedback without destroying a valid proof.

### 17. Expired YouTube lease can block other users indefinitely — Medium availability, reproduced

`youtube_browser_session.py:171` checks `_busy` before expiring the old owner's lease. Expiry does not itself release `_busy`. After A's lease expired, B's start still returned busy. If A leaves permanently, another path must explicitly clear the claim.

Fix: expire the global claim before admission, release it atomically, and fence delayed cleanup with the retired attempt ID.

### 18. Google credential submission does not require a trusted origin — Medium, defense-in-depth source finding

`browser-auth/google_signin.py:339` fills a password based on finding a password field, without requiring HTTPS `accounts.google.com`. The classifier also accepts password/code fields on arbitrary hosts. A redirected/changed browser page could receive submitted credentials. An attacker-controlled navigation path was not demonstrated.

Fix: check exact trusted origin immediately before typing each secret and immediately before submission; reject unexpected origins and frames. Apply equivalent bounded-host/response-size validation to Amazon captcha fetches.

## Systemic risks and positive boundaries

- Multi-user initialization catches all exceptions and falls back to legacy operation (`server.py:12859`). When multi-user is explicitly enabled, startup should fail closed on security-layer initialization failure. Actual exposure depends on the remaining legacy credentials and registered routes.
- Public/admin/player routes implement different authentication policies. Consolidate sensitive mutations in services; require typed action-specific step-up proofs rather than caller-supplied `verified=True` booleans.
- The default trusted-proxy rule trusts all private peers. Prefer explicit proxy CIDRs; this matters if untrusted private-network clients can reach the origin. No public-header spoofing through the supported Caddy path was demonstrated.
- Numeric email codes are encrypted and bounded by attempts; successful consumption is atomic against replay. Password hashing uses a substantial PBKDF2 work factor. CSRF exists on self-service/admin mutations. Browser controller calls require a private control token. Provider replacements are staged and normally validated before promotion. These controls should remain.
- Account deletion cascades related database rows and queues failed external-artifact purges. Preserve that design, while fixing the owner invariant and revocation gaps above. No SQL-injection or arbitrary-profile-path deletion was established by this review.
- The public Amazon mirrored proxy delegates its session/access protection to AlexaPy. Caddy exposes it without Flask owner auth. The dependency's actual proxy authorization was not audited here, so this is an unresolved boundary, not a claimed unauthenticated takeover.

## Verification

Existing offline suites: **207 tests passed**, plus **52 subtests**: self-service 58, multi-user 33, two-factor login 33, YouTube browser session 32, Amazon switch/transport 7, Google sign-in/browser steps 44. Each group used an isolated process where required.

Additional temporary probes confirmed findings 1–10 where marked, 12, 13, and 17. Probe scripts remain under `/tmp/musicbox-auth-audit*.py`; their outputs contain synthetic test identities only. The probes demonstrate boundary behavior and are not committed regression tests. Provider race/injection findings marked source-level require targeted deterministic concurrency tests and deployment verification before stronger exploit claims.

Recommended order: unify email-change and second-factor policy; fix second-factor throttling and pending-proof invalidation; repair session/token identity and revocation; then add attempt fencing across provider sign-in and fix transactional invariants. Follow with permanent regression tests for each demonstrated failure.
