# Human admin login (userpass user `bao-admin`, see setup-users.sh): day-to-day
# management - secrets, policies, auth methods (users, AppRoles), mounts, identity,
# leases - so the root token can stay unused. Deliberately no seal/unseal,
# generate-root or rekey; those still need keys.json. Anyone who can write policies
# and users can grant themselves more, so treat this login as fully trusted.
# Not the app's policy - custom-backend-admin's AppRole uses policy-admin.hcl.

# Secrets (KV v2 at secret/)
path "secret/*" {
  capabilities = ["create", "read", "update", "patch", "delete", "list"]
}

# ACL policies
path "sys/policies/acl" {
  capabilities = ["list"]
}
path "sys/policies/acl/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

# Auth methods: enable/tune them, and manage what's inside (userpass users, AppRoles)
path "sys/auth" {
  capabilities = ["read"]
}
path "sys/auth/*" {
  capabilities = ["create", "read", "update", "delete", "sudo"]
}
path "auth/*" {
  capabilities = ["create", "read", "update", "patch", "delete", "list"]
}

# Secrets engines (mounts)
path "sys/mounts" {
  capabilities = ["read"]
}
path "sys/mounts/*" {
  capabilities = ["create", "read", "update", "delete"]
}

# Identity (entities, groups, aliases)
path "identity/*" {
  capabilities = ["create", "read", "update", "patch", "delete", "list"]
}

# Leases and tokens
path "sys/leases/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}

# Status
path "sys/health" {
  capabilities = ["read", "sudo"]
}
