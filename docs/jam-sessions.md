# Jam sessions

Jam owns one queue, transport state and playback clock. Account playback remains
an output adapter; provider credentials, likes, playlists and listening history
remain scoped to the acting account. There are no participant permission modes,
queue attribution or voting/abuse-management screens.

## Joining and host management

The device picker contains Start Jam / Invite / Leave Jam and host management.
Account settings no longer contains Jam. HTTPS `/j/<invite>` and `/jam/<invite>`
open a consent dialog in an updated APK through verified Android App Links.
`/.well-known/assetlinks.json` associates the protected Actions signing certificate
with `in.synthora.musicbox`. Android's supported-link settings still apply.

A signed-out APK opens `/jam-invite/<invite>` in the browser. That route is outside
the Android link filter, avoiding an app/browser loop. The browser can join as a
guest or sign in and return to the invitation. Simply viewing an invitation does
not join or start audio. An older APK without the verified link filter uses the
browser. Build configurations only accept invitations for their configured server.

The host chooses single-output or multiple-output mode and selects registered
outputs. A native output is **that joined APK's currently selected audio target**:
this phone, one of its account's other phones, or its linked Echo. Its reported
name follows changes in the existing device picker. A browser registers its own
audio element. Joining grants no access to unrelated, unselected account devices.
Volume remains local to the target; transport and queue operations are shared.
New participants are controllers until the host selects their output.

The host can remove a participant, revoking all that account's memberships in the
room. The host cannot remove people from another room or use their provider
credentials. Removed/departed outputs are deselected. A guest has a stable signed
browser identity, so clicking Leave and reopening the invite cannot bypass removal.
A fresh anonymous browser/cookie jar is a new identity; preventing that requires
sign-in. No device fingerprinting is used.

## Protocol and recovery

- `GET /api/jam/state/`: membership role, host-only member management handles,
  online/error states of outputs, selected-output flag and canonical playback.
- `POST /j/<invite>`: explicit join, with the device ID being connected.
- `POST /api/jam/presence/`: updates that membership's own output only.
- `POST /api/jam/control/`: queue/transport commands. Configure is host-only.
- `POST /alexa/jam/remove/`: removes an account/guest from the host's room.
- `POST /api/jam/leave/`: disconnects this membership without changing personal login.
- `POST /alexa/jam/stop/`: ends the host's room.
- Browser audio uses membership-authorized `/api/jam/audio/<video>`; the video
  must be in the queue. Open streams recheck revocation, not just initial access.

Mutations require JSON. SQLite transactions order commands across workers; command
IDs deduplicate retries. Destructive queue edits and output configuration carry the
revision the user saw. Concurrent stale edits return 409 rather than overwriting
another participant's change. Duplicate video IDs have distinct queue entry IDs.
A selected device's end-of-track event never advances the room. Server elapsed
time advances it once, including when no app is connected. Missing track duration
can be supplied from real playback metadata. An unavailable output reports an
error without forcing the entire group to skip a song.

Room and membership state persist in SQLite. Stable polls do not rewrite the room
clock. Clients send queue epoch/version and retain their queue when it is unchanged;
queues from another session cannot be reused. Polling/presence has a separate
participant budget so it does not exhaust personal search/library requests or a
shared NAT's general API budget.

Native following belongs to the application playback model and continues when the
activity closes. The app remembers only whether a Jam needs recovery; existing
protected login cookies authorize recovery after process restart. Membership is
revalidated. After 12 seconds without a successful room response, a client pauses
its selected output and catches up after reconnection. Removal/end/expiry stops
following; personal account access remains available. Hosts can close their app
without ending the room; explicit logout/end and the configured invite TTL end it.

This is coordinated playback with drift correction, not sample-accurate speaker
synchronization. Phones correct drift above 1.5 seconds; remotely controlled phones
above 2 seconds; Alexa above 3 seconds. Network/buffering/device latency affects
startup. Real phones and Echo hardware still require a listening check before a
production rollout; CI cannot establish acoustic synchronization.

## Builds and validation

Both repositories use `feat/jam-sessions`. Backend workflow `jam-build.yml` runs
SQLite concurrency/membership tests, production route authorization tests,
Chromium consent/host-management/layout tests, and existing isolation regressions.
It produces an ARM64 Docker image archive and SHA-256 file. Native workflow
`android-jam.yml` runs JVM regressions, native host/member management tests on an
Android emulator, and builds the protected-key signed APK.

All compilation/image/APK builds run on GitHub Actions. These feature workflows
upload artifacts and do not deploy or restart a server. Deploy the matching
backend artifact before distributing the APK. Serve the app-association document
on each configured HTTPS hostname and verify installed-link routing against the
actual signed APK. Existing pre-upgrade rooms need to be restarted to create an
independent playback state.
