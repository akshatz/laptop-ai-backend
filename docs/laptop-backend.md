# Custom backend

## Custom Backend

Located in [laptop-backend/](../laptop-backend/). A FastAPI app using SQLAlchemy async ORM with `asyncpg`.

Layout: `main.py` (app setup), `db.py` (OpenBao-backed engine), `models.py`, `schemas.py`, `chat_service.py` (LangChain + Milvus), `sessions.py` (signed session cookie, `current_user`), and `routers/auth.py` / `routers/chats.py` / `routers/sso.py`. API only: the chat UI is the separate [laptop-frontend](laptop-frontend.md) app.

Endpoints:
- `GET /health` — health check
- `GET /models` — the `CHAT_MODELS` (default `llama3.2,fast-ai,gemma3:4b`) that are pulled into Ollama, plus the default, for laptop-frontend's model dropdown; 502 when Ollama is down
- Sign-in through authentik (`routers/sso.py`): `GET /auth/login` (redirect to authentik, OIDC code flow + PKCE), `GET /auth/callback` (code exchange and userinfo over `AUTHENTIK_INTERNAL_URL`, user matched by email or created, session cookie set), `GET /auth/me`, `POST /auth/logout` (returns authentik's invalidation URL so the browser ends that session too). The callback URL is built from the request's `Host` and must be one of `FRONTEND_ORIGINS`. The OIDC client and the session key are `custom_frontend_oidc_client_id`/`_secret` and `session_secret` in OpenBao's `apps/default/custom-backend` (environment variables as fallback). The session is an HttpOnly, SameSite=Lax cookie signed with HMAC-SHA256, valid 7 days (`SESSION_MAX_AGE_SECONDS`).
- Users & password auth (`routers/auth.py`, older and not used by laptop-frontend; none of these sign you in for the chat endpoints): `POST /users` (register), `GET /users`, `POST /users/verify`, `POST /users/deactivate`, `POST /users/reactivate`, `POST /login`, `POST /mfa/enable`, `POST /mfa/disable`, `POST /mfa/verify-otp`, `POST /password/change`, `POST /password/forgot`, `POST /password/reset`
- Chats & RAG (`routers/chats.py`, all need the session; the user comes from it, never from the request body, and someone else's chat is a 404): `GET /chats` (your chats, most recently used first; a chat still called "New Chat" is renamed after its first question), `POST /chats`, `GET /chats/{chat_id}/messages`, `POST /chats/{chat_id}/messages`, `POST /chats/{chat_id}/documents` (ingest up to 5 documents per request into Milvus)
- Documents: `POST /chats/{chat_id}/files` (multipart `files`, up to 5, 10 MB each; text from PDF via pypdf, .docx via python-docx, or text files, `file_text.py`; pictures (.png/.jpg/.jpeg/.webp/.gif) are read in the background task by Tesseract OCR (installed in the image, English) plus a description from the vision model `IMAGE_MODEL` (default `gemma3:4b`, ~30–50 s on CPU; the prompt asks it to copy labels exactly and not guess versions), stored together as the document's text, either one alone if the other fails; every file is checked before any is added), `GET /documents` (your own, newest first, without contents, with each file's `sha256`), `GET /documents/{id}/file` (the original, inline; HTML/SVG/XML served as plain text, `nosniff`, CSP `sandbox`; someone else's is a 404). `DELETE /documents/{id}` (your own, or anyone's for admins; removes its chunks from Milvus first when it's the live copy and re-embeds other live documents with the same name in that chat, since chunks carry only `chat_id` + `source`; 409 while embedding, 502 and nothing deleted if Milvus fails). `DELETE /chats/{id}` (your own; removes the chat's chunks). Both are soft deletes (`deleted_at` on `user_chats`/`chat_documents`, migration `d6f1a8c3e527`): deleted rows are hidden from every list and lookup, `GET /trash` lists your own (documents only when deleted on their own) and first removes for good those deleted more than `TRASH_DAYS` (30) ago, and `POST /chats/{id}/restore` / `POST /documents/{id}/restore` bring them back, re-embedding the stored originals in the background (a document with a newer live version comes back `replaced`; one whose chat is deleted is a 409). Both upload routes embed into Milvus and keep the original in `chat_documents` (migration `a7c3e9f2b416`; pasted texts become `Pasted text N.txt`); if Milvus fails, nothing is kept. laptop-frontend's nginx allows 60 MB request bodies.
- Web search (`web_search.py`), per question: the model (`WEB_SEARCH_DECISION_MODEL`, default the default chat model) is asked WEB or NO with temperature 0 and 3 tokens (~1 s on CPU; 10/10 right on a quick check); WEB queries SearXNG (`SEARXNG_URL`, default `http://searxng:8080/search`) and the top `WEB_SEARCH_RESULTS` (5) snippets become numbered sources in the context. The answer returned by `POST /chats/{id}/messages` carries `web_searched` and `web_sources` (not stored). `WEB_SEARCH=always`/`off` skips the decision; any error means no web search. The system prompt also gives today's date.

Run locally without Docker (skips migrations; needs `OPENBAO_ADDR`, `OPENBAO_ROLE_ID` and `OPENBAO_SECRET_ID` exported):

```bash
cd laptop-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### Database schema / migrations

[database/init_db.py](../database/init_db.py) defines the schema (`users`, `user_chats`, `messages`) that [database/alembic/](../database/alembic/) migrations track. `laptop-backend`'s container entrypoint runs `alembic upgrade head` on every start, so no manual step is needed under Docker. To apply migrations from the host instead (uses `INIT_DB_DATABASE_URL` against the mapped port `5433`):

```bash
export $(grep -v '^#' .env | xargs)
cd database && alembic upgrade head
```

## Paid cloud models (admins, or everyone)

Besides the Ollama models in `CHAT_MODELS`, admins (`users.role = 'admin'`) can pick paid OpenRouter models in laptop-frontend's model dropdown, shown as "<name> (paid)". Their ids are `openrouter/<OpenRouter id>`; `CLOUD_MODELS` lists them (default `anthropic/claude-haiku-5.5,anthropic/claude-sonnet-5.5,openai/gpt-5.4-mini`; set `CLOUD_MODELS` in `.env` to change it, then `docker compose -f devops/docker-compose.yml up -d laptop-backend`). The key is `openrouter_api_key` in OpenBao's `apps/default/custom-backend` (`bootstrap.sh` writes it when `OPENROUTER_API_KEY` is set in `.env`; `OPENROUTER_API_KEY` in laptop-backend's environment is the fallback). Without a key no cloud model is offered. `CLOUD_MODELS_FOR=all` in `.env` offers them to every signed-in user instead (keep a credit limit on the key). `/models` lists them only for those users, and sending or regenerating with one is a 403 for anyone else (also for a chat created with a cloud `model_id`). The WEB/NO web-search decision still runs on the local model. To add only the key without re-running all of `bootstrap.sh`:

```bash
bao kv patch apps/default/custom-backend openrouter_api_key="$OPENROUTER_API_KEY"
```

then restart `laptop-backend` (secrets are read at start). See also [cloud-models.md](cloud-models.md).
