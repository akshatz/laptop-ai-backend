#!/usr/bin/env bash
# One-time setup for the openbao service: init, unseal, enable KV, create an
# AppRole for custom-backend, and write the Postgres/webui secrets into it.
#
# Run this once after `docker compose up -d openbao`, with the stack's .env
# already populated (POSTGRES_USER/PASSWORD/DB, WEBUI_SECRET_KEY).
#
# Safe to re-run: if openbao/keys.json already exists, init is skipped and
# the script just unseals + re-writes secrets.
#
# Runs the `bao` CLI inside the openbao container via `docker exec` so no
# host install is required.
set -euo pipefail

cd "$(dirname "$0")"
CONTAINER="laptop-openbao"
KEYS_FILE="keys.json"

bao() {
  docker exec -e BAO_ADDR="http://127.0.0.1:8200" -e BAO_TOKEN="${BAO_TOKEN:-}" "$CONTAINER" bao "$@"
}

if [ ! -f "$KEYS_FILE" ]; then
  echo "Initializing OpenBao (first run)..."
  bao operator init -key-shares=1 -key-threshold=1 -format=json > "$KEYS_FILE"
  chmod 600 "$KEYS_FILE"
fi

UNSEAL_KEY=$(jq -r '.unseal_keys_b64[0]' "$KEYS_FILE")
ROOT_TOKEN=$(jq -r '.root_token' "$KEYS_FILE")

echo "Unsealing..."
bao operator unseal "$UNSEAL_KEY" || true

export BAO_TOKEN="$ROOT_TOKEN"

echo "Enabling KV v2 secrets engine at secret/ (ok if already enabled)..."
bao secrets enable -path=secret kv-v2 2>/dev/null || true

echo "Writing app secrets..."
set -a
source ../.env
set +a
bao kv put secret/custom-backend \
  postgres_user="$POSTGRES_USER" \
  postgres_password="$POSTGRES_PASSWORD" \
  postgres_db="$POSTGRES_DB" \
  webui_secret_key="$WEBUI_SECRET_KEY"

echo "Writing policy for custom-backend..."
docker cp policy.hcl "$CONTAINER":/tmp/policy.hcl
bao policy write custom-backend /tmp/policy.hcl

echo "Enabling AppRole auth (ok if already enabled)..."
bao auth enable approle 2>/dev/null || true

bao write auth/approle/role/custom-backend \
  token_policies="custom-backend" \
  token_ttl=1h \
  token_max_ttl=4h

ROLE_ID=$(bao read -field=role_id auth/approle/role/custom-backend/role-id)
SECRET_ID=$(bao write -f -field=secret_id auth/approle/role/custom-backend/secret-id)

echo ""
echo "Root token and unseal key are stored in openbao/keys.json (gitignored) - keep it safe."
echo ""
echo "Add these to your .env:"
echo "OPENBAO_ROLE_ID=$ROLE_ID"
echo "OPENBAO_SECRET_ID=$SECRET_ID"
