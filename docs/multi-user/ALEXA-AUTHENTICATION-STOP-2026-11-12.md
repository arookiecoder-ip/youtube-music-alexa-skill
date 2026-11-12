# Terminal Alexa authentication failures

Authentication failures stop playback processing and clear pending Alexa dispatches, armed requests and retries for the affected user. They do not select another song, expand radio, or reuse a saved stream. The selected song and queue remain available for an explicit retry after correcting access.

The player receives a terminal error with an actionable message for expired Amazon sessions, account mismatches, missing email permission, revoked credentials or disabled access. A request that never reaches authenticated playback confirmation stops with a settings/connection check message rather than claiming a particular cause. Music availability and offline-device handling remain separate.

Deploy the backend and updated `lambda_function.py` and `mediaUtils/player.py` together. Keep the private `.env` unchanged. Only a fresh verified playback intent or an explicit website play can release the stopped state; background audio callbacks cannot. A stale callback cannot stop a newer song or phone playback, and invalid credentials cannot update another user's state.

## Cross-verification

- With valid access, play, pause/resume, seek and change songs on an online Echo.
- In a test account, expire/disconnect Amazon, deny email permission or use a different linked Amazon account. Verify the actionable error appears and the spinner/progress stops.
- Rotate/revoke the test token or disable the user's Alexa access. Verify rejection and no repeated requests, alternative song, queue advancement or cached-stream fallback.
- Correct access and explicitly retry. Verify playback resumes with the selected song and queue intact.
- Trigger a late callback while a newer song or phone output is active. Verify it does not stop the newer playback.
- Keep another user's playback running during the failure. Verify its state and queue remain unchanged.
- Test an unavailable song and an offline Echo separately; verify they are not labelled as authentication failures.

Automated coverage includes per-user authorization, stale request fencing, terminal dispatch/watchdog behavior, queue preservation, frontend error rendering, and actual ASK SDK responses. Authentication failures return Stop/ClearQueue where the Alexa request context permits them, with no Play directives or radio fallback. Live account revocation is left to the test-account checks above.
