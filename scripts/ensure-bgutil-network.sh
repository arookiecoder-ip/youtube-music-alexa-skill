#!/usr/bin/env bash
# Idempotently attach the bgutil-ytdlp-pot-provider container to the "web"
# Docker network so ytmusic (which shares gluetun's network namespace, and
# gluetun is on "web") can reach it at http://bgutil-provider:4416.
#
# bgutil-provider is a standalone container (not part of this repo's
# docker-compose.yml -- see README) started separately on this host with
# --restart unless-stopped, bound only to 127.0.0.1:4416 on the host. That
# loopback binding is not reachable from ytmusic's network namespace, so it
# also needs a second interface on the "web" network. `docker network
# connect` is a live, in-memory attachment: it is lost if bgutil-provider (or
# the "web" network itself) is ever removed and recreated, so this script
# re-applies it and is safe to run any number of times.
#
# Run manually after recreating bgutil-provider, or install the accompanying
# systemd unit (see ensure-bgutil-network.service in this directory) to run
# it automatically on every boot / docker.service start.
#
# NOTE on the installed systemd unit: it is Type=oneshot with
# RemainAfterExit=yes, so `systemctl start ensure-bgutil-network` is a no-op
# once the unit is already "active (exited)" -- systemd will not re-run
# ExecStart for an already-active oneshot. To force a re-check/re-attach
# after manually recreating bgutil-provider, run this script directly, or
# use `systemctl restart ensure-bgutil-network` (not `start`).
set -euo pipefail

NETWORK="web"
CONTAINER="bgutil-provider"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "ensure-bgutil-network: container '$CONTAINER' does not exist, nothing to do" >&2
    exit 0
fi

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    echo "ensure-bgutil-network: network '$NETWORK' does not exist yet, nothing to do" >&2
    exit 0
fi

already_attached=$(docker inspect "$CONTAINER" \
    --format "{{if index .NetworkSettings.Networks \"$NETWORK\"}}yes{{else}}no{{end}}")

if [ "$already_attached" = "yes" ]; then
    echo "ensure-bgutil-network: '$CONTAINER' is already on '$NETWORK', nothing to do"
    exit 0
fi

docker network connect "$NETWORK" "$CONTAINER"
echo "ensure-bgutil-network: attached '$CONTAINER' to '$NETWORK'"
