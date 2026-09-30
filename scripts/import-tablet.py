#!/usr/bin/env python3
"""Bake Пилюльница test Meta Ads (ad set) CSV into elixir daily + H1–H4 sheets."""
from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=7))
DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
SRC = Path("/Users/vaceslavlusnikov/Downloads/16581-_Elixir-_-JGGL-_-1_A$-Ad-sets-31-Aug-2026-29-Sep-2026.csv")
HYPOTHESES = [("h1", "H1"), ("h2", "H2"), ("h3", "H3"), ("h4", "H4")]
SKIP = {"start", "completed"}


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
    raw_out = DASH / "data" / "tablet-meta-ads.csv"
    shutil.copyfile(SRC, raw_out)
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
    res_i = col("Results")
    clicks_i = col("Link clicks")
    imps_i = col("Impressions")
    lpv_i = col("Landing page views")
    recs = []
    for row in rows[1:]:
        if not row or name_i >= len(row):
            continue
        name = str(row[name_i] or "").strip()
        if not name or name.lower() in SKIP:
            continue
        date = iso_to_ru(row[date_i] if date_i >= 0 else "")
        if not date:
            continue
        recs.append({
            "date": date,
            "adset": name,
            "spend": parse_num(row[spend_i] if 0 <= spend_i < len(row) else 0),
            "trials": parse_num(row[res_i] if 0 <= res_i < len(row) else 0),
            "fb": parse_num(row[lpv_i] if 0 <= lpv_i < len(row) else 0),
            "clicks": parse_num(row[clicks_i] if 0 <= clicks_i < len(row) else 0),
            "impressions": parse_num(row[imps_i] if 0 <= imps_i < len(row) else 0),
        })
    fields = ["spend", "trials", "fb", "clicks", "impressions"]
    total = aggregate(recs)
    write_elixir_csv(DASH / "data" / "tablet-daily.csv", total, fields)
    niches = {}
    for nid, label in HYPOTHESES:
        daily = aggregate([r for r in recs if r["adset"] == label])
        write_elixir_csv(DASH / "data" / f"tablet-{nid}.csv", daily, fields)
        niches[nid] = {
            "name": label,
            "days": len(daily),
            "spend": round(sum(r["spend"] for r in daily), 2),
            "checkout": int(round(sum(r["trials"] for r in daily))),
        }
    totals = {k: sum(r.get(k, 0) for r in total) for k in fields}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in total]
    meta = {
        "generated_at": now_iso(),
        "project": "tablet",
        "kind": "test",
        "name": "Пилюльница",
        "anchor": "2026-09-18",
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "trials": "initiate_checkout",
            "fb": "landing_page_views",
            "spend": "meta_ads",
            "clicks": "link_clicks",
            "impressions": "impressions",
        },
        "goals": [
            {"id": "initiate_checkout", "key": "checkout", "label": "Checkout", "csv": "trials", "costLabel": "CPA checkout"},
            {"id": "landing_page_view", "key": "lpv", "label": "LPV", "csv": "fb", "costLabel": "Cost / LPV"},
        ],
        "niches": [{"id": nid, "name": label} for nid, label in HYPOTHESES],
        "sources": {"spend": "meta_ads_csv", "file": SRC.name},
        "days": len(total),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "niche_days": niches,
        "currency": "USD",
        "roas_config": None,
    }
    (DASH / "data" / "tablet-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"days": len(total), "spend": round(totals["spend"], 2), "checkout": int(totals["trials"]), "niches": niches}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
