# OpenBao secrets setup

`custom-backend` fetches its Postgres credentials from OpenBao at startup via
AppRole auth, instead of reading `DATABASE_URL` directly from the environment.

## One-time setup

1. Start OpenBao (and Postgres, since bootstrap reads `.env` for the values to store):

   ```bash
   docker compose -f devops/docker-compose.yml up -d postgres-db openbao
   ```

2. Make sure `jq` is installed on the host (`sudo apt install jq`). The `bao` CLI itself doesn't need a host install — the scripts run it inside the `laptop-openbao` container via `docker exec`.

3. Run the bootstrap script (reads the repo root's `.env`):

   ```bash
   cd openbao && ./bootstrap.sh
   ```

   This initializes OpenBao (single unseal key/share — fine for a personal
   laptop, not for anything shared), unseals it, enables the KV v2 engine,
   writes `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`/`WEBUI_SECRET_KEY`
   from `.env` into `secret/custom-backend`, and creates two AppRoles scoped
   to that one path: `custom-backend` (read-only, long-lived token, used by
   the running FastAPI app) and `custom-backend-admin` (read/write,
   short-lived token, for a future rotation script only — never give its
   creds to the app itself).

   It prints `OPENBAO_ROLE_ID`/`OPENBAO_SECRET_ID` (for the app's `.env`) and
   `OPENBAO_ADMIN_ROLE_ID`/`OPENBAO_ADMIN_SECRET_ID` (for whatever rotation
   tooling ends up using them — not currently consumed by anything) at the
   end.

4. Start the backend:

   ```bash
   docker compose -f devops/docker-compose.yml up -d custom-backend
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
- `policy-readonly.hcl` — read-only policy for the `custom-backend` AppRole,
  scoped to `secret/data/custom-backend` only.
- `policy-admin.hcl` — read/write policy for the `custom-backend-admin`
  AppRole, scoped to `secret/data/custom-backend` and
  `secret/metadata/custom-backend` — intended for a future credential
  rotation script, not for the running app.
- `setup-users.sh` — creates the human logins (userpass): `bao-admin` and
  `bao-readonly`, with passwords from `OPENBAO_ADMIN_PASSWORD` /
  `OPENBAO_READONLY_PASSWORD` in `.env`. Safe to re-run; `bootstrap.sh` runs it
  at the end once both passwords are set. Run it on its own to add or change
  the users, since `bootstrap.sh` issues new AppRole secret IDs every run.
- `policy-user-readonly.hcl` — `bao-readonly`'s policy: read/list every secret
  under `secret/`, no writes, no policy/auth access.
- `policy-user-admin.hcl` — `bao-admin`'s policy: secrets, policies, auth
  methods (users, AppRoles), mounts, identity, leases. No seal, generate-root
  or rekey (those still need `keys.json`). It can write policies and users,
  so it can grant itself more — treat it as fully trusted.
- `keys.json` (gitignored, created by `bootstrap.sh`) — root token + unseal
  key. Treat this like a master password.

## UI

http://localhost:8200/ui — Method: **Username**, as `bao-readonly` to look
secrets up or `bao-admin` to change things (passwords in `.env`, created by
`setup-users.sh`). Keep the root token from `keys.json` (Method: **Token**) for
break-glass only, e.g. if the userpass method is broken. There's no OIDC/SSO or
MFA on OpenBao logins; AppRole is for `custom-backend` only.

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
