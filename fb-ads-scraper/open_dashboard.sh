#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRAPER="$(cd "$(dirname "$0")" && pwd)"
DASH_PORT="${DASH_PORT:-8080}"
TRIGGER_PORT="${TRIGGER_PORT:-8765}"

bash "$SCRAPER/start_trigger.sh"

if ! curl -s --max-time 1 "http://127.0.0.1:${DASH_PORT}/elixir.html" >/dev/null 2>&1; then
  cd "$ROOT"
  nohup python3 -m http.server "$DASH_PORT" >> /tmp/elixir_dashboard.out 2>> /tmp/elixir_dashboard.err &
  sleep 1
fi

URL="http://127.0.0.1:${DASH_PORT}/elixir.html"
echo "Дашборд (локально, для FB-кнопки): $URL"
echo "Trigger Mac: http://127.0.0.1:${TRIGGER_PORT}"
open "$URL" 2>/dev/null || echo "Откройте в браузере: $URL"
