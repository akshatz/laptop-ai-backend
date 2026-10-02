# Human read-only login (userpass user `bao-readonly`, see setup-users.sh):
# read and list every secret under the KV v2 mount `apps/`, change nothing.
# Not the app's policy - custom-backend's AppRole uses policy-readonly.hcl.

path "apps/data/*" {
  capabilities = ["read"]
}

path "apps/metadata/*" {
  capabilities = ["read", "list"]
}
