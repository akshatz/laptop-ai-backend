#!/usr/bin/env bash
# Bundles keys.json + the openbao-data volume into a single GPG-encrypted
# archive, safe to copy to a pendrive, Google Drive, or any other backup
# destination. Without this, keys.json (unseal key + root token) or the
# secrets volume would sit there in plaintext.
#
# Usage: ./backup.sh [output-directory]   (defaults to ./backups)
set -euo pipefail

cd "$(dirname "$0")"
OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"

if [ ! -f keys.json ]; then
  echo "No keys.json found - run bootstrap.sh first." >&2
  exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

cp keys.json "$WORKDIR/keys.json"

echo "Exporting openbao-data volume..."
docker run --rm -v laptop-ai-backend_openbao-data:/data -v "$WORKDIR":/backup \
  alpine tar czf /backup/openbao-data.tgz -C /data .

TARBALL="$WORKDIR/openbao-backup-$TS.tar"
tar -cf "$TARBALL" -C "$WORKDIR" keys.json openbao-data.tgz

echo "Encrypting (you'll be prompted for a passphrase - remember it, there is no recovery without it)..."
gpg --symmetric --cipher-algo AES256 --output "$OUT_DIR/openbao-backup-$TS.tar.gpg" "$TARBALL"

echo ""
echo "Wrote $OUT_DIR/openbao-backup-$TS.tar.gpg"
echo "Copy this file to Google Drive / a pendrive / wherever. It's useless without the passphrase you just set."
echo ""
echo "To restore:"
echo "  gpg --decrypt openbao-backup-$TS.tar.gpg > restored.tar"
echo "  tar -xf restored.tar"
echo "  # then: cp keys.json openbao/keys.json"
echo "  # and:  docker run --rm -v laptop-ai-backend_openbao-data:/data -v \$PWD:/backup alpine tar xzf /backup/openbao-data.tgz -C /data"
