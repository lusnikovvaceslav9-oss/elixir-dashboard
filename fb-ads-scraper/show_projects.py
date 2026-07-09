#!/usr/bin/env python3
"""Показать маппинг проектов FB scraper ↔ Elixir dashboard."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from remote_config import dashboard_to_scraper_projects, fetch_dashboard_projects, load_projects


def load_config() -> dict:
    path = ROOT / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def print_project(p: dict, source: str) -> None:
    flag = "ON " if p.get("enabled") else "off"
    print(f"[{flag}] {p.get('name')} ({p.get('dashboard_id')})  [{source}]")
    print(f"     AdsPower profile: {p.get('profile_id') or '—'}")
    print(f"     Business Manager: {p.get('bm_id') or '—'}")
    print(f"     Ad account (act): {p.get('ad_account_id') or '—'}")
    print(f"     Google Sheet:     {p.get('sheet_id')}")
    print(f"     Dashboard tab:    {p.get('dashboard_sheet')}")
    print(f"     Detail tab:       {p.get('detail_sheet')}")
    print(f"     Export mode:      {p.get('export_mode')}")
    print()


def main() -> None:
    config = load_config()
    print("FB Ads Scraper — маппинг проектов\n")

    try:
        projects, source = load_projects(config, ROOT, manual=True)
        for p in projects:
            print_project(p, source)
        return
    except Exception as exc:
        print(f"Не удалось загрузить конфиг: {exc}\n")

    payload = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
    for p in payload.get("projects", []):
        print_project(p, "local-file")


if __name__ == "__main__":
    main()
