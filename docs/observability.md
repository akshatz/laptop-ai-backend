# Logs and alerts in OpenObserve

## Logs in OpenObserve

OpenObserve (http://localhost:5080, **Logs**) collects these streams:

| Stream | Sent by | What's in it |
|---|---|---|
| `openwebui_backend` | `open-webui` (OTel logs) | Open WebUI's warnings and errors (`GLOBAL_LOG_LEVEL=WARNING`) |
| `openwebui_audit` | `openwebui-audit-shipper` | Who did what in Open WebUI (below) |
| `openwebui_queries` | `openwebui-audit-shipper` | One line per chat request from the Query Log Function ([devops/open-webui/functions/query_log.py](../devops/open-webui/functions/query_log.py), enabled and **Global**): user, model, the generated search queries, source sites and chunk count. The question and the answer themselves aren't logged |
| `ollama` | `ollama-log-shipper` | Ollama's API requests (status, duration, path) and server events. A 500 often only means the caller hung up (Stop, Regenerate, an `open-webui` restart); check Open WebUI's log before blaming Ollama |
| `openbao_audit` | `openbao-audit-shipper` | Who read or changed which OpenBao secret path, from where, and errors such as `permission denied`. Secret values and tokens are HMAC-hashed by OpenBao |
| `authentik_events` | `authentik-events-shipper` | Logins, failed logins, logouts, password and MFA changes (below) |

### Email alerts

OpenObserve emails `OPENOBSERVE_ALERT_EMAIL` (in `.env`, copied to OpenBao by `bootstrap.sh`) through the shared Gmail relay (`SMTP_*`) when something needs a look: bursts of Ollama errors, several questions in a row without web results, a refused OpenBao request, repeated failed sign-ins, the SSO sign-in loop, or a log stream that has gone quiet. On a normal day nothing is sent. Each alert is silenced for an hour or more after it fires, and there's no "resolved" email.

The alerts are defined in [devops/openobserve/alerts.yaml](../devops/openobserve/alerts.yaml) (query, threshold, how often it's checked). After editing it, run `python3 devops/openobserve/alerts.py` on the host; it creates or updates them by name (`--dry-run` shows what it would send). To test delivery, make one refused OpenBao request (e.g. read `sys/policies/acl` with the `bao-readonly` token); the email arrives within about 10 minutes.

## Open WebUI audit log in OpenObserve

Open WebUI's regular logs (stream `openwebui_backend`) are mostly web-server request lines with the client IP but **no user**. To see who did what, Open WebUI's audit log is on (`AUDIT_LOG_LEVEL=METADATA` in compose) and shipped to O2 stream **`openwebui_audit`** by `openwebui-audit-shipper`, an OpenTelemetry Collector ([devops/otel-collector/openwebui-audit.yaml](../devops/otel-collector/openwebui-audit.yaml)). Open WebUI only writes audit entries to `data/audit.log`, never over OTel, so the collector tails that file from the `open-webui-data` volume (read-only).

- Each entry's message reads `<email> <METHOD> <URL> <status>`, e.g. `alice@example.com POST http://localhost:8082/api/v1/users/update 200`, and has searchable fields `user_email`, `user_name`, `user_role`, `user_id`, `verb`, `request_uri`, `response_status_code`, `source_ip` and `user_agent`. Example O2 query: `SELECT * FROM "openwebui_audit" WHERE user_email = 'alice@example.com'`.
- `METADATA` records no request or response bodies. Don't raise it to `REQUEST`: that would log sign-in passwords and chat content.
- Only POST/PUT/PATCH/DELETE requests are audited, and `/chats`, `/chat` and `/folders` are skipped (`AUDIT_EXCLUDED_PATHS`), so chatting itself isn't logged. Set `ENABLE_AUDIT_GET_REQUESTS=true` to include reads, at the cost of much more volume.
- Requests without a signed-in user, such as a password sign-in attempt, show `-` in place of the email.
- The collector keeps its read position in the `otelcol-audit-storage` volume, so restarts don't re-send entries. On its first start it ships whatever `audit.log` already holds. Open WebUI rotates the file at 10 MB (zipped copies stay in `data/`).

## authentik events in OpenObserve

authentik's audit events go to O2 stream **`authentik_events`**, next to Open WebUI's `openwebui_audit`. Covered: logins, failed logins, logouts, password changes, MFA setup, Open WebUI authorizations and admin changes.

- How: `authentik-server`/`authentik-worker` use Docker's `fluentd` logging driver to send their output to `authentik-events-shipper`, an OpenTelemetry Collector ([devops/otel-collector/authentik-events.yaml](../devops/otel-collector/authentik-events.yaml)). It keeps only the event lines (authentik logs each event as JSON with `"event": "Created Event"`) and drops the per-request access logs.
- Each entry's message reads `<username> <action> <IP>`, e.g. `akadmin login_failed 192.168.1.50`. Fields: `action`, `user_username`, `user_email`, `client_ip`, and `context` (a JSON string with the stage, request details, etc.). Example: `SELECT * FROM "authentik_events" WHERE action = 'login_failed'`.
- The driver is async, so authentik starts even when the shipper is down (events from that time are lost), and `docker logs` still works through Docker's local cache. The shipper's port 24224 is published on 127.0.0.1 only, because the Docker daemon on the host is what connects to it.
- authentik also keeps its own event log (**Events → Logs** in the admin UI, 1 year by default).
