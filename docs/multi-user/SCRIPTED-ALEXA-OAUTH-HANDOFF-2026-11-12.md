# Match the working single-user Alexa login handoff

The original single-user implementation was reviewed without modifying it: the GitHub backend reference, the original Music box skill's Alexa-hosted repository obtained through read-only ASK operations, and authenticated GETs to its configured server. The reference returned the online Echo as online and the other Echo as offline. Its existing device query and online-only filtering are retained.

The new scripted form flow missed a step performed by AlexaProxy: capturing `openid.oa2.authorization_code` from Amazon's callback before AlexaPy calls `get_tokens()`. The saved multi-user session had neither an access token nor a refresh token. Basic Amazon web cookies allowed the account check to succeed through a fallback, but did not provide the properly registered Alexa session used by the working connection. Treating the resulting negative device states as unknown was the wrong fix and has been reverted, including the Android changes.

The corrected multi-user implementation:

- Captures the OAuth authorization code/access token from approved HTTPS Amazon callback paths, including intermediate manual POST redirects and automatic GET redirect history.
- Hands the code to the existing AlexaPy registration flow with the same login object's PKCE verifier. It does not replace registration with a typed email or copy credentials from the reference deployment.
- Restricts callback capture to known Amazon hosts, default HTTPS ports and the maplanding/SPA callback paths. Credentials on arbitrary URLs are ignored, and callback values are not logged.
- Rejects promotion of an OAuth-enabled login that did not obtain its durable registration token, keeping the previous working session intact.
- Keeps each login's cookies, callback state and verified account identity isolated. Existing account/token/access controls and verified customer-ID checks remain in force.

The reference server, reference hosted skill and GitHub master branch were not deployed or edited. Only the multi-user branch/backend is updated. Android source has been restored to the version before the discarded availability workaround; its picker behavior remains unchanged.

Verification: 100 focused backend cases passed, including the complete OTP → callback-code capture → registration → canonical identity → staged-cookie promotion flow. All 69 session/redirect/confirmation/promotion/form cases also passed with the deployed AlexaPy 1.34.0 dependency. The full-flow fixture requires the authorization code to be present when registration runs, so a web-only fake success cannot mask the missing handoff again.

The reconnect path now also recreates this account’s storage directory before staging a new login. Disconnecting removes the directory while the remote object may stay cached; previously, immediate reconnection could fail with a missing-directory HTTP 500. The regression verifies that reconnect creates only its own account directory and preserves another account’s files.

A session saved before this correction needs one fresh Amazon sign-in and OTP through the Music Box connection form. The old web cookies cannot supply the missing one-time OAuth callback code. Confirm the online Echo appears and the offline Echo stays hidden after that reauthorization. Real-provider verification remains pending until the user completes this step.
