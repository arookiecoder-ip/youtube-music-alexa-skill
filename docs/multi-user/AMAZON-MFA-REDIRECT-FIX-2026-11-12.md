# Amazon sign-in stopping before MFA

The reported attempts stopped on `/ap/mfa` with `login_failed`, no detected form and no explicit Amazon error text. The existing generic response incorrectly suggested a bad email/password. Those logs alone do not establish that the credentials were wrong, and did not record the response HTTP status.

The installed AlexaPy 1.34.0 manually follows sign-in POST redirects. For 301/302 it replays the credential POST rather than loading the destination with GET, and can stop on a further empty redirect. Its 303 branch can also put the submitted fields into GET query parameters. A regression using the real AlexaPy parser and a mocked Amazon sign-in → MFA exchange verifies that the corrected flow reaches `securitycode_required` without reposting the password or adding it to a GET query.

The per-login session adapter now follows only explicit manual sign-in POSTs under `/ap/` and `/ax/`:

- 301/302/303 switch to a GET without submitted data, query parameters or authorization/content headers.
- 307/308 retain a POST body only within the same origin.
- Destinations must be HTTPS on the configured Amazon marketplace or amazon.com, use the default HTTPS port, and contain no URL credentials. Redirect chains are bounded and intermediate responses released.
- Other requests retain their existing behavior. The adapter retains the original session/cookie jar and configured private egress.

An empty/unrecognized MFA page now reports that the verification page could not be loaded. Diagnostics record HTTP status and character count alongside the existing structural page fingerprint; they do not log response bodies, form values, full query strings, cookies or passwords. Account eligibility, verified linked Amazon identity, per-user session directories, staged-cookie promotion and token authorization are unchanged.

Validation: 94 focused backend cases plus 11 subtests passed, covering redirects, the real AlexaPy parser, session restoration/private egress, MFA states, cancelled/failed replacement logins, canonical identity and access-token/account isolation. All six Amazon browser sign-in cases passed. All ten redirect/session cases also passed against the deployed AlexaPy 1.34.0 runtime, using synthetic data and a mocked transport. No Android source or compilation was involved.

After deployment, choose **Start over** and submit a fresh Amazon sign-in. Confirm that the actual account reaches its MFA/approval step and completes. Real customer sign-in remains a manual check; the automated tests do not establish Amazon's acceptance of a particular account.

Deployment verification: the test backend is healthy, readiness returns HTTP 200, invalid Alexa credentials return HTTP 403, and both deployed source hashes match the reviewed files. Existing account IDs match the private pre-deployment SQLite backup.
