#!/bin/sh
set -eu

if [ "$(id -u)" = 0 ]; then
  mkdir -p "${CHROME_USER_DATA_DIR:-/profile}"
  if [ "$(stat -c %u "${CHROME_USER_DATA_DIR:-/profile}")" != 10001 ]; then
    chown -R musicbox:musicbox "${CHROME_USER_DATA_DIR:-/profile}"
  fi
  exec gosu musicbox "$0" "$@"
fi
mkdir -p "${CHROME_USER_DATA_DIR:-/profile}"
Xvfb "${DISPLAY:-:99}" -screen 0 "${SCREEN_GEOMETRY:-1920x1080x24}" -nolisten tcp &
# Wait for Xvfb before starting the window manager and authenticated touch API.
python - <<'PY'
import ctypes
import os
import time

x11 = ctypes.CDLL("libX11.so.6")
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
for _ in range(100):
    display = x11.XOpenDisplay(os.environ.get("DISPLAY", ":99").encode())
    if display:
        x11.XCloseDisplay(display)
        break
    time.sleep(0.1)
else:
    raise SystemExit("Virtual display did not become ready")
PY
openbox >/tmp/openbox.log 2>&1 &
# Sign-in uses the authenticated, owner-leased /surface API. Do not run a
# passwordless VNC bridge or continuously scan the full desktop in the background.
exec waitress-serve --host=0.0.0.0 --port=8765 --threads=4 service:app
