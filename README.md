# Laptop AI Backend

A local, hybrid AI development stack for a personal laptop: a custom FastAPI backend backed by PostgreSQL, alongside Ollama for local model inference, Open WebUI as a chat frontend, and Grafana LGTM for observability.

## Stack

| Service | Purpose | Port |
|---|---|---|
| `postgres-db` | PostgreSQL 16 database | 5433 → 5432 |
| `ollama` | Local LLM inference engine | (internal) |
| `open-webui` | Chat UI, talks to Ollama | 8082 → 8080 |
| `lgtm` | Grafana + Loki + Tempo + Mimir (metrics/traces/logs) | 3001 (UI), 4317/4318 (OTLP) |
| `custom-backend` | FastAPI app with user management | 8001 |

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
| `DATABASE_URL` | `custom-backend` | Full async connection string (via Docker network, `postgres-db:5432`) |
| `INIT_DB_DATABASE_URL` | `database/init_db.py` | Full async connection string for running migrations from the host (mapped port `5433`) |

## Running

```bash
docker compose up -d
```

- Open WebUI: http://localhost:8082
- Grafana: http://localhost:3001
- Custom backend API: http://localhost:8001
- Postgres: `localhost:5433` (credentials from `.env`)

## Custom Backend

Located in [custom-backend/](custom-backend/). A FastAPI app using SQLAlchemy async ORM with `asyncpg`.

Endpoints:
- `GET /health` — health check
- `POST /users` — register a user
- `GET /users` — list all users

Run locally without Docker:

```bash
cd custom-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### Database schema / migrations

[database/init_db.py](database/init_db.py) defines the full schema (`users`, `user_chats`, `messages`) and can be run directly against the Postgres container to create tables and indexes. It reads `INIT_DB_DATABASE_URL` from the environment, so export your `.env` first:

```bash
export $(grep -v '^#' .env | xargs)
python database/init_db.py
```

Note: `main.py` currently only registers the `UserModel` table; `user_chats` and `messages` live in `init_db.py` and aren't yet wired into the API.

## Known gaps / TODO

- Passwords are stored in plaintext (`password_hash` = raw password) — needs real hashing (e.g. `passlib`/`bcrypt`) before any real use.
