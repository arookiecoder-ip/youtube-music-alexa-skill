# Personal Music Box Alexa skill

Deploy this code in your own private Alexa skill. The backend stays multi-user;
this deployment uses only your Music Box account.

Copy `.env.example` to a private `.env` beside `data.py` in your deployed Lambda code and fill in these values. Lambda environment variables can override them:

```dotenv
MUSICBOX_SERVER_URL=https://your-server.example
MUSICBOX_ACCOUNT_ID=your-musicbox-account-id
MUSICBOX_ALEXA_TOKEN=your-admin-issued-token
```

An administrator must enable Amazon / Alexa access for your account and generate
the token in Admin → Users → your account. Rotating/revoking it or disabling access
rejects old skill requests and old audio URLs. Do not share the configured token.

Use `skill-package-multiuser` for the manifest/models, enable email permission and
grant it in the Alexa app. Connect Amazon in your Music Box user panel first. Use the same Amazon account
for that connection, the private skill in Developer Console and the Echo.
Your Music Box email can differ. Configure the Alexa Skills Kit trigger/resource policy
in AWS/Amazon's deployment tools. No skill-ID setting or server administrator
API key belongs in this configuration.

Prefer a supported Python runtime with the locked dependencies. Existing
Alexa-hosted Python 3.8 deployments have the dependency limitations documented in
the security audit; this source change alone does not upgrade their runtime.

See [setup, behavior and cross-verification checklist](../docs/multi-user/PRIVATE-SKILL-ACCESS-2026-11-11.md).

## Set up through the Alexa Developer Console

`skill.json` is the skill manifest for ASK CLI/SMAPI deployment. Do not paste it into the interaction-model JSON Editor or upload it as Lambda code. When configuring the skill manually in the console, set its options through the pages below.

1. Open your skill and choose **Build → Interfaces**. Enable **Audio Player**, then click **Save Interfaces**.
2. Choose the locale that matches your Echo language. Open **Build → Interaction Model → JSON Editor**, then paste the contents of `skill-package-multiuser/interactionModels/custom/en-IN.json` for English (India). Use `en-US.json`, `en-GB.json`, `en-CA.json` or `en-AU.json` for the corresponding locale. Click **Save Model → Build Model**. This editor takes the locale file, **not `skill.json`**.
3. Open **Build → Tools → Permissions** and enable **Customer Email Address**. Also grant this permission to the skill in the Alexa mobile app.
4. For an Alexa-hosted skill, put the files and subfolders from `lambda/` in the **Code** tab. Keep `lambda_function.py`, `data.py`, your private `.env`, `skill_security.py`, `requirements.txt`, `mediaUtils/` and `models/` together. Save and deploy the code. The `skill-package-multiuser/` directory belongs to the skill configuration, not the Code tab. For a self-hosted Lambda, upload the Lambda code to AWS and configure its Alexa Skills Kit trigger and the console's **Endpoint** page instead.
5. Copy `.env.example` to `.env` in the same deployed folder as `data.py`. Fill in your private server URL, Music Box account ID from Account settings and admin-issued token, then save and deploy. The skill loads this `.env` automatically; Lambda environment variables override file values, including explicitly empty values. The example is never loaded automatically. Delete the old `api_key.py` from your deployed code.
6. In **Test**, enable testing in **Development**. Test using the configured invocation name and an Echo registered to the eligible Amazon developer account. Use the same Amazon account in the Developer Console, on the Echo and in your Music Box user panel. Your Music Box email can differ.

Amazon documentation: [interaction-model JSON Editor](https://developer.amazon.com/en-US/docs/alexa/custom-skills/create-the-interaction-model-for-your-skill.html), [AudioPlayer interface](https://developer.amazon.com/en-US/docs/alexa/custom-skills/use-long-form-audio.html), [customer email permission](https://developer.amazon.com/en-US/docs/alexa/custom-skills/request-customer-contact-information-for-use-in-your-skill.html), [skill manifest and console settings](https://developer.amazon.com/en-US/docs/alexa/ask-toolkit/vs-code-ask-skills.html).


The private `.env` supports one `KEY=value` per line, optional single/double quotes, comments and an optional `export` prefix. It does not expand variables or execute commands. The three Music Box settings above and the legacy MUSICBOX_EMAIL compatibility setting are read, only from the file beside `data.py`; no parent-directory search occurs. GitHub and the downloadable ZIP contain only `.env.example` with placeholders. Keep your filled `.env` in your private deployment. `api_key.py` is no longer used.
