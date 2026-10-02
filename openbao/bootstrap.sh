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
  webui_secret_key="$WEBUI_SECRET_KEY" \
  smtp_user="$PASSBOLT_SMTP_USER" \
  smtp_password="$PASSBOLT_SMTP_PASSWORD" \
  smtp_from="$PASSBOLT_SMTP_FROM" \
  milvus_root_password="$MILVUS_ROOT_PASSWORD"

echo "Writing read-only policy for custom-backend..."
docker cp policy-readonly.hcl "$CONTAINER":/tmp/policy-readonly.hcl
bao policy write custom-backend /tmp/policy-readonly.hcl

echo "Writing admin (read/write) policy for custom-backend-admin..."
docker cp policy-admin.hcl "$CONTAINER":/tmp/policy-admin.hcl
bao policy write custom-backend-admin /tmp/policy-admin.hcl

echo "Enabling AppRole auth (ok if already enabled)..."
bao auth enable approle 2>/dev/null || true

bao write auth/approle/role/custom-backend \
  token_policies="custom-backend" \
  token_ttl=1h \
  token_max_ttl=4h

bao write auth/approle/role/custom-backend-admin \
  token_policies="custom-backend-admin" \
  token_ttl=15m \
  token_max_ttl=1h

ROLE_ID=$(bao read -field=role_id auth/approle/role/custom-backend/role-id)
SECRET_ID=$(bao write -f -field=secret_id auth/approle/role/custom-backend/secret-id)

ADMIN_ROLE_ID=$(bao read -field=role_id auth/approle/role/custom-backend-admin/role-id)
ADMIN_SECRET_ID=$(bao write -f -field=secret_id auth/approle/role/custom-backend-admin/secret-id)

# Human logins (bao-admin / bao-readonly), only once their passwords are in .env.
if [ -n "${OPENBAO_ADMIN_PASSWORD:-}" ] && [ -n "${OPENBAO_READONLY_PASSWORD:-}" ]; then
  ./setup-users.sh
else
  echo "Skipping human logins: set OPENBAO_ADMIN_PASSWORD and OPENBAO_READONLY_PASSWORD in .env, then run ./setup-users.sh"
fi

echo ""
echo "Root token and unseal key are stored in openbao/keys.json (gitignored) - keep it safe."
echo ""
echo "Add these to your .env:"
echo "OPENBAO_ROLE_ID=$ROLE_ID"
echo "OPENBAO_SECRET_ID=$SECRET_ID"
echo ""
echo "Admin (read/write) AppRole credentials - for the rotation script only, do NOT give these to custom-backend:"
echo "OPENBAO_ADMIN_ROLE_ID=$ADMIN_ROLE_ID"
echo "OPENBAO_ADMIN_SECRET_ID=$ADMIN_SECRET_ID"
