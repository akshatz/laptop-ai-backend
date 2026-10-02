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
   writes each app's secrets from `.env` into its own folder under
   `apps/default/` (see "Secret layout" below), and creates two
   AppRoles scoped to `apps/default/custom-backend` only: `custom-backend` (read-only, long-lived token, used by
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
  scoped to `apps/data/default/custom-backend` only.
- `policy-admin.hcl` — read/write policy for the `custom-backend-admin`
  AppRole, scoped to `apps/data/default/custom-backend` and
  `apps/metadata/default/custom-backend` — intended for a future credential
  rotation script, not for the running app.
- `config.hcl` also declares the audit log (`audit "file" "file"`, written to
  `/openbao/logs/audit.log` in the `openbao-logs` volume). The
  `openbao-audit-shipper` service sends it to OpenObserve stream `openbao_audit`:
  who read or changed which path, from which address, and any error. Secret
  values in it are HMAC-hashed. OpenBao refuses requests it can't write to the
  audit log, so a full disk stops OpenBao.
- `setup-users.sh` — creates the human logins (userpass): `bao-admin` and
  `bao-readonly`, with passwords from `OPENBAO_ADMIN_PASSWORD` /
  `OPENBAO_READONLY_PASSWORD` in `.env`. Safe to re-run; `bootstrap.sh` runs it
  at the end once both passwords are set. Run it on its own to add or change
  the users, since `bootstrap.sh` issues new AppRole secret IDs every run.
  With `OPENBAO_OIDC_CLIENT_ID`/`OPENBAO_OIDC_CLIENT_SECRET` set, it also sets
  up sign-in through authentik (method **OIDC** on the login page, or
  `bao login -method=oidc`): same password + TOTP as Open WebUI. The role you
  sign in with sets what you can do: `readonly` (the default) gets
  `user-readonly` and is open to authentik groups `openbao-admins` and
  `openbao-readers`; `admin` gets `user-admin` and is open to `openbao-admins`
  only. Anyone in neither group is refused.
  Add people under authentik's Directory → Groups. The authentik side is
  `authentik/blueprints/openbao-sso.yaml`. The `bao-admin`/`bao-readonly`
  passwords keep working as the fallback for when authentik is down.
- `policy-user-readonly.hcl` — `bao-readonly`'s policy: read/list every secret
  under `apps/`, no writes, no policy/auth access.
- `policy-user-admin.hcl` — `bao-admin`'s policy: secrets, policies, auth
  methods (users, AppRoles), mounts, identity, leases. No seal, generate-root
  or rekey (those still need `keys.json`). It can write policies and users,
  so it can grant itself more — treat it as fully trusted.
- `keys.json` (gitignored, created by `bootstrap.sh`) — root token + unseal
  key. Treat this like a master password.

## UI

http://localhost:8200/ui — Method: **OIDC** to sign in through authentik with
your password + TOTP. Leave Role empty (= `readonly`) to look secrets up, or
enter `admin` to change things (authentik group `openbao-admins` only; on the
CLI `bao login -method=oidc role=admin`). You need to be in authentik group
`openbao-admins` or `openbao-readers`. If authentik is down, use Method:
**Username** as `bao-readonly` to look secrets up or `bao-admin` to change
things (passwords in `.env`, created by `setup-users.sh`). Keep the root token
from `keys.json` (Method: **Token**) for break-glass only, e.g. if both are
broken. AppRole is for `custom-backend` only.

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

## Secret layout

Secrets are grouped as `apps/<environment>/<app>` (the KV v2 engine is mounted at
`apps/`, so the UI shows Secrets engines → apps → default); this stack is the
`default` environment, so another one (say `apps/prod/…`) could sit beside it.

| Path | Holds | Read by |
|---|---|---|
| `apps/default/custom-backend` | Postgres user/password/db, WebUI secret key, SMTP, Milvus root password | `custom-backend` (`db.py`, `resolve_db_url.py`) through its AppRole |
| `apps/default/postgres` | Postgres user/password/db | people (bao-admin / bao-readonly) |
| `apps/default/open-webui` | WebUI secret key | people |
| `apps/default/milvus` | Milvus root password | people |
| `apps/default/authentik` | secret key, bootstrap login, Open WebUI OIDC client, Open WebUI API token | people |
| `apps/default/passbolt` | SMTP relay, MariaDB password | people |
| `apps/default/openobserve` | root login, basic-auth header value | people |
| `apps/default/searxng` | secret | people |

Only `custom-backend` reads from OpenBao; the other services still take their
secrets from `.env`, so these are reference copies. After changing a value in
`.env`, re-run `./bootstrap.sh` to update OpenBao. It stops on any empty or
misspelled `.env` value instead of writing a blank secret. `custom-backend`'s
path can be overridden with `OPENBAO_SECRET_PATH` (default
`default/custom-backend`) and the mount with `OPENBAO_SECRET_MOUNT` (default `apps`).

The KV v2 engine used to be mounted at `secret/` (then briefly `secrets/`); re-running
`bootstrap.sh` on an older install moves it to `apps/` (`bao secrets move`) and removes
the old-layout paths.
