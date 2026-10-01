#!/usr/bin/env python3
"""Bake SkinAI: Meta Ads spend + quiz funnel/values from skin admin."""
from __future__ import annotations

import csv
import json
import os
import ssl
import shutil
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

GEN_TZ = timezone(timedelta(hours=7))
QUIZ_TZ = ZoneInfo("Europe/Moscow")
DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
SRC = Path("/Users/vaceslavlusnikov/Downloads/菏泽安荧网络-1-Ad-sets-Oct-1-2026-Oct-1-2026.csv")
ADMIN_DEFAULT = "https://skin-snowy-phi.vercel.app"
ADSETS = [("phi", "phi · impact"), ("mauve", "mauve · routine")]
ADS_FILES = [
    Path("/Users/vaceslavlusnikov/Downloads/菏泽安荧网络-1-Ad-sets-Sep-29-2026-Sep-30-2026 (2).csv"),
    Path("/Users/vaceslavlusnikov/Downloads/菏泽安荧网络-1-Ad-sets-Sep-30-2026-Sep-30-2026.csv"),
    SRC,
]
FIELDS = ["spend", "installs", "trials", "sold", "fb", "contact_sent", "clicks", "impressions"]
SPEND_KEYS = ("spend", "fb", "clicks", "impressions")
QUIZ_KEYS = ("installs", "trials", "sold", "contact_sent")


