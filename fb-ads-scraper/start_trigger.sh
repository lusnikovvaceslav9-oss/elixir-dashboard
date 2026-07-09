#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${TRIGGER_PORT:-8765}"
if curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "Trigger уже работает: http://127.0.0.1:${PORT}"
  exit 0
fi

nohup python3 trigger_server.py >> /tmp/fb_scraper_trigger.out 2>> /tmp/fb_scraper_trigger.err &
sleep 1
curl -s "http://127.0.0.1:${PORT}/health" && echo ""
echo "Trigger запущен: http://127.0.0.1:${PORT}"
