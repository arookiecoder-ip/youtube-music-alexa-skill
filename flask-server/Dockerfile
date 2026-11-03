# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS youtube-client
WORKDIR /youtube-stream
COPY youtube-stream/package*.json ./
RUN npm ci --omit=dev --ignore-scripts
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 10001 musicbox && useradd -u 10001 -g musicbox -m musicbox && mkdir -p /data /app/alexa_cookies && chown -R musicbox:musicbox /data /app
ENV YOUTUBE_STREAMING=1
WORKDIR /app
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca PIP_CERT="$(test ! -f /run/secrets/proxy_ca || echo /run/secrets/proxy_ca)" pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . .
COPY --from=youtube-client /usr/local/bin/node /usr/local/bin/node
COPY --from=youtube-client /youtube-stream/node_modules /app/youtube-stream/node_modules
USER 10001:10001
EXPOSE 5000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health/ready', timeout=2)"
CMD ["python3", "serve.py"]
