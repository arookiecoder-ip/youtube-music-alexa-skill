#!/usr/bin/env bash
# Fill missing secrets in the private .env (never prints values).
# Usage: ./scripts/setup-secrets.sh [--env PATH]
# Only appends API_KEY / SECRET_KEY when absent; existing entries are kept.
# Load order stays: env -> api_key.txt / secret_key.txt -> generated persistent.
set -euo pipefail

ENV_FILE="${1:-${ENV_FILE:-}}"
if [ "${1:-}" = "--env" ]; then
    ENV_FILE="${2:?missing path}"
    shift 2
fi
if [ -z "${ENV_FILE}" ]; then
    ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
fi

if [ ! -f "$ENV_FILE" ]; then
    cp "$(dirname "$ENV_FILE")/.env.example" "$ENV_FILE"
    echo "created $ENV_FILE from .env.example" >&2
fi
chmod 600 "$ENV_FILE"

gen_hex() { # bytes -> hex on stdout, no logging
    openssl rand -hex "$1"
}

ensure_key() { # NAME BYTES
    local name="$1" bytes="$2" value
    if grep -qE "^${name}=" "$ENV_FILE"; then
        echo "kept existing $name" >&2
        return 0
    fi
    value="$(gen_hex "$bytes")"
    printf '%s=%s\n' "$name" "$value" >> "$ENV_FILE"
    echo "generated $name (value hidden)" >&2
}

command -v openssl >/dev/null 2>&1 || {
    echo "error: openssl is required" >&2
    exit 1
}

ensure_key API_KEY 16
ensure_key SECRET_KEY 32

echo "done. values were never printed; restart with: docker compose up -d ytmusic" >&2
