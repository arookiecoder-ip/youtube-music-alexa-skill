# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
RUN groupadd -g 10001 musicbox && useradd -u 10001 -g musicbox musicbox && mkdir /state && chown musicbox:musicbox /state
WORKDIR /app
COPY alexa-endpoint/requirements.lock ./requirements.lock
RUN --mount=type=secret,id=proxy_ca PIP_CERT="$(test ! -f /run/secrets/proxy_ca || echo /run/secrets/proxy_ca)" pip install --no-cache-dir --require-hashes -r requirements.lock
COPY lambda/*.py ./
COPY lambda/mediaUtils ./mediaUtils
COPY lambda/models ./models
COPY alexa-endpoint/*.py ./
ENV MUSICBOX_PERSISTENCE_MODE=relay MUSICBOX_SKILL_DB=/state/requests.db PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
CMD ["python", "service.py"]
