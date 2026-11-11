# Admin user-page redesign

The user detail page now presents account identity and readiness before editable controls. Profile editing, email actions, password management and token generation have separate dialogs. Alexa access is a blue-accented permission panel, security uses an amber accent, connected services have their own icon treatment, and deletion is separated in a muted red panel.

Access changes, credential revocation, provider disconnect, authenticator reset and deletion require confirmation. A newly generated Alexa token opens in a one-time private dialog with copy feedback. Token generation is disabled until the account meets the backend's actual eligibility rules. Token history distinguishes active, revoked, expired and inactive credentials.

Devices show an accurate active-session count; expired sessions are not labelled live. Device details retain IP, creation/expiry times and client information in a dialog. Mobile sections stack consistently and have jump links. The users directory retains search/filter behavior with clearer row and action styling.

The shared confirmation helper now preserves the clicked submit button, so named action values survive confirmation. Forms continue to use the existing POST routes, role checks and CSRF protection. No provider credentials or token secrets are saved in browser storage.

Validation: 21 Chromium cases passed, including dark/light layouts at 320, 375, 768, 1024 and 1440 pixels, modal saves and cancellation, focus return, one-time token reveal/copy, access enable/disable, token revocation, device details, provider-disconnect confirmation, deleted accounts and users-list filters. The admin/account authorization and Alexa-access suites passed 52 tests plus three subtests. JavaScript syntax, Python syntax and patch whitespace checks passed.

Deployment target: `testalexa.synthora.in`. Source branch: `feat/multi-user`. The default branch is not changed.
