"""AppMetrica Reporting API — lightweight daily aggregates (no Logs API)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

STAT_BYTIME = "https://api.appmetrica.yandex.com/stat/v1/data/bytime"
STAT_TABLE = "https://api.appmetrica.yandex.com/stat/v1/data"


def _get_json(url: str, token: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"OAuth {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"AppMetrica HTTP {exc.code}: {body}") from exc


def _build_url(base: str, params: dict) -> str:
    return f"{base}?{urllib.parse.urlencode(params)}"


def _parse_bytime_series(payload: dict) -> dict[str, float]:
    """Map ISO date -> metric value from bytime response."""
    out: dict[str, float] = {}
    time_rows = payload.get("time_intervals") or []
    data_rows = payload.get("data") or []
    if not data_rows:
        return out
    metrics_series = data_rows[0].get("metrics") or []
    if not metrics_series:
        return out
    values = metrics_series[0] if isinstance(metrics_series[0], list) else metrics_series
    for i, interval in enumerate(time_rows):
        if not interval or len(interval) < 1:
            continue
        day = str(interval[0])[:10]
        if i < len(values):
            out[day] = float(values[i] or 0)
    return out


def _parse_table_by_date(payload: dict) -> dict[str, float]:
    """Fallback: table API with date dimension."""
    out: dict[str, float] = {}
    for row in payload.get("data") or []:
        dims = row.get("dimensions") or []
        metrics = row.get("metrics") or []
        if not dims or not metrics:
            continue
        day = (dims[0].get("name") or dims[0].get("id") or "")[:10]
        if len(day) == 10:
            out[day] = float(metrics[0] or 0)
    return out


def fetch_installs_by_day(
    token: str,
    app_id: str,
    date_since: date,
    date_until: date,
) -> dict[str, int]:
    """Unique install devices per day via Reporting API."""
    params = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:i:installDevices",
        "date_dimension": "day",
        "accuracy": "medium",
    }
    try:
        payload = _get_json(_build_url(STAT_BYTIME, params), token)
        raw = _parse_bytime_series(payload)
    except RuntimeError:
        params_table = {
            "ids": app_id,
            "id": app_id,
            "date1": date_since.isoformat(),
            "date2": date_until.isoformat(),
            "metrics": "ym:i:installDevices",
            "dimensions": "ym:i:date",
            "limit": "10000",
        }
        payload = _get_json(_build_url(STAT_TABLE, params_table), token)
        raw = _parse_table_by_date(payload)
    return {d: int(round(v)) for d, v in sorted(raw.items())}


def fetch_event_by_day(
    token: str,
    app_id: str,
    event_name: str,
    date_since: date,
    date_until: date,
) -> dict[str, int]:
    """Event count per day (e.g. trial_started) via Reporting API."""
    params = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:ce:allEvents",
        "dimensions": "ym:ce:date",
        "filters": f"ym:ce:eventLabel=='{event_name}'",
        "date_dimension": "day",
        "limit": "10000",
    }
    try:
        payload = _get_json(_build_url(STAT_BYTIME, params), token)
        raw = _parse_bytime_series(payload)
        if raw:
            return {d: int(round(v)) for d, v in sorted(raw.items())}
    except RuntimeError:
        pass
    params_table = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:ce:allEvents",
        "dimensions": "ym:ce:date",
        "filters": f"ym:ce:eventLabel=='{event_name}'",
        "limit": "10000",
    }
    payload = _get_json(_build_url(STAT_TABLE, params_table), token)
    raw = _parse_table_by_date(payload)
    return {d: int(round(v)) for d, v in sorted(raw.items())}


def fetch_window(
    token: str,
    app_id: str,
    anchor: date,
    until: date,
    refresh_days: int,
    attribution_lag_days: int,
) -> tuple[dict[str, int], dict[str, int]]:
    """Fetch only the refresh window (not full history)."""
    window_start = max(anchor, until - timedelta(days=refresh_days + attribution_lag_days))
    installs = fetch_installs_by_day(token, app_id, window_start, until)
    trials_am: dict[str, int] = {}
    try:
        trials_am = fetch_event_by_day(token, app_id, "trial_started", window_start, until)
    except RuntimeError as exc:
        print(f"  AppMetrica trial_started skipped: {exc}")
    return installs, trials_am
