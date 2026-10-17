#!/usr/bin/env bash
# Validates config files with the same images docker-compose.yml runs them in, so a typo is caught
# before a recreate breaks the service: the Caddyfile (caddy validate) and every OpenTelemetry
# Collector config in devops/otel-collector/ (otelcol-contrib validate). Needs only Docker; run from
# anywhere. Used by the "Build check" workflow. Exit code 1 if any config is invalid.
set -uo pipefail

cd "$(dirname "$0")"
COMPOSE=docker-compose.yml
image_of() { grep -m1 -o "image: $1[^ ]*" "$COMPOSE" | cut -d' ' -f2; }

CADDY_IMAGE=$(image_of caddy)
OTEL_IMAGE=$(image_of otel/opentelemetry-collector-contrib)
failed=0

check() {  # name, command...
  local name="$1"; shift
  if output=$("$@" 2>&1); then
    echo "ok      $name"
  else
    echo "FAILED  $name"; echo "$output" | tail -5 | sed 's/^/        /'
    failed=1
  fi
}

# Placeholder values: only the syntax and the config structure are checked.
check "caddy/Caddyfile ($CADDY_IMAGE)" \
  docker run --rm -e OPEN_WEBUI_HOST=192.0.2.1 -e OPENCODE_BASIC_AUTH=placeholder \
    -v "$PWD/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

for config in otel-collector/*.yaml; do
  # The file_storage extension (read offsets) requires its directory to exist.
  check "$config ($OTEL_IMAGE)" \
    docker run --rm -e OPENOBSERVE_BASIC_AUTH=placeholder --tmpfs /var/lib/otelcol/file_storage \
      -v "$PWD/$config:/config.yaml:ro" --entrypoint /otelcol-contrib "$OTEL_IMAGE" \
      validate --config=/config.yaml
done

exit $failed
