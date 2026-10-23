FROM node:22-bookworm-slim AS youtube-client
WORKDIR /youtube-stream
COPY youtube-stream/package*.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ffmpeg \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && apt-get purge -y curl unzip \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/bin:${PATH}"
ENV YOUTUBE_STREAMING=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=youtube-client /usr/local/bin/node /usr/local/bin/node
COPY --from=youtube-client /youtube-stream/node_modules /app/youtube-stream/node_modules

EXPOSE 5000
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=16", "server:app"]
