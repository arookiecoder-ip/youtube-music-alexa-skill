#!/usr/bin/env bash
# Idempotently attach the bgutil-ytdlp-pot-provider container to the "web"
# Docker network so ytmusic can reach it at http://bgutil-provider:4416, and to the
# private egress network so its preferred-VPN/fallback proxy resolves.
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

CONTAINER="bgutil-provider"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "ensure-bgutil-network: container '$CONTAINER' does not exist, nothing to do" >&2
    exit 0
fi

# The provider must both serve ytmusic on web and resolve its outbound proxy
# on the private egress network. Discover the latter by its explicit alias,
# rather than assuming a Compose project/network name or fixed container IP.
networks=(web)
while IFS= read -r gateway; do
    [ -n "$gateway" ] || continue
    while IFS= read -r network; do
        [ -n "$network" ] && networks+=("$network")
    done < <(docker inspect "$gateway" --format '{{range $name, $config := .NetworkSettings.Networks}}{{range $config.Aliases}}{{if eq . "musicbox-egress-internal"}}{{$name}}{{"\n"}}{{end}}{{end}}{{end}}')
done < <(docker ps -aq --filter label=com.docker.compose.service=musicbox-egress)

for network in "${networks[@]}"; do
    if ! docker network inspect "$network" >/dev/null 2>&1; then
        echo "ensure-bgutil-network: network '$network' does not exist yet, skipping" >&2
        continue
    fi
    attached=$(docker inspect "$CONTAINER" --format "{{if index .NetworkSettings.Networks \"$network\"}}yes{{else}}no{{end}}")
    if [ "$attached" != "yes" ]; then
        docker network connect "$network" "$CONTAINER"
        echo "ensure-bgutil-network: attached '$CONTAINER' to '$network'"
    fi
done
