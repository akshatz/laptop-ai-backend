#!/usr/bin/env bash
# Creates the HTTPS certificate the passbolt service serves on port 8443, in
# devops/passbolt/certs/ (gitignored, bind-mounted read-only into the container).
# Without it the Passbolt image generates a fresh self-signed cert on every
# container recreate, which the browser extension then rejects ("Cannot read
# properties of null (reading 'isPluginEnabled')").
#
# Uses mkcert when installed (cert signed by mkcert's local CA, trusted by the
# browser once `mkcert -install` has run), otherwise a 10-year self-signed cert
# from openssl (browser warns once; the exception then sticks since the cert
# no longer changes). Refuses to overwrite an existing cert unless --force.
#
# Usage: ./devops/passbolt/make-cert.sh [--force]
# Then:  docker compose -f devops/docker-compose.yml up -d passbolt
set -euo pipefail

cd "$(dirname "$0")"
CERT_DIR="certs"
CRT="$CERT_DIR/certificate.crt"
KEY="$CERT_DIR/certificate.key"

if [ -e "$CRT" ] || [ -e "$KEY" ]; then
  if [ "${1:-}" != "--force" ]; then
    echo "$CRT / $KEY already exist - pass --force to replace them." >&2
    exit 1
  fi
fi

mkdir -p "$CERT_DIR"

if command -v mkcert >/dev/null 2>&1; then
  echo "Using mkcert (CA: $(mkcert -CAROOT))"
  mkcert -cert-file "$CRT" -key-file "$KEY" localhost 127.0.0.1 ::1
else
  echo "mkcert not found - generating a self-signed cert with openssl instead."
  openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout "$KEY" -out "$CRT" -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1"
fi

chmod 600 "$KEY"
chmod 644 "$CRT"
echo "Wrote $(pwd)/$CRT and $(pwd)/$KEY"
