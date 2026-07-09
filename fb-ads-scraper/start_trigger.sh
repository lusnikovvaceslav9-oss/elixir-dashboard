#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${TRIGGER_PORT:-8765}"
if curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "Trigger уже работает: http://127.0.0.1:${PORT}"
  exit 0
fi

nohup python3 trigger_server.py >> /tmp/fb_scraper_trigger.out 2>> /tmp/fb_scraper_trigger.err &
for _ in 1 2 3 4 5; do
  sleep 1
  if curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "Trigger запущен: http://127.0.0.1:${PORT}"
    exit 0
  fi
done

echo "Trigger не запустился. Лог:" >&2
tail -10 /tmp/fb_scraper_trigger.err 2>/dev/null || true
exit 1
