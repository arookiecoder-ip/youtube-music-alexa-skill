# Private Alexa skills with per-account access

Each customer deploys their own development skill. The shared Music Box server remains multi-user; each skill can access only the account named by its credential. Publishing and pairing codes are not required.

## Skill configuration

Copy `lambda/.env.example` to a private `lambda/.env` and fill in these three settings. Deploy `.env` beside `data.py`. Lambda environment variables can override the file:

```dotenv
MUSICBOX_SERVER_URL=https://your-server.example
MUSICBOX_EMAIL=your-verified-musicbox-email@example.com
MUSICBOX_ALEXA_TOKEN=the-token-issued-by-your-administrator
```

No application ID or server-wide API key is required in this configuration. The server's administrator credential stays private to the server. Configure the Alexa Skills Kit Lambda trigger/resource policy through Amazon's deployment tools, so only Alexa can invoke the function. State partitions use the application ID already present in Alexa's request automatically.

Use `skill-package-multiuser/skill.json` and its interaction models. Enable **Customer Permissions → Email Address** (`alexa::profile:email:read`) and grant that permission in the Alexa app for the deployed development skill. Retain the AudioPlayer interface. Private development skills are usable by the developer's eligible Amazon account/devices; distributing the source does not make one private skill available to every Amazon customer.

The server asks Amazon's Customer Profile API for the account email using the current request's `context.System.apiAccessToken`. It never authorizes a claim such as “my Amazon email is X” on its own. The configured MUSICBOX_EMAIL must match the verified Music Box account associated with the admin token. Separately, the returned Amazon profile email must match the canonical Amazon email verified from the authenticated Amazon session connected in that user’s panel. The two emails can differ. A typed login identifier is never accepted as Amazon identity proof. The server cannot independently inspect the Developer Console owner’s login; create/test the private skill in the same Amazon account as the Echo and panel connection. Missing permission, missing identity, unavailable profile service or a mismatch denies playback. The profile is Amazon's active default Alexa profile; it does not identify the human currently speaking.

## Set up through the Alexa Developer Console

`skill.json` is the skill manifest for ASK CLI/SMAPI deployment. Do not paste it into the interaction-model JSON Editor or upload it as Lambda code. When configuring the skill manually in the console, set its options through the pages below.

1. Open your skill and choose **Build → Interfaces**. Enable **Audio Player**, then click **Save Interfaces**.
2. Choose the locale that matches your Echo language. Open **Build → Interaction Model → JSON Editor**, then paste the contents of `skill-package-multiuser/interactionModels/custom/en-IN.json` for English (India). Use `en-US.json`, `en-GB.json`, `en-CA.json` or `en-AU.json` for the corresponding locale. Click **Save Model → Build Model**. This editor takes the locale file, **not `skill.json`**.
3. Open **Build → Tools → Permissions** and enable **Customer Email Address**. Also grant this permission to the skill in the Alexa mobile app.
4. For an Alexa-hosted skill, put the files and subfolders from `lambda/` in the **Code** tab. Keep `lambda_function.py`, `data.py`, your private `.env`, `skill_security.py`, `requirements.txt`, `mediaUtils/` and `models/` together. Save and deploy the code. The `skill-package-multiuser/` directory belongs to the skill configuration, not the Code tab. For a self-hosted Lambda, upload the Lambda code to AWS and configure its Alexa Skills Kit trigger and the console's **Endpoint** page instead.
5. Copy `.env.example` to `.env` in the same deployed folder as `data.py`. Fill in your private server URL, verified Music Box email and admin-issued token, then save and deploy. The skill loads this `.env` automatically; Lambda environment variables override file values, including explicitly empty values. The example is never loaded automatically. Delete the old `api_key.py` from your deployed code.
6. In **Test**, enable testing in **Development**. Test using the configured invocation name and an Echo registered to the eligible Amazon developer account. Use the same Amazon account in the Developer Console, on the Echo and in the Music Box user panel. The Music Box login email can differ.

