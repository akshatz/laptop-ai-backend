# Laptop AI Backend

A local, hybrid AI development stack for a personal laptop: a custom FastAPI backend backed by PostgreSQL, alongside Ollama for local model inference, Open WebUI as a chat frontend, Milvus for RAG vector storage, and Grafana LGTM / OpenObserve for observability.

## Architecture

```mermaid
flowchart TB
    subgraph client[" "]
        browser["Browser"]
    end

    subgraph app["Application layer"]
        proxy["open-webui-proxy (Caddy)\n:8444 HTTPS"]
        webui["open-webui\n:8082\n+ email verification Function"]
        backend["custom-backend (FastAPI)\n:8011\nrouters: auth, chats"]
    end

    subgraph inference["Inference"]
        ollama["ollama\n(local LLM engine)"]
    end

    subgraph data["Data"]
        postgres[("postgres-db\n:5433\n(app / open_webui DBs)")]
        passboltDb[("passbolt-db\n(MariaDB)")]
        milvus["milvus\n(vector store)"]
        milvusEtcd["milvus-etcd\n(metadata)"]
        milvusMinio["milvus-minio\n(object storage)"]
        milvusInit["milvus-init-auth\n(one-shot password rotation)"]
    end

    subgraph secrets["Secrets & identity"]
        openbao["openbao\n:8200"]
        passbolt["passbolt\n:8443"]
    end

    subgraph observability["Observability"]
        lgtm["lgtm (Grafana/Loki/Tempo/Mimir)\n:3001, OTLP 4317/4318"]
        openobserve["openobserve (O2)\n:5080 (Open WebUI logs + audit)"]
        auditshipper["openwebui-audit-shipper\n(OTel Collector)"]
    end

    browser --> webui
    browser -- LAN --> proxy
    proxy --> webui
    browser --> backend

    webui --> ollama
    webui --> milvus
    webui --> postgres
    webui -. OTEL traces/metrics .-> lgtm
    webui -. OTEL logs .-> openobserve
    webui -. audit.log .-> auditshipper
    auditshipper -. OTLP .-> openobserve

    backend --> ollama
    backend --> milvus
    backend --> postgres
    backend -- AppRole auth --> openbao

    milvus --> milvusEtcd
    milvus --> milvusMinio
    milvusInit -. rotates root pw, gates startup .-> milvus
    milvusInit -.-> webui
    milvusInit -.-> backend

    passbolt --> passboltDb
    openbao -. stores root token/unseal key .-> passbolt
```

All services share the `ai-network` Docker bridge network, orchestrated via [devops/docker-compose.yml](devops/docker-compose.yml).

## Stack

