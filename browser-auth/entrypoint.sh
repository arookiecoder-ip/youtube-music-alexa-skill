#!/bin/sh
set -eu

mkdir -p "${CHROME_USER_DATA_DIR:-/profile}"
Xvfb "${DISPLAY:-:99}" -screen 0 "${SCREEN_GEOMETRY:-1365x768x24}" -nolisten tcp &
# Xvfb starts asynchronously. Do not let x11vnc exit permanently while the
# display socket is still being initialized.
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
# Chromium renders with the GPU process / ozone on the X display, and x11vnc's
# XDamage tracking silently stops picking up its updates (log: "XDAMAGE is not
# working well... misses"). The noVNC client then freezes on the last known
# frame and the signed-in browser appears to never have opened. Disable Damage
# and fall back to timed full-frame polling so the viewer always sees the live
# Chromium window. -wait=20ms poll, -deferupdate=10ms cuts encoder overhead.
x11vnc -display "${DISPLAY:-:99}" -forever -shared -nopw -localhost -rfbport 5900 \
  -noxdamage -wait 20 -deferupdate 10 -o /tmp/x11vnc.log >/dev/null 2>&1 &
websockify --web=/usr/share/novnc/ "${NOVNC_PORT:-6080}" localhost:5900 >/tmp/novnc.log 2>&1 &

exec waitress-serve --host=0.0.0.0 --port=8765 --threads=4 service:app
