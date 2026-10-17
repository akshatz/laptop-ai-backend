#!/usr/bin/env bash
# Every timer run (opencode-idle-stop.timer, every 5 min; see systemd/ next to this file): copies
# opencode's sessions to Postgres when anything changed (sync-to-postgres.sh), and stops the
# container after IDLE_MINUTES without any open connection to it, to free its ~0.7 GB of RAM. An
# open opencode browser tab keeps a connection open, so it counts as use. Start opencode again by
# hand: docker start laptop-opencode
set -euo pipefail

CONTAINER="${OPENCODE_CONTAINER:-laptop-opencode}"
IDLE_MINUTES="${OPENCODE_IDLE_MINUTES:-30}"
STATE_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/opencode-idle-stop/last-active"

mkdir -p "$(dirname "$STATE_FILE")"

if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]; then
  rm -f "$STATE_FILE"   # next start begins a fresh idle period
  exit 0
fi

# Copy sessions to Postgres whenever anything changed since the last copy, so a finished session
# reaches Postgres within one timer run (5 min). Latest update time plus row counts, so a deleted
# session counts as a change too. A failed copy leaves the marker, so the next run retries.
SYNC_MARKER="$(dirname "$STATE_FILE")/last-synced"
marker=$(docker exec "$CONTAINER" opencode db --format tsv \
  "select (select max(time_updated) from session) || '/' || (select max(time_updated) from message)
    || '/' || (select max(time_updated) from part) || '/' || (select count(*) from session)
    || '/' || (select count(*) from message) || '/' || (select count(*) from part)" \
  2>/dev/null | tail -1 || true)
if [ -n "$marker" ] && [ "$marker" != "$(cat "$SYNC_MARKER" 2>/dev/null)" ]; then
  if "$(dirname "$0")/sync-to-postgres.sh"; then
    echo "$marker" > "$SYNC_MARKER"
  else
    echo "copy to Postgres failed" >&2
  fi
fi

# Established (state 01) TCP connections to local port 4096 (hex 1000), IPv4 and IPv6.
connections=$(docker exec "$CONTAINER" awk \
  '$4 == "01" && $2 ~ /:1000$/ { n++ } END { print n + 0 }' /proc/net/tcp /proc/net/tcp6)

now=$(date +%s)
if [ "$connections" -gt 0 ] || [ ! -f "$STATE_FILE" ]; then
  echo "$now" > "$STATE_FILE"
  exit 0
fi

idle=$(( (now - $(cat "$STATE_FILE")) / 60 ))
if [ "$idle" -ge "$IDLE_MINUTES" ]; then
  echo "opencode idle for ${idle} min, copying sessions to Postgres, then stopping $CONTAINER"
  # A failed copy is logged but doesn't keep opencode running; the next idle period retries.
  "$(dirname "$0")/sync-to-postgres.sh" || echo "copy to Postgres failed" >&2
  docker stop "$CONTAINER" >/dev/null
  rm -f "$STATE_FILE"
fi
