"""Kelpie auto-feed: spend + воронка с kelpie-dash (не крео).

Источник: GET https://kelpie-dash.vercel.app/api/dashboard → days[]
(spend/clicks из Meta в том дэше, воронка PostHog). /api/creatives не зовём.
PostHog напрямую — только если дэш недоступен (spend тогда 0).
"""

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


KELPIE_DASH_DEFAULT = "https://kelpie-dash.vercel.app"


def _http_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_kelpie_dash_days(base_url: str) -> list[dict]:
    """Сутки обзора дэша: spend/clicks + воронка. Без карточек крео."""
    url = base_url.rstrip("/") + "/api/dashboard"
    data = _http_json(url)
    days = data.get("days") if isinstance(data, dict) else None
    if not isinstance(days, list):
        raise RuntimeError(f"kelpie-dash: нет days в {url}")
    out: list[dict] = []
    for rec in days:
        if not isinstance(rec, dict):
            continue
        day = str(rec.get("d") or "")[:10]
        if len(day) != 10:
            continue
        out.append(
            {
                "d": day,
                "spend": float(rec.get("spend") or 0),
                "clicks": int(float(rec.get("clicks") or 0)),
                "installs": int(rec.get("visitors") or 0),
                "trials": int(rec.get("qs") or 0),
                "fb": int(rec.get("qc") or 0),
                "sold": int(rec.get("co") or 0),
                "purchase": int(rec.get("gift") or 0),
                "contact_sent": int(rec.get("app") or 0),
            }
        )
    return out


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


def _ensure_range(by_day: dict[str, dict], start: date, until: date) -> None:
    day = start
    while day <= until:
        by_day.setdefault(day.isoformat(), _empty_row(day))
        day += timedelta(days=1)


def _apply_dash_days(by_day: dict[str, dict], dash_days: list[dict]) -> None:
    for rec in dash_days:
        raw_day = rec["d"]
        row = by_day.get(raw_day) or _empty_row(date.fromisoformat(raw_day))
        row["spend"] = round(float(rec.get("spend") or 0), 2)
        row["clicks"] = int(rec.get("clicks") or 0)
        row["installs"] = int(rec.get("installs") or 0)
        row["trials"] = int(rec.get("trials") or 0)
        row["fb"] = int(rec.get("fb") or 0)
        row["sold"] = int(rec.get("sold") or 0)
        row["purchase"] = int(rec.get("purchase") or 0)
        row["contact_sent"] = int(rec.get("contact_sent") or 0)
        by_day[raw_day] = row


def _apply_posthog(by_day: dict[str, dict], results: list) -> None:
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


def run_feed(work_dir: Path, config_path: Path) -> int:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    tz_name = cfg.get("timezone") or "Europe/Moscow"
    today = datetime.now(ZoneInfo(tz_name)).date()
    anchor = date.fromisoformat(cfg["anchor"])
    dash_url = (
        os.environ.get("KELPIE_DASH_URL")
        or cfg.get("kelpie_dash_url")
        or KELPIE_DASH_DEFAULT
    ).rstrip("/")

    daily_path = work_dir / cfg.get("daily_csv", "data/kelpie-daily.csv")
    meta_path = work_dir / cfg.get("meta_json", "data/kelpie-meta.json")
    errors: list[str] = []
    source = "kelpie-dash"
    by_day: dict[str, dict] = {}

    dash_days: list[dict] = []
    try:
        print(f"Kelpie dash {dash_url}/api/dashboard …")
        dash_days = fetch_kelpie_dash_days(dash_url)
        print(f"  dash days: {len(dash_days)}")
    except Exception as exc:
        errors.append(f"kelpie-dash: {exc}")
        print(f"  dash failed: {exc}")
        source = "posthog"

    if dash_days:
        first = min(date.fromisoformat(r["d"]) for r in dash_days)
        last = max(date.fromisoformat(r["d"]) for r in dash_days)
        _ensure_range(by_day, min(anchor, first), max(today, last))
        _apply_dash_days(by_day, dash_days)
    else:
        host = (os.environ.get("POSTHOG_HOST") or cfg.get("posthog_host") or "https://us.posthog.com").rstrip("/")
        project_id = os.environ.get("POSTHOG_PROJECT_ID") or cfg.get("posthog_project_id")
        token = _secret("POSTHOG_API_KEY", work_dir)
        if not token or not project_id:
            raise RuntimeError("kelpie-dash недоступен и нет POSTHOG_API_KEY / POSTHOG_PROJECT_ID")
        _ensure_range(by_day, anchor, today)
        print("PostHog HogQL daily (fallback, spend=0)…")
        sql = _daily_sql(cfg, today)
        results = _hogql(host, str(project_id), token, sql, "kelpie daily funnel")
        _apply_posthog(by_day, results)

    _write_csv(daily_path, by_day)
    until = max(date.fromisoformat(k) for k in by_day) if by_day else today
    spend_total = round(sum(float(r.get("spend") or 0) for r in by_day.values()), 2)
    checkout = sum(int(r.get("sold") or 0) for r in by_day.values())
    visitors = sum(int(r.get("installs") or 0) for r in by_day.values())
    meta = {
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "project": "kelpie",
        "anchor": cfg["anchor"],
        "until": until.isoformat(),
        "timezone": tz_name,
        "source": source,
        "kelpie_dash_url": dash_url,
        "website": cfg.get("website"),
        "kpi": cfg.get("kpi_label") or "Checkout Lead",
        "metric_map": {
            "installs": "kelpie_dash_visitors",
            "trials": "kelpie_dash_quiz_start",
            "fb": "kelpie_dash_quiz_complete",
            "sold": "kelpie_dash_checkout",
            "purchase": "kelpie_dash_gift",
            "contact_sent": "kelpie_dash_appstore",
            "spend": "kelpie_dash_meta_spend",
            "clicks": "kelpie_dash_meta_clicks",
            "impressions": None,
        },
        "days": len(by_day),
        "spend_total": spend_total,
        "errors": errors,
    }
    if source != "kelpie-dash":
        meta["metric_map"].update({
            "installs": "posthog_visitors",
            "trials": "quiz_started",
            "fb": "quiz_complete",
            "sold": "checkout_lead",
            "purchase": "gift_email",
            "contact_sent": "app_store_click",
            "spend": "unavailable",
            "clicks": "unavailable",
        })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Kelpie feed ok: {len(by_day)} days · spend ${spend_total} · "
        f"visitors={visitors} · checkout={checkout} · until={until} · src={source}"
    )
    return 0
