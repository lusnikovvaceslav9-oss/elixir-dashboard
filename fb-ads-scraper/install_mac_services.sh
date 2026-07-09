#!/bin/bash
# Один раз: Mac сам подхватывает задачи из дашборда. Дальше — только кнопки в браузере.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

cp "$DIR/com.user.fbscraper-worker.plist" ~/Library/LaunchAgents/
launchctl bootout "gui/$(id -u)/com.user.fbscraper-worker" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.user.fbscraper-worker.plist

echo "✅ FB Worker установлен — работает в фоне, слушает дашборд"
echo "   Лог: $DIR/fb_scraper_worker.log"
echo ""
echo "Теперь открывайте дашборд на github.io и жмите «Парсить» — Mac сделает сам."
