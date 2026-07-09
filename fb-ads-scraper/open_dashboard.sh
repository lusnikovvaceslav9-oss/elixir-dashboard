#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRAPER="$(cd "$(dirname "$0")" && pwd)"
DASH_PORT="${DASH_PORT:-8080}"
TRIGGER_PORT="${TRIGGER_PORT:-8765}"

bash "$SCRAPER/start_trigger.sh"

start_dashboard() {
  if curl -s --max-time 1 "http://127.0.0.1:${DASH_PORT}/elixir.html" >/dev/null 2>&1; then
    return 0
  fi
  cd "$ROOT"
  nohup python3 -m http.server "$DASH_PORT" --bind 127.0.0.1 \
    >> /tmp/elixir_dashboard.out 2>> /tmp/elixir_dashboard.err &
  for _ in 1 2 3 4 5; do
    sleep 1
    if curl -s --max-time 1 "http://127.0.0.1:${DASH_PORT}/elixir.html" >/dev/null 2>&1; then
      return 0
    fi
  done
  echo "Не удалось запустить дашборд на порту ${DASH_PORT}" >&2
  tail -5 /tmp/elixir_dashboard.err 2>/dev/null || true
  return 1
}

start_dashboard

URL="http://127.0.0.1:${DASH_PORT}/elixir.html"
echo "Дашборд: $URL"
echo "Trigger: http://127.0.0.1:${TRIGGER_PORT}"
open "$URL" 2>/dev/null || echo "Откройте: $URL"
