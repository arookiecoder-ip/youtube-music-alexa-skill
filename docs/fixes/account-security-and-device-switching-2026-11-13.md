# Account security, device selection and Music Box 2.5.6

Password, recovery email, username and authenticator changes preserve existing
sessions and Alexa credentials. Migration 13 removes credential changes from the
session/token revocation trigger. Role changes, account disabling, explicit
session revocation and explicit Alexa access/token revocation still take effect.
Normal credential expiry remains in place. A password update cannot restore a
session invalidated by an earlier revocation or authorization change.

Private skill configuration uses MUSICBOX_ACCOUNT_ID, copied from the account
page in the app or website. The server verifies this ID against the bearer
credential's owner. Legacy email headers remain compatible with the email bound
to that token, so changing recovery email does not strand an older deployment.
Amazon's verified identity proof is still checked separately on every request.

Browser devices appear only while focused or playing. Idle background tabs retain
internal presence for ownership cleanup and pending stop commands, but cannot be
selected as playback targets. Focus, blur, visibility and audio events update
availability; ordered revisions prevent a stale heartbeat from undoing a change.

Echo handoff attempts a skill pause with a one-second Amazon timeout and a bounded
400 ms stop-callback wait. Known invalid authentication skips the command. The
selected phone/browser takes priority on timeout or authentication failure;
concurrent song/output changes still reject a stale transfer. An unreachable Echo
cannot be guaranteed silent, so handoff deliberately does not wait indefinitely.

The Android account page fills the screen, shows an account ID copy control and
uses toast feedback. Connection dialogs group full-width actions. Login recovery
links no longer move over Sign in when the keyboard opens. OTP delivery state is
scoped to each security action; a newly opened flow shows its field and expiry
only after its own Send code succeeds. Sending fades the label behind a spinner.

Validation includes credential/session/token lifecycle and migration regressions,
explicit revocation and tenant-isolation tests, source/target handoff fencing,
browser focus/playing visibility tests, responsive Chromium account/login checks,
and GitHub Actions release, JVM and emulator checks for Android.
