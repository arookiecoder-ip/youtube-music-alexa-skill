# Proposed Actions workflow updates

These files are reviewable drafts, not active workflows. GitHub rejected writes to `.github/workflows/` with the available credentials. After review, a maintainer with workflow-write permission can install:

- `alexa-regressions.yml` → backend repository `.github/workflows/alexa-regressions.yml` (adds the multi-user interaction-model path to existing triggers).
- `android-multiuser.yml` → Android repository `.github/workflows/android-multiuser.yml` (dedicated account-access checks using the existing protected signing secret).

The existing Android Feature Build workflow already compiled and verified the app in GitHub Actions: https://github.com/arookiecoder-ip/MusicBox-APP/actions/runs/43064822378. No Android compilation ran on the server.
