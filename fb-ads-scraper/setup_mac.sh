#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "======================================"
echo "  FB Ads Scraper — установка на Mac"
echo "======================================"

if ! command -v python3 >/dev/null; then
  echo "Python 3 не найден"
  exit 1
fi

python3 -m pip install --upgrade -r requirements.txt

if [[ ! -f config.json ]]; then
  cp config.example.json config.json
  echo "Создан config.json — заполните adspower_api_key и профили"
fi

echo ""
echo "Дальше:"
echo "1. Положите credentials.json (OAuth) или service_account.json в эту папку"
echo "2. Запустите: bash run.sh"
echo "3. Для автозапуска: отредактируйте com.user.fbscraper.plist и load в LaunchAgents"
