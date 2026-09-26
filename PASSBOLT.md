# Passbolt setup

Self-hosted password manager for storing the OpenBao root token, unseal key,
and AppRole credentials somewhere more usable than raw files on disk. See
`openbao/README.md` for what those values are and where they otherwise live.

## One-time setup

1. Fill in `.env`: `PASSBOLT_SMTP_USER` (your Gmail address) and
   `PASSBOLT_SMTP_PASSWORD` (a Gmail **App Password**, not your normal
   password — requires 2FA enabled on the account:
   https://myaccount.google.com/apppasswords).

2. Start Postgres and Passbolt:

   ```bash
   docker compose up -d postgres-db passbolt
   ```

   First run generates a server GPG keypair and JWT signing keys (persisted
   in the `passbolt-gpg` / `passbolt-jwt` volumes) — this takes a minute.

3. Register yourself as the admin user:

   ```bash
   docker exec laptop-passbolt su -m -c \
     "/usr/share/php/passbolt/bin/cake passbolt register_user \
       -u you@example.com -f YourFirstName -l YourLastName -r admin" \
     -s /bin/sh www-data
   ```

   This sends a registration email (via the Gmail SMTP config from step 1)
   with a link to set up your account and import the Passbolt browser
   extension / desktop app.

4. Open https://localhost:8443 — the cert is self-signed, so your browser
   will warn; accept the exception for localhost only.

## Storing the OpenBao secrets

Once logged in, create entries for:
- OpenBao root token (`openbao/keys.json` → `root_token`)
- OpenBao unseal key (`openbao/keys.json` → `unseal_keys_b64[0]`)
- `OPENBAO_ROLE_ID` / `OPENBAO_SECRET_ID` (from `.env`)

This doesn't replace `openbao/keys.json` or `.env` — those still have to
exist on disk for the scripts to work. Passbolt is just a second, more
convenient copy for you to reference/share, not the source of truth.

## Notes

- Port `8443` maps to the container's `443` — HTTPS only, self-signed cert.
- `passbolt-gpg` and `passbolt-jwt` volumes are as important to back up as
  `openbao/keys.json` — losing them means losing access to everything
  stored in Passbolt. Extend `openbao/backup.sh`'s approach (GPG-encrypt
  before it leaves the laptop) if you want this backed up the same way.
