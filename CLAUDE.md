# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personal laptop AI dev stack, orchestrated via `docker-compose.yml`: PostgreSQL, Ollama, Open WebUI, Grafana LGTM observability, and a custom FastAPI backend (`custom-backend/`).

## Structure

- `custom-backend/main.py` — FastAPI app: DB engine setup, SQLAlchemy models, Pydantic schemas, routes, all in one file.
- `database/init_db.py` — standalone schema/migration script (also defines `UserModel`, plus `UserChatModel` and `MessageModel`, which `main.py` does not yet use).
- `docker-compose.yml` — defines all 5 services and the shared `ai-network`.

## Known inconsistencies (don't silently "fix" without asking)

- `main.py` and `database/init_db.py` both declare `UserModel` independently (duplicate source of truth) — `init_db.py` has the fuller schema (`user_chats`, `messages`) that `main.py`'s API doesn't expose yet.
- Passwords are stored as plaintext in `password_hash` — this is a known gap, not an oversight to preserve; hashing should use `passlib`/`bcrypt` if implementing auth.

## Conventions observed in this repo

- Async SQLAlchemy (`asyncpg` driver) throughout.
- FastAPI with `ORJSONResponse` as the default response class.
- Single-file backend so far — no routers/services split yet. Don't introduce that structure unless the app actually grows to need it.

## Configuration

All credentials/secrets/host paths come from environment variables — none are hardcoded in `docker-compose.yml`, `main.py`, or `init_db.py`. `.env` (gitignored, see `.env.example`) holds `OLLAMA_MODELS_PATH` (host path mounted into the `ollama` container), `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `WEBUI_SECRET_KEY`, `DATABASE_URL` (for `custom-backend`, via the Docker network on port 5432), and `INIT_DB_DATABASE_URL` (for running `init_db.py` from the host against the mapped port 5433).

## Running things

```bash
docker compose up -d              # full stack (reads ./.env automatically)
cd custom-backend && uvicorn main:app --reload --port 8001   # backend only (needs DATABASE_URL exported)
export $(grep -v '^#' .env | xargs) && python database/init_db.py   # apply schema to Postgres
```