| Service | Purpose | Port |
|---|---|---|
| `postgres-db` | PostgreSQL 16 database (hosts app and `open_webui` DBs) | 5433 → 5432 |
| `ollama` | Local LLM inference engine | (internal) |
| `open-webui` | Chat UI, talks to Ollama + Milvus for RAG; signups need email verification (see [Open WebUI signup verification](#open-webui-signup-verification)) | 8082 → 8080 |
| `open-webui-proxy` | Caddy HTTPS front for Open WebUI, for other devices on the LAN (cert from Caddy's local CA) | 8444 → 443 |
| `milvus` + `milvus-etcd` + `milvus-minio` | Vector store for RAG document embeddings | (internal, 19530) |
| `milvus-init-auth` | One-shot job that rotates Milvus's default root password | (n/a, runs once) |
| `lgtm` | Grafana + Loki + Tempo + Mimir (metrics/traces/logs) | 3001 (UI), 4317/4318 (OTLP) |
| `openobserve` | O2 observability platform; receives Open WebUI's logs | 5080 |
| `searxng` | Self-hosted metasearch engine for Open WebUI's web search | (internal, 8080) |
| `authentik-events-shipper` | OpenTelemetry Collector shipping authentik's audit events to O2 | 127.0.0.1:24224 (fluentd, from Docker) |
| `openwebui-audit-shipper` | OpenTelemetry Collector shipping Open WebUI's audit log (who did what) to O2 | (internal) |
| `openbao` | Secrets storage (Postgres creds, AppRole broker) | 8200 |
| `passbolt-db` | MariaDB for Passbolt (dedicated, separate from postgres-db) | (internal) |
| `passbolt` | Password manager (stores OpenBao/AppRole tokens) | 8443 → 443 |
| `custom-backend` | FastAPI app: auth, chats, RAG document ingestion | 8011 → 8000 |

All services share the `ai-network` Docker bridge network.

## Prerequisites

- Docker and Docker Compose
- Ollama models already pulled somewhere on the host; point `OLLAMA_MODELS_PATH` in `.env` at that directory (mounted into the `ollama` container)

## Configuration

All credentials and secrets are read from environment variables — none are hardcoded. Copy the example file and fill in real values before running anything:

```bash
cp .env.example .env
```

`.env` is gitignored and is picked up automatically by `docker compose`. Variables:

| Variable | Used by | Purpose |
|---|---|---|
| `OLLAMA_MODELS_PATH` | `ollama` | Host path to your pulled Ollama models, mounted into the container |
| `POSTGRES_USER` | `postgres-db`, `custom-backend` | Postgres role |
| `POSTGRES_PASSWORD` | `postgres-db`, `custom-backend` | Postgres password |
| `POSTGRES_DB` | `postgres-db`, `custom-backend` | Database name |
| `WEBUI_SECRET_KEY` | `open-webui` | Session signing secret |
| `INIT_DB_DATABASE_URL` | `database/alembic/` | Full async connection string for running Alembic migrations from the host (mapped port `5433`) |
| `OPENBAO_ROLE_ID` / `OPENBAO_SECRET_ID` | `custom-backend` | AppRole credentials used to authenticate to OpenBao and fetch Postgres credentials at startup — generated by `openbao/bootstrap.sh`, see [openbao/README.md](openbao/README.md) |
| `PASSBOLT_SMTP_USER` / `PASSBOLT_SMTP_PASSWORD` / `PASSBOLT_SMTP_FROM` | `passbolt`, `open-webui` | Gmail SMTP relay Passbolt uses to send registration/recovery emails — see [PASSBOLT.md](PASSBOLT.md). The user/password are also passed to `open-webui` as `SMTP_USER`/`SMTP_PASSWORD` for signup verification emails |
| `PASSBOLT_DB_PASSWORD` | `passbolt-db`, `passbolt` | Password for the dedicated MariaDB instance Passbolt uses (its officially supported database) |
| `MILVUS_ROOT_PASSWORD` | `milvus-init-auth`, `open-webui` | Milvus root password — rotated in from the `root`/`Milvus` default on first boot, then used by Open WebUI to authenticate |
| `OPENOBSERVE_ROOT_USER_EMAIL` / `OPENOBSERVE_ROOT_USER_PASSWORD` | `openobserve`, `open-webui` | Root login for the OpenObserve UI/API, created on O2's first start (password needs lower, upper, digit and special characters) |
| `OPENOBSERVE_BASIC_AUTH` | `open-webui`, `openwebui-audit-shipper` | base64 of `email:password` above, used to send Open WebUI's logs to O2's `openwebui_backend` stream and its audit log to `openwebui_audit`. Regenerate when either changes: `printf '%s:%s' "$OPENOBSERVE_ROOT_USER_EMAIL" "$OPENOBSERVE_ROOT_USER_PASSWORD" \| base64 -w0` |
| `OPEN_WEBUI_HOST` | `open-webui-proxy` | LAN IP or hostname other devices use to reach Open WebUI over HTTPS on port 8444 |
| `OLLAMA_BASE_URL` / `DEFAULT_CHAT_MODEL` | `custom-backend` | Optional — Ollama endpoint (default `http://ollama:11434`) and chat model (default `llama3.2`, must already be pulled) for the RAG chat endpoints |

`custom-backend` does not read `POSTGRES_PASSWORD` or a `DATABASE_URL` directly — it fetches its Postgres credentials from OpenBao (see below) at startup.

## Running

```bash
docker compose -f devops/docker-compose.yml up -d postgres-db openbao   # bring up the secrets dependency first
cd openbao && ./bootstrap.sh               # one-time: unseal, write secrets, create AppRole (see openbao/README.md)
# copy the OPENBAO_ROLE_ID / OPENBAO_SECRET_ID it prints into .env
docker compose -f devops/docker-compose.yml up -d   # start everything else
```

- Open WebUI: http://localhost:8082 (this machine), or https://`OPEN_WEBUI_HOST`:8444 from other devices on the LAN — plain `http://<LAN-IP>` doesn't work, because Open WebUI's frontend needs a secure context. Trust Caddy's root CA once per device: `docker cp laptop-open-webui-proxy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt`, then import it into the OS/browser trust store.
- Grafana: http://localhost:3001
- Custom backend API: http://localhost:8011
- OpenBao UI: http://localhost:8200/ui (log in with the root token from `openbao/keys.json`)
- Passbolt: https://localhost:8443 (self-signed cert; see [PASSBOLT.md](PASSBOLT.md) for one-time admin registration)
- Postgres: `localhost:5433` (credentials from `.env`)

Note: OpenBao starts **sealed** after every container restart or host reboot — run `cd openbao && ./auto-unseal.sh` before `custom-backend` will be able to start.

## Open WebUI SSO + MFA (authentik)

Open WebUI sign-in goes through [authentik](https://goauthentik.io) (`authentik-server` + `authentik-worker`, using a separate `authentik` database in `postgres-db`), which requires a TOTP code from Google Authenticator or any other TOTP app on every login. Open WebUI's own email/password sign-in is turned off.

- authentik is served by `open-webui-proxy` at `https://<OPEN_WEBUI_HOST>:9443` (same Caddy CA as Open WebUI on 8444). Open WebUI shows a **Continue with authentik** button.
- Its configuration is the blueprint [authentik/blueprints/open-webui-sso.yaml](authentik/blueprints/open-webui-sso.yaml), applied by the worker on start and whenever the file changes. It makes MFA mandatory in authentik's default login flow (users without a TOTP device get a QR code to set one up before their first login completes; TOTP and static recovery codes only) and registers the Open WebUI OIDC client.
- Open WebUI calls authentik server-side at the same `https://<OPEN_WEBUI_HOST>:9443` URL browsers use, so issuer and endpoint URLs match. It trusts Caddy's CA through `caddy-ca-export`, a one-shot service that copies only Caddy's public root cert (never the CA key) into the `caddy-ca-public` volume.
- Anyone can sign up: Open WebUI's sign-in page has a **Sign up** button under **Continue with authentik** (added by [devops/open-webui/static/loader.js](devops/open-webui/static/loader.js); recreate `open-webui` after editing it), and authentik's own login page has a **Sign up** link. Both go to the `self-enrollment` flow ([authentik/blueprints/self-enrollment.yaml](authentik/blueprints/self-enrollment.yaml)); afterwards authentik sends the user on to Open WebUI. It asks for name, email and password only (the email doubles as the authentik username), refuses an email that's already registered, and creates the account **inactive** until the emailed confirmation link (valid 30 minutes) is opened. Only then does it continue to TOTP setup and login. Confirming the email matters because of the account linking below. Admins can also invite people (see [Inviting users](#inviting-users)). The first SSO login links an existing Open WebUI account with the same email (`OAUTH_MERGE_ACCOUNTS_BY_EMAIL`). A new email gets a new Open WebUI account in the default role (`pending`) and is sent the signup verification email; clicking its link promotes the account to `user` (an admin can also approve it under **Admin → Users**).

### First-time setup

1. Add the authentik variables to `.env` (see `.env.example`), e.g.
   ```bash
   echo "AUTHENTIK_SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n')" >> .env
   echo "OPEN_WEBUI_OIDC_CLIENT_ID=$(openssl rand -hex 20)" >> .env
   echo "OPEN_WEBUI_OIDC_CLIENT_SECRET=$(openssl rand -base64 60 | tr -d '\n')" >> .env
   ```
   plus `AUTHENTIK_BOOTSTRAP_EMAIL` / `AUTHENTIK_BOOTSTRAP_PASSWORD`. Use your Open WebUI admin email as `AUTHENTIK_BOOTSTRAP_EMAIL`: then `akadmin` signs in to Open WebUI as the existing admin.
2. Let containers reach the host's published port 9443. A host firewall (ufw by default denies incoming) blocks container → host traffic, and Open WebUI's server-side OIDC calls go that way:
   ```bash
   sudo ufw allow from 172.16.0.0/12 to any port 9443 proto tcp comment 'open-webui -> authentik'
   ```
3. `docker compose -f devops/docker-compose.yml up -d`. From here, Open WebUI password sign-in is rejected (`ENABLE_PASSWORD_AUTH=false`).
4. Open `https://<OPEN_WEBUI_HOST>:9443`, sign in as `akadmin`, and scan the QR code with Google Authenticator when asked.
5. Invite each existing Open WebUI user (see [Inviting users](#inviting-users)) with their **same email**, so their accounts get linked. They choose their own password and set up TOTP while signing up.
6. Turn off Open WebUI's login form. `ENABLE_LOGIN_FORM=false` in compose is ignored because Open WebUI has already saved `ui.enable_login_form` in its database, and while that form is on, `/api/v1/auths/signup` still accepts password signups. Delete the saved value so the compose setting applies, then restart:
   ```bash
   docker exec laptop-postgres psql -U "$POSTGRES_USER" -d open_webui -c "DELETE FROM config WHERE key = 'ui.enable_login_form'"
   docker compose -f devops/docker-compose.yml restart open-webui
   ```

**Rollback:** set `ENABLE_PASSWORD_AUTH=true` and `ENABLE_LOGIN_FORM=true` on `open-webui` and run `up -d` again. If authentik is down, nobody can sign in to Open WebUI, and the rollback is how you get back in.

**Lost authenticator:** in authentik, open the user under **Directory → Users → MFA Authenticators** and delete the TOTP device. The user enrolls a new one on their next login.

### Inviting users

Besides self sign-up, you can send new users a one-time invite link ([authentik/blueprints/invitations.yaml](authentik/blueprints/invitations.yaml)):

1. In authentik's admin UI (`https://<OPEN_WEBUI_HOST>:9443`), go to **Directory → Invitations → Create**.
2. Fill in:
   - **Name**: e.g. `invite-jatin`
   - **Flow**: `invitation-enrollment`
   - **Expires**: a date a few days out
   - **Single use**: on
   - **Custom attributes**: `{"email": "their@email.com"}`. Use the email of their existing Open WebUI account, if they have one, so the accounts get linked.
3. Open the new invitation's row, copy the **link**, and send it to them.
4. They open the link, choose a username, name and password (the [password rules](#password-rules) apply), and scan a QR code with Google Authenticator. That creates and signs in their authentik account.
5. They click **Continue with authentik** on Open WebUI. People with an existing Open WebUI account (same email) go straight in. New people get a **pending** account, which you approve under **Admin → Users**.

Links without a valid invitation are refused ("Invalid invite/invite not found"). Delete an unused invitation to revoke it. Don't open a link yourself to check it: opening it uses up a single-use invitation.

To see how far each person has got, run `devops/open-webui/sync-user-status.sh`. It records each person's stage (not invited → invited → authentik account → MFA set up → linked to Open WebUI) and when they reached it in the `fn_user_sso_status` table (see [Function tables](#function-tables)), prints that table, and shows the stage as each Open WebUI user's profile status (e.g. "📨 SSO: invited"). It only replaces a profile status that's empty or one it wrote itself. Profile statuses are visible to other users and editable by their owner; the table is admin-only. It's a snapshot, so re-run it after inviting or onboarding someone.

### Password rules

New passwords must be **at least 8 characters, with an uppercase letter, a lowercase letter, a number and a symbol**.

- **authentik** ([authentik/blueprints/password-policy.yaml](authentik/blueprints/password-policy.yaml)) also rejects passwords found in known data breaches (Have I Been Pwned; only the first 5 characters of the password's SHA-1 hash leave the server). Its zxcvbn strength check is off, since it rejected random 8-character passwords that meet the rules. The rules apply when users sign up through an invite and when they change their own password. Passwords an admin sets under **Directory → Users → Set password** are not checked, so follow the rules there by hand.
- **Open WebUI** checks the same length and character rules (`ENABLE_PASSWORD_VALIDATION` / `PASSWORD_VALIDATION_REGEX_PATTERN` in compose) on signup, password change, admin create/edit and the Password Reset Function. With SSO on, these only matter if password sign-in is re-enabled.
- Existing passwords aren't affected until they're next changed.
- **After upgrading authentik**, re-apply the blueprint: authentik re-applies its own default password-change blueprint when it changes, which resets the policy to its default (8 characters, no character rules). Use **Customization → Blueprints → laptop-ai-backend - Password creation rules → Apply**, or:
  ```bash
  docker exec laptop-authentik-worker ak apply_blueprint /blueprints/custom/password-policy.yaml
  ```
- To change the rules, edit both places and keep them in sync. authentik picks up blueprint edits by itself; Open WebUI needs `docker compose -f devops/docker-compose.yml up -d open-webui`.

## Open WebUI signup verification

New Open WebUI signups start as `pending` (`DEFAULT_USER_ROLE=pending`) and are emailed a verification link; confirming it promotes them to `user`. This is an Open WebUI event Function, [devops/open-webui/functions/signup_email_verification.py](devops/open-webui/functions/signup_email_verification.py) (Open WebUI declined adding it to core — [discussion #31626](https://github.com/open-webui/open-webui/discussions/31626)).

One-time setup, after the stack is up:

1. In Open WebUI, go to **Admin → Functions → Import**, choose that file, save, and switch it on.
2. Open the Function's settings (Valves) and set `base_url` to the address users open Open WebUI at — `https://<OPEN_WEBUI_HOST>:8444` for LAN users (default `http://localhost:8082`). Leave the SMTP fields empty to use `SMTP_USER`/`SMTP_PASSWORD` from compose.
3. Restart once so it registers its routes: `docker restart laptop-open-webui`, then check `docker logs laptop-open-webui 2>&1 | grep "verification routes"`.
4. Check **Admin → Settings → General → Default User Role** is `pending` — a value saved there overrides the env var.

How it behaves:

- Links expire after 2 hours (`token_max_age_seconds` Valve). The token sits in the link's `#` fragment and the page asks the user to click **Verify email**, so tokens don't reach server logs and mail scanners that open links don't verify accounts.
- Pending users see a "Check your email" screen (`PENDING_USER_OVERLAY_TITLE`/`PENDING_USER_OVERLAY_CONTENT` in compose) linking to `/api/v1/auths/verify-email/resend`, a page where they can request a new link (rate-limited, same response whether or not the account exists). If **Admin → Settings → Authentication** already has saved overlay text, that wins over the compose values — clear it or paste the same text there.
- Each account is verified at most once (recorded in the `fn_email_verified` table — see [Function tables](#function-tables)), so an admin can suspend a user by setting them back to `pending` without an old link or a resend re-activating them.
- The Function is stored in Open WebUI's database, not read from the repo — re-import it after editing the file.
- The `open-webui` image is pinned by digest because the Function uses Open WebUI internals. When bumping it, re-test a signup end to end.

### Password reset

Open WebUI has no "forgot password" of its own. A second, independent event Function, [devops/open-webui/functions/password_reset.py](devops/open-webui/functions/password_reset.py), adds it — import and enable it the same way, set its `base_url` Valve, and restart once.

- Users go to `/api/v1/auths/forgot-password` (Open WebUI's login page can't be customised to link it, so share or bookmark the URL), enter their email, and get a reset link that expires in 30 minutes and works once — any password change invalidates older links. Pending and deactivated accounts can't reset.
- Whenever a password changes (by the user, an admin, or a reset), the user gets a notice email with the forgot-password link, so an unexpected change doesn't go unnoticed. Turn off with the `notify_on_password_change` Valve.
- A reset only logs out the user's other sessions if Open WebUI has Redis configured; this stack doesn't, so existing sessions stay valid until they expire (`JWT_EXPIRES_IN`, default 4 weeks).

### Password expiry

A third event Function, [devops/open-webui/functions/password_expiry.py](devops/open-webui/functions/password_expiry.py), makes passwords expire after 180 days (`max_age_days` Valve). It needs the Password Reset Function, since expired users recover through the forgot-password page (or an admin sets a new password). NIST SP 800-63B advises against forced periodic changes; enable this only if a policy requires it.

- Open WebUI doesn't record password age, so the Function keeps its own record in the `fn_password_age` table (see [Function tables](#function-tables)), reset on signup and on every password change. Existing users (no record yet) are counted from their account creation date — Open WebUI's `user.created_at`, since it doesn't record password changes — but always get at least 14 days, with reminders, from when the Function first sees them (`grace_days_for_existing`), so enabling it doesn't lock out old accounts. Turn off `start_from_account_creation` to count from that first sighting instead.
- An expired user who enters the correct password gets an error on the login page saying the password has expired, and is **emailed a one-time link to set a new one** (the Password Reset Function's page; valid 30 minutes, at most one email per 10 minutes). Open WebUI's login page can't be redirected, so the email is the way forward. If the Password Reset Function isn't enabled, the message tells them to ask an admin instead. A wrong password gets Open WebUI's normal error, so nothing leaks about whether an account exists.
- Users signing in within 14 days of expiry (`warn_days`) get a reminder email, at most once a day.
- Admin passwords never expire (`exempt_admins`, on by default).
- **Overview:** while signed in to Open WebUI as an admin, open `/api/v1/auths/password-expiry` (e.g. http://localhost:8082/api/v1/auths/password-expiry) for every user's password date, expiry date, days left and status, soonest first; add `?format=json` for JSON. Dates marked * are estimated for users the Function hasn't recorded yet, and viewing the page doesn't start anyone's clock.
- The 180 days and other settings are the Function's Valves (**Admin → Functions → Password Expiry → ⚙**), stored in Open WebUI's database — not environment variables.
- Only the email/password sign-in is checked (not LDAP, OAuth or API keys), and already-signed-in sessions last until they expire.

### Function tables

The Functions (and the SSO status script) keep their state in Postgres, in Open WebUI's `open_webui` database, so it's backed up with the rest of Open WebUI's data:

| Table | Used by | Holds |
|---|---|---|
| `fn_email_verified` | Signup Email Verification | Users verified once (`user_id`, `verified_at`) |
| `fn_password_age` | Password Expiry | When each password was last set, and the last reminder (`user_id`, `changed_at`, `last_warned_at`) |
| `fn_user_sso_status` | `devops/open-webui/sync-user-status.sh` (not a Function) | Each person's SSO onboarding stage and when they reached each step, keyed by lowercased email (see [Inviting users](#inviting-users)) |

They're created by a separate Alembic setup, [devops/open-webui/migrations/](devops/open-webui/migrations/), which tracks its history in `fn_alembic_version` so it never touches Open WebUI's own migrations in the same database. The one-shot `open-webui-fn-migrate` service applies it on every `docker compose up` (a no-op once current), and `open-webui` waits for it. Its first revision also imports rows from the Functions' earlier SQLite files (`email_verification.db` / `password_expiry.db` on the `open-webui-data` volume), if present. Once that's done, those files are unused and can be deleted.

To add a table or column, add a new revision under `devops/open-webui/migrations/versions/` (the Functions don't create tables themselves).

## Custom Backend

Located in [custom-backend/](custom-backend/). A FastAPI app using SQLAlchemy async ORM with `asyncpg`.

Layout: `main.py` (app setup), `db.py` (OpenBao-backed engine), `models.py`, `schemas.py`, `chat_service.py` (LangChain + Milvus), and `routers/auth.py` / `routers/chats.py`.

Endpoints:
- `GET /health` — health check
- `/ui` — minimal static chat page for exercising the chat/RAG endpoints by hand
- Users & auth (`routers/auth.py`): `POST /users` (register), `GET /users`, `POST /users/verify`, `POST /users/deactivate`, `POST /users/reactivate`, `POST /login`, `POST /mfa/enable`, `POST /mfa/disable`, `POST /mfa/verify-otp`, `POST /password/change`, `POST /password/forgot`, `POST /password/reset`
- Chats & RAG (`routers/chats.py`): `POST /chats`, `GET /chats/{chat_id}/messages`, `POST /chats/{chat_id}/messages`, `POST /chats/{chat_id}/documents` (ingest up to 5 documents per request into Milvus)

Run locally without Docker (skips migrations; needs `OPENBAO_ADDR`, `OPENBAO_ROLE_ID` and `OPENBAO_SECRET_ID` exported):

```bash
cd custom-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### Database schema / migrations

[database/init_db.py](database/init_db.py) defines the schema (`users`, `user_chats`, `messages`) that [database/alembic/](database/alembic/) migrations track. `custom-backend`'s container entrypoint runs `alembic upgrade head` on every start, so no manual step is needed under Docker. To apply migrations from the host instead (uses `INIT_DB_DATABASE_URL` against the mapped port `5433`):

```bash
export $(grep -v '^#' .env | xargs)
cd database && alembic upgrade head
```

## RAG embeddings

Documents are embedded with Ollama's **`embeddinggemma`** (Google, 768-dim, 2048-token context) in both Open WebUI and `custom-backend`. It replaced `nomic-embed-text` for better retrieval: in a quick test it separated the right chunk from the runner-up about 3× more clearly. Both apps send embeddinggemma's task prefixes (`task: search result | query: ` on questions, `title: none | text: ` on documents), which the model is trained with.

- Pull the model before first use: `docker exec laptop-ollama ollama pull embeddinggemma`.
- **Open WebUI** saves the embedding engine/model in its database the first time they're set, and the saved values override the compose file. Change them under **Admin → Settings → Documents**. After changing the model, click **Reindex Knowledge Base Vectors** there, since old vectors don't match the new model.
- **custom-backend** keeps one Milvus collection per model (`custom_backend_docs_<model>`), so switching `EMBEDDING_MODEL` starts an empty collection. Re-upload documents to chats after a switch.
- To change the model, update `RAG_EMBEDDING_MODEL` (open-webui) and `EMBEDDING_MODEL` (custom-backend) in compose together, plus the prefixes: the query/document prefixes in compose and `_PREFIXES` in [custom-backend/chat_service.py](custom-backend/chat_service.py). Other models need different prefixes, or none.

## Web search

Open WebUI can search the web through **SearXNG** (`searxng` service, [devops/searxng/settings.yml](devops/searxng/settings.yml)), a self-hosted metasearch engine that queries Google, Bing, Brave, DuckDuckGo and others without API keys or accounts. It's only reachable inside the Docker network.

- In a chat, click **Web Search** (the globe icon under the message box) for questions that need current information. Open WebUI searches, fetches the top 3 result pages (`WEB_SEARCH_RESULT_COUNT`), picks the relevant parts with the embedding model, and answers from them with sources. It works with any model.
- Searches leave your machine through SearXNG, so each engine sees your IP but not who asked.
- Enable/engine/URL are saved Open WebUI settings (`web.search.*`), which override the compose values once saved. Change them under **Admin → Settings → Web Search**.
- `SEARXNG_SECRET` in `.env` signs SearXNG's cookies. Generate it with `openssl rand -hex 32`.
- Harmless startup errors: SearXNG logs `ahmia`/`torch` "can't register engine" (Tor-only engines) and a missing `limiter.toml` (the limiter is off, since the service is internal).

## Open WebUI audit log in OpenObserve

Open WebUI's regular logs (stream `openwebui_backend`) are mostly web-server request lines with the client IP but **no user**. To see who did what, Open WebUI's audit log is on (`AUDIT_LOG_LEVEL=METADATA` in compose) and shipped to O2 stream **`openwebui_audit`** by `openwebui-audit-shipper`, an OpenTelemetry Collector ([devops/otel-collector/openwebui-audit.yaml](devops/otel-collector/openwebui-audit.yaml)). Open WebUI only writes audit entries to `data/audit.log`, never over OTel, so the collector tails that file from the `open-webui-data` volume (read-only).

- Each entry's message reads `<email> <METHOD> <URL> <status>`, e.g. `alice@example.com POST http://localhost:8082/api/v1/users/update 200`, and has searchable fields `user_email`, `user_name`, `user_role`, `user_id`, `verb`, `request_uri`, `response_status_code`, `source_ip` and `user_agent`. Example O2 query: `SELECT * FROM "openwebui_audit" WHERE user_email = 'alice@example.com'`.
- `METADATA` records no request or response bodies. Don't raise it to `REQUEST`: that would log sign-in passwords and chat content.
- Only POST/PUT/PATCH/DELETE requests are audited, and `/chats`, `/chat` and `/folders` are skipped (`AUDIT_EXCLUDED_PATHS`), so chatting itself isn't logged. Set `ENABLE_AUDIT_GET_REQUESTS=true` to include reads, at the cost of much more volume.
- Requests without a signed-in user, such as a password sign-in attempt, show `-` in place of the email.
- The collector keeps its read position in the `otelcol-audit-storage` volume, so restarts don't re-send entries. On its first start it ships whatever `audit.log` already holds. Open WebUI rotates the file at 10 MB (zipped copies stay in `data/`).

## authentik events in OpenObserve

authentik's audit events go to O2 stream **`authentik_events`**, next to Open WebUI's `openwebui_audit`. Covered: logins, failed logins, logouts, password changes, MFA setup, Open WebUI authorizations and admin changes.

- How: `authentik-server`/`authentik-worker` use Docker's `fluentd` logging driver to send their output to `authentik-events-shipper`, an OpenTelemetry Collector ([devops/otel-collector/authentik-events.yaml](devops/otel-collector/authentik-events.yaml)). It keeps only the event lines (authentik logs each event as JSON with `"event": "Created Event"`) and drops the per-request access logs.
- Each entry's message reads `<username> <action> <IP>`, e.g. `akadmin login_failed 192.168.29.119`. Fields: `action`, `user_username`, `user_email`, `client_ip`, and `context` (a JSON string with the stage, request details, etc.). Example: `SELECT * FROM "authentik_events" WHERE action = 'login_failed'`.
- The driver is async, so authentik starts even when the shipper is down (events from that time are lost), and `docker logs` still works through Docker's local cache. The shipper's port 24224 is published on 127.0.0.1 only, because the Docker daemon on the host is what connects to it.
- authentik also keeps its own event log (**Events → Logs** in the admin UI, 1 year by default).

## Known gaps / TODO

- `custom-backend/models.py` and `database/init_db.py` declare the same models independently — keep them in sync by hand when changing either.
- Only Open WebUI's logs reach OpenObserve (stream `openwebui_backend`; its traces and metrics go to `lgtm`), and they're sent with the O2 root credentials — a dedicated ingestion-only user would be safer.
