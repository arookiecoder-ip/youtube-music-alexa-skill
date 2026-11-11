# Alexa multi-user implementation review

Historical review/deployment record. For the current per-account token architecture and setup, see [Private skill access](PRIVATE-SKILL-ACCESS-2026-11-11.md).


Reviewed 2026-11-11. **Verdict: multi-user foundations exist, but the skill is not ready to be considered reliably isolated between users.** The main gaps are credential exposure through configurable API destinations, Lambda state isolation, account reassignment, and the running backend's invocation name.

Follow-up: the security boundaries described below have now been patched and deployed to the test backend and Music Box Multi skill. See [security fixes](ALEXA-SECURITY-FIXES-2026-11-11.md) and the [deployment/app audit](DEPLOYMENT-AUDIT-2026-11-11.md) for the current release, additional UI/backend fixes, verification, and remaining limits. The findings below record the pre-fix state.

This was a review. No application code, account bindings, environment settings, or remote deployments were changed. Reproductions used synthetic credentials, mocked HTTP, and temporary databases.

## Scope and deployed configuration

- Read the Lambda handlers, player/controller, data/configuration, models, both skill packages, and relevant backend identity, pairing, authorization, provider-session, playback-state, and revocation paths.
- Used the authenticated ASK CLI to identify **Music Box Multi**, skill `amzn1.ask.skill.8cd77e6f-497a-40db-8057-d6aefcf8fc09`, in the development stage.
- Retrieved its Alexa-hosted Git repository into `/tmp/musicbox-multiuser-review-20261111`. Its `master` commit is `71a49aaf4df7226d50872950ba813e3c15dceb93`; SMAPI reports successful hosted deployment of that exact commit. The functional Python source matches the workspace. Private configuration and dependency requirements differ from the workspace templates.
- The hosted configuration sets the expected application ID and skill namespace to the new skill ID, and the backend URL to `https://testalexa.synthora.in`. The backend key is configured; its value was not needed in the report or reproductions.
- SMAPI reports successful builds for all five locales. Fetched en-IN, en-US, en-CA, and en-AU interaction models match their local multi-user models and invoke **music box multi**. Retrieving en-GB returned 404 on two attempts despite its successful build status, so its deployed model could not be compared; investigate that discrepancy before relying on the UK locale.
- SMAPI's account-linking configuration endpoint returns 404. The implementation uses custom Echo pairing, rather than Alexa OAuth account linking.
- The running `ytmusic` container has `MULTI_USER_ENABLED=1`, with no `SKILL_INVOCATION_NAME` override. Source defaults that setting to **music box**.
- Hosted metadata reports Python 3.8. Local verification used Python 3.12; these checks do not establish compatibility of every dependency with the hosted runtime.

## Findings, ordered by priority

### 1. High: a voice intent can send the deployment API key to an arbitrary server

Locations: `lambda/lambda_function.py:462`, `lambda/mediaUtils/player.py:527`, `lambda/mediaUtils/player.py:192`.

`SetApiurlIntent` accepts a hex-encoded arbitrary URL and persists it. There is no HTTPS requirement, approved-origin check, or administrator restriction. Every subsequent backend call uses that URL and includes the shared `X-Api-Key`, Alexa user ID, and device ID. The intent is present in the deployed interaction model and does not require successful account pairing first.

An offline reproduction set a synthetic HTTP destination, invoked the actual API helper with mocked HTTP, and confirmed that the synthetic shared key was forwarded there. This exposes a deployment-wide credential, rather than a credential limited to the requesting Music Box account.

**Required fix:** pin the multi-user deployment to an administrator-configured HTTPS origin; remove or disable the voice override for that deployment and ignore existing persisted overrides. Restrict redirects so credentials cannot follow a redirect to an unapproved origin. Consider replacing the broad backend key with skill-specific authorization.

Related hardening: `server.py:1439` only enters Alexa identity resolution when the subject header is present. The legacy-key rejection at `server.py:1491` covers four routes. An offline request with a valid synthetic key and skill/device headers, but no subject, reached `/get_stream/` under the `owner` identity. Require complete identity headers for requests identifying themselves as the new skill; keep any intended owner administration API explicitly separate.

