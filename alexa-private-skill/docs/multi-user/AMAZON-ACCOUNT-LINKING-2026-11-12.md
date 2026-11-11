# Amazon sign-in and developer-account access

The user-panel Amazon connection accepts any Amazon account the signed-in Music Box user successfully authenticates. Its email or mobile number does not have to match the Music Box login. This connection remains in that Music Box user's own cookie/session/device slot. The user must still have active Amazon/Alexa access and meet the existing Music Box account requirements. Failed or cancelled Amazon sign-in does not replace a working session; disabling access during login still prevents promotion.

These are separate identities:

| Account | What it controls |
| --- | --- |
| Music Box account | Website/app sign-in, music state and per-user server authorization |
| Amazon account connected in the user panel | Amazon session and Echo devices available to that user connection |
| Amazon developer account | Skill ownership and access to an unpublished development skill |

If developer account A owns the private skill and the website connects Amazon account B, the connection does not make A's private skill available to B. In normal development testing, use the developer account's registered Echo, or configure eligible beta-test access for B. The website can discover B's devices after successful authentication, but Music Box playback that invokes the private skill on an Echo still requires that Echo's account to have access to the skill. The skill's locale/invocation must also match the configuration.

This website-sign-in fix does not silently change private-skill voice authorization. In the current private skill, MUSICBOX_EMAIL identifies the verified Music Box account associated with the admin-issued token, and the backend also checks the invoking Amazon profile email. That separate matching-email rule remains in place unless the voice authorization design is explicitly changed. The developer console's login email is not a backend authentication proof.

References: [Amazon device/development testing](https://www.developer.amazon.com/en-US/docs/alexa/test/test-your-skill-overview.html), [beta access](https://www.developer.amazon.com/en-US/docs/alexa/custom-skills/skills-beta-testing-for-alexa-skills.html), [profile API identity](https://developer.amazon.com/en-US/docs/alexa/custom-skills/customer-profile-api-reference.html).

Verification: 61 focused backend cases plus three subtests passed, including different Amazon identifiers, per-user permission isolation, restrictions, staged-login preservation and private-skill token authorization. All 69 account-page Chromium checks passed, including submitting a different email and a mobile number. Real Amazon credentials were not used in tests.
