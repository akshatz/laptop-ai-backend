# Custom frontend

A React chat UI for [custom-backend](custom-backend.md), kept as its own app in [custom-frontend/](../custom-frontend/) (Vite + React + TypeScript). It shares no code or image with the backend and reaches it only over HTTP.

## Screens

- **Sign in**: email and password (`POST /login`). When the account has MFA on, a second step asks for the code emailed by the backend (`POST /mfa/verify-otp`).
- **Sign up**: name, email and password typed twice (`POST /users`), then the verification token from the email (`POST /users/verify`). Back on Sign in, the email is filled in.
- **Chat** (after signing in): pick a model (from `GET /models`), create a chat, optionally add up to 5 texts as RAG context, and chat. The **Account** section shows whether MFA is on and turns it on or off (`/mfa/enable`, `/mfa/disable`, which ask for the password again).

The signed-in user is kept in `sessionStorage` (this tab only) and Sign out clears it.

**Not a security boundary.** The backend has no session tokens yet: the chat endpoints accept any `user_id`, so the sign-in here only decides which screen to show.

## Running it

`docker compose -f devops/docker-compose.yml up -d --build custom-frontend` builds the app (Node stage: `npm ci`, `tsc`, `vite build`) and serves it with nginx at http://localhost:8012. nginx forwards `/api/<path>` to `custom-backend:8000/<path>` (see [nginx.conf](../custom-frontend/nginx.conf); 600 s read timeout, since answers from a CPU model can take minutes). Rebuild after changing the app.

The container is limited to 1 GB of memory and half a CPU (`mem_limit`/`cpus` in compose); the limits apply to the running nginx, not to the image build. The port is published on every interface, like custom-backend's 8011, so devices on the LAN can open it too.

## Developing

```bash
cd custom-frontend
npm install
npm run dev        # http://localhost:5173, /api → http://localhost:8011 (override with BACKEND_URL)
npm run build      # type-check + production build into dist/
```

Dependencies are pinned to exact versions in `package.json`, with `package-lock.json` committed. CI's Build check builds the image (`frontend-build` job), which fails on type errors.
