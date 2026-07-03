#!/usr/bin/env python3
"""Smoke check for buyer-package data files."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    daily = DATA / "planto-daily.csv"
    cohort = DATA / "planto-cohort.json"
    meta = DATA / "planto-meta.json"
    errors: list[str] = []

    for p in (
        ROOT / "elixir.html",
        ROOT / "elixir.css",
        ROOT / "elixir.js",
        daily,
        cohort,
        meta,
    ):
        if not p.is_file():
            errors.append(f"missing {p.name}")

    if errors:
        print("FAIL:", "; ".join(errors))
        return 1

    html = (ROOT / "elixir.html").read_text(encoding="utf-8")
    if 'href="elixir.css"' not in html or 'src="elixir.js"' not in html:
        errors.append("elixir.html must link elixir.css and elixir.js")

    rows = list(csv.DictReader(daily.open(encoding="utf-8")))
    if len(rows) < 1:
        errors.append("planto-daily.csv empty")
    for r in rows[:3]:
        if not r.get("date"):
            errors.append("daily row missing date")

    c = json.loads(cohort.read_text(encoding="utf-8"))
    if not c.get("rows"):
        errors.append("cohort rows empty")
    if c.get("anchor") != "2026-06-05":
        errors.append(f"unexpected anchor {c.get('anchor')}")

    m = json.loads(meta.read_text(encoding="utf-8"))
    if m.get("errors"):
        errors.append(f"meta errors: {m['errors']}")

    spend = sum(float(r["spend"]) for r in rows)
    trials = sum(int(float(r.get("trials") or 0)) for r in rows)
    print(
        f"OK: {len(rows)} days, spend={spend:.0f}, trials={trials}, "
        f"cohorts={len(c['rows'])}, meta={m.get('generated_at')}"
    )
    if errors:
        print("FAIL:", "; ".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