Amazon documentation: [interaction-model JSON Editor](https://developer.amazon.com/en-US/docs/alexa/custom-skills/create-the-interaction-model-for-your-skill.html), [AudioPlayer interface](https://developer.amazon.com/en-US/docs/alexa/custom-skills/use-long-form-audio.html), [customer email permission](https://developer.amazon.com/en-US/docs/alexa/custom-skills/request-customer-contact-information-for-use-in-your-skill.html), [skill manifest and console settings](https://developer.amazon.com/en-US/docs/alexa/ask-toolkit/vs-code-ask-skills.html).

## Administrator controls

Open **Admin → Users → select account → Amazon / Alexa access**.

1. Enable access for the intended account and save.
2. Ensure the account is active, its email is verified and its temporary password has been changed.
3. Generate a token and copy it into that user's private deployment. The usable token is shown in that one response only; it is not placed in a flash/session cookie or stored in plaintext.
4. Rotate when replacing a credential. Previous tokens and their signed audio URLs stop authorizing new requests immediately.
5. Revoke to retire credentials without changing the integration's display permission.
6. Disable access to hide Amazon/Alexa integration UI and revoke all tokens. Enabling access again never revives those tokens; issue a new one.

Tokens last one year. Password/security-proof, role, status and email changes invalidate the credential's stored account security version. The backend rechecks live account/token state on every skill request and every new audio URL request. Already buffered audio cannot be recalled from an Echo.

New accounts default to access **off**. Schema migration 10 preserves access for existing active accounts to avoid disconnecting an installation unexpectedly; administrators can disable them individually. This migration does not issue tokens or expose credentials.

## User interfaces and stale clients

When access is off, account provider payloads omit Amazon. Account HTML omits its service row, profile status omits Amazon connection/debug fields, native account/settings rows disappear, and device pickers remove Echo devices while keeping phone/browser outputs. Existing account pages refresh permission within 15 seconds; native device/settings checks and playback updates also refresh it. Authorization changes take effect at the server immediately, independent of display refresh.

Direct Amazon login/device/status routes, stale client requests and physical Echo controls are denied when disabled. Pending Amazon login promotion and remote-command execution check the live policy again. Website Amazon sign-in accepts any Amazon account the user successfully authenticates, including an email or mobile number different from the Music Box login. Private voice requests require the Amazon profile to match the verified panel connection, while the token remains bound to the verified Music Box email. Amazon’s rules for access to unpublished skills still apply. Queue and provider state remain isolated even when more than one account has the owner role; changing administrators never transfers the bootstrap owner's cookies.

## Cross-verification checklist

- [ ] Admin toggle persists across page reload, server restart and sign-out/sign-in.
- [ ] Ordinary users cannot enable access or generate/revoke tokens; admins cannot manage owners or other admins.
- [ ] Missing/invalid CSRF rejects every admin mutation.
- [ ] Generated token appears once, is absent from cookies/logs/subsequent GET responses, and the database contains only its hash.
- [ ] Enabled + active + verified Music Box email + verified linked Amazon account + current token plays on a real Echo.
- [ ] Wrong configured email, Amazon profile different from the linked account, denied email permission and unavailable Amazon API each reject without playback.
- [ ] Link Amazon with an email different from Music Box (also test a mobile-number login); voice succeeds only for that verified linked Amazon account.
- [ ] Unlink/relink or replace Amazon: old binding, callbacks and signed audio URLs reject; repeated verification of the same session preserves playback.
- [ ] Missing user/device identity and malformed/stale generation headers reject cleanly.
- [ ] Each supported Alexa region uses an allowlisted Amazon HTTPS endpoint; redirects and arbitrary hosts are rejected.
- [ ] Rotate/revoke/expire the token: old requests, callbacks, persisted playback and old signed audio URL requests cannot authorize.
- [ ] Disable access during profile verification or Amazon sign-in: the in-flight operation cannot commit authorization or a new saved session.
- [ ] Disable then re-enable: old tokens remain invalid; a new token succeeds only with the remaining account checks satisfied.
- [ ] Disable/restrict/delete the account, require password change, or change its email/password/security proofs: skill requests reject.
- [ ] With access off, inspect web account, profile menu, Amazon deep links, native onboarding/account/settings, device picker and an already-open screen; no Amazon linking/status/device options remain.
- [ ] With access off, phone/browser playback, seeking, queue editing and switching between permitted outputs still work.
- [ ] Two accounts/private skills and two Echo devices never share queue, cookies, account data or persistence buckets.
- [ ] A second owner or promotion/demotion never inherits the bootstrap owner's playback/provider slot.
- [ ] Install the APK produced by GitHub Actions, repeat toggle and playback checks, and test session expiry independently of Amazon access.
- [ ] Test physical Echo pause/resume/next/previous, queued callbacks and permission consent. Automated tests mock Amazon and cannot prove physical hardware/provider behavior.

## Verification and operational limits

Backend tests cover live credential authorization, token/stream revocation, account restrictions, in-flight revocation, admin CSRF/one-time reveal, hidden payloads/HTML, region allowlisting and isolation. Browser checks exercise seven viewport widths and disabled access. Lambda tests include actual ASK SDK serialization and authorization-before-persistence.

Android compilation, JVM tests, instrumentation APKs, lint and emulator runs are performed **only in GitHub Actions**. The existing `Android Feature Build` workflow supports these runs on `feat/multi-user`. A proposed dedicated `android-multiuser.yml` is present locally in the native checkout but cannot be pushed with the available credentials because GitHub denied workflow-file write access. Successful existing workflow runs remain usable for builds and verification.

Alexa-hosted Python 3.8 dependency constraints from the earlier security audit remain a separate hosting migration issue; this change does not claim to eliminate those runtime dependency advisories. Never publish a private `.env` or a generated token with the shareable source.

References: [Amazon Customer Profile API](https://developer.amazon.com/en-US/docs/alexa/custom-skills/customer-profile-api-reference.html), [Alexa request context and API access tokens](https://developer.amazon.com/en-US/docs/alexa/custom-skills/request-and-response-json-reference.html).

## Recorded verification

- Backend/web logic: all 116 isolated regression files passed (86 Python files, 1,511 test cases, and 30 JavaScript files). Latest state-restriction/account-policy additions also passed focused reruns.
- Account page in Chromium: 67 checks passed across seven viewport widths, including hidden Amazon access.
- Lambda: all 49 discovered tests passed; the isolated real ASK SDK authorization suite passed 17 cases.
- Android commit `6d2620d1987af4307ef8552c3f51875943b9423e`: [GitHub Actions run 43064822378](https://github.com/arookiecoder-ip/MusicBox-APP/actions/runs/43064822378) passed APK compilation, 513 JVM tests, 21 phone emulator UI checks, APK entry-point verification and lint with zero errors. Lint still reports nonfatal warnings/hints.
- Backend deployed to `https://testalexa.synthora.in`: liveness/readiness HTTP 200, schema 10, existing 19 account rows preserved, invalid Alexa credential HTTP 403. No customer tokens were generated automatically.
- The current Alexa Lambda source/shareable package is ready for each user's private deployment; an existing hosted skill is not assigned an arbitrary customer's email/token automatically. Physical Echo permission/voice checks remain in the manual checklist above.

Latest identity-policy implementation and verification: [Amazon account linking update](AMAZON-ACCOUNT-LINKING-2026-11-12.md). The configured Music Box email can differ from the verified Amazon account connected in the user panel.

The private `.env` supports one `KEY=value` per line, optional single/double quotes, comments and an optional `export` prefix. It does not expand variables or execute commands. Only the three Music Box settings above are read, only from the file beside `data.py`; no parent-directory search occurs. GitHub and the downloadable ZIP contain only `.env.example` with placeholders. Keep your filled `.env` in your private deployment. `api_key.py` is no longer used.

Configuration-loader verification: all 57 Lambda regression tests passed, including eight private `.env` cases covering precedence, missing files, ignored examples/parent files/legacy Python config, quoting/comments, literal values and redacted malformed-setting errors. No dependency or backend/native-app changes are required.
