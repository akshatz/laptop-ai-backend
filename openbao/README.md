# OpenBao secrets setup

`custom-backend` fetches its Postgres credentials from OpenBao at startup via
AppRole auth, instead of reading `DATABASE_URL` directly from the environment.

## One-time setup

1. Start OpenBao (and Postgres, since bootstrap reads `.env` for the values to store):

   ```bash
   docker compose up -d postgres-db openbao
   ```

2. Make sure `jq` is installed on the host (`sudo apt install jq`). The `bao` CLI itself doesn't need a host install — the scripts run it inside the `laptop-openbao` container via `docker exec`.

3. Run the bootstrap script (reads the repo root's `.env`):

   ```bash
   cd openbao && ./bootstrap.sh
   ```

   This initializes OpenBao (single unseal key/share — fine for a personal
   laptop, not for anything shared), unseals it, enables the KV v2 engine,
   writes `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`/`WEBUI_SECRET_KEY`
   from `.env` into `secret/custom-backend`, and creates an AppRole scoped to
   read-only access on that one path.

   It prints `OPENBAO_ROLE_ID` and `OPENBAO_SECRET_ID` at the end — copy those
   into the repo root `.env`.

4. Start the backend:

   ```bash
   docker compose up -d custom-backend
   ```

## After a restart

OpenBao starts **sealed** every time its container restarts (file storage
backend, not dev mode). Unseal it before `custom-backend` can start successfully:

```bash
cd openbao && ./auto-unseal.sh
```

## Files

- `config.hcl` — server config (raft storage, TLS disabled — this stack is
  laptop-local only, don't expose port 8200 beyond `localhost`).
- `bootstrap.sh` — one-time init/unseal/policy/AppRole/secret-write. Safe to
  re-run (skips init if `keys.json` exists).
- `auto-unseal.sh` — unseals using the key saved by `bootstrap.sh`. Run after
  every container restart or host reboot.
- `policy.hcl` — read-only policy for the `custom-backend` AppRole, scoped to
  `secret/data/custom-backend` only.
- `keys.json` (gitignored, created by `bootstrap.sh`) — root token + unseal
  key. Treat this like a master password.

## UI

http://localhost:8200/ui — Method: **Token**, using the root token from
`keys.json` (there's no username/password or OIDC method configured, only
Token for humans and AppRole for `custom-backend`).

## Backup (do this — a laptop crash otherwise loses all secrets)

Everything OpenBao knows lives in two places, neither of which is backed up
by anything else in this repo:

- `openbao/keys.json` — the unseal key and root token. Without it you cannot
  unseal OpenBao even if the data survives.
- the `openbao-data` Docker volume — the actual encrypted secrets (raft
  storage). Without it there's nothing to unseal.

Never copy `keys.json` or the raw volume contents anywhere off the laptop
unencrypted — it's the unseal key and root token in plaintext. Use
`backup.sh`, which bundles both into a single archive and GPG-encrypts it
with a passphrase before it touches disk anywhere else:

```bash
cd openbao && ./backup.sh              # writes openbao/backups/openbao-backup-<timestamp>.tar.gpg
```

That `.tar.gpg` file is what's safe to drop into Google Drive, a pendrive,
or any other backup destination — it's useless without the passphrase you
set when running the script. `openbao/backups/` is gitignored; move the file
off the laptop yourself after it's created (this script only encrypts, it
doesn't upload anywhere).

To restore, decrypt and follow the instructions the script prints at the end
(`gpg --decrypt ... | tar -x`, then copy `keys.json` back and reload the
volume tarball).

If both `keys.json` and the volume are lost with no backup, there is no
recovery — you'd have to re-run `bootstrap.sh` from scratch, which
re-derives the AppRole secrets from whatever is currently in `.env` (fine for
this stack, since `.env` itself is the ultimate source of truth for the
Postgres/webui values — OpenBao is a broker in front of it, not the only
copy).
