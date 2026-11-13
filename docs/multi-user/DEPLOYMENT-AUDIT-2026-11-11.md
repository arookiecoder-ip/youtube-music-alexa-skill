# Multi-user deployment and app audit — 2026-11-11

Historical review/deployment record. For the current per-account token architecture and setup, see [Private skill access](PRIVATE-SKILL-ACCESS-2026-11-11.md).


Subsequent change: [Echo code pairing was removed at the user's request](ECHO-PAIRING-REMOVED-2026-11-11.md). The pairing-panel descriptions below record the preceding release, not the current UI.

## Released

- Test backend: `https://testalexa.synthora.in`, Docker service `musicbox-test/ytmusic`. Final running image: `sha256:9757c917acf3d174ec19946066851dddf295e3d83864eda5224e7ed6e22d6262`.
- Alexa-hosted development skill: **Music Box Multi**, `amzn1.ask.skill.8cd77e6f-497a-40db-8057-d6aefcf8fc09`. Amazon reports commit `cc95707cdde953680c6b175b44c27bbd381c88fd` deployed successfully. The preceding security release was `c63920924bf2ce797a2b8fdb3a0ceb46a6372036`.
- Database schema 9 is active, integrity check is `ok`, and all 19 existing account rows remain. The pre-release snapshot had zero Echo bindings. No real account was created, deleted, or signed out for the live checks.
- The backend's persistent invocation setting is now `music box multi`.
- The previously unavailable en-GB interaction model was uploaded from the reviewed local multi-user package. Its build succeeded and retrieving it now exactly matches the local model.
- Final deployed backend source/template/style/script hashes match this workspace. Hosted functional Python source also matches; the hosted private key configuration and runtime-specific dependency pins were preserved.
- The original Alexa skill and its interaction models were not deployed or changed.

Backups and verification artifacts are stored privately under the gitignored `.alexa-new/security-release-20261111/`. These include a verified SQLite backup, the previous hosted source, release metadata, source hashes, browser reports/screenshots, and safe skill-probe responses. The old backend image is tagged `musicbox-test-ytmusic:before-security-20261111`.

## Fixed during this audit

The [security patch](ALEXA-SECURITY-FIXES-2026-11-11.md) is deployed: administrator-pinned HTTPS backend destinations; no authenticated redirects; complete Alexa identity checks; per-device/per-binding DynamoDB partitions; revocable binding-scoped stream capabilities; authorization before saved-state access; rejection of previous-binding callbacks; and freshly authorized resume/seek streams.

Additional fixes:

1. **Browser GET requests could mutate liked songs.** A cross-site top-level GET can carry a Lax session cookie. Cookie-authenticated GET mutations are now rejected before provider access; browser JSON POSTs and the key-authenticated skill's existing GET contract are retained. Regression tests cover all three paths.
2. **Echo unlinking also signed out the browser.** Deleting binding generations already revokes Echo stream capabilities. The unrelated account-wide security-version bump was removed, so unlinking Echos preserves web sessions. A regression verifies both continued web authorization and rejected Echo capabilities.
3. **Pairing guidance invoked the wrong skill.** The account page uses the backend's configured invocation name. The pairing panel now lives under Services and follows the page's existing responsive layout.
4. **Unlinking lacked confirmation.** It now uses the existing accessible confirmation dialog; cancellation sends no request. Pairing requests disable duplicate submission, preserve all eight digits, display failures, and retire the displayed code on expiry.
5. **Account failures could look like successful saves.** Non-JSON and unsuccessful HTTP responses now become explicit errors. Network and server failures are displayed inside the active dialog, with retry controls restored and typed input retained.
6. **Loop commands wrote the wrong settings dictionary.** Both handlers now update the setting consumed by playback. Declared Repeat and Navigate Home intents also have functional handlers.
7. **Two public routes raised template errors.** Privacy and terms pages now render factual service information; account door pages link to them. Deployment-specific policies remain the operator's responsibility.
8. **Multi-user model changes did not trigger regression CI.** The workflow now includes `skill-package-multiuser/**`.

The app's existing custom skill, per-Echo pairing, and `alexapy` remote command transport remain in use. This release does not add OAuth account linking or casting.

## Verification evidence

| Area | Result |
| --- | --- |
| Isolated backend/JavaScript regressions | **115/115 files passed**: 85 Python files with **1,485 tests**, zero skipped/failing/error cases, plus 30 JavaScript regression files |
| Initial Chromium browser suite | **119 passed**, covering account dialogs/layout, provider sign-in challenges, collections, artists, playback feedback, progress controls, and status UI |
| Final account-page Chromium suite | **69 passed**, including eight new pairing/error cases; replaces its earlier 61-case run, giving **127 distinct browser cases** across the suite |
| Follow-up backend auth contracts | **8 passed**, including browser GET mutation rejection and restored public pages |
| Follow-up Alexa security checks | **14 passed**, including preserved browser login after Echo unlinking |
| Follow-up self-service flows | **58 passed** after the account template and unlink changes |
| Lambda controllers/serialization | **51 passed**; the isolated real-SDK security suite now contains 16 cases, including loop settings and declared navigation intents |
| Node streaming resolver | **20 passed** |
| Node dependency audit | **Zero reported vulnerabilities** in the production dependency audit |
| Live public pages | **28/28 checks passed** at 320, 390, 768, and 1440 CSS pixels; no horizontal overflow or JavaScript page errors |
| Live endpoint access | Readiness/login/signup/privacy/terms return 200; anonymous account and Alexa identity endpoints return 401 |
| Live Alexa probes | URL changes receive the administrator-only refusal; an unlinked metadata request is refused. SMAPI invocation reports success |
| Release consistency | Backend and hosted source match; schema 9, database integrity, preserved account count, healthy container, compilation, JavaScript syntax, and whitespace checks verified |

Authentication coverage includes regular and administrator login, passwordless administrator links, TOTP enrollment/confirmation/replay/lockout, pending second-factor binding, email verification/change, invitations, password reset/change, forced password changes, CSRF, session revocation, provider-attempt isolation, device identity, role checks, account deletion/purge, audio-token scope, and account/binding proof revocation. Tests use temporary accounts/databases and scripted provider responses rather than sending email or changing real provider accounts.

Screenshots of the deployed anonymous pages and responsive account Services panel are saved with the private release artifacts. The account Services panel was visually inspected at mobile width in addition to its automated geometry and interaction checks.

## Remaining limits and maintenance work

- **Hosted runtime/dependency risk remains.** The complete 52-package backend lock audit found no known advisories. The Alexa-hosted repository still pins `urllib3==1.26.20`; its five explicitly pinned dependencies were audited and urllib3 reports known advisories. The scanner identifies fixes through 2.8.0, which [requires Python 3.10 or newer](https://pypi.org/project/urllib3/2.8.0/). Amazon reports Python 3.8 for this hosted skill. This cannot be resolved by blindly installing the current urllib3 release into that runtime; it needs a supported hosting/runtime migration. The hosted audit is not a complete transitive dependency inventory.
- No physical Echo audio output, real Amazon/Google reauthentication, or real email-delivery round trip was exercised. Provider challenges and account recovery are covered with temporary fixtures; live probes establish deployment/auth behavior rather than audible playback.
- Responsive Chromium testing covers the web app on desktop/tablet/mobile widths. Native Android integration sources were inspected, but no new APK was built and no Android device/emulator UI run is claimed. The existing app checkout and its untracked workflow were preserved.
- Existing YouTube server-session fallback is intentionally shared for accounts without their own provider session. It is not a private personal library for each such account; this existing product policy was not silently changed.
- The root workspace's legacy `ask-resources.json` must not be assumed to identify the new skill. This deployment used the explicitly identified private hosted repository and skill ID throughout.
- Passing these checks is evidence for the listed flows, not a guarantee that every possible UI, device, provider, or security failure has been eliminated.

Detailed logs: `/tmp/musicbox-full-audit-20261111/`, `/tmp/musicbox-account-final-browser.log`, `/tmp/musicbox-final-selfservice.log`, `/tmp/musicbox-resolver-audit.log`, and the dependency-audit JSON files under `/tmp/musicbox-*-dependency-audit.json`.
