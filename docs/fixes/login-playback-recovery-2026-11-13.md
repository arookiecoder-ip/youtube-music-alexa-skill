# Login and playback recovery

The production image was being restarted by an installed copy of the deployment
helper that still read `.env.testalexa`. The checked-in helper had already been
updated to `.env.production`, but `/usr/local/bin/musicbox-ci-deploy` had not.
The running service therefore dispatched commands to `music box multi` although
the promoted production interaction models use `music box`. Voice invocation of
`music box` did not use the broken remote-command path.

Install the reviewed `scripts/deploy-prebuilt-backend.py` at the forced-command
path before deploying the image. Keep its existing root ownership and executable
permissions. The helper now explicitly pins `COMPOSE_ENV_FILE` for the replace
operation, including rollback, so an inherited variable cannot select test
settings. The production and test environments share the existing credentials
and volumes; the differing settings are the public origins and invocation name.

Provider sign-in replaces credential/answer inputs with a centered loading state
and animates the next step, with reduced-motion support. Amazon offers interactive
same-origin sign-in only after an unsuccessful attempt (or the user's explicit
selection from that recovery link). The fallback requires re-entering the
password and keeps polling until the verified session has been persisted.
Cancel requests carry their attempt identifiers. YouTube's standalone page also
handles verification-method selection and picture challenges using its own
endpoints, which matters inside the Android account browser.

Robot-check pictures can use opaque CDN filenames or lazy image attributes.
Image fetching follows at most three redirects and validates HTTPS, host, port
and absence of user credentials at each hop before making a request. Missing
pictures offer retry and interactive recovery instead of repeating a dead end.

Browser handoff activates the media element in the user gesture, waits for
browser presence registration, and uses the browser's actual controller ID.
The existing pause-acknowledgment and ownership-token checks remain in force.

Regression checks cover both provider pages at 390px and 1280px, loading while a
request is held, Amazon recovery/interactive cancel, Google method and picture
answers, trusted CDN redirects and blocked untrusted redirects, plus device
registration and controller identity before transfer. Full server/JS regressions,
real Chromium playback checks and skill SDK tests run separately.
