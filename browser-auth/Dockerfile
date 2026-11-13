# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    CHROME_USER_DATA_DIR=/profile \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-sandbox xvfb openbox xdotool gosu ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY service.py google_signin.py touch_browser.py entrypoint.sh novnc-controls.js ./
RUN groupadd --gid 10001 musicbox && useradd --uid 10001 --gid musicbox --create-home musicbox \
    && chmod 0755 /app/entrypoint.sh && mkdir -p /profile && chown musicbox:musicbox /profile

EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
