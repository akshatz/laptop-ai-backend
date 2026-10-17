#!/usr/bin/env bash
# Every timer run (opencode-idle-stop.timer, every 5 min; see systemd/ next to this file), for each
# opencode container — laptop-opencode (the admin's) and every personal one provision.py created
# (label laptop.opencode.user) — or only $OPENCODE_CONTAINER when set:
# - copies its sessions to Postgres when anything changed (sync-to-postgres.sh, owner = the label);
# - stops it after IDLE_MINUTES without any open connection to it, to free its ~0.7 GB of RAM. An
#   open opencode browser tab keeps a connection open, so it counts as use. provision.py starts it
#   again when its owner opens https://<host>:4443 (or by hand: docker start <container>).
set -euo pipefail

IDLE_MINUTES="${OPENCODE_IDLE_MINUTES:-30}"
STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/opencode-idle-stop"
DEFAULT_OWNER="${OPENCODE_DEFAULT_OWNER:-akadmin}"
SYNC="$(dirname "$0")/sync-to-postgres.sh"

check() {  # container
  local container="$1" state="$STATE_ROOT/$1" owner
  local active="$state/last-active" synced="$state/last-synced"
  mkdir -p "$state"

  if [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" != "true" ]; then
    rm -f "$active"   # next start begins a fresh idle period
    return 0
  fi
  owner=$(docker inspect -f '{{index .Config.Labels "laptop.opencode.user"}}' "$container" 2>/dev/null || true)
  [ -n "$owner" ] && [ "$owner" != "<no value>" ] || owner="$DEFAULT_OWNER"

  # Copy sessions to Postgres whenever anything changed since the last copy, so a finished session
  # reaches Postgres within one timer run (5 min). Latest update time plus row counts, so a deleted
  # session counts as a change too. A failed copy leaves the marker, so the next run retries.
  local marker
  marker=$(docker exec "$container" opencode db --format tsv \
    "select (select max(time_updated) from session) || '/' || (select max(time_updated) from message)
      || '/' || (select max(time_updated) from part) || '/' || (select count(*) from session)
      || '/' || (select count(*) from message) || '/' || (select count(*) from part)" \
    2>/dev/null | tail -1 || true)
  if [ -n "$marker" ] && [ "$marker" != "$(cat "$synced" 2>/dev/null)" ]; then
    if OPENCODE_CONTAINER="$container" OPENCODE_OWNER="$owner" "$SYNC"; then
      echo "$marker" > "$synced"
    else
      echo "copy to Postgres failed ($container)" >&2
    fi
  fi

  # Established (state 01) TCP connections to local port 4096 (hex 1000), IPv4 and IPv6.
  local connections now idle
  connections=$(docker exec "$container" awk \
    '$4 == "01" && $2 ~ /:1000$/ { n++ } END { print n + 0 }' /proc/net/tcp /proc/net/tcp6)

  now=$(date +%s)
  if [ "$connections" -gt 0 ] || [ ! -f "$active" ]; then
    echo "$now" > "$active"
    return 0
  fi

  idle=$(( (now - $(cat "$active")) / 60 ))
  if [ "$idle" -ge "$IDLE_MINUTES" ]; then
    echo "$container idle for ${idle} min, copying sessions to Postgres, then stopping it"
    # A failed copy is logged but doesn't keep opencode running; the next idle period retries.
    OPENCODE_CONTAINER="$container" OPENCODE_OWNER="$owner" "$SYNC" || echo "copy to Postgres failed ($container)" >&2
    docker stop "$container" >/dev/null
    rm -f "$active"
  fi
}

if [ -n "${OPENCODE_CONTAINER:-}" ]; then
  containers="$OPENCODE_CONTAINER"
else
  containers="laptop-opencode $(docker ps -a --filter label=laptop.opencode.user --format '{{.Names}}' 2>/dev/null || true)"
fi
status=0
for container in $containers; do
  check "$container" || { echo "checking $container failed" >&2; status=1; }
done
exit $status
