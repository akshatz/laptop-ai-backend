# Custom backend

## Custom Backend

Located in [custom-backend/](../custom-backend/). A FastAPI app using SQLAlchemy async ORM with `asyncpg`.

Layout: `main.py` (app setup), `db.py` (OpenBao-backed engine), `models.py`, `schemas.py`, `chat_service.py` (LangChain + Milvus), `sessions.py` (signed session cookie, `current_user`), and `routers/auth.py` / `routers/chats.py` / `routers/sso.py`. API only: the chat UI is the separate [custom-frontend](custom-frontend.md) app.

Endpoints:
- `GET /health` — health check
- `GET /models` — the `CHAT_MODELS` (default `llama3.2,fast-ai,gemma3:4b`) that are pulled into Ollama, plus the default, for custom-frontend's model dropdown; 502 when Ollama is down
- Sign-in through authentik (`routers/sso.py`): `GET /auth/login` (redirect to authentik, OIDC code flow + PKCE), `GET /auth/callback` (code exchange and userinfo over `AUTHENTIK_INTERNAL_URL`, user matched by email or created, session cookie set), `GET /auth/me`, `POST /auth/logout` (returns authentik's invalidation URL so the browser ends that session too). The callback URL is built from the request's `Host` and must be one of `FRONTEND_ORIGINS`. The OIDC client and the session key are `custom_frontend_oidc_client_id`/`_secret` and `session_secret` in OpenBao's `apps/default/custom-backend` (environment variables as fallback). The session is an HttpOnly, SameSite=Lax cookie signed with HMAC-SHA256, valid 7 days (`SESSION_MAX_AGE_SECONDS`).
- Users & password auth (`routers/auth.py`, older and not used by custom-frontend; none of these sign you in for the chat endpoints): `POST /users` (register), `GET /users`, `POST /users/verify`, `POST /users/deactivate`, `POST /users/reactivate`, `POST /login`, `POST /mfa/enable`, `POST /mfa/disable`, `POST /mfa/verify-otp`, `POST /password/change`, `POST /password/forgot`, `POST /password/reset`
- Chats & RAG (`routers/chats.py`, all need the session; the user comes from it, never from the request body, and someone else's chat is a 404): `GET /chats` (your chats), `POST /chats`, `GET /chats/{chat_id}/messages`, `POST /chats/{chat_id}/messages`, `POST /chats/{chat_id}/documents` (ingest up to 5 documents per request into Milvus)

Run locally without Docker (skips migrations; needs `OPENBAO_ADDR`, `OPENBAO_ROLE_ID` and `OPENBAO_SECRET_ID` exported):

```bash
cd custom-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### Database schema / migrations

[database/init_db.py](../database/init_db.py) defines the schema (`users`, `user_chats`, `messages`) that [database/alembic/](../database/alembic/) migrations track. `custom-backend`'s container entrypoint runs `alembic upgrade head` on every start, so no manual step is needed under Docker. To apply migrations from the host instead (uses `INIT_DB_DATABASE_URL` against the mapped port `5433`):

```bash
export $(grep -v '^#' .env | xargs)
cd database && alembic upgrade head
```
