#!/usr/bin/env bash
# One-time setup for the openbao service: init, unseal, enable KV, create an
# AppRole for custom-backend, and write each app's secrets from .env to
# apps/default/<app> (custom-backend, postgres, open-webui, milvus,
# authentik, passbolt, openobserve, searxng).
#
# Run this once after `docker compose up -d openbao`, with the stack's .env
# fully populated (every secret in .env.example; the script stops on a missing one).
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

# The KV v2 engine used to be mounted at secret/ (later secrets/); move it to apps/.
# The secrets themselves are re-written below from .env, so only the mount is moved.
for old_mount in secret secrets; do
  if bao secrets list -format=json | jq -e --arg m "$old_mount/" 'has($m) and (has("apps/") | not)' >/dev/null; then
    echo "Moving KV v2 secrets engine $old_mount/ -> apps/..."
    bao secrets move "$old_mount/" apps/
  fi
done

echo "Enabling KV v2 secrets engine at apps/ (ok if already enabled)..."
bao secrets enable -path=apps kv-v2 2>/dev/null || true

echo "Writing app secrets..."
set -a
source ../.env
set +a

# Secrets live at apps/<environment>/<app> (KV v2 engine mounted at apps/); this stack is the
# `default` environment.
APPS="apps/default"

# Every value must be set: a missing or misspelled .env variable stops the script here
# (set -u / :?) instead of writing an empty secret.
put() {
  local path="$1"; shift
  local kv
  for kv in "$@"; do
    [ -n "${kv#*=}" ] || { echo "Refusing to write empty ${kv%%=*} to $path - check .env" >&2; exit 1; }
  done
  bao kv put "$path" "$@" >/dev/null
  echo "  $path"
}

# custom-backend: the only secret an app reads from OpenBao (db.py / resolve_db_url.py), through
# its AppRoles, which are scoped to just this path (policy-readonly.hcl / policy-admin.hcl).
put "$APPS/custom-backend" \
  postgres_user="$POSTGRES_USER" \
  postgres_password="$POSTGRES_PASSWORD" \
  postgres_db="$POSTGRES_DB" \
  webui_secret_key="$WEBUI_SECRET_KEY" \
  smtp_user="$PASSBOLT_SMTP_USER" \
  smtp_password="$PASSBOLT_SMTP_PASSWORD" \
  smtp_from="$PASSBOLT_SMTP_FROM" \
  milvus_root_password="$MILVUS_ROOT_PASSWORD"

# The other services still read these from .env; these are copies kept in one place for the
# human logins (bao-admin / bao-readonly). Update .env and re-run this script when rotating.
put "$APPS/postgres" \
  user="$POSTGRES_USER" password="$POSTGRES_PASSWORD" db="$POSTGRES_DB"
put "$APPS/open-webui" \
  webui_secret_key="$WEBUI_SECRET_KEY"
put "$APPS/milvus" \
  root_password="$MILVUS_ROOT_PASSWORD"
put "$APPS/authentik" \
  secret_key="$AUTHENTIK_SECRET_KEY" \
  bootstrap_email="$AUTHENTIK_BOOTSTRAP_EMAIL" \
  bootstrap_password="$AUTHENTIK_BOOTSTRAP_PASSWORD" \
  open_webui_oidc_client_id="$OPEN_WEBUI_OIDC_CLIENT_ID" \
  open_webui_oidc_client_secret="$OPEN_WEBUI_OIDC_CLIENT_SECRET" \
  open_webui_api_token="$OPEN_WEBUI_AUTHENTIK_API_TOKEN"
put "$APPS/passbolt" \
  smtp_from="$PASSBOLT_SMTP_FROM" \
  smtp_user="$PASSBOLT_SMTP_USER" \
  smtp_password="$PASSBOLT_SMTP_PASSWORD" \
  db_password="$PASSBOLT_DB_PASSWORD"
put "$APPS/openobserve" \
  root_user_email="$OPENOBSERVE_ROOT_USER_EMAIL" \
  root_user_password="$OPENOBSERVE_ROOT_USER_PASSWORD" \
  basic_auth="$OPENOBSERVE_BASIC_AUTH"
put "$APPS/searxng" \
  secret="$SEARXNG_SECRET"

# Remove earlier layouts carried over by the mount move: flat paths directly under the mount
# (custom-backend, and copies for authentik, openobserve, passbolt, searxng), and
# apps/apps/default/<app> from when the mount was secrets/ (secrets/apps/default/<app>).
for old in custom-backend authentik openobserve passbolt searxng; do
  bao kv metadata delete "apps/$old" >/dev/null 2>&1 || true
done
for old in $(bao kv list -format=json apps/apps/default 2>/dev/null | jq -r '.[]'); do
  bao kv metadata delete "apps/apps/default/$old" >/dev/null
done

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
