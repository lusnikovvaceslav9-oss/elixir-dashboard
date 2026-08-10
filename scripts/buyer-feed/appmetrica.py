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
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AppMetrica network error: {exc}") from exc


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
    """Event count per day (matches AppMetrica Events UI «События»).

    Earlier we used ym:ce:users (unique users). That undercounts vs the default
    AppMetrica Events report when the same user fires the event more than once
    in a day (e.g. today: 4 users / 5 events for trial_started).
    """
    # Prefer table + allEvents: same number as AppMetrica UI Events column.
    params_events = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:ce:allEvents",
        "dimensions": "ym:ce:date",
        "filters": f"ym:ce:eventLabel=='{event_name}'",
        "limit": "10000",
        "sort": "ym:ce:date",
        "accuracy": "full",
    }
    try:
        payload = _get_json(_build_url(STAT_TABLE, params_events), token)
        raw = _parse_table_by_date(payload)
        if raw:
            return {d: int(round(v)) for d, v in sorted(raw.items())}
    except RuntimeError:
        pass

    params_bytime = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:ce:allEvents",
        "dimensions": "ym:ce:date",
        "filters": f"ym:ce:eventLabel=='{event_name}'",
        "date_dimension": "day",
        "limit": "10000",
        "accuracy": "full",
    }
    try:
        payload = _get_json(_build_url(STAT_BYTIME, params_bytime), token)
        raw = _parse_bytime_series(payload)
        if raw:
            return {d: int(round(v)) for d, v in sorted(raw.items())}
    except RuntimeError:
        pass

    # Fallback: unique users (older behaviour)
    params_users = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:ce:users",
        "dimensions": "ym:ce:date",
        "filters": f"ym:ce:eventLabel=='{event_name}'",
        "limit": "10000",
        "sort": "ym:ce:date",
        "accuracy": "full",
    }
    payload = _get_json(_build_url(STAT_TABLE, params_users), token)
    raw = _parse_table_by_date(payload)
    return {d: int(round(v)) for d, v in sorted(raw.items())}


def fetch_event_totals(
    token: str,
    app_id: str,
    event_name: str,
    date_since: date,
    date_until: date,
) -> dict[str, int]:
    """Period totals: unique users + event count for one event label."""
    params = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:ce:users,ym:ce:allEvents",
        "filters": f"ym:ce:eventLabel=='{event_name}'",
        "limit": "5",
    }
    payload = _get_json(_build_url(STAT_TABLE, params), token)
    rows = payload.get("data") or []
    if not rows:
        return {"users": 0, "events": 0}
    metrics = rows[0].get("metrics") or [0, 0]
    users = int(round(float(metrics[0] or 0)))
    events = int(round(float(metrics[1] or 0))) if len(metrics) > 1 else 0
    return {"users": users, "events": events}


def fetch_active_users(
    token: str,
    app_id: str,
    date_since: date,
    date_until: date,
) -> int:
    """Unique active users (ym:u:users) for the period."""
    params = {
        "ids": app_id,
        "id": app_id,
        "date1": date_since.isoformat(),
        "date2": date_until.isoformat(),
        "metrics": "ym:u:users",
        "limit": "5",
    }
    payload = _get_json(_build_url(STAT_TABLE, params), token)
    rows = payload.get("data") or []
    if not rows:
        return 0
    metrics = rows[0].get("metrics") or [0]
    return int(round(float(metrics[0] or 0)))


# Product / ICP events used by Planto dashboard blocks.
PRODUCT_EVENTS = (
    "plant_added",
    "paywall_shown",
    "paywall_cta_clicked",
    "user_score5",
    "chat_message_sent",
    "watering_configured",
    "watering_marked",
    "ai_route",
    "scan_success",
    "user_activated",
)


def _pct(num, den):
    return round(num / den * 100, 2) if den else None


