#!/usr/bin/env bash
# Creates the two human logins for OpenBao (userpass auth), so people don't have to
# use the root token:
#
#   bao-admin     policy user-admin     manage secrets, policies, auth, mounts (policy-user-admin.hcl)
#   bao-readonly  policy user-readonly  read/list every secret under apps/ (policy-user-readonly.hcl)
#
# and, when OPENBAO_OIDC_CLIENT_ID/SECRET are set in .env, sign-in through authentik (OIDC, same
# password + TOTP as Open WebUI). The OIDC role picked at sign-in sets the access, limited by
# authentik group:
#
#   role readonly (default)  policy user-readonly  for openbao-admins and openbao-readers
#   role admin               policy user-admin     for openbao-admins only
#
# (authentik side: authentik/blueprints/openbao-sso.yaml). The userpass logins stay as the
# fallback for when authentik is down.
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

if [ -n "${OPENBAO_OIDC_CLIENT_ID:-}" ] && [ -n "${OPENBAO_OIDC_CLIENT_SECRET:-}" ]; then
  : "${OPEN_WEBUI_HOST:?set OPEN_WEBUI_HOST in .env}"
  echo "Configuring OIDC sign-in through authentik..."
  bao auth enable -description="authentik (password + TOTP)" oidc 2>/dev/null || true

  # authentik is served by Caddy with its local CA; OpenBao gets the public root cert only.
  CA_PEM=$(docker exec laptop-open-webui-proxy cat /data/caddy/pki/authorities/local/root.crt)
  jq -n --arg url "https://$OPEN_WEBUI_HOST:9443/application/o/openbao/" \
    --arg id "$OPENBAO_OIDC_CLIENT_ID" --arg secret "$OPENBAO_OIDC_CLIENT_SECRET" --arg ca "$CA_PEM" \
    '{oidc_discovery_url: $url, oidc_client_id: $id, oidc_client_secret: $secret,
      oidc_discovery_ca_pem: $ca, default_role: "readonly"}' |
    bao write auth/oidc/config - >/dev/null

  # One role per access level; the policy comes from the role entered at sign-in (Role field in
  # the UI, role=... on the CLI), so admins sign in read-only unless they ask for `admin`.
  # bound_claims refuses anyone outside the role's authentik groups (authentik already refuses
  # anyone in neither group, this is the second check). Redirect URIs must match
  # authentik/blueprints/openbao-sso.yaml.
  for spec in "readonly:user-readonly:openbao-admins,openbao-readers" "admin:user-admin:openbao-admins"; do
    IFS=: read -r role policy groups <<<"$spec"
    jq -n --arg host "$OPEN_WEBUI_HOST" --arg policy "$policy" --arg groups "$groups" '{
        role_type: "oidc",
        user_claim: "preferred_username",
        oidc_scopes: ["profile", "email"],
        bound_claims: {groups: ($groups | split(","))},
        claim_mappings: {preferred_username: "username", email: "email"},
        allowed_redirect_uris: [
          "http://localhost:8200/ui/vault/auth/oidc/oidc/callback",
          "http://127.0.0.1:8200/ui/vault/auth/oidc/oidc/callback",
          ("http://" + $host + ":8200/ui/vault/auth/oidc/oidc/callback"),
          "http://localhost:8250/oidc/callback"
        ],
        token_policies: [$policy],
        token_ttl: "1h",
        token_max_ttl: "8h"
      }' | bao write "auth/oidc/role/$role" - >/dev/null
  done

  # Earlier setup: a single role `authentik` with access from identity groups openbao-admins /
  # openbao-readers. Those groups would add user-admin to every admin login whatever the role, so
  # remove them.
  bao delete auth/oidc/role/authentik >/dev/null
  for group in openbao-admins openbao-readers; do
    bao delete identity/group/name/"$group" >/dev/null
  done

  # Show both sign-in methods as tabs on the UI's login page.
  bao auth tune -listing-visibility=unauth oidc/ >/dev/null
  bao auth tune -listing-visibility=unauth userpass/ >/dev/null
  echo "OIDC ready. Add people to authentik groups openbao-admins / openbao-readers, then sign in with"
  echo "method 'OIDC' in the UI (Role empty = readonly, or 'admin'), or: bao login -method=oidc [role=admin]"
fi

echo "Done. Fallback login: method 'Username', or: bao login -method=userpass username=bao-readonly"
