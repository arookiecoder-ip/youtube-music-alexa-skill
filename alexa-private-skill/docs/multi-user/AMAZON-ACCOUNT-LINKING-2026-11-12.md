# Amazon sign-in and developer-account access

The user-panel Amazon connection accepts any Amazon account the signed-in Music Box user successfully authenticates. Its email or mobile number does not have to match the Music Box login. This connection remains in that Music Box user's own cookie/session/device slot. The user must still have active Amazon/Alexa access and meet the existing Music Box account requirements. Failed or cancelled Amazon sign-in does not replace a working session; disabling access during login still prevents promotion.

These are separate identities:

| Account | What it controls |
| --- | --- |
| Music Box account | Website/app sign-in, music state and per-user server authorization |
| Amazon account connected in the user panel | Amazon session and Echo devices available to that user connection |
| Amazon developer account | Skill ownership and access to an unpublished development skill |

If developer account A owns the private skill and the website connects Amazon account B, the connection does not make A's private skill available to B. In normal development testing, use the developer account's registered Echo, or configure eligible beta-test access for B. The website can discover B's devices after successful authentication, but Music Box playback that invokes the private skill on an Echo still requires that Echo's account to have access to the skill. The skill's locale/invocation must also match the configuration.

For this private-skill setup, use the same Amazon account in the Developer Console, on the Echo and in the user panel. MUSICBOX_ACCOUNT_ID is the stable Music Box account ID associated with the admin-issued token. Copy it from Account settings; it remains unchanged when the recovery email changes. After successful Amazon sign-in, the server asks the authenticated Alexa session for its canonical customer email and ID, stores that verified identity encrypted under the Music Box user, and compares Alexa's Customer Profile API email against it on every skill request. Entering an email or phone number alone never proves that identity.

The developer console's login email is not present in an Alexa skill request, so the backend cannot independently verify skill ownership. The server verifies the actual Alexa account against the linked Amazon account; Amazon enforces availability of the unpublished skill. Creating the skill under a different developer account does not transfer it to the panel's Amazon account.

Unlinking or replacing Amazon changes the authorization generation. Old stream capabilities and persisted callback bindings stop authorizing. Revalidating the same canonical Amazon account preserves its generation; reconnecting after unlink creates a new one. Saved sessions from before this update verify their canonical identity when account status is checked. If that verification fails, reconnect Amazon; voice fails closed until verified. Access OFF, account restrictions, token expiry/rotation/revocation and Music Box security-version changes continue to reject playback. Already buffered audio cannot be recalled.

References: [Amazon device/development testing](https://www.developer.amazon.com/en-US/docs/alexa/test/test-your-skill-overview.html), [beta access](https://www.developer.amazon.com/en-US/docs/alexa/custom-skills/skills-beta-testing-for-alexa-skills.html), [profile API identity](https://developer.amazon.com/en-US/docs/alexa/custom-skills/customer-profile-api-reference.html).

## Verification for the linked Amazon identity update

- All 118 files in the isolated backend/web-logic regression runner passed; the five cases in the new canonical-identity file passed separately. Together: 119 regression files (89 Python files with 1,539 cases, plus 30 JavaScript files), zero failures/errors/skips.
- All 105 account/sign-in/admin browser cases passed across the initial run and focused reruns. Two copy expectations were updated for the clarified rule; an older connection-status fixture was corrected to supply the current access-permission and connection fields. The 12-case sign-in suite passed again in full.
- All 49 Lambda regression tests passed, including the isolated 17-case real ASK SDK suite. The shared package carries the same production Lambda source.
- Backend Docker build and test deployment succeeded; readiness returned HTTP 200 and an invalid Alexa token returned HTTP 403. The account-row count matched the private pre-deployment backup. No customer tokens were generated and no real Amazon credentials were used in tests.
- No Android source changed and no Android compilation ran on the server. Android builds remain restricted to GitHub Actions.

Before relying on a private Echo deployment, cross-check:

1. Use the same Amazon account in Developer Console, on the Echo and in the user panel; Music Box may use a different email. Confirm a real song plays after granting the skill email permission and deploying the updated Lambda source.
2. Invoke with another Amazon account: authorization must reject even with the right Music Box email/token. A skill owned by a different developer account is not automatically available on the connected Echo account.
3. Unlink, reconnect or replace Amazon: old audio URLs/callback bindings must reject. Normal status revalidation of the same account should preserve playback.
4. Rotate/revoke/expire the token, disable Amazon access, or restrict/delete the user: new requests and audio URL fetches must reject. Access OFF must hide Amazon options/status in both web and native clients, including already-open screens.
5. Exercise mobile-number sign-in, MFA, cancellation, a failed replacement login and disabled access during login. If canonical identity verification fails, the previous session must remain intact and no unverified Amazon claim may authorize playback.
6. Test real Echo pause/resume/next/previous and automatic queue advancement. Provider availability, private-skill access and buffered audio behavior require physical-device verification.
