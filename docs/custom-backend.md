# Custom backend

## Custom Backend

Located in [custom-backend/](../custom-backend/). A FastAPI app using SQLAlchemy async ORM with `asyncpg`.

Layout: `main.py` (app setup), `db.py` (OpenBao-backed engine), `models.py`, `schemas.py`, `chat_service.py` (LangChain + Milvus), `sessions.py` (signed session cookie, `current_user`), and `routers/auth.py` / `routers/chats.py` / `routers/sso.py`. API only: the chat UI is the separate [custom-frontend](custom-frontend.md) app.

Endpoints:
- `GET /health` — health check
- `GET /models` — the `CHAT_MODELS` (default `llama3.2,fast-ai,gemma3:4b`) that are pulled into Ollama, plus the default, for custom-frontend's model dropdown; 502 when Ollama is down
- Sign-in through authentik (`routers/sso.py`): `GET /auth/login` (redirect to authentik, OIDC code flow + PKCE), `GET /auth/callback` (code exchange and userinfo over `AUTHENTIK_INTERNAL_URL`, user matched by email or created, session cookie set), `GET /auth/me`, `POST /auth/logout` (returns authentik's invalidation URL so the browser ends that session too). The callback URL is built from the request's `Host` and must be one of `FRONTEND_ORIGINS`. The OIDC client and the session key are `custom_frontend_oidc_client_id`/`_secret` and `session_secret` in OpenBao's `apps/default/custom-backend` (environment variables as fallback). The session is an HttpOnly, SameSite=Lax cookie signed with HMAC-SHA256, valid 7 days (`SESSION_MAX_AGE_SECONDS`).
- Users & password auth (`routers/auth.py`, older and not used by custom-frontend; none of these sign you in for the chat endpoints): `POST /users` (register), `GET /users`, `POST /users/verify`, `POST /users/deactivate`, `POST /users/reactivate`, `POST /login`, `POST /mfa/enable`, `POST /mfa/disable`, `POST /mfa/verify-otp`, `POST /password/change`, `POST /password/forgot`, `POST /password/reset`
- Chats & RAG (`routers/chats.py`, all need the session; the user comes from it, never from the request body, and someone else's chat is a 404): `GET /chats` (your chats, most recently used first; a chat still called "New Chat" is renamed after its first question), `POST /chats`, `GET /chats/{chat_id}/messages`, `POST /chats/{chat_id}/messages`, `POST /chats/{chat_id}/documents` (ingest up to 5 documents per request into Milvus)
- Documents: `POST /chats/{chat_id}/files` (multipart `files`, up to 5, 10 MB each; text from PDF via pypdf, .docx via python-docx, or text files, `file_text.py`; every file is checked before any is added), `GET /documents` (your own, newest first, without contents), `GET /documents/{id}/file` (the original, inline; HTML/SVG/XML served as plain text, `nosniff`, CSP `sandbox`; someone else's is a 404). Both upload routes embed into Milvus and keep the original in `chat_documents` (migration `a7c3e9f2b416`; pasted texts become `Pasted text N.txt`); if Milvus fails, nothing is kept. custom-frontend's nginx allows 60 MB request bodies.
- Web search (`web_search.py`), per question: the model (`WEB_SEARCH_DECISION_MODEL`, default the default chat model) is asked WEB or NO with temperature 0 and 3 tokens (~1 s on CPU; 10/10 right on a quick check); WEB queries SearXNG (`SEARXNG_URL`, default `http://searxng:8080/search`) and the top `WEB_SEARCH_RESULTS` (5) snippets become numbered sources in the context. The answer returned by `POST /chats/{id}/messages` carries `web_searched` and `web_sources` (not stored). `WEB_SEARCH=always`/`off` skips the decision; any error means no web search. The system prompt also gives today's date.

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
