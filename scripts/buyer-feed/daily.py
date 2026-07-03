"""Read/write planto daily CSV (elixir-compatible headers)."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path


CSV_HEADERS = ("date", "spend", "installs", "trials", "sold", "fb")


def parse_day_iso(s: str) -> date | None:
    s = (s or "").strip()[:10]
    if len(s) != 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_day_display(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_display(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def load_daily_csv(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    out: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return out
        headers = [h.lower().strip() for h in reader.fieldnames]
        date_key = next((h for h in reader.fieldnames if h.lower().strip() in ("date", "дата")), "date")
        for row in reader:
            raw_date = row.get(date_key) or row.get("date") or row.get("Дата") or ""
            dt = parse_day_display(raw_date) or parse_day_iso(raw_date)
            if not dt:
                continue
            key = dt.isoformat()
            out[key] = {
                "date": fmt_display(dt),
                "spend": float(row.get("spend") or row.get("Spend") or row.get("спенд") or 0),
                "installs": int(float(row.get("installs") or row.get("Installs") or row.get("install") or 0)),
                "trials": int(float(row.get("trials") or row.get("Trials") or row.get("trial") or 0)),
                "sold": int(float(row.get("sold") or row.get("Sold") or row.get("sold_trials") or 0)),
                "fb": int(float(row.get("fb") or row.get("FB") or row.get("bills") or row.get("Bills") or 0)),
            }
    return out


def write_daily_csv(path: Path, daily: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [daily[k] for k in sorted(daily.keys())]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "date": r["date"],
                    "spend": round(float(r.get("spend") or 0), 2),
                    "installs": int(r.get("installs") or 0),
                    "trials": int(r.get("trials") or 0),
                    "sold": int(r.get("sold") or 0),
                    "fb": int(r.get("fb") or 0),
                }
            )


def merge_daily(
    existing: dict[str, dict],
    *,
    spend: dict[str, float],
    installs: dict[str, int],
    trials: dict[str, int],
    bills: dict[str, int],
    anchor: date,
    until: date,
    sold: dict[str, int] | None = None,
) -> dict[str, dict]:
    sold = sold or {}
    merged = dict(existing)
    d = anchor
    while d <= until:
        key = d.isoformat()
        prev = merged.get(key, {"date": fmt_display(d), "spend": 0, "installs": 0, "trials": 0, "sold": 0, "fb": 0})
        if key in spend:
            prev["spend"] = spend[key]
        if key in installs:
            prev["installs"] = installs[key]
        if key in trials:
            prev["trials"] = trials[key]
        if key in sold:
            prev["sold"] = sold[key]
        if key in bills:
            prev["fb"] = bills[key]
        prev["date"] = fmt_display(d)
        merged[key] = prev
        d = date.fromordinal(d.toordinal() + 1)
    return merged
