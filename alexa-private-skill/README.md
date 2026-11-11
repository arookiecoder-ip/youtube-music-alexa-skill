# Personal Alexa skill package

Deploy this skill in your own Amazon developer account and connect it to your Music Box account. Publishing the skill is not required.

**[Download the complete ZIP](musicbox-private-skill.zip)** · [Lambda source and setup](lambda/README.md) · [Manifest and interaction models](skill-package-multiuser) · [Full setup and verification checklist](docs/multi-user/PRIVATE-SKILL-ACCESS-2026-11-11.md)

Use only these three private settings:

```dotenv
MUSICBOX_SERVER_URL=https://your-server.example
MUSICBOX_EMAIL=your-verified-musicbox-email@example.com
MUSICBOX_ALEXA_TOKEN=your-admin-issued-token
```

An administrator enables Amazon / Alexa access and generates the token under **Admin → Users → your account**. Enable and grant Alexa email permission. Your authenticated Amazon email must match your verified Music Box email.

No skill-ID setting or server-wide administrator API key is required in this configuration. The included credentials are placeholders; put real values only in your private deployment. Configure the Alexa Skills Kit trigger/resource policy through Amazon's deployment tools.

This directory is the self-contained shareable package. The repository's other top-level skill code may belong to a different deployment; use the manifest and Lambda code together from this directory.
