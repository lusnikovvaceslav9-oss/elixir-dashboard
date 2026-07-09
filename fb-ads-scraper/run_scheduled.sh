#!/bin/bash
# Автопарсинг: отдельный прогон для каждого проекта с enabled=true в дашборде
set -euo pipefail
cd "$(dirname "$0")"

IDS=$(python3 - <<'PY'
import json
from pathlib import Path
from remote_config import fetch_dashboard_projects, dashboard_to_scraper_projects

cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
dash = fetch_dashboard_projects(cfg)
for p in dashboard_to_scraper_projects(dash, manual=False):
    print(p["dashboard_id"])
PY
)

if [[ -z "$IDS" ]]; then
  echo "Нет проектов с автопарсингом (enabled в дашборде)"
  exit 0
fi

for id in $IDS; do
  echo "=== Парсинг: $id ==="
  python3 fb_ads_scraper.py --manual --project-id "$id" || true
  sleep 5
done
