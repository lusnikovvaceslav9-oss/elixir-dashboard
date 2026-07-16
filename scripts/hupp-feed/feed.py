"""Hupp auto-feed: Metrika visits and goals (Direct spend comes from CSV upload in dashboard)."""

from __future__ import annotations

import csv
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


CSV_HEADERS = (
    "date", "spend", "installs", "trials", "sold", "fb", "purchase",
    "contact_info", "form_submit", "contact_sent", "clicks", "impressions",
)
METRIKA_KEYS = (
    "installs", "trials", "sold", "fb", "purchase",
    "contact_info", "form_submit", "contact_sent",
)
DIRECT_KEYS = ("spend", "clicks", "impressions")


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _request_json(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"OAuth {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read()[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error


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
                item[key] = float(raw) if key == "spend" else int(float(raw))
            result[day.isoformat()] = item
    return result


def _fetch_metrika(
    token: str,
    counter_id: str,
    anchor: date,
    until: date,
    goals: list[dict],
) -> dict[str, dict]:
    metrics = ["ym:s:visits", *[f"ym:s:goal{goal['id']}reaches" for goal in goals]]
    params = {
        "ids": counter_id,
        "metrics": ",".join(metrics),
        "dimensions": "ym:s:date",
        "date1": anchor.isoformat(),
        "date2": until.isoformat(),
        "accuracy": "full",
        "limit": 10000,
    }
    payload = _request_json(
        f"https://api-metrika.yandex.net/stat/v1/data?{urllib.parse.urlencode(params)}",
        token,
    )
    result: dict[str, dict] = {}
    for raw in payload.get("data") or []:
        dimensions = raw.get("dimensions") or []
        values = raw.get("metrics") or []
        if not dimensions:
            continue
        day = str(dimensions[0].get("name") or "")[:10]
        if len(day) != 10:
            continue
        item = {"installs": int(float(values[0])) if values else 0}
        for index, goal in enumerate(goals, start=1):
            item[goal["csv"]] = int(float(values[index])) if len(values) > index else 0
        result[day] = item
    return result


def _write_daily(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for day in sorted(rows):
            row = rows[day]
            writer.writerow({
                "date": datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y"),
                **{key: round(float(row.get(key) or 0), 2) if key == "spend" else int(row.get(key) or 0)
                   for key in CSV_HEADERS[1:]},
            })


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def run_feed(work_dir: Path) -> int:
    _load_env_file(work_dir / "secrets.env")
    config = json.loads((work_dir / "config/hupp.json").read_text(encoding="utf-8"))
    anchor = date.fromisoformat(config["anchor"])
    until = datetime.now(ZoneInfo("Europe/Moscow")).date()
    daily_path = work_dir / config["daily_csv"]
    rows = _load_existing(daily_path)
    errors: list[str] = []
    sources: dict[str, str] = {}

    metrika_token = os.environ.get("HUPP_METRIKA_OAUTH_TOKEN") or os.environ.get("METRIKA_OAUTH_TOKEN")
    counter_id = (
        os.environ.get("HUPP_METRIKA_COUNTER_ID")
        or os.environ.get("METRIKA_COUNTER_ID")
        or config["metrika_counter_id"]
    )
    if metrika_token:
        try:
            metrika = _fetch_metrika(metrika_token, counter_id, anchor, until, config["goals"])
            for day, values in metrika.items():
                prev = rows.setdefault(day, {})
                for key in METRIKA_KEYS:
                    if key in values:
                        prev[key] = values[key]
            sources["metrika"] = f"counter_{counter_id}"
            print(f"  Metrika: {len(metrika)} days")
        except Exception as error:
            errors.append(f"metrika: {error}")
            print(f"  Metrika failed: {error}")
    else:
        errors.append("metrika: HUPP_METRIKA_OAUTH_TOKEN missing")

    day = anchor
    while day <= until:
        key = day.isoformat()
        row = rows.setdefault(key, {})
        for field in CSV_HEADERS[1:]:
            row.setdefault(field, 0)
        day += timedelta(days=1)
    _write_daily(daily_path, rows)

    totals = {
        key: round(sum(float(row.get(key) or 0) for row in rows.values()), 2)
        for key in CSV_HEADERS[1:]
    }
    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": "hupp",
        "anchor": anchor.isoformat(),
        "until": until.isoformat(),
        "metric_map": {goal["csv"]: goal["key"] for goal in config["goals"]} | {
            "installs": "metrika_visits",
            "spend": "direct_csv_upload",
            "clicks": "direct_csv_upload",
            "impressions": "direct_csv_upload",
        },
        "goals": config["goals"],
        "sources": sources,
        "errors": errors,
        "days": len(rows),
        "totals": totals,
        "metrika_counter_id": counter_id,
        "direct_source": "csv_upload",
    }
    meta_path = work_dir / config["meta_json"]
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Hupp feed: {len(rows)} days; errors={errors}")
    return 0