def fetch_product_metrics_spec(
    token: str,
    app_id: str,
    date_since: date,
    date_until: date,
    installs_total: int,
    spec: dict,
) -> dict:
    """Product KPIs по спецификации проекта (config → product_section).

    Считаем по УНИКАЛЬНЫМ устройствам (ym:ce:users), а не по количеству событий:
    в стилисте один юзер делает много hair/outfit, и counts/installs дают >100%.
    Метрики, которых Reporting API не умеет (пересечение фич на юзера), возвращаем
    как None + note — в UI это «нет данных», а не ноль с чужим лейблом.
    """
    names: set[str] = set()
    for k in spec.get("kpis") or []:
        for side in ("numerator", "fallback_numerator", "denominator"):
            v = k.get(side)
            if isinstance(v, dict):
                if v.get("event"):
                    names.add(v["event"])
                for e in v.get("events") or []:
                    names.add(e)
    for f in spec.get("features") or []:
        if f.get("event"):
            names.add(f["event"])
    for q in spec.get("quality") or []:
        for side in ("numerator", "denominator"):
            v = q.get(side)
            if isinstance(v, dict) and v.get("event"):
                names.add(v["event"])

    events: dict[str, dict] = {}
    errors: list[str] = []
    for name in sorted(names):
        try:
            totals = fetch_event_totals(token, app_id, name, date_since, date_until)
            users, ev = totals["users"], totals["events"]
            events[name] = {
                "users": users,
                "events": ev,
                "vs_install_pct": _pct(users, installs_total),
                "avg_per_user": round(ev / users, 2) if users else None,
            }
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            events[name] = {"users": 0, "events": 0, "vs_install_pct": None, "avg_per_user": None}

    def side_value(v, mode="users"):
        """→ (значение, доступно?). None+False = посчитать нельзя."""
        if v == "installs":
            return installs_total, True
        if isinstance(v, dict):
            if v.get("event"):
                return int((events.get(v["event"]) or {}).get(mode) or 0), True
            if v.get("type") == "unique_devices_with_at_least_n_events":
                return None, False  # нужен Logs API: пересечение фич на юзера
        return None, False

    kpis = []
    for k in spec.get("kpis") or []:
        mode = "events" if k.get("count_mode") == "events" else "users"
        num, num_ok = side_value(k.get("numerator"), mode)
        if num_ok and not num and k.get("fallback_numerator"):
            num, num_ok = side_value(k["fallback_numerator"], mode)
        den, den_ok = side_value(k.get("denominator"), mode)
        value = _pct(num, den) if (num_ok and den_ok) else None
        kpis.append({
            "id": k.get("id"),
            "label": k.get("label"),
            "formula_label": k.get("formula_label"),
            "value_pct": value,
            "primary": bool(k.get("primary")),
            "accent": bool(k.get("accent")),
            "note": None if (num_ok and den_ok) else "нужен Logs API (пересечение фич на пользователя)",
        })

    features = [
        {
            "id": f.get("id"),
            "label": f.get("label"),
            "event": f.get("event"),
            "value_pct": _pct(int((events.get(f.get("event")) or {}).get("users") or 0), installs_total),
        }
        for f in spec.get("features") or []
    ]

    quality = []
    for q in spec.get("quality") or []:
        mode = "events" if q.get("count_mode") == "events" else "users"
        num, num_ok = side_value(q.get("numerator"), mode)
        den, den_ok = side_value(q.get("denominator"), mode)
        quality.append({
            "id": q.get("id"),
            "label": q.get("label"),
            "value_pct": _pct(num, den) if (num_ok and den_ok) else None,
        })

    active_users = 0
    try:
        active_users = fetch_active_users(token, app_id, date_since, date_until)
    except Exception as exc:
        errors.append(f"active_users: {exc}")

    return {
        "from": date_since.isoformat(),
        "to": date_until.isoformat(),
        "installs": installs_total,
        "active_users": active_users,
        "count_mode": spec.get("count_mode") or "unique_devices",
        "subtitle": spec.get("subtitle"),
        "events": events,
        "kpis": kpis,
        "features": features,
        "quality": quality,
        "retention": {
            "d1": None, "d3": None, "d7": None,
            "note": "AppMetrica Reporting API не отдаёт D1/D3/D7 без Logs/Retention export",
        },
        "errors": errors,
    }


def fetch_product_metrics(
    token: str,
    app_id: str,
    date_since: date,
    date_until: date,
    installs_total: int,
) -> dict:
    """Aggregate product KPIs from AppMetrica event labels."""
    events: dict[str, dict] = {}
    errors: list[str] = []
    for name in PRODUCT_EVENTS:
        try:
            totals = fetch_event_totals(token, app_id, name, date_since, date_until)
            users = totals["users"]
            ev = totals["events"]
            events[name] = {
                "users": users,
                "events": ev,
                "vs_install_pct": round(users / installs_total * 100, 2) if installs_total else None,
                "avg_per_user": round(ev / users, 2) if users else None,
            }
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            events[name] = {"users": 0, "events": 0, "vs_install_pct": None, "avg_per_user": None}

    plant = events.get("plant_added") or {}
    paywall_shown = events.get("paywall_shown") or {}
    paywall_cta = events.get("paywall_cta_clicked") or {}
    score5 = events.get("user_score5") or {}
    chat = events.get("chat_message_sent") or {}
    watering = events.get("watering_configured") or {}
    ai = events.get("ai_route") or {}
    scan = events.get("scan_success") or {}

    shown_u = int(paywall_shown.get("users") or 0)
    cta_u = int(paywall_cta.get("users") or 0)
    plant_u = int(plant.get("users") or 0)
    plant_e = int(plant.get("events") or 0)

    active_users = 0
    try:
        active_users = fetch_active_users(token, app_id, date_since, date_until)
    except Exception as exc:
        errors.append(f"active_users: {exc}")

    return {
        "from": date_since.isoformat(),
        "to": date_until.isoformat(),
        "installs": installs_total,
        "active_users": active_users,
        "events": events,
        "garden_activation_pct": round(plant_u / installs_total * 100, 2) if installs_total else None,
        "garden_avg_plants": round(plant_e / plant_u, 2) if plant_u else None,
        "garden_depth_icp_pct": None,  # needs per-user ≥3 plants (Logs API)
        "paywall_cta_pct": round(cta_u / shown_u * 100, 2) if shown_u else None,
        "care_engagement_pct": round(int(score5.get("users") or 0) / installs_total * 100, 2) if installs_total else None,
        "chat_per_plant_pct": round(int(chat.get("users") or 0) / plant_u * 100, 2) if plant_u else None,
        "feature_activation": {
            "scan_pct": round(int(scan.get("users") or 0) / installs_total * 100, 2) if installs_total else None,
            "watering_pct": round(int(watering.get("users") or 0) / installs_total * 100, 2) if installs_total else None,
            "ai_pct": round(int(ai.get("users") or 0) / installs_total * 100, 2) if installs_total else None,
        },
        "retention": {
            "d1": None,
            "d3": None,
            "d7": None,
            "note": "AppMetrica Reporting API не отдаёт D1/D3/D7 без Logs/Retention export",
        },
        "errors": errors,
    }


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
