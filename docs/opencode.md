# opencode

## opencode

[opencode](https://opencode.ai) is a coding agent with a web UI. **Everyone gets their own**: own container, sessions and workspace. Each runs shell commands as root inside its container, never in this repo.

- **Address:** `https://<OPEN_WEBUI_HOST>:4443` (also the **opencode** link in Open WebUI's sidebar). Sign-in is authentik (password + TOTP); then Caddy sends each person to their own container. Allow port 4443 in the host firewall only for devices that need it.
- **Who gets one:** every active authentik account, oldest first, up to `OPENCODE_MAX_USERS` (default 4), created within a minute of signing up by [devops/opencode/provision.py](../devops/opencode/provision.py) — a host-side script run every minute by a systemd timer, so nothing a signed-up user can reach controls Docker. It puts two headers in the person's authentik attributes (`additionalHeaders`: their container and its password); authentik ([authentik/blueprints/opencode-sso.yaml](../authentik/blueprints/opencode-sso.yaml)) lets only people with them through, and Caddy routes by them. Deactivated or deleted accounts lose their container; their volumes (`opencode-u<pk>-data`, `opencode-u<pk>-workspace`) are kept until removed by hand. The admin (`OPENCODE_ADMIN_USER`, default `akadmin`) keeps compose's `laptop-opencode`.
- **Isolation:** personal containers (`opencode-u<pk>`, 1 GB memory limit) are only on the `opencode-net` network. Through `open-webui-proxy` they reach Ollama's chat API (`/v1/*` only — no pulling or deleting models) and SearXNG's search, nothing else: no Postgres, OpenBao, Milvus or authentik, and no Open WebUI API key.
- **Models:** personal containers use [devops/opencode/opencode-user.json](../devops/opencode/opencode-user.json) (Ollama only). The admin's `laptop-opencode` uses [devops/opencode/opencode.json](../devops/opencode/opencode.json): provider `openwebui` through Open WebUI's API (`OPENWEBUI_API_KEY`, so Global Functions run) and `ollama`. Only models listed show up; `enabled_providers` turns off opencode's built-in free cloud models (some Chinese, some of undisclosed origin). After editing `opencode.json`, recreate `opencode`; `opencode-user.json` applies to containers created afterwards (`docker rm -f opencode-u<pk>` and the next provisioner run recreates it, with the same password and volumes).
- **Web search:** the MCP server `mcp-searxng` (pinned) gives opencode `searxng_web_search` and `web_url_read` tools on the stack's own SearXNG, run through the opencode binary acting as Bun (`BUN_BE_BUN=1 opencode x …`; no Node in the image). Space out heavy use: SearXNG's upstream engines block bursts.
- **Stopped when idle, started on request:** [devops/opencode/idle-stop.sh](../devops/opencode/idle-stop.sh) stops each opencode after 30 minutes with no open connection (an open browser tab counts as use), freeing ~0.7 GB RAM each. When its owner opens :4443 again, Caddy shows "starting" and the provisioner starts it within a minute (it watches Caddy's log for the failed request).
- **Copy in Postgres:** whenever something changed, and once more before stopping, [devops/opencode/sync-to-postgres.sh](../devops/opencode/sync-to-postgres.sh) copies each opencode's sessions, messages and parts into the `opencode` database on `postgres-db` (tables `sessions`, `messages`, `parts`, with `owner` = the authentik username). opencode itself can't use Postgres (`OPENCODE_DB` is a SQLite path only). The copy is one-way and keeps deleted sessions (`deleted_at` set).
- **Timers** (systemd user units):

  ```bash
  mkdir -p ~/.config/systemd/user
  ln -sf ~/laptop-ai-backend/devops/opencode/systemd/opencode-{idle-stop,provision}.{service,timer} ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now opencode-idle-stop.timer opencode-provision.timer
  ```

  `python3 devops/opencode/provision.py --dry-run` shows what it would do; it refuses to run until the running Caddy routes :4443 per person.
