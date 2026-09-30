#!/usr/bin/env python3
"""Bake SkinAI test Meta Ads (ad set) spend CSV into elixir daily + phi/mauve sheets."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=7))
DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
SRC = Path("/Users/vaceslavlusnikov/Downloads/菏泽安荧网络-1-Ad-sets-Sep-29-2026-Sep-30-2026 (2).csv")
ADSETS = [("phi", "phi"), ("mauve", "mauve")]


def now_iso() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_num(val) -> float:
    s = str(val or "").strip().replace("\xa0", "").replace(" ", "")
    if not s or s in {"-", "—", "–"}:
        return 0.0
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return 0.0


def iso_to_ru(iso: str) -> str | None:
    s = str(iso or "").strip()[:10]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return dt.strftime("%d.%m.%Y")


def write_elixir_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + fields)
        for r in sorted(rows, key=lambda x: datetime.strptime(x["date"], "%d.%m.%Y")):
            out = [r["date"]]
            for k in fields:
                v = r.get(k, 0) or 0
                out.append(f"{v:.2f}" if k == "spend" else str(int(round(v))))
            w.writerow(out)


def aggregate(recs: list[dict]) -> list[dict]:
    keys = ["spend", "trials", "fb", "clicks", "impressions"]
    by_date: dict[str, dict] = {}
    for rec in recs:
        slot = by_date.setdefault(rec["date"], {k: 0.0 for k in keys} | {"date": rec["date"]})
        for k in keys:
            slot[k] += rec.get(k, 0) or 0
    return [v for v in by_date.values() if any(v[k] for k in keys)]


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Missing source CSV: {SRC}")
    shutil.copyfile(SRC, DASH / "data" / "skinai-meta-ads.csv")
    rows = list(csv.reader(SRC.read_text(encoding="utf-8-sig").splitlines()))
    if not rows:
        raise SystemExit("Empty CSV")
    headers = rows[0]
    idx = {h: i for i, h in enumerate(headers)}

    def col(*names: str) -> int:
        for n in names:
            if n in idx:
                return idx[n]
        return -1

    date_i = col("Reporting starts")
    name_i = col("Ad set name")
    spend_i = col("Amount spent (USD)")
    clicks_i = col("Link clicks")
    imps_i = col("Impressions")
    lpv_i = col("Landing page views")
    cv_i = col("Content views")
    recs = []
    for row in rows[1:]:
        if not row or name_i >= len(row):
            continue
        name = str(row[name_i] or "").strip()
        date = iso_to_ru(row[date_i] if date_i >= 0 else "")
        if not date or not name:
            continue
        recs.append({
            "date": date,
            "adset": name,
            "spend": parse_num(row[spend_i] if 0 <= spend_i < len(row) else 0),
            "trials": parse_num(row[cv_i] if 0 <= cv_i < len(row) else 0),
            "fb": parse_num(row[lpv_i] if 0 <= lpv_i < len(row) else 0),
            "clicks": parse_num(row[clicks_i] if 0 <= clicks_i < len(row) else 0),
            "impressions": parse_num(row[imps_i] if 0 <= imps_i < len(row) else 0),
        })
    fields = ["spend", "trials", "fb", "clicks", "impressions"]
    total = aggregate(recs)
    write_elixir_csv(DASH / "data" / "skinai-daily.csv", total, fields)
    niches = {}
    known = {label for _, label in ADSETS}
    for nid, label in ADSETS:
        daily = aggregate([r for r in recs if r["adset"].lower() == label.lower()])
        write_elixir_csv(DASH / "data" / f"skinai-{nid}.csv", daily, fields)
        niches[nid] = {
            "name": label,
            "days": len(daily),
            "spend": round(sum(r["spend"] for r in daily), 2),
            "lpv": int(round(sum(r["fb"] for r in daily))),
            "content_views": int(round(sum(r["trials"] for r in daily))),
        }
    unknown = sorted({r["adset"] for r in recs} - known)
    totals = {k: sum(r.get(k, 0) for r in total) for k in fields}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in total]
    meta = {
        "generated_at": now_iso(),
        "project": "skinai",
        "kind": "test",
        "name": "SkinAI",
        "anchor": "2026-09-29",
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "trials": "content_views",
            "fb": "landing_page_views",
            "spend": "meta_ads",
            "clicks": "link_clicks",
            "impressions": "impressions",
        },
        "goals": [
            {"id": "landing_page_view", "key": "lpv", "label": "LPV", "csv": "fb", "costLabel": "Cost / LPV"},
            {"id": "content_view", "key": "content_view", "label": "Content views", "csv": "trials", "costLabel": "Cost / view"},
        ],
        "niches": [{"id": nid, "name": label} for nid, label in ADSETS],
        "unknown_adsets": unknown,
        "sources": {"spend": "meta_ads_csv", "file": SRC.name},
        "days": len(total),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "niche_days": niches,
        "currency": "USD",
        "roas_config": None,
    }
    (DASH / "data" / "skinai-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"days": len(total), "spend": round(totals["spend"], 2), "lpv": int(totals["fb"]), "content_views": int(totals["trials"]), "niches": niches, "unknown": unknown}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
