#!/usr/bin/env python3
"""Bake Бездна test Meta Ads (campaign) CSV into elixir daily + campaign sheets."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=7))
DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
SRC = Path("/Users/vaceslavlusnikov/Downloads/-_16581_-ELIXIR_adskill_-3_A-Campaigns-27-Sep-2026-30-Sep-2026.csv")
CAMPAIGNS = [
    ("leads", "«Играть» · 29/09", "bezdna - US - 29/09"),
    ("reg", "Аккаунт · 30/09", "bezdna - US - 30/09 - reg"),
]


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
    keys = ["spend", "trials", "sold", "fb", "clicks", "impressions"]
    by_date: dict[str, dict] = {}
    for rec in recs:
        slot = by_date.setdefault(rec["date"], {k: 0.0 for k in keys} | {"date": rec["date"]})
        for k in keys:
            slot[k] += rec.get(k, 0) or 0
    return [v for v in by_date.values() if any(v[k] for k in keys)]


def col(idx: dict, *names: str) -> int:
    for n in names:
        if n in idx:
            return idx[n]
    return -1


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Missing source CSV: {SRC}")
    shutil.copyfile(SRC, DASH / "data" / "bezdna-meta-ads.csv")
    rows = list(csv.reader(SRC.read_text(encoding="utf-8-sig").splitlines()))
    if not rows:
        raise SystemExit("Empty CSV")
    headers = rows[0]
    idx = {h: i for i, h in enumerate(headers)}
    date_i = col(idx, "Reporting starts")
    name_i = col(idx, "Campaign name")
    spend_i = col(idx, "Amount spent (USD)")
    leads_i = col(idx, "Website leads", "Leads")
    regs_i = col(idx, "Registrations completed")
    clicks_i = col(idx, "Link clicks")
    imps_i = col(idx, "Impressions")
    lpv_i = col(idx, "Landing page views")
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
            "campaign": name,
            "spend": parse_num(row[spend_i] if 0 <= spend_i < len(row) else 0),
            "trials": parse_num(row[leads_i] if 0 <= leads_i < len(row) else 0),
            "sold": parse_num(row[regs_i] if 0 <= regs_i < len(row) else 0),
            "fb": parse_num(row[lpv_i] if 0 <= lpv_i < len(row) else 0),
            "clicks": parse_num(row[clicks_i] if 0 <= clicks_i < len(row) else 0),
            "impressions": parse_num(row[imps_i] if 0 <= imps_i < len(row) else 0),
        })
    fields = ["spend", "trials", "sold", "fb", "clicks", "impressions"]
    total = aggregate(recs)
    write_elixir_csv(DASH / "data" / "bezdna-daily.csv", total, fields)
    niches = {}
    by_name = {src: (nid, label) for nid, label, src in CAMPAIGNS}
    for nid, label, src in CAMPAIGNS:
        daily = aggregate([r for r in recs if r["campaign"] == src])
        write_elixir_csv(DASH / "data" / f"bezdna-{nid}.csv", daily, fields)
        niches[nid] = {
            "name": label,
            "campaign": src,
            "days": len(daily),
            "spend": round(sum(r["spend"] for r in daily), 2),
            "leads": int(round(sum(r["trials"] for r in daily))),
            "regs": int(round(sum(r["sold"] for r in daily))),
        }
    unknown = sorted({r["campaign"] for r in recs} - set(by_name))
    totals = {k: sum(r.get(k, 0) for r in total) for k in fields}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in total]
    meta = {
        "generated_at": now_iso(),
        "project": "bezdna",
        "kind": "test",
        "name": "Бездна",
        "anchor": "2026-09-29",
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "fb": "page_view",
            "trials": "lead_play_click",
            "sold": "complete_registration",
            "spend": "meta_ads",
            "clicks": "link_clicks",
            "impressions": "impressions",
        },
        "goals": [
            {"id": "landing_page_view", "key": "lpv", "label": "Открыл сайт", "csv": "fb", "costLabel": "Cost / открытие"},
            {"id": "website_lead", "key": "lead", "label": "Нажал «Играть»", "csv": "trials", "costLabel": "Cost / «Играть»"},
            {"id": "registration", "key": "reg", "label": "Создал аккаунт", "csv": "sold", "costLabel": "Cost / аккаунт"},
        ],
        "niches": [{"id": nid, "name": label, "campaign": src} for nid, label, src in CAMPAIGNS],
        "unknown_campaigns": unknown,
        "sources": {"spend": "meta_ads_csv", "file": SRC.name},
        "days": len(total),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "niche_days": niches,
        "currency": "USD",
        "roas_config": None,
    }
    (DASH / "data" / "bezdna-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"days": len(total), "spend": round(totals["spend"], 2), "leads": int(totals["trials"]), "regs": int(totals["sold"]), "niches": niches, "unknown": unknown}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