def now_iso() -> str:
    return datetime.now(GEN_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


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


def load_secrets() -> dict[str, str]:
    out: dict[str, str] = {}
    path = DASH / "secrets.env"
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_sessions(base: str, password: str) -> list[dict]:
    ctx = ssl_context()
    login = urllib.request.Request(
        base.rstrip("/") + "/api/admin/login",
        data=json.dumps({"password": password}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(login, timeout=30, context=ctx) as resp:
        cookie = resp.headers.get("Set-Cookie") or ""
        if resp.status >= 400:
            raise SystemExit(f"Skin admin login failed: HTTP {resp.status}")
    token = ""
    for part in cookie.split(";"):
        if part.strip().startswith("skin_admin="):
            token = part.strip().split("=", 1)[1]
            break
    if not token:
        raise SystemExit("Skin admin login: no skin_admin cookie")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/admin/sessions",
        headers={"Accept": "application/json", "Cookie": f"skin_admin={token}"},
    )
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        data = json.loads(resp.read().decode())
    if not isinstance(data, list):
        raise SystemExit("Skin admin sessions: expected a list")
    return data


def session_day(row: dict) -> str | None:
    raw = str(row.get("createdAt") or row.get("updatedAt") or "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(QUIZ_TZ)
    except ValueError:
        return None
    return dt.strftime("%d.%m.%Y")


def session_sheet(row: dict) -> str:
    utm = row.get("utm") if isinstance(row.get("utm"), dict) else {}
    term = unquote(str(utm.get("utm_term") or "")).strip().lower()
    if term == "phi":
        return "phi"
    if term == "mauve":
        return "mauve"
    if str(row.get("door") or "") == "routine":
        return "mauve"
    return "phi"


def empty_slot(date: str) -> dict:
    return {"date": date, **{k: 0.0 for k in FIELDS}}


def add_into(slot: dict, rec: dict, keys: tuple[str, ...]) -> None:
    for k in keys:
        slot[k] = (slot.get(k) or 0) + (rec.get(k) or 0)


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


def median(nums: list[float]) -> float | None:
    if not nums:
        return None
    s = sorted(nums)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def quiz_values(sessions: list[dict]) -> dict:
    def subset(door: str) -> list[dict]:
        return [r for r in sessions if str(r.get("door") or "") == door]

    def stats(rows: list[dict]) -> dict:
        done = [r for r in rows if r.get("completed")]
        pay = Counter()
        scores: list[float] = []
        score_max = None
        for r in done:
            answers = r.get("answers") if isinstance(r.get("answers"), dict) else {}
            plan = r.get("plan") if isinstance(r.get("plan"), dict) else {}
            pay_key = answers.get("impact_pay") or answers.get("routine_pay")
            if pay_key:
                pay[str(pay_key)] += 1
            if plan.get("score") is not None:
                scores.append(float(plan["score"]))
                if plan.get("scoreMax") is not None:
                    score_max = float(plan["scoreMax"])
        out = {
            "sessions": len(rows),
            "completed": len(done),
            "offers": sum(1 for r in rows if r.get("offerClicked")),
            "emails": sum(1 for r in rows if str(r.get("email") or "").strip()),
            "first_step_drop": sum(1 for r in rows if not r.get("completed") and len(r.get("path") or []) <= 1),
            "pay": {
                "clear_skin": pay.get("clear_skin", 0),
                "serum": pay.get("serum", 0),
                "stop": pay.get("stop", 0),
            },
        }
        if scores:
            out["median_score"] = median(scores)
            out["score_max"] = score_max
            out["scores_n"] = len(scores)
        return out

    impact = stats(subset("impact"))
    routine = stats(subset("routine"))
    impact["label"] = "Насколько мешает"
    impact["sheet"] = "phi"
    routine["label"] = "Как у конкурентов"
    routine["sheet"] = "mauve"
    age = Counter()
    skin = Counter()
    for r in subset("routine"):
        if not r.get("completed"):
            continue
        answers = r.get("answers") if isinstance(r.get("answers"), dict) else {}
        if answers.get("routine_age"):
            age[str(answers["routine_age"])] += 1
        if answers.get("routine_type"):
            skin[str(answers["routine_type"])] += 1
    routine["age"] = dict(age)
    routine["skin_type"] = dict(skin)
    return {"impact": impact, "routine": routine, "all": stats(sessions)}


def adset_sheet(name: str) -> str | None:
    n = str(name or "").strip().lower()
    if n in ("phi", "mauve", "creo5"):
        return n
    if "creo 5" in n:
        return "creo5"
    return None


def parse_ads_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    if not rows:
        return []
    headers = rows[0]
    idx = {h: i for i, h in enumerate(headers)}

    def col(*names: str) -> int:
        for n in names:
            if n in idx:
                return idx[n]
        return -1

    date_i, name_i = col("Reporting starts"), col("Ad set name")
    spend_i, clicks_i = col("Amount spent (USD)"), col("Link clicks")
    imps_i, lpv_i = col("Impressions"), col("Landing page views")
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
            "adset": name.lower(),
            "spend": parse_num(row[spend_i] if 0 <= spend_i < len(row) else 0),
            "fb": parse_num(row[lpv_i] if 0 <= lpv_i < len(row) else 0),
            "clicks": parse_num(row[clicks_i] if 0 <= clicks_i < len(row) else 0),
            "impressions": parse_num(row[imps_i] if 0 <= imps_i < len(row) else 0),
        })
    return recs


def parse_meta_ads() -> tuple[list[dict], list[str]]:
    dest = DASH / "data" / "skinai-meta-ads.csv"
    by: dict[tuple[str, str], dict] = {}
    latest = None
    for path in ADS_FILES:
        if not path.exists():
            continue
        latest = path
        for rec in parse_ads_file(path):
            by[(rec["date"], rec["adset"])] = rec
    if not latest:
        raise SystemExit("Missing SkinAI Meta CSV")
    shutil.copyfile(latest, dest)
    recs = [r for r in by.values() if "creo 5" not in r["adset"]]
    known = {nid for nid, _ in ADSETS}
    unknown = sorted({r["adset"] for r in recs} - known)
    return recs, unknown


def main() -> None:
    secrets = load_secrets()
    password = (os.environ.get("SKIN_ADMIN_PASSWORD") or secrets.get("SKIN_ADMIN_PASSWORD") or "").strip()
    if not password:
        raise SystemExit("Need SKIN_ADMIN_PASSWORD in secrets.env or env")
    admin = (os.environ.get("SKIN_ADMIN_URL") or secrets.get("SKIN_ADMIN_URL") or ADMIN_DEFAULT).rstrip("/")
    ads, unknown = parse_meta_ads()
    sessions = fetch_sessions(admin, password)
    by: dict[str, dict[str, dict]] = {nid: {} for nid, _ in ADSETS}
    by["total"] = {}

    def slot(sheet: str, date: str) -> dict:
        return by[sheet].setdefault(date, empty_slot(date))

    for rec in ads:
        add_into(slot("total", rec["date"]), rec, SPEND_KEYS)
        sheet = adset_sheet(rec["adset"])
        if sheet:
            add_into(slot(sheet, rec["date"]), rec, SPEND_KEYS)
    for row in sessions:
        date = session_day(row)
        if not date:
            continue
        quiz = {
            "installs": 1,
            "trials": 1 if row.get("completed") else 0,
            "sold": 1 if row.get("offerClicked") else 0,
            "contact_sent": 1 if str(row.get("email") or "").strip() else 0,
        }
        add_into(slot("total", date), quiz, QUIZ_KEYS)
        add_into(slot(session_sheet(row), date), quiz, QUIZ_KEYS)

    def dump(sheet: str) -> list[dict]:
        rows = [v for v in by[sheet].values() if any(v.get(k) for k in FIELDS)]
        write_elixir_csv(DASH / "data" / (f"skinai-{sheet}.csv" if sheet != "total" else "skinai-daily.csv"), rows)
        return rows

    total = dump("total")
    niches = {}
    for nid, label in ADSETS:
        daily = dump(nid)
        niches[nid] = {
            "name": label,
            "days": len(daily),
            "spend": round(sum(r["spend"] for r in daily), 2),
            "sessions": int(round(sum(r["installs"] for r in daily))),
            "completed": int(round(sum(r["trials"] for r in daily))),
            "offers": int(round(sum(r["sold"] for r in daily))),
            "emails": int(round(sum(r["contact_sent"] for r in daily))),
            "lpv": int(round(sum(r["fb"] for r in daily))),
        }
    totals = {k: sum(r.get(k, 0) for r in total) for k in FIELDS}
    dates = [datetime.strptime(r["date"], "%d.%m.%Y") for r in total]
    values = quiz_values(sessions)
    meta = {
        "generated_at": now_iso(),
        "project": "skinai",
        "kind": "test",
        "name": "SkinAI",
        "anchor": min(dates).strftime("%Y-%m-%d") if dates else None,
        "until": max(dates).strftime("%Y-%m-%d") if dates else None,
        "metric_map": {
            "installs": "quiz_sessions",
            "trials": "quiz_completed",
            "sold": "offer_39_clicks",
            "contact_sent": "emails",
            "fb": "landing_page_views",
            "spend": "meta_ads",
            "clicks": "link_clicks",
            "impressions": "impressions",
        },
        "goals": [
            {"id": "quiz_session", "key": "session", "label": "Сессии", "csv": "installs", "costLabel": "Cost / сессия"},
            {"id": "quiz_complete", "key": "completed", "label": "Дошли до итога", "csv": "trials", "costLabel": "CPA итог"},
            {"id": "offer_click", "key": "offer", "label": "$39 клик", "csv": "sold", "costLabel": "Cost / $39"},
            {"id": "email", "key": "email", "label": "Почты", "csv": "contact_sent", "costLabel": "Cost / почта"},
            {"id": "landing_page_view", "key": "lpv", "label": "LPV", "csv": "fb", "costLabel": "Cost / LPV"},
        ],
        "niches": [{"id": nid, "name": label} for nid, label in ADSETS],
        "unknown_adsets": unknown,
        "sources": {
            "spend": "meta_ads_csv",
            "file": SRC.name,
            "quiz": admin + "/admin",
        },
        "quiz": values,
        "days": len(total),
        "totals": {k: (round(v, 2) if k == "spend" else int(round(v))) for k, v in totals.items()},
        "niche_days": niches,
        "currency": "USD",
        "roas_config": None,
    }
    (DASH / "data" / "skinai-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "days": len(total),
        "spend": round(totals["spend"], 2),
        "sessions": int(totals["installs"]),
        "completed": int(totals["trials"]),
        "offers": int(totals["sold"]),
        "emails": int(totals["contact_sent"]),
        "lpv": int(totals["fb"]),
        "niches": niches,
        "quiz": values,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
