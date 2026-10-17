#!/usr/bin/env bash
# Copies opencode's sessions, messages and message parts from its SQLite database (opencode.db in
# the opencode-data volume) into new tables in the `opencode` database on postgres-db: sessions,
# messages, parts. One-way: opencode keeps using SQLite. Rows are upserted by id; a session,
# message or part that disappears from opencode is kept and gets deleted_at set. Called by
# idle-stop.sh just before it stops the container; can also be run by hand while opencode runs.
# Reads opencode through `opencode db` (works while the server runs) and writes through
# `docker exec laptop-postgres psql`, like devops/open-webui/settings.py.
# opencode's tables are internal: re-check this after bumping the opencode image.
set -euo pipefail

CONTAINER="${OPENCODE_CONTAINER:-laptop-opencode}"
PG_CONTAINER="${POSTGRES_CONTAINER:-laptop-postgres}"
PG_DB="${OPENCODE_PG_DB:-opencode}"
ENV_FILE="$(cd "$(dirname "$0")/../.." && pwd)/.env"
PG_USER="$(grep '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)"

psql_on() { docker exec -i -e PGOPTIONS=--client-min-messages=warning "$PG_CONTAINER" psql -U "$PG_USER" -v ON_ERROR_STOP=1 -q "$@"; }
oc_json() { docker exec "$CONTAINER" opencode db --format json "$1" 2>/dev/null; }

if [ "$(psql_on -d postgres -tAc "select 1 from pg_database where datname = '$PG_DB'")" != "1" ]; then
  psql_on -d postgres -c "create database $PG_DB"
fi

psql_on -d "$PG_DB" <<'SQL'
create table if not exists sessions (
  id text primary key,
  project_id text,
  parent_id text,
  title text,
  directory text,
  agent text,
  model text,
  tokens_input bigint,
  tokens_output bigint,
  cost double precision,
  created_at timestamptz,
  updated_at timestamptz,
  raw jsonb not null,
  synced_at timestamptz not null default now(),
  deleted_at timestamptz
);
create table if not exists messages (
  id text primary key,
  session_id text not null,
  role text,
  created_at timestamptz,
  updated_at timestamptz,
  data jsonb not null,
  synced_at timestamptz not null default now(),
  deleted_at timestamptz
);
create index if not exists messages_session_id on messages (session_id);
create table if not exists parts (
  id text primary key,
  message_id text not null,
  session_id text not null,
  type text,
  created_at timestamptz,
  updated_at timestamptz,
  data jsonb not null,
  synced_at timestamptz not null default now(),
  deleted_at timestamptz
);
create index if not exists parts_message_id on parts (message_id);
SQL

sessions=$(oc_json "select * from session")
messages=$(oc_json "select id, session_id, time_created, time_updated, data from message")
parts=$(oc_json "select id, message_id, session_id, time_created, time_updated, data from part")
# A failed read must not look like "everything was deleted".
for out in "$sessions" "$messages" "$parts"; do
  case "$out" in
    \[*) ;;
    *) echo "opencode db read failed, nothing copied" >&2; exit 1 ;;
  esac
done

# Each JSON array goes in as a dollar-quoted literal with a random tag, so its content can't end
# the literal early.
tag="oc$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
{
  echo "begin;"
  echo "create temp table src_sessions as select \$$tag\$$sessions\$$tag\$::jsonb as j;"
  echo "create temp table src_messages as select \$$tag\$$messages\$$tag\$::jsonb as j;"
  echo "create temp table src_parts as select \$$tag\$$parts\$$tag\$::jsonb as j;"
  cat <<'SQL'
insert into sessions (id, project_id, parent_id, title, directory, agent, model, tokens_input,
                      tokens_output, cost, created_at, updated_at, raw, synced_at, deleted_at)
select r->>'id', r->>'project_id', r->>'parent_id', r->>'title', r->>'directory', r->>'agent',
       r->>'model', (r->>'tokens_input')::bigint, (r->>'tokens_output')::bigint,
       (r->>'cost')::double precision,
       to_timestamp((r->>'time_created')::bigint / 1000.0),
       to_timestamp((r->>'time_updated')::bigint / 1000.0), r, now(), null
from src_sessions, jsonb_array_elements(j) r
on conflict (id) do update set
  project_id = excluded.project_id, parent_id = excluded.parent_id, title = excluded.title,
  directory = excluded.directory, agent = excluded.agent, model = excluded.model,
  tokens_input = excluded.tokens_input, tokens_output = excluded.tokens_output,
  cost = excluded.cost, created_at = excluded.created_at, updated_at = excluded.updated_at,
  raw = excluded.raw, synced_at = now(), deleted_at = null;

insert into messages (id, session_id, role, created_at, updated_at, data, synced_at, deleted_at)
select r->>'id', r->>'session_id', (r->>'data')::jsonb->>'role',
       to_timestamp((r->>'time_created')::bigint / 1000.0),
       to_timestamp((r->>'time_updated')::bigint / 1000.0), (r->>'data')::jsonb, now(), null
from src_messages, jsonb_array_elements(j) r
on conflict (id) do update set
  session_id = excluded.session_id, role = excluded.role, created_at = excluded.created_at,
  updated_at = excluded.updated_at, data = excluded.data, synced_at = now(), deleted_at = null;

insert into parts (id, message_id, session_id, type, created_at, updated_at, data, synced_at,
                   deleted_at)
select r->>'id', r->>'message_id', r->>'session_id', (r->>'data')::jsonb->>'type',
       to_timestamp((r->>'time_created')::bigint / 1000.0),
       to_timestamp((r->>'time_updated')::bigint / 1000.0), (r->>'data')::jsonb, now(), null
from src_parts, jsonb_array_elements(j) r
on conflict (id) do update set
  message_id = excluded.message_id, session_id = excluded.session_id, type = excluded.type,
  created_at = excluded.created_at, updated_at = excluded.updated_at, data = excluded.data,
  synced_at = now(), deleted_at = null;

-- Gone from opencode: keep the copy, mark it.
update sessions set deleted_at = now() where deleted_at is null
  and id not in (select r->>'id' from src_sessions, jsonb_array_elements(j) r);
update messages set deleted_at = now() where deleted_at is null
  and id not in (select r->>'id' from src_messages, jsonb_array_elements(j) r);
update parts set deleted_at = now() where deleted_at is null
  and id not in (select r->>'id' from src_parts, jsonb_array_elements(j) r);
commit;
SQL
  echo "select format('opencode → postgres: %s sessions, %s messages, %s parts',"
  echo "  (select count(*) from sessions where deleted_at is null),"
  echo "  (select count(*) from messages where deleted_at is null),"
  echo "  (select count(*) from parts where deleted_at is null));"
} | psql_on -d "$PG_DB" -tA
