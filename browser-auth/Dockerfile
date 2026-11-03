# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    CHROME_USER_DATA_DIR=/profile \
    NOVNC_PORT=6080

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-sandbox xvfb openbox x11vnc novnc websockify xdotool ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca PIP_CERT="$(test ! -f /run/secrets/proxy_ca || echo /run/secrets/proxy_ca)" pip install --no-cache-dir --require-hashes -r requirements.lock
COPY service.py google_signin.py touch_browser.py entrypoint.sh novnc-controls.js ./
RUN chmod 0755 /app/entrypoint.sh \
    && mkdir -p /profile \
    && cp /app/novnc-controls.js /usr/share/novnc/novnc-controls.js \
    && sed -i 's#</body>#<script src="novnc-controls.js"></script></body>#' /usr/share/novnc/vnc.html

RUN groupadd -g 10001 musicbox && useradd -u 10001 -g musicbox -m musicbox && chown -R musicbox:musicbox /profile /app
ENV HOME=/home/musicbox
USER 10001:10001
EXPOSE 6080 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import socket, urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2); s=socket.create_connection(('127.0.0.1',5900),timeout=2); assert s.recv(12).startswith(b'RFB '); s.close(); urllib.request.urlopen('http://127.0.0.1:6080/vnc.html', timeout=2)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
