FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    CHROME_USER_DATA_DIR=/profile \
    NOVNC_PORT=6080

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium xvfb openbox x11vnc novnc websockify xdotool ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY service.py entrypoint.sh novnc-controls.js ./
RUN chmod 0755 /app/entrypoint.sh \
    && mkdir -p /profile \
    && cp /app/novnc-controls.js /usr/share/novnc/novnc-controls.js \
    && sed -i 's#</body>#<script src="novnc-controls.js"></script></body>#' /usr/share/novnc/vnc.html

EXPOSE 6080 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
