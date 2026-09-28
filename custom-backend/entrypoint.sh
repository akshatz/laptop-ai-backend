#!/usr/bin/env bash
# Container entrypoint: fetches DB credentials from OpenBao (same AppRole
# db.py uses), applies pending Alembic migrations against them, then hands
# off to uvicorn. Runs on every container start, so migrations stay applied
# without a separate manual step.
set -euo pipefail

echo "Resolving database URL from OpenBao..."
export INIT_DB_DATABASE_URL
INIT_DB_DATABASE_URL="$(python resolve_db_url.py)"

echo "Applying database migrations (upgrade to head)..."
alembic -c database/alembic.ini upgrade head

echo "Starting uvicorn..."
exec python -m uvicorn main:app --host 0.0.0.0 --port 8000
