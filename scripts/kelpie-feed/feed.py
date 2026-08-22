"""Kelpie auto-feed: PostHog funnel (Meta spend comes from CSV upload in dashboard)."""

from __future__ import annotations

import csv
import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


# Совпадает с колонками Hupp/Planto daily, чтобы parseSheet не ломался.
# mapping: installs=visitors, trials=quiz_start, fb=quiz_complete,
# sold=checkout lead, purchase=gift email, contact_sent=appstore.
CSV_HEADERS = (
    "date", "spend", "installs", "trials", "sold", "fb", "purchase",
    "contact_info", "form_submit", "contact_sent", "clicks", "impressions",
)
FUNNEL_KEYS = ("installs", "trials", "fb", "sold", "purchase", "contact_sent")
ADS_KEYS = ("spend", "clicks", "impressions")


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _load_secrets_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _secret(name: str, work_dir: Path) -> str:
    env = os.environ.get(name, "").strip()
    if env:
        return env
    for p in (work_dir / "secrets.env", work_dir / "supabase" / "secrets.env"):
        val = _load_secrets_file(p).get(name, "").strip()
        if val:
            return val
    kelpie_env = Path.home() / "Desktop" / "dash kelpie" / ".env.local"
    val = _load_secrets_file(kelpie_env).get(name, "").strip()
    return val


def _hogql(host: str, project_id: str, token: str, sql: str, name: str) -> list:
    url = f"{host.rstrip('/')}/api/projects/{project_id}/query/"
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}, "name": name}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        err_body = error.read()[:600].decode("utf-8", errors="replace")
        raise RuntimeError(f"PostHog HTTP {error.code}: {err_body}") from error
    if data.get("error"):
        raise RuntimeError(f"PostHog: {data['error']}")
    return data.get("results") or []


def _in_list(events: list[str]) -> str:
    quoted = ", ".join("'" + e.replace("'", "\\'") + "'" for e in events)
    return f"({quoted})"


def _daily_sql(cfg: dict, until: date) -> str:
    tz = cfg.get("timezone") or "Europe/Moscow"
    anchor = cfg["anchor"]
    ev = cfg.get("events") or {}
    visitors = ev.get("visitors") or ["$pageview", "page_view"]
    qs = ev.get("quiz_start") or ["quiz_started"]
    qc = ev.get("quiz_complete") or ["survey_completed", "funnel_complete"]
    co = ev.get("checkout") or ["card_submitted", "Lead_Submitted"]
    gift = ev.get("gift") or ["email_submitted", "checkout_email"]
    app = ev.get("appstore") or ["app_store_click"]
    day = f"toDate(toTimeZone(timestamp, '{tz}'))"
    return f"""
SELECT
  {day} AS day,
  uniqIf(person_id, event IN {_in_list(visitors)}) AS visitors,
  uniqIf(person_id, event IN {_in_list(qs)}) AS qs,
  uniqIf(person_id, event IN {_in_list(qc)}) AS qc,
  uniqIf(person_id, event IN {_in_list(co)}) AS checkout,
  uniqIf(person_id, event IN {_in_list(gift)}) AS gift,
  uniqIf(person_id, event IN {_in_list(app)}) AS appstore
FROM events
WHERE timestamp >= toDateTime('{anchor} 00:00:00')
  AND timestamp < toDateTime('{(until + timedelta(days=1)).isoformat()} 00:00:00')
GROUP BY day
ORDER BY day
""".strip()


def _load_existing(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    result: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                day = datetime.strptime(row["date"], "%d.%m.%Y").date()
            except (KeyError, ValueError):
                continue
            item = {"date": row["date"]}
            for key in CSV_HEADERS[1:]:
                raw = row.get(key) or 0
                item[key] = float(raw) if key in ADS_KEYS else int(float(raw or 0))
            result[day.isoformat()] = item
    return result


def _empty_row(day: date) -> dict:
    item = {"date": day.strftime("%d.%m.%Y")}
    for key in CSV_HEADERS[1:]:
        item[key] = 0.0 if key in ADS_KEYS else 0
    return item


def _write_csv(path: Path, by_day: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [by_day[k] for k in sorted(by_day)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, 0) for k in CSV_HEADERS})


def run_feed(work_dir: Path, config_path: Path) -> int:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    tz_name = cfg.get("timezone") or "Europe/Moscow"
    today = datetime.now(ZoneInfo(tz_name)).date()
    anchor = date.fromisoformat(cfg["anchor"])
    host = (os.environ.get("POSTHOG_HOST") or cfg.get("posthog_host") or "https://us.posthog.com").rstrip("/")
    project_id = os.environ.get("POSTHOG_PROJECT_ID") or cfg.get("posthog_project_id")
    token = _secret("POSTHOG_API_KEY", work_dir)
    if not token or not project_id:
        raise RuntimeError("POSTHOG_API_KEY / POSTHOG_PROJECT_ID не заданы")

    daily_path = work_dir / cfg.get("daily_csv", "data/kelpie-daily.csv")
    meta_path = work_dir / cfg.get("meta_json", "data/kelpie-meta.json")

    sql = _daily_sql(cfg, today)
    print("PostHog HogQL daily…")
    results = _hogql(host, str(project_id), token, sql, "kelpie daily funnel")

    by_day: dict[str, dict] = {}
    day = anchor
    while day <= today:
        # Spend/clicks всегда из CSV в дашборде — в фиде ads = 0, воронку переписываем.
        row = _empty_row(day)
        for k in ADS_KEYS:
            row[k] = 0
        by_day[day.isoformat()] = row
        day += timedelta(days=1)

    for rec in results:
        raw_day = str(rec[0] or "")[:10]
        if not raw_day:
            continue
        row = by_day.get(raw_day) or _empty_row(date.fromisoformat(raw_day))
        row["installs"] = int(rec[1] or 0)
        row["trials"] = int(rec[2] or 0)
        row["fb"] = int(rec[3] or 0)
        row["sold"] = int(rec[4] or 0)
        row["purchase"] = int(rec[5] or 0)
        row["contact_sent"] = int(rec[6] or 0)
        by_day[raw_day] = row

    _write_csv(daily_path, by_day)
    until = max(date.fromisoformat(k) for k in by_day) if by_day else today
    meta = {
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "project": "kelpie",
        "anchor": cfg["anchor"],
        "until": until.isoformat(),
        "timezone": tz_name,
        "source": "posthog",
        "website": cfg.get("website"),
        "kpi": cfg.get("kpi_label") or "Checkout Lead",
        "metric_map": {
            "installs": "posthog_visitors",
            "trials": "quiz_started",
            "fb": "quiz_complete",
            "sold": "checkout_lead",
            "purchase": "gift_email",
            "contact_sent": "app_store_click",
            "spend": "meta_csv_upload",
            "clicks": "meta_csv_upload",
            "impressions": "meta_csv_upload",
        },
        "days": len(by_day),
        "errors": [],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checkout = sum(int(r.get("sold") or 0) for r in by_day.values())
    visitors = sum(int(r.get("installs") or 0) for r in by_day.values())
    print(f"Kelpie feed ok: {len(by_day)} days, visitors={visitors}, checkout={checkout}, until={until}")
    return 0
