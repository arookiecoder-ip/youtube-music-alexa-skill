FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ffmpeg \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && apt-get purge -y curl unzip \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=8", "server:app"]
