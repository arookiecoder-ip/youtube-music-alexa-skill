# Personal Alexa skill package

Deploy this skill in your own Amazon developer account and connect it to your Music Box account. Publishing the skill is not required.

**[Download the complete ZIP](musicbox-private-skill.zip)** · [Lambda source and setup](lambda/README.md) · [Manifest and interaction models](skill-package-multiuser) · [Full setup and verification checklist](docs/multi-user/PRIVATE-SKILL-ACCESS-2026-11-11.md)

## Set up through the Alexa Developer Console

`skill.json` is the skill manifest for ASK CLI/SMAPI deployment. Do not paste it into the interaction-model JSON Editor or upload it as Lambda code. When configuring the skill manually in the console, set its options through the pages below.

1. Open your skill and choose **Build → Interfaces**. Enable **Audio Player**, then click **Save Interfaces**.
2. Choose the locale that matches your Echo language. Open **Build → Interaction Model → JSON Editor**, then paste the contents of `skill-package-multiuser/interactionModels/custom/en-IN.json` for English (India). Use `en-US.json`, `en-GB.json`, `en-CA.json` or `en-AU.json` for the corresponding locale. Click **Save Model → Build Model**. This editor takes the locale file, **not `skill.json`**.
3. Open **Build → Tools → Permissions** and enable **Customer Email Address**. Also grant this permission to the skill in the Alexa mobile app.
4. For an Alexa-hosted skill, put the files and subfolders from `lambda/` in the **Code** tab. Keep `lambda_function.py`, `data.py`, your private `.env`, `skill_security.py`, `requirements.txt`, `mediaUtils/` and `models/` together. Save and deploy the code. The `skill-package-multiuser/` directory belongs to the skill configuration, not the Code tab. For a self-hosted Lambda, upload the Lambda code to AWS and configure its Alexa Skills Kit trigger and the console's **Endpoint** page instead.
5. Copy `.env.example` to `.env` in the same deployed folder as `data.py`. Fill in your private server URL, verified Music Box email and admin-issued token, then save and deploy. The skill loads this `.env` automatically; Lambda environment variables override file values, including explicitly empty values. The example is never loaded automatically. Delete the old `api_key.py` from your deployed code.
6. In **Test**, enable testing in **Development**. Test using the configured invocation name and an Echo registered to the eligible Amazon developer account. Use the same Amazon account in the Developer Console, on the Echo and in the Music Box user panel. The Music Box login email can differ.

Amazon documentation: [interaction-model JSON Editor](https://developer.amazon.com/en-US/docs/alexa/custom-skills/create-the-interaction-model-for-your-skill.html), [AudioPlayer interface](https://developer.amazon.com/en-US/docs/alexa/custom-skills/use-long-form-audio.html), [customer email permission](https://developer.amazon.com/en-US/docs/alexa/custom-skills/request-customer-contact-information-for-use-in-your-skill.html), [skill manifest and console settings](https://developer.amazon.com/en-US/docs/alexa/ask-toolkit/vs-code-ask-skills.html).

Use only these three private settings:

```dotenv
MUSICBOX_SERVER_URL=https://your-server.example
MUSICBOX_EMAIL=your-verified-musicbox-email@example.com
MUSICBOX_ALEXA_TOKEN=your-admin-issued-token
```

An administrator enables Amazon / Alexa access and generates the token under **Admin → Users → your account**. Enable and grant Alexa email permission. Connect Amazon in your Music Box user panel first. Use that same Amazon account for the private Developer Console skill and Echo; your Music Box email can differ.

No skill-ID setting or server-wide administrator API key is required in this configuration. The included credentials are placeholders; put real values only in your private deployment. Configure the Alexa Skills Kit trigger/resource policy through Amazon's deployment tools.

This directory is the self-contained shareable package. The repository's other top-level skill code may belong to a different deployment; use the manifest and Lambda code together from this directory.

The user-panel Amazon connection can use an email/mobile different from your Music Box login. This is separate from private-skill voice authorization and access to development skills. See [Amazon account versus developer account](docs/multi-user/AMAZON-ACCOUNT-LINKING-2026-11-12.md).

The private `.env` supports one `KEY=value` per line, optional single/double quotes, comments and an optional `export` prefix. It does not expand variables or execute commands. Only the three Music Box settings above are read, only from the file beside `data.py`; no parent-directory search occurs. GitHub and the downloadable ZIP contain only `.env.example` with placeholders. Keep your filled `.env` in your private deployment. `api_key.py` is no longer used.
