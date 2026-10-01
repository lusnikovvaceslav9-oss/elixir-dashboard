#!/usr/bin/env python3
"""Bake Quadcode Meta Ads (ad sets) into elixir daily + games / site / Discord sheets."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=7))
DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
SRC = Path(
    "/Users/vaceslavlusnikov/Downloads/_-_-YL-50137-3-Ad-sets-Sep-30-2026-Sep-30-2026.csv"
)
CAMPAIGNS = Path(
    "/Users/vaceslavlusnikov/Downloads/_-_-YL-50137-3-Campaigns-Sep-17-2026-Sep-30-2026.csv"
)
FIELDS = ["spend", "regs", "qregs", "clicks", "impressions"]
SHEETS = [
    ("games", "Игры · лендинг"),
    ("site", "Сайт"),
    ("discord", "Discord"),
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


def write_elixir_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + FIELDS)
        for r in sorted(rows, key=lambda x: datetime.strptime(x["date"], "%d.%m.%Y")):
            out = [r["date"]]
            for k in FIELDS:
                v = r.get(k, 0) or 0
                out.append(f"{v:.2f}" if k == "spend" else str(int(round(v))))
            w.writerow(out)


def classify(name: str) -> str | None:
    n = str(name or "").strip().lower()
    if not n:
        return None
    if n.startswith("new pack") or "jggl" in n:
        return None
    if n == "join" or "discord" in n:
        return "discord"
    if "no game" in n:
        return "site"
    if n == "game - lead" or n.startswith("game"):
        return "games"
    return None


def aggregate(recs: list[dict]) -> list[dict]:
    by_date: dict[str, dict] = {}
    for rec in recs:
        slot = by_date.setdefault(rec["date"], {k: 0.0 for k in FIELDS} | {"date": rec["date"]})
        for k in FIELDS:
            slot[k] += rec.get(k, 0) or 0
    return [v for v in by_date.values() if any(v[k] for k in FIELDS)]


def read_daily(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    out = []
    for r in rows:
        rec = {"date": r.get("date") or ""}
        if not rec["date"]:
            continue
        for k in FIELDS:
            rec[k] = parse_num(r.get(k))
        out.append(rec)
    return out


def merge_daily(old: list[dict], new: list[dict]) -> list[dict]:
    by = {r["date"]: r for r in old}
    for r in new:
        by[r["date"]] = r
    return [v for v in by.values() if any(v.get(k) for k in FIELDS)]


def col_idx(headers: list[str], *names: str) -> int:
    idx = {h: i for i, h in enumerate(headers)}
    for n in names:
        if n in idx:
            return idx[n]
    return -1


def cell(row: list[str], i: int) -> str:
    if i < 0 or i >= len(row):
        return ""
    return row[i]


def parse_adsets(path: Path) -> tuple[list[dict], list[str]]:
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    if not rows:
        raise SystemExit("Empty ad-set CSV")
    headers = rows[0]
    date_i = col_idx(headers, "Reporting starts")
    name_i = col_idx(headers, "Ad set name")
    spend_i = col_idx(headers, "Amount spent (USD)")
    res_i = col_idx(headers, "Results")
    ind_i = col_idx(headers, "Result indicator")
    web_i = col_idx(headers, "Website registrations completed")
    reg_i = col_idx(headers, "Registrations completed")
    clicks_i = col_idx(headers, "Clicks (all)", "Link clicks")
    imps_i = col_idx(headers, "Impressions")
    recs: list[dict] = []
    unknown: set[str] = set()
    for row in rows[1:]:
        if not row or name_i >= len(row):
            continue
        name = str(cell(row, name_i) or "").strip()
        kind = classify(name)
        if kind is None:
            if name:
                unknown.add(name)
            continue
        date = iso_to_ru(cell(row, date_i))
        if not date:
            continue
        indicator = str(cell(row, ind_i) or "").lower()
        results = parse_num(cell(row, res_i))
        qregs = results if "discordjoin" in indicator else 0.0
        web = parse_num(cell(row, web_i))
        if not web:
            web = parse_num(cell(row, reg_i))
        recs.append({
            "date": date,
            "adset": name,
            "kind": kind,
            "spend": parse_num(cell(row, spend_i)),
            "regs": web,
            "qregs": qregs,
            "clicks": parse_num(cell(row, clicks_i)),
            "impressions": parse_num(cell(row, imps_i)),
        })
    return recs, sorted(unknown)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Missing ad-set CSV: {SRC}")
    shutil.copyfile(SRC, DASH / "data" / "quadcode-meta-adsets.csv")
    if CAMPAIGNS.exists():
        shutil.copyfile(CAMPAIGNS, DASH / "data" / "quadcode-meta-campaigns.csv")

    recs, unknown = parse_adsets(SRC)
    total = merge_daily(read_daily(DASH / "data" / "quadcode-daily.csv"), aggregate(recs))
    write_elixir_csv(DASH / "data" / "quadcode-daily.csv", total)

    niches: dict[str, dict] = {}
    for nid, label in SHEETS:
        daily = merge_daily(
            read_daily(DASH / "data" / f"quadcode-{nid}.csv"),
            aggregate([r for r in recs if r["kind"] == nid]),
        )
        write_elixir_csv(DASH / "data" / f"quadcode-{nid}.csv", daily)
        niches[nid] = {
            "name": label,
            "days": len(daily),
            "spend": round(sum(r["spend"] for r in daily), 2),
            "regs": int(round(sum(r["regs"] for r in daily))),
            "qregs": int(round(sum(r["qregs"] for r in daily))),
            "clicks": int(round(sum(r["clicks"] for r in daily))),
        }

    totals = {k: sum(r.get(k, 0) for r in total) for k in FIELDS}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in total]
    meta = {
        "generated_at": now_iso(),
        "project": "quadcode",
        "kind": "live",
        "name": "Quadcode AI",
        "anchor": "2026-09-17",
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "regs": "website_complete_registration",
            "qregs": "discord_join",
            "spend": "meta_ads",
            "clicks": "clicks_all",
            "impressions": "impressions",
        },
        "niches": [{"id": nid, "name": label} for nid, label in SHEETS],
        "unknown_adsets": unknown,
        "sources": {
            "spend": "meta_ads_csv",
            "file": SRC.name,
            "campaigns": CAMPAIGNS.name if CAMPAIGNS.exists() else None,
            "google_sheet": "1GRLgK6CnvH4innf7n4k8K_bhQ9nsTl2Q",
        },
        "days": len(total),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "niche_days": niches,
        "currency": "USD",
        "roas_config": None,
    }
    (DASH / "data" / "quadcode-meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "days": len(total),
        "spend": round(totals["spend"], 2),
        "regs": int(round(totals["regs"])),
        "qregs": int(round(totals["qregs"])),
        "niches": niches,
        "unknown": unknown,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
