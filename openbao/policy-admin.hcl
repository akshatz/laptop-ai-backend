path "secret/data/custom-backend" {
  capabilities = ["create", "update", "read"]
}

path "secret/metadata/custom-backend" {
  capabilities = ["read", "list"]
}
