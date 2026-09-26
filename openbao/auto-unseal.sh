#!/usr/bin/env bash
# Unseals openbao after a container restart using the key saved by bootstrap.sh.
# Run this any time `docker compose restart openbao` (or a host reboot) leaves
# it sealed. Requires openbao/keys.json to already exist.
set -euo pipefail

cd "$(dirname "$0")"
CONTAINER="laptop-openbao"
KEYS_FILE="keys.json"

bao() {
  docker exec -e BAO_ADDR="http://127.0.0.1:8200" "$CONTAINER" bao "$@"
}

if [ ! -f "$KEYS_FILE" ]; then
  echo "No keys.json found - run bootstrap.sh first." >&2
  exit 1
fi

UNSEAL_KEY=$(jq -r '.unseal_keys_b64[0]' "$KEYS_FILE")

if bao status -format=json | jq -e '.sealed == false' >/dev/null 2>&1; then
  echo "OpenBao is already unsealed."
  exit 0
fi

echo "Unsealing..."
bao operator unseal "$UNSEAL_KEY"