### 2. High: Lambda persistence groups different paired Echos into the same state

Locations: `lambda/lambda_function.py:19`, `lambda/lambda_function.py:821`, `lambda/mediaUtils/player.py:314`.

Backend bindings distinguish `(skill, Alexa user, Echo device)`. Lambda uses the DynamoDB adapter's default Alexa-user partition key, and then selects a nested state dictionary using only that same Alexa user ID. Neither includes the device or resolved Music Box account.

Example: Echo A and Echo B belong to the same Amazon account, but are paired to different Music Box users. Their backend requests resolve separately; their Lambda playlist, saved playlists, current token, cached stream URL, index, and settings occupy the same state. Sequential requests can read each other's selections, and concurrent request saves can overwrite each other. Playback callbacks also read this shared state.

An offline reproduction confirmed both the identical SDK partition key and identical attribute bucket for two distinct devices under one Alexa user.

**Required fix:** define a composite DynamoDB partition key covering skill, Alexa subject, and device, and associate state with the current backend binding/account generation. Changing only the nested dictionary key would still leave concurrent requests replacing the same DynamoDB item. Define how account-level saved playlists should be shared independently of device playback state.

### 3. High: re-pairing an Echo preserves the previous user's state and stream access

Locations: `lambda/lambda_function.py:27`, `lambda/mediaUtils/player.py:1335`, `lambda/mediaUtils/player.py:1424`, `flask-server/alexa_identity.py:62`.

Successful pairing replaces the backend binding but does not clear Lambda's playlist, metadata, saved playlists, current token, or cached stream URL. `resume()` discards errors from its armed-play lookup and can issue a Play directive from the cached URL. `seek()` can also reuse that URL without a fresh identity check. Metadata and saved-playlist announcements are entirely local.

Stream capabilities identify an account, video, and account security version, with a two-hour lifetime. They do not identify the originating Echo binding. Re-pairing the Echo to user B does not invalidate a cached capability for active user A.

Offline reproductions confirmed that successful pairing retained A's state; resume emitted A's cached URL even when the backend lookup returned an authorization error; and the backend still accepted A's capability after the Echo binding changed to B.

**Required fix:** return a binding generation from pairing/identity validation; reset or replace state when it changes; authorize before using persisted personal data or stream URLs; bind Echo capabilities to a revocable binding generation. Distinguish an authorization failure from an ordinary absent resume arm.

The explicit account-unlink endpoint already removes bindings and increments the security version. The gap above concerns reassignment through pairing, which does not take that path.

### 4. High: the running backend targets the older skill invocation

Location: `flask-server/alexa_remote.py:108`, and its transport/play dispatch methods.

The new skill's invocation is `music box multi`. The running backend has no override for `SKILL_INVOCATION_NAME`, so web-triggered custom commands use the default `music box`. Plays and transport commands can invoke the older skill rather than the new skill and its independent pairing namespace, causing failed pairing authorization or control of the wrong skill session.

**Required fix:** configure `SKILL_INVOCATION_NAME=music box multi` persistently for the test backend and recreate/restart the affected service, then verify web play, pause/resume, seek, and queue synchronization against the new skill. No configuration change was made during this review.

### 5. Medium: cached stream capabilities are reused after expiration

Locations: `lambda/mediaUtils/player.py:1379`, `lambda/mediaUtils/player.py:1438`, `flask-server/alexa_identity.py:89`.

Resume and seek reuse a URL when its video ID matches, without checking its age. The backend expires that URL's capability after two hours. A legitimate user resuming an older paused track can therefore receive a Play directive containing an expired URL, even with a valid current binding.

**Required fix:** obtain a fresh authorized stream URL on resume/seek, or persist and enforce a conservative expiry. Authorization failures must stop playback rather than fall back to stale capabilities.

### 6. Medium: loop intents update a different dictionary than playback reads

Locations: `lambda/lambda_function.py:314`, `lambda/lambda_function.py:325`.

Loop handlers write `playback_info['loop']`; queue/controller code reads `playback_setting['loop']`. The skill announces success without changing the setting used by playback. Confirmed with the actual handler body and real SDK response builder offline.

**Required fix:** update `get_playback_setting()` in both loop handlers and verify next-track/continuation behavior.

