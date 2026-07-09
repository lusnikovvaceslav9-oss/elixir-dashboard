#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f config.json ]]; then
  echo "Создайте config.json из config.example.json"
  exit 1
fi

if [[ ! -f credentials.json && ! -f service_account.json ]]; then
  echo "Положите credentials.json (OAuth) или service_account.json в $(pwd)"
  exit 1
fi

python3 -m pip install -q -r requirements.txt
python3 fb_ads_scraper.py
