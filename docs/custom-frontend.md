# Custom frontend

A React chat UI for [custom-backend](custom-backend.md), kept as its own app in [custom-frontend/](../custom-frontend/) (Vite + React + TypeScript). It shares no code or image with the backend and reaches it only over HTTP.

## Sign-in

Opening the app without a session goes straight to authentik (like Open WebUI's auto-redirect). If you're already signed in to authentik, for example through Open WebUI, there's no login page: you land on the chat page (one sign-in lasts 7 days, see [One sign-in for all apps](sso.md#one-sign-in-for-all-apps)). The app doesn't redirect after a sign-in error (`?signin=`), or a second time within 30 seconds (`sessionStorage` `signin.autoRedirectAt`), so a sign-in that keeps failing doesn't loop. Instead it shows its normal layout with a **Sign in** button at the bottom of the sidebar and a sign-in prompt in the main area. Sign-in uses the same account, password and TOTP as Open WebUI, so there's no separate sign-up here (new people use authentik's sign-up or an invitation, see [sso.md](sso.md)).

1. The redirect (or the button) opens `/api/auth/login`. custom-backend sends the browser to authentik (`https://<OPEN_WEBUI_HOST>:9443`) with PKCE and a state value kept in a short-lived signed cookie.
2. authentik sends the browser back to `/api/auth/callback`. custom-backend exchanges the code with authentik over the Docker network, reads the user's email and name, finds the `users` row with that email or creates one, and sets its session cookie (HttpOnly, signed, 7 days).
3. Every chat endpoint takes the user from that cookie; the request body can't name another user, and other people's chats answer 404.

**Sign out** ends the app's session and then authentik's (its invalidation flow), otherwise the next Sign in would go straight through.

## Dashboard

Full-width, like other chat apps: a sidebar with **New chat**, **Add documents** and **Recent chats** (most recently used first; each chat is named after its first question), and the account at the bottom (initials, name, Sign out; or Sign in when signed out; on phones the sidebar opens from ☰). The conversation is a centred column with a message box at the bottom (Enter sends, Shift+Enter adds a line); the model is picked in the top bar for a new chat and stays fixed for an existing one. New chat only clears the screen: the chat is created with the first message or the first documents, so empty chats don't pile up.

**Reloading keeps your place**: the open screen and chat are in the URL hash (`#/chat/<id>`, `#/documents`, `#/library`, `#/feedback`, `#/sessions`; none = Home), so a reload reopens them, Back/Forward move between them, and a copied link opens that chat for its owner (someone else's chat id, or a deleted one, falls back to Home).

**Home** (sidebar link 🏠 Home, `href="/"`, so it also opens in a new tab; same as New chat): the start page greets you and shows six suggested questions (news, Sensex, weather, your documents, a recipe, a leave application; `SUGGESTIONS` in `ChatScreen.tsx`); one click asks it in a new chat.

**Regenerate** (↻ under the latest answer, with a counter such as 1/3): asks the model again for the same question and replaces the answer in place, clearing its 👍/👎 (`POST /chats/{id}/messages/{id}/regenerate`). custom-backend's `MAX_REGENERATIONS` (default 2, so 3 answers in all like Open WebUI's Regenerate Limit; 0 = no limit, reported by `/models`) caps it with 409; `messages.regenerations` counts them (migration `e5a1c8d3f962`).

**Answers only from sources** (custom-backend's `ANSWER_FROM_SOURCES_ONLY`, default true): every answer comes from the chat's documents or a web search, never from the model's own knowledge. A question the documents don't cover is always searched on the web (no WEB/NO decision); with neither documents nor web results, the reply is "I couldn't find this in your documents or on the web." without asking the model, and the stricter prompt (`SOURCES_ONLY_PROMPT` in `chat_service.py`) tells it to say the same when the sources lack the answer. So poems, translations or greetings get that reply too; `false` restores the earlier behaviour.

**Admin views** (users with `role = 'admin'`): **🗂️ All documents** shows everyone's documents as cards (owner, chat, status, version, size, date; search, status filter, Everyone/Only mine) — others see the same view as **My documents**, own documents only (`GET /documents?all=true`, 403 for non-admins; admins can open any document's file). **🛡️ Signed-in users** lists everyone signed in to custom-frontend with signed-in, last-seen and expiry timestamps, "Active now" (seen in the last 5 minutes) and the device, refreshed every 30 s (`GET /admin/sessions`). Sign-ins are rows in `user_sessions` (migration `f7b3d9e2a184`): the callback creates one and puts its id in the session cookie, requests update `last_seen_at` at most once a minute, sign-out sets `ended_at` and that cookie stops working. Cookies from before the table carry no id and keep working, unlisted, until they expire.

**Questions per chat** (custom-backend's `MAX_QUESTIONS_PER_CHAT`, default 0 = no limit; reported by `/models`): with a limit (e.g. 1, so small models can't mix in earlier topics, but no follow-ups), once a chat reaches it, the message box is replaced by **New chat**, and the backend refuses another question with 409, from any tab or device. While an answer is being written the message box is locked ("Waiting for the answer…").

**Uploads** finish as soon as the files are stored; embedding them into Milvus runs in the background (FastAPI background task, own DB session), and a snackbar at the bottom right (full width on phones) shows each file: uploading → embedding → ready (chunks) or failed (with the error), polling `/documents` every 1.5 s, also when you leave the Documents screen. The same content again in the same chat (sha256) is a duplicate and isn't added; a changed file with the same name is its next version: the old version's chunks are removed from Milvus (by `chat_id` + `source`) and its row becomes `replaced`. Pasted texts are only checked for duplicates. My documents shows status and version.

**Documents**: upload files (choose or drag; PDF with a text layer, Word .docx, or text files; up to 5 at a time, 10 MB each), or paste text, as context for the open chat (or a new one). Their chunks are embedded with `embeddinggemma` into the Milvus collection `custom_backend_docs_embeddinggemma`, tagged with the chat id; the original file is kept in Postgres (`chat_documents`) so **My documents** lists everything you added (only your own) and opens it in the browser. Scanned PDFs without a text layer are refused (no OCR here).

**Web search** happens only when the question needs it: custom-backend asks the model WEB or NO first (`web_search.py`, ~1 s), and only WEB searches SearXNG. The answer then shows its web sources as links under it.

## Running it

`docker compose -f devops/docker-compose.yml up -d --build custom-frontend` builds the app (Node stage: `npm ci`, `tsc`, `vite build`) and serves it with nginx at http://localhost:8012 and `http://<OPEN_WEBUI_HOST>:8012`. nginx forwards `/api/<path>` to `custom-backend:8000/<path>` with the browser's `Host` (port included) and `X-Forwarded-Proto`, from which custom-backend builds the callback URL (see [nginx.conf](../custom-frontend/nginx.conf); 600 s read timeout, since answers from a CPU model can take minutes). Rebuild after changing the app.

The container is limited to 1 GB of memory and half a CPU (`mem_limit`/`cpus` in compose); the limits apply to the running nginx, not to the image build.

### One-time setup for sign-in

1. `.env`: `CUSTOM_FRONTEND_OIDC_CLIENT_ID`, `CUSTOM_FRONTEND_OIDC_CLIENT_SECRET` and `CUSTOM_BACKEND_SESSION_SECRET` (`openssl rand -hex 32` for the two secrets).
2. OpenBao: custom-backend reads all three from `apps/default/custom-backend`. Re-running `openbao/bootstrap.sh` writes them, but it also issues new AppRole secret ids; to add just these keys, as `bao-admin`: `bao kv patch apps/default/custom-backend session_secret=… custom_frontend_oidc_client_id=… custom_frontend_oidc_client_secret=…`.
3. authentik: recreate `authentik-server`/`authentik-worker` so they get the two `CUSTOM_FRONTEND_OIDC_*` variables, then `ak apply_blueprint /blueprints/custom/custom-frontend-sso.yaml` in the worker. It registers the OIDC application with redirect URIs `http://localhost:8012/api/auth/callback` and `http://<OPEN_WEBUI_HOST>:8012/api/auth/callback` (keep in sync with `FRONTEND_ORIGINS` on custom-backend).
4. Rebuild and recreate `custom-backend` and `custom-frontend`.

Plain HTTP on the LAN: the session cookie crosses the network unencrypted, as Open WebUI's does on `:8082`. An HTTPS front through Caddy (like Open WebUI's `:8444`) would fix that.

## Developing

```bash
cd custom-frontend
npm install
npm run dev        # http://localhost:5173, /api → http://localhost:8011 (override with BACKEND_URL)
npm run build      # type-check + production build into dist/
```

Sign-in in dev mode needs `http://localhost:5173` in `FRONTEND_ORIGINS` and in the blueprint's redirect URIs.

Dependencies are pinned to exact versions in `package.json`, with `package-lock.json` committed. CI's Build check builds the image (`frontend-build` job), which fails on type errors.
