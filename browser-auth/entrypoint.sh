#!/bin/sh
set -eu

mkdir -p "${CHROME_USER_DATA_DIR:-/profile}"
Xvfb "${DISPLAY:-:99}" -screen 0 "${SCREEN_GEOMETRY:-1365x768x24}" -nolisten tcp &
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
