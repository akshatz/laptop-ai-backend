#!/usr/bin/env bash
# Creates the two human logins for OpenBao (userpass auth), so people don't have to
# use the root token:
#
#   bao-admin     policy user-admin     manage secrets, policies, auth, mounts (policy-user-admin.hcl)
#   bao-readonly  policy user-readonly  read/list every secret under apps/ (policy-user-readonly.hcl)
#
# Passwords come from OPENBAO_ADMIN_PASSWORD / OPENBAO_READONLY_PASSWORD in the root
# .env. Safe to re-run: re-writes both policies and users, so changing a password in
# .env and re-running sets it. bootstrap.sh runs this at the end; run it on its own
# to add/update the users without bootstrap re-issuing AppRole secret IDs.
#
# Needs OpenBao unsealed (auto-unseal.sh) and the root token in keys.json.
#
# Usage: openbao/setup-users.sh
set -euo pipefail

cd "$(dirname "$0")"
CONTAINER="laptop-openbao"

set -a
source ../.env
set +a
: "${OPENBAO_ADMIN_PASSWORD:?set OPENBAO_ADMIN_PASSWORD in .env}"
: "${OPENBAO_READONLY_PASSWORD:?set OPENBAO_READONLY_PASSWORD in .env}"

BAO_TOKEN=$(jq -r '.root_token' keys.json)
bao() {
  docker exec -i -e BAO_ADDR="http://127.0.0.1:8200" -e BAO_TOKEN="$BAO_TOKEN" "$CONTAINER" bao "$@"
}

echo "Writing user-admin and user-readonly policies..."
bao policy write user-admin - < policy-user-admin.hcl
bao policy write user-readonly - < policy-user-readonly.hcl

echo "Enabling userpass auth (ok if already enabled)..."
bao auth enable userpass 2>/dev/null || true

# Passwords go in on stdin as JSON, never on the docker/bao command line (visible in ps).
echo "Writing users bao-admin and bao-readonly..."
jq -n --arg p "$OPENBAO_ADMIN_PASSWORD" \
  '{password: $p, token_policies: "user-admin", token_ttl: "1h", token_max_ttl: "8h"}' |
  bao write auth/userpass/users/bao-admin -
jq -n --arg p "$OPENBAO_READONLY_PASSWORD" \
  '{password: $p, token_policies: "user-readonly", token_ttl: "8h", token_max_ttl: "24h"}' |
  bao write auth/userpass/users/bao-readonly -

echo "Done. Log in to the OpenBao UI with method 'Username', or: bao login -method=userpass username=bao-readonly"
