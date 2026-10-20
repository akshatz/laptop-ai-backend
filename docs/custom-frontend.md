# Custom frontend

A React chat UI for [custom-backend](custom-backend.md), kept as its own app in [custom-frontend/](../custom-frontend/) (Vite + React + TypeScript). It shares no code or image with the backend and reaches it only over HTTP.

## Sign-in

Without a session the app shows its normal layout with a **Sign in** button at the bottom of the sidebar and a sign-in prompt in the main area. Sign-in uses the same account, password and TOTP as Open WebUI, so there's no separate sign-up here (new people use authentik's sign-up or an invitation, see [sso.md](sso.md)).

1. The button opens `/api/auth/login`. custom-backend sends the browser to authentik (`https://<OPEN_WEBUI_HOST>:9443`) with PKCE and a state value kept in a short-lived signed cookie.
2. authentik sends the browser back to `/api/auth/callback`. custom-backend exchanges the code with authentik over the Docker network, reads the user's email and name, finds the `users` row with that email or creates one, and sets its session cookie (HttpOnly, signed, 7 days).
3. Every chat endpoint takes the user from that cookie; the request body can't name another user, and other people's chats answer 404.

**Sign out** ends the app's session and then authentik's (its invalidation flow), otherwise the next Sign in would go straight through.

## Dashboard

Full-width, like other chat apps: a sidebar with **New chat**, **Add documents** and **Recent chats** (most recently used first; each chat is named after its first question), and the account at the bottom (initials, name, Sign out; or Sign in when signed out; on phones the sidebar opens from ☰). The conversation is a centred column with a message box at the bottom (Enter sends, Shift+Enter adds a line); the model is picked in the top bar for a new chat and stays fixed for an existing one. New chat only clears the screen: the chat is created with the first message or the first documents, so empty chats don't pile up.

**Add documents**: up to 5 pasted texts at a time, added as context for the open chat (or a new one). Pasted text only, not file upload.

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