### 7. Medium: the workspace's default ASK deployment target is stale

Location: `ask-resources.json:5`.

The default profile references skill `amzn1.ask.skill.88e08ac3-8775-47be-9b80-fd506c85f532`, which is neither Music Box Multi nor the other skill returned by the current vendor's skill list. A default deployment from this workspace cannot be assumed to target the new skill.

**Required fix:** establish a dedicated ASK project/profile targeting the new skill, its hosted repository, and the multi-user interaction models. Verify the target before deployment. Do not overwrite the older skill's project configuration inadvertently.

## Working foundations and architecture limits

- Pairing codes are eight digits, expire after five minutes, are single use, and are tied to an account security version. Pair consumption is transactional.
- Bindings are separated by skill namespace, subject, and device. Unknown subjects/devices, malformed namespaces, disabled accounts, and forced-password-change accounts are rejected on the paired path.
- Skill/application validation is configured in the hosted copy. Alexa identity comes from `context.System`, so it is available to sessionless audio callbacks.
- The backend uses per-identity playback state, locks, output leases, durable snapshots, Amazon sessions/device lists, provider credentials, and background-job context propagation.
- Echo audio URLs use signed, video-scoped capabilities rather than exposing the shared backend key in the URL. Security-version checks provide account-level revocation.
- Initialization errors in the configured multi-user layer prevent server startup.
- This is **per-Echo pairing**, not recognition of different people speaking to the same Echo. No `context.System.person` mapping or person-level account linking is implemented. One binding on a shared Echo selects one Music Box account for its voice requests.
- YouTube server-session fallback is an explicit existing policy: accounts without their own YouTube session may use the deployment's Home/library/history and provider mutations. This does not provide isolated personal YouTube libraries for those accounts. Decide whether that shared experience meets the product requirement; personal library writes should require an individual provider session if isolation is required.
- General API throttles remain keyed by source address. Accounts sharing Lambda egress may share those budgets, despite pairing's additional device-specific limiter.
- The model declares `AMAZON.RepeatIntent` and `AMAZON.NavigateHomeIntent`, but dedicated handlers are absent; they reach the reflector. Help/launch speech also retains older DJ branding and lacks pairing guidance.
- CI path filters include `skill-package/**`, but omit `skill-package-multiuser/**`; a change confined to the new package does not trigger that workflow through its path filters.

## Verification

- **50 Lambda tests passed**, using `/tmp/pwvenv/bin/python` with the real ASK SDK installed. An initial run in a different environment lacked SDK dependencies; the complete-environment rerun passed.
- **230 backend tests passed across 13 relevant files**, plus five reported subtests. Files covered Alexa skill isolation, Amazon identity/session separation, auth route contracts, player state, account service, durable playback, purge, queue revisions, direct Alexa commands, account switching, YouTube server sessions, audio credentials, and security boundaries. Each backend file ran in a separate process to avoid cross-suite import stubs.
- **Five offline Lambda reproduction assertions and two backend reproduction tests confirmed the findings above.** Existing passing regressions do not cover these isolation failures.
- Temporary probe scripts and backend regression logs are in `/tmp/musicbox-review-results-20261111/`.
- No physical Echo, simultaneous live household playback, real credential-exfiltration request, or remote skill invocation was used. The review establishes code/configuration defects, not a successful end-to-end multi-user deployment.

Recommended order: close arbitrary API destinations; align persistence with device/binding identity; fix reassignment and capability refresh; correct the backend invocation and ASK target; then verify two Music Box users on two Echos under the same Amazon account, Echo reassignment, unlink/revocation, old paused playback, and concurrent callbacks before wider use.

Amazon reference: [Validate and Use Access Tokens in Custom Skill Code](https://developer.amazon.com/docs/alexaplus/account-linking/add-account-linking-logic-custom-skill) explains `context.System.user` access for sessionless audio requests. [Add Account Linking for Alexa](https://developer.amazon.com/en-US/docs/alexa/custom-skills/link-an-alexa-user-with-a-user-in-your-system.html) distinguishes account linking from more advanced voice-profile personalization. These references describe Alexa behavior; custom pairing findings above come from this repository and offline reproductions.
