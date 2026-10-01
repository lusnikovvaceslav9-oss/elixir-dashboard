#!/usr/bin/env python3
"""One-shot: bake Ekonomik + Tutorplace daily CSVs for elixir.html.

Sources:
  Ekonomik — ~/бублик , счетчик /data/meta-ads-09-*.csv  (Direct USD; not Desktop/счетчик)
  Tutorplace — ~/туторплейс/data/direct-2026-09-*.csv   (Direct RUB)
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

MSK = timezone(timedelta(hours=7))
DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
BUBLIK = Path("/Users/vaceslavlusnikov/бублик , счетчик ") / "data"
TUTOR = Path("/Users/vaceslavlusnikov/туторплейс") / "data"
FORBIDDEN = Path("/Users/vaceslavlusnikov/Desktop/счетчик")

EKONOMIK_CAMPAIGN = "714245040"
TUTOR_NICHES = [
    ("astro", "Астро", "714559787"),
    ("food", "Дневник питания", "714559866"),
    ("pilates", "Пилатес", "714559844"),
    ("dance", "Танцы", "714559807"),
    ("hiro", "Хиро", "714559825"),
]
TUTOR_BY_CID = {cid: (nid, name) for nid, name, cid in TUTOR_NICHES}
OFFER_NICHE = [
    ("танц", "dance"),
    ("пилат", "pilates"),
    ("питани", "food"),
    ("астро", "astro"),
    ("хиро", "hiro"),
]


def niche_from_offer(name: str) -> str | None:
    n = str(name or "").lower()
    for needle, nid in OFFER_NICHE:
        if needle in n:
            return nid
    return None


def apply_offerrum_bills(recs: list[dict]) -> list[dict]:
    """Overlay CPA bills from offerrum-YYYY-MM-DD.csv onto matching Direct days."""
    bills: dict[tuple[str, str], int] = {}
    for path in TUTOR.glob("offerrum-*.csv"):
        stamp = file_stamp(path)
        try:
            date = datetime.strptime(stamp, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            continue
        rows = read_csv(path)
        if not rows:
            continue
        headers = [h.strip() for h in rows[0]]
        name_i = next((i for i, h in enumerate(headers) if "оффер" in h.lower()), 0)
        bills_i = next((i for i, h in enumerate(headers) if "билл" in h.lower()), -1)
        if bills_i < 0:
            continue
        for row in rows[1:]:
            if not row:
                continue
            nid = niche_from_offer(row[name_i] if name_i < len(row) else "")
            if not nid:
                continue
            n = int(round(parse_num(row[bills_i] if bills_i < len(row) else 0)))
            key = (date, nid)
            bills[key] = max(bills.get(key, 0), n)
    if not bills:
        return recs
    out = [dict(r) for r in recs]
    for rec in out:
        extra = bills.get((rec["date"], rec["niche"]))
        if extra:
            rec["sold"] = max(rec.get("sold") or 0, extra)
    return out

GOAL_TRIALS = "632612615"
GOAL_SOLD = "632571942"
GOAL_CONTACT_INFO = "640536982"
GOAL_CONTACT_SENT = "640536984"
GOAL_FILE = "640536980"
GOAL_SOCIAL = "640536983"
GOAL_START_TRIAL = "661785580"


def now_iso() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_num(val) -> float:
    s = str(val or "").strip().replace("\xa0", "").replace(" ", "")
    if not s or s in {"-", "—", "–"}:
        return 0.0
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_ru_date(val: str) -> str | None:
    s = str(val or "").strip()
    if not s or re.search(r"итого|total", s, re.I):
        return None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{d:02d}.{mo:02d}.{y}"


def file_stamp(path: Path) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if m:
        return m.group(1)
    m = re.search(r"(\d{2})-(\d{2})", path.name)
    if m:
        return f"2026-{m.group(1)}-{m.group(2)}"
    return path.name


def read_csv(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    return list(csv.reader(text.splitlines()))


def col_idx(headers: list[str], *needles: str) -> int:
    lower = [h.lower().replace("\xa0", " ") for h in headers]
    for i, h in enumerate(lower):
        if all(n.lower() in h for n in needles):
            return i
    return -1


def goal_col(headers: list[str], goal_id: str) -> int:
    for i, h in enumerate(headers):
        hl = h.lower()
        if goal_id in h and "конверси" in hl and "cr /" not in hl and "cpa /" not in hl:
            return i
    return -1


def fmt_num(n: float, digits: int = 2) -> str:
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.{digits}f}"


def write_daily(path: Path, header: list[str], rows: list[dict], keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r["date"]] + [fmt_num(r[k]) if k == "spend" else fmt_num(r[k], 0) if k != "spend" else fmt_num(r[k]) for k in keys])


def write_elixir_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    """fields after date; spend keeps decimals, counts are ints."""
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


def build_ekonomik() -> dict:
    if FORBIDDEN.resolve() == BUBLIK.resolve():
        raise SystemExit("Refusing to read Desktop/счетчик")
    files = sorted(BUBLIK.glob("meta-ads-09-*.csv"), key=lambda p: (file_stamp(p), p.name))
    if not files:
        raise SystemExit(f"No ekonomik CSVs in {BUBLIK}")
    by_date: dict[str, tuple[str, dict]] = {}
    for path in files:
        stamp = file_stamp(path)
        rows = read_csv(path)
        if not rows:
            continue
        headers = rows[0]
        date_i = col_idx(headers, "день")
        if date_i < 0:
            date_i = 0
        cid_i = next(
            (i for i, h in enumerate(headers) if "№" in h and "кампан" in h.lower()),
            -1,
        )
        name_i = next(
            (i for i, h in enumerate(headers) if "название" in h.lower() and "кампан" in h.lower()),
            -1,
        )
        spend_i = next((i for i, h in enumerate(headers) if "расход" in h.lower()), 4)
        imps_i = next((i for i, h in enumerate(headers) if "показ" in h.lower()), -1)
        clicks_i = next((i for i, h in enumerate(headers) if "клик" in h.lower() and "ctr" not in h.lower() and "cpc" not in h.lower()), -1)
        sub_i = next((i for i, h in enumerate(headers) if "609953998" in h and "конверси" in h.lower() and "cr /" not in h.lower() and "cpa /" not in h.lower()), -1)
        tg_i = next((i for i, h in enumerate(headers) if "609953996" in h and "конверси" in h.lower() and "cr /" not in h.lower() and "cpa /" not in h.lower()), -1)
        for row in rows[1:]:
            if not row:
                continue
            date = parse_ru_date(row[date_i] if date_i < len(row) else "")
            if not date:
                continue
            cid = str(row[cid_i] if 0 <= cid_i < len(row) else "").strip()
            name = str(row[name_i] if 0 <= name_i < len(row) else "").strip()
            if cid and cid != EKONOMIK_CAMPAIGN and "экономик" not in name.lower():
                continue
            rec = {
                "date": date,
                "spend": parse_num(row[spend_i] if spend_i < len(row) else 0),
                "impressions": parse_num(row[imps_i] if imps_i >= 0 and imps_i < len(row) else 0),
                "clicks": parse_num(row[clicks_i] if clicks_i >= 0 and clicks_i < len(row) else 0),
                "trials": parse_num(row[sub_i] if sub_i >= 0 and sub_i < len(row) else 0),
                "fb": parse_num(row[tg_i] if tg_i >= 0 and tg_i < len(row) else 0),
            }
            prev = by_date.get(date)
            if prev is None or stamp >= prev[0]:
                by_date[date] = (stamp, rec)
    daily = [v[1] for _, v in sorted(by_date.items(), key=lambda kv: datetime.strptime(kv[0], "%d.%m.%Y"))]
    out = DASH / "data" / "ekonomik-daily.csv"
    write_elixir_csv(out, daily, ["spend", "trials", "fb", "clicks", "impressions"])
    totals = {k: sum(r[k] for r in daily) for k in ["spend", "trials", "fb", "clicks", "impressions"]}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in daily]
    meta = {
        "generated_at": now_iso(),
        "project": "ekonomik",
        "anchor": "2026-09-08",
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "trials": "sub_start",
            "fb": "tg_click",
            "spend": "direct_csv",
            "clicks": "direct_csv",
            "impressions": "direct_csv",
        },
        "goals": [
            {"id": "609953998", "key": "sub_start", "label": "Подписки", "csv": "trials", "costLabel": "CPA sub"},
            {"id": "609953996", "key": "tg_click", "label": "TG", "csv": "fb", "costLabel": "CPA TG"},
        ],
        "sources": {"spend": "direct_csv", "bublik": str(BUBLIK)},
        "errors": [],
        "days": len(daily),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "direct_source": "direct_csv",
        "currency": "USD",
        "campaign_id": EKONOMIK_CAMPAIGN,
        "roas_config": None,
    }
    (DASH / "data" / "ekonomik-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"file": str(out), "days": len(daily), "spend": round(totals["spend"], 2), "subs": int(totals["trials"]), "tg": int(totals["fb"])}


def parse_tutor_row(headers: list[str], row: list[str]) -> dict | None:
    date_i = col_idx(headers, "день")
    cid_i = next((i for i, h in enumerate(headers) if "№ кампании" in h.lower() or h.lower().strip() in {"№ кампании", "кампании"}), 1)
    spend_i = next((i for i, h in enumerate(headers) if "расход" in h.lower()), 3)
    clicks_i = next((i for i, h in enumerate(headers) if h.lower().strip() == "клики" or (h.lower().startswith("клик") and "ctr" not in h.lower() and "cpc" not in h.lower())), 4)
    date = parse_ru_date(row[date_i] if 0 <= date_i < len(row) else "")
    if not date:
        return None
    cid = str(row[cid_i] if 0 <= cid_i < len(row) else "").strip()
    if cid not in TUTOR_BY_CID:
        return None

    def g(gid: str) -> float:
        i = goal_col(headers, gid)
        return parse_num(row[i]) if 0 <= i < len(row) else 0.0

    return {
        "date": date,
        "campaign_id": cid,
        "niche": TUTOR_BY_CID[cid][0],
        "spend": parse_num(row[spend_i] if spend_i < len(row) else 0),
        "clicks": parse_num(row[clicks_i] if clicks_i < len(row) else 0),
        "trials": g(GOAL_TRIALS),
        "sold": g(GOAL_SOLD),
        "contact_info": g(GOAL_CONTACT_INFO),
        "contact_sent": g(GOAL_CONTACT_SENT),
        "form_submit": g(GOAL_FILE),
        "fb": g(GOAL_SOCIAL),
        "purchase": g(GOAL_START_TRIAL),
    }


def aggregate_days(recs: list[dict]) -> list[dict]:
    by_date: dict[str, dict] = {}
    keys = ["spend", "clicks", "trials", "sold", "contact_info", "contact_sent", "form_submit", "fb", "purchase"]
    for rec in recs:
        slot = by_date.setdefault(rec["date"], {k: 0.0 for k in keys} | {"date": rec["date"]})
        for k in keys:
            slot[k] += rec.get(k, 0) or 0
    return list(by_date.values())


def build_tutor() -> dict:
    files = sorted(
        [p for p in TUTOR.glob("direct-2026-*.csv")],
        key=lambda p: (file_stamp(p), p.name),
    )
    if not files:
        raise SystemExit(f"No tutorplace Direct CSVs in {TUTOR}")
    # (date, campaign_id) → (stamp, rec)
    by_key: dict[tuple[str, str], tuple[str, dict]] = {}
    for path in files:
        stamp = file_stamp(path)
        rows = read_csv(path)
        if not rows:
            continue
        headers = rows[0]
        for row in rows[1:]:
            rec = parse_tutor_row(headers, row)
            if not rec:
                continue
            key = (rec["date"], rec["campaign_id"])
            prev = by_key.get(key)
            if prev is None or stamp >= prev[0]:
                by_key[key] = (stamp, rec)
    recs = [v[1] for v in by_key.values()]
    recs = apply_offerrum_bills(recs)
    fields = ["spend", "trials", "sold", "fb", "contact_info", "form_submit", "contact_sent", "clicks"]
    total_daily = aggregate_days(recs)
    write_elixir_csv(DASH / "data" / "tutorplace-daily.csv", total_daily, fields)
    niche_days = {}
    for nid, name, cid in TUTOR_NICHES:
        subset = [r for r in recs if r["campaign_id"] == cid]
        daily = aggregate_days(subset)
        write_elixir_csv(DASH / "data" / f"tutorplace-{nid}.csv", daily, fields)
        niche_days[nid] = {"name": name, "campaign_id": cid, "days": len(daily), "spend": round(sum(r["spend"] for r in daily), 2)}
    totals = {k: sum(r.get(k, 0) for r in total_daily) for k in fields}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in total_daily]
    meta = {
        "generated_at": now_iso(),
        "project": "tutorplace",
        "anchor": "2026-09-18",
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "trials": "tryConv",
            "sold": "trialPay",
            "fb": "social",
            "contact_info": "contact_info",
            "form_submit": "fileDownload",
            "contact_sent": "contacts",
            "spend": "direct_csv",
            "clicks": "direct_csv",
        },
        "goals": [
            {"id": GOAL_TRIALS, "key": "tryConv", "label": "Заполнили данные для оплаты", "csv": "trials", "costLabel": "Cost / tryConv"},
            {"id": GOAL_SOLD, "key": "trialPay", "label": "Оплата триала", "csv": "sold"},
            {"id": GOAL_CONTACT_INFO, "key": "contact_info", "label": "Заполнил контактные данные", "csv": "contact_info"},
            {"id": GOAL_CONTACT_SENT, "key": "contacts", "label": "Отправил контактные данные", "csv": "contact_sent"},
            {"id": GOAL_FILE, "key": "fileDownload", "label": "Скачивание файла", "csv": "form_submit"},
            {"id": GOAL_SOCIAL, "key": "social", "label": "Переход в соцсеть", "csv": "fb"},
        ],
        "niches": [{"id": nid, "name": name, "campaign_id": cid} for nid, name, cid in TUTOR_NICHES],
        "sources": {"spend": "direct_csv", "tutorplace": str(TUTOR)},
        "errors": [],
        "days": len(total_daily),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "niche_days": niche_days,
        "direct_source": "direct_csv",
        "currency": "RUB",
        "roas_config": None,
    }
    (DASH / "data" / "tutorplace-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "days": len(total_daily),
        "spend": round(totals["spend"], 2),
        "tryConv": int(totals["trials"]),
        "niches": niche_days,
    }


def main() -> None:
    ek = build_ekonomik()
    tp = build_tutor()
    print(json.dumps({"ekonomik": ek, "tutorplace": tp}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
