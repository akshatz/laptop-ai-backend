# Human read-only login (userpass user `bao-readonly`, see setup-users.sh):
# read and list every secret under the KV v2 mount `secret/`, change nothing.
# Not the app's policy - custom-backend's AppRole uses policy-readonly.hcl.

path "secret/data/*" {
  capabilities = ["read"]
}

path "secret/metadata/*" {
  capabilities = ["read", "list"]
}
