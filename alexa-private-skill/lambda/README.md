# Personal Music Box Alexa skill

Deploy this code in your own private Alexa skill. The backend stays multi-user;
this deployment uses only your Music Box account.

Configure these values in Lambda's environment panel (or your private deployed
`api_key.py` for Alexa-hosted code):

```dotenv
MUSICBOX_SERVER_URL=https://testalexa.synthora.in
MUSICBOX_EMAIL=your-verified-musicbox-email@example.com
MUSICBOX_ALEXA_TOKEN=your-admin-issued-token
```

An administrator must enable Amazon / Alexa access for your account and generate
the token in Admin → Users → your account. Rotating/revoking it or disabling access
rejects old skill requests and old audio URLs. Do not share the configured token.

Use `skill-package-multiuser` for the manifest/models, enable email permission and
grant it in the Alexa app. Your authenticated Amazon email must match your
verified Music Box email. Configure the Alexa Skills Kit trigger/resource policy
in AWS/Amazon's deployment tools. No skill-ID setting or server administrator
API key belongs in this configuration.

Prefer a supported Python runtime with the locked dependencies. Existing
Alexa-hosted Python 3.8 deployments have the dependency limitations documented in
the security audit; this source change alone does not upgrade their runtime.

See [setup, behavior and cross-verification checklist](../docs/multi-user/PRIVATE-SKILL-ACCESS-2026-11-11.md).
