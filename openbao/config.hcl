storage "raft" {
  path    = "/openbao/data"
  node_id = "node1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

api_addr     = "http://openbao:8200"
cluster_addr = "http://openbao:8201"
disable_mlock = true
ui = true

# Audit log: one JSON line per request and per response (who, which path, from where). Secret
# values, tokens and accessors in it are HMAC-SHA256 hashed, not plaintext. Lives in the
# openbao-logs volume; openbao-audit-shipper tails it into OpenObserve stream `openbao_audit`.
# Declared here because OpenBao doesn't allow enabling audit devices through the API by default.
# While an audit device is enabled, OpenBao refuses requests it can't write to the log.
audit "file" "file" {
  description = "Audit log shipped to OpenObserve"
  options = {
    file_path = "/openbao/logs/audit.log"
  }
}
