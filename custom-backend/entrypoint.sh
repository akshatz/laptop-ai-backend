#!/usr/bin/env bash
# Container entrypoint: fetches DB credentials from OpenBao (same AppRole
# db.py uses), applies pending Alembic migrations against them, then hands
# off to uvicorn. Runs on every container start, so migrations stay applied
# without a separate manual step.
set -euo pipefail

echo "Applying database migrations (upgrade to head), DB URL from OpenBao..."
# resolve_db_url.py passes the URL (which includes the DB password) to Alembic
# only through Alembic's environment, so it's never printed or left exported
# in this shell for uvicorn to inherit.
python resolve_db_url.py alembic -c database/alembic.ini upgrade head

echo "Starting uvicorn..."
exec python -m uvicorn main:app --host 0.0.0.0 --port 8000
