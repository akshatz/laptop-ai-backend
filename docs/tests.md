# Tests and known gaps

## Tests

`pytest` covers custom-backend's endpoints (auth, chats, RAG ingestion) and the logic of every Open WebUI Function in `devops/open-webui/functions/`, including the sign-in wrappers, the class patches (Audit Status Code, Chat Soft Delete, Embedding Cache) and a check that Password Expiry's emailed links are accepted by Password Reset (the two must share a token format). GitHub Actions runs them on every push ([.github/workflows/tests.yml](../.github/workflows/tests.yml)).

- **custom-backend** tests import the real app with stand-ins for OpenBao and Milvus (no running stack needed) and use a real Postgres, given by `TEST_DATABASE_URL`. Its tables are dropped and recreated, so the database name must end in `_test`. Without `TEST_DATABASE_URL` these tests are skipped.
- **Migrations** ([tests/migrations/](../tests/migrations/)): both Alembic setups run upgrade → downgrade → upgrade on an empty `migrations_test` database; custom-backend's migrations must match `database/init_db.py`'s models (apart from two indexes the models don't declare), `custom-backend/models.py` must match `init_db.py` column for column, and the Functions' migrations must only create `fn_*` tables and use `fn_alembic_version`.
- **settings.py** ([tests/settings/](../tests/settings/)): export → diff → edit → apply against Open WebUI's `config`/`model`/`function` tables recreated with the same column types in `settings_test`, so its generated SQL really runs; secret valves never reach the file, backups keep them, revisions are recorded once per content.
- **Evals** ([tests/evals/](../tests/evals/)): `run.py`'s scoring (facts, exclusions, sites, abstaining), case loading, the streamed-answer parser and whole runs against a fake Open WebUI, `--compare`, and that every case in `evals/cases.yaml` is well formed (regexes compile); `feedback_to_cases.py`'s SQL against Open WebUI-shaped `feedback`/`chat_message` tables.
- **Configs**: [tests/configs/](../tests/configs/) checks the SearXNG settings web search relies on (`json` format, google and bing on, limiter off). [devops/validate-configs.sh](../devops/validate-configs.sh) validates the Caddyfile and the OpenTelemetry Collector configs in the images compose uses (Build check → config-check in CI; run it after editing one of them).
- **opencode** tests check [devops/opencode/opencode.json](../devops/opencode/opencode.json) (only local providers, every named model listed and `name:tag`-shaped, add-on pinned) and run `idle-stop.sh` and `sync-to-postgres.sh` with a fake `docker` ([tests/opencode/fake-docker](../tests/opencode/fake-docker)) whose `psql` calls go to the test Postgres (database `opencode_test`); they need the `psql` client.
- **Functions** tests load each file from `devops/open-webui/functions/` with stand-ins for the Open WebUI internals it imports; their database helpers are replaced in the tests, so the SQL itself isn't covered.

Run them on the laptop (Python 3.11, against a throwaway database on `postgres-db`):

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
docker exec laptop-postgres psql -U "$POSTGRES_USER" -d postgres -c "create database custom_backend_test"   # once
export $(grep -v '^#' .env | xargs)
TEST_DATABASE_URL="postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:5433/custom_backend_test" .venv/bin/pytest -v
```

Saved runs (with start times) are in [tests/runs/](../tests/runs/).

## Known gaps / TODO

- `custom-backend/models.py` and `database/init_db.py` declare the same models independently — keep them in sync by hand when changing either.
- Logs are sent to OpenObserve with the O2 root credentials — a dedicated ingestion-only user would be safer.
- Open WebUI's traces and metrics are off while `lgtm` isn't running (`ENABLE_OTEL_TRACES`/`ENABLE_OTEL_METRICS=false`); turn them back on after starting it.
- Next steps are in [ROADMAP.md](../ROADMAP.md).
