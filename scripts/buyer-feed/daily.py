"""Read/write planto daily CSV (elixir-compatible headers)."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path


CSV_HEADERS = ("date", "spend", "installs", "trials", "sold", "fb", "clicks", "impressions")


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


def _int_field(row: dict, *keys: str) -> int:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return int(float(row[k]))
            except ValueError:
                continue
    return 0


def _float_field(row: dict, *keys: str) -> float:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except ValueError:
                continue
    return 0.0


def load_daily_csv(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    out: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return out
        date_key = next((h for h in reader.fieldnames if h.lower().strip() in ("date", "дата")), "date")
        for row in reader:
            raw_date = row.get(date_key) or row.get("date") or row.get("Дата") or ""
            dt = parse_day_display(raw_date) or parse_day_iso(raw_date)
            if not dt:
                continue
            key = dt.isoformat()
            out[key] = {
                "date": fmt_display(dt),
                "spend": _float_field(row, "spend", "Spend", "спенд"),
                "installs": _int_field(row, "installs", "Installs", "install"),
                "trials": _int_field(row, "trials", "Trials", "trial"),
                "sold": _int_field(row, "sold", "Sold", "sold_trials"),
                "fb": _int_field(row, "fb", "FB", "bills", "Bills"),
                "clicks": _int_field(row, "clicks", "Clicks", "click"),
                "impressions": _int_field(row, "impressions", "Impressions", "impression"),
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
                    "clicks": int(r.get("clicks") or 0),
                    "impressions": int(r.get("impressions") or 0),
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
    clicks: dict[str, int] | None = None,
    impressions: dict[str, int] | None = None,
) -> dict[str, dict]:
    sold = sold or {}
    clicks = clicks or {}
    impressions = impressions or {}
    merged = dict(existing)
    d = anchor
    while d <= until:
        key = d.isoformat()
        prev = merged.get(
            key,
            {
                "date": fmt_display(d),
                "spend": 0,
                "installs": 0,
                "trials": 0,
                "sold": 0,
                "fb": 0,
                "clicks": 0,
                "impressions": 0,
            },
        )
        if key in spend:
            new_spend = spend[key]
            # Direct often lags on the current day — don't wipe a known spend with 0.
            if d == until and new_spend == 0 and (prev.get("spend") or 0) > 0:
                pass
            else:
                prev["spend"] = new_spend
        if key in installs:
            prev["installs"] = installs[key]
        if key in trials:
            prev["trials"] = trials[key]
        if key in sold:
            prev["sold"] = sold[key]
        if key in bills:
            prev["fb"] = bills[key]
        if key in clicks:
            prev["clicks"] = clicks[key]
        if key in impressions:
            prev["impressions"] = impressions[key]
        prev["date"] = fmt_display(d)
        prev.setdefault("clicks", 0)
        prev.setdefault("impressions", 0)
        merged[key] = prev
        d = date.fromordinal(d.toordinal() + 1)
    return merged


def estimate_today_spend(merged: dict[str, dict], until: date, lookback_days: int = 7) -> bool:
    """Fill today's spend from recent CPI when Direct has not reported yet."""
    key = until.isoformat()
    row = merged.get(key)
    if not row or (row.get("spend") or 0) > 0 or (row.get("installs") or 0) <= 0:
        return False
    cpi_samples: list[float] = []
    for i in range(1, lookback_days + 1):
        prev = merged.get((until - timedelta(days=i)).isoformat())
        if not prev:
            continue
        inst = int(prev.get("installs") or 0)
        sp = float(prev.get("spend") or 0)
        if inst > 0 and sp > 0:
            cpi_samples.append(sp / inst)
    if not cpi_samples:
        return False
    cpi = sum(cpi_samples) / len(cpi_samples)
    row["spend"] = round(int(row["installs"]) * cpi, 2)
    return True
