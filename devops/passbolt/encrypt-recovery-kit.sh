#!/usr/bin/env bash
# GPG-encrypts a downloaded Passbolt recovery kit (.asc private key file) and
# deletes the plaintext original, so only the encrypted copy is ever safe to
# upload to Google Drive / a pendrive / anywhere off this laptop.
#
# Usage: devops/passbolt/encrypt-recovery-kit.sh /path/to/passbolt-recovery-kit.asc
set -euo pipefail

SRC="${1:?Usage: devops/passbolt/encrypt-recovery-kit.sh /path/to/passbolt-recovery-kit.asc}"

if [ ! -f "$SRC" ]; then
  echo "File not found: $SRC" >&2
  exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR="$(dirname "$0")/../backups"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/passbolt-recovery-kit-$TS.asc.gpg"

echo "Encrypting (you'll be prompted for a passphrase - remember it, there is no recovery without it)..."
gpg --symmetric --cipher-algo AES256 --output "$OUT" "$SRC"

echo ""
echo "Wrote $OUT"
echo "Copy this file to Google Drive / a pendrive / wherever. It's useless without the passphrase you just set."
echo ""
read -rp "Delete the original plaintext file at $SRC now? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
  rm -f "$SRC"
  echo "Deleted $SRC"
else
  echo "Left $SRC in place - delete it yourself once you've confirmed the backup is safe."
fi

echo ""
echo "To restore: gpg --decrypt $OUT > passbolt-recovery-kit.asc"
