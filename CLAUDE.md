# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personal laptop AI dev stack, orchestrated via `devops/docker-compose.yml`: PostgreSQL, Ollama, Open WebUI, Milvus (vector store, with etcd + MinIO dependencies), Grafana LGTM observability, OpenObserve (O2) observability, OpenBao (secrets), Passbolt (password manager), and a custom FastAPI backend (`custom-backend/`).

## Structure

- `custom-backend/main.py` — thin FastAPI app: creates the app, mounts the static `/ui` chat page, includes `routers/auth.py` and `routers/chats.py`.
- `custom-backend/db.py` — fetches Postgres (and other) credentials from OpenBao at import time via AppRole, builds the async SQLAlchemy engine, exposes `get_db()`.
- `custom-backend/models.py` — SQLAlchemy ORM models (`UserModel`, `UserChatModel`, `MessageModel`), duplicating `database/init_db.py`'s schema.
- `custom-backend/schemas.py` — all Pydantic request/response schemas.
- `custom-backend/chat_service.py` — LangChain + Milvus setup: `ChatOllama`-backed RAG chain, the `Milvus` vector store client (embeddings via Ollama's `nomic-embed-text`), and the text splitter used for document ingestion.
- `custom-backend/routers/auth.py` — user registration/login/MFA/password endpoints.
- `custom-backend/routers/chats.py` — `/chats`, `/chats/{chat_id}/messages` (GET+POST), and `/chats/{chat_id}/documents` (RAG document ingestion, capped at 5 documents per request), using `chat_service` for the LangChain/Milvus calls.
- `custom-backend/static/index.html` — minimal single-page UI (no build step) for exercising the chat/RAG endpoints by hand, served at `/ui`.
- `custom-backend/entrypoint.sh` — container entrypoint: resolves the DB URL from OpenBao (via `resolve_db_url.py`), runs `alembic upgrade head` against `database/alembic/`, then execs uvicorn. Runs on every container start.
- `database/init_db.py` — defines the Alembic-tracked schema (`UserModel`, `UserChatModel`, `MessageModel`, duplicating `UserModel` from `main.py`). No longer run directly to create tables; `database/alembic/` migrations (applied by `custom-backend/entrypoint.sh`) are now the source of schema changes. Still reads `INIT_DB_DATABASE_URL` directly, not via OpenBao — `alembic/env.py` uses the same env var.
- `database/alembic/` — Alembic migrations against `init_db.py`'s models. Build context for `custom-backend`'s image is the repo root so this directory can be copied into it.
- `database/init-passbolt-db.sh` — Postgres init script (auto-run on first `postgres-db` start only) that creates the separate `passbolt` database.
- `devops/docker-compose.yml` — defines all services (including `milvus` and its `milvus-etcd`/`milvus-minio` dependencies, which back Open WebUI's RAG vector store) and the shared `ai-network`. Build context for `custom-backend` is `..` (repo root), since it copies `database/alembic/` into the image. Pins `name: laptop-ai-backend` so volumes/network keep stable names regardless of invocation directory — always run with `--project-directory .` from the repo root (see Running things) so `.env` and bind-mount paths resolve correctly.
- OpenObserve (`openobserve` service) — standalone O2 instance running alongside Grafana LGTM, not integrated with it; its web UI/API/OTLP-HTTP ingestion is on port 5080. Nothing currently exports telemetry to it (`open-webui`'s OTEL env vars still point at `lgtm`); point a service's `OTEL_EXPORTER_OTLP_ENDPOINT` at `http://openobserve:5080/api/<org>/` (with the org's auth header) to send it data.
- `openbao/` — OpenBao config (`config.hcl`), one-time setup script (`bootstrap.sh`), post-restart unseal script (`auto-unseal.sh`), backup script (`backup.sh`), and the read-only policy for `custom-backend`'s AppRole. See `openbao/README.md`.
- `milvus/` — `init_auth.py`, the one-shot script the `milvus-init-auth` service runs to rotate Milvus's root password.
- `devops/backups/` — gitignored general-purpose backup output directory (separate from `openbao/backups/`, OpenBao's own backup destination).
- `PASSBOLT.md` — one-time admin registration and usage notes for the `passbolt` service.

## Known inconsistencies (don't silently "fix" without asking)

- `main.py` and `database/init_db.py` both declare `UserModel`/`UserChatModel`/`MessageModel` independently (duplicate source of truth) — keep them in sync by hand when changing either.
- Passwords are hashed with `passlib`/`bcrypt` (`pwd_context` in `main.py`) — not plaintext.
- OpenBao uses file storage with a single unseal key/share (not dev mode, so secrets persist), which means it starts **sealed** after every container restart or host reboot — `custom-backend` will fail to start until `openbao/auto-unseal.sh` is run. This tradeoff was chosen deliberately for a personal laptop stack; don't "fix" it by switching to dev mode without asking, since that would lose secrets on every restart instead.
- `milvus-init-auth` rotates Milvus's root password on every `docker compose up`, not just the first time (Milvus has no env var to set the initial password, and the container doesn't persist whether it already ran) — it fails harmlessly on subsequent runs since `root`/`Milvus` no longer authenticates once rotated. This is a known no-op-after-first-run rather than a bug to "fix" with added state tracking.

## Conventions observed in this repo

- Async SQLAlchemy (`asyncpg` driver) throughout.
- FastAPI with `ORJSONResponse` as the default response class.
- `custom-backend` is split into `db.py`/`models.py`/`schemas.py`/`chat_service.py` plus `routers/` (`auth.py`, `chats.py`) — this split happened once the app grew past a single manageable file (auth + chat + RAG). Keep new endpoints in the router matching their domain rather than growing `main.py` again.

## Configuration

All credentials/secrets/host paths come from environment variables — none are hardcoded in `docker-compose.yml`, `main.py`, or `init_db.py`. `.env` (gitignored, see `.env.example`, both at repo root) holds `OLLAMA_MODELS_PATH` (host path mounted into the `ollama` container), `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `WEBUI_SECRET_KEY`, `INIT_DB_DATABASE_URL` (for running `init_db.py` from the host against the mapped port 5433), `OPENBAO_ROLE_ID`/`OPENBAO_SECRET_ID` (AppRole creds `custom-backend` uses to authenticate to OpenBao — generated by `openbao/bootstrap.sh`, not hand-set), `PASSBOLT_SMTP_USER`/`PASSBOLT_SMTP_PASSWORD`/`PASSBOLT_SMTP_FROM` (Gmail SMTP relay for Passbolt's registration/recovery emails), and `MILVUS_ROOT_PASSWORD` (Milvus root user password — rotated in from Milvus's `root`/`Milvus` default by the one-shot `milvus-init-auth` service on first boot, then used by `open-webui` via `MILVUS_TOKEN` to authenticate). `custom-backend`'s LangChain chat endpoints also read `OLLAMA_BASE_URL` (defaults to `http://ollama:11434`) and `DEFAULT_CHAT_MODEL` (defaults to `llama3.2`, must already be pulled into the `ollama` container).

`custom-backend` no longer reads `DATABASE_URL` from the environment — it builds the connection string at startup from Postgres credentials fetched out of OpenBao's `secret/custom-backend` KV path.

## Running things

```bash
docker compose -f devops/docker-compose.yml --project-directory . up -d   # full stack, run from repo root (so .env and bind-mount paths resolve); custom-backend applies migrations on startup via entrypoint.sh
cd openbao && ./bootstrap.sh           # one-time: init/unseal OpenBao, write secrets, create AppRole (see openbao/README.md)
cd openbao && ./auto-unseal.sh         # after any openbao container restart / host reboot
cd custom-backend && uvicorn main:app --reload --port 8001   # backend only, skips migrations (needs OPENBAO_ADDR/OPENBAO_ROLE_ID/OPENBAO_SECRET_ID exported)
export $(grep -v '^#' .env | xargs) && cd database && alembic upgrade head   # apply schema to Postgres from the host (uses INIT_DB_DATABASE_URL against the mapped port 5433)
```
