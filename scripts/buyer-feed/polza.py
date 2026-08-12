"""Polza.ai — фактический спенд на ИИ по дням (не оценка).

В приложении есть ИИ, поэтому в юнит-экономике нужен реальный burn по ключам:
total_burn = direct_spend + polza_spend. Тянем историю генераций и складываем
стоимость по дате (МСК, как и остальной фид).

Ключ: секрет POLZA_API_KEY или POLZA_AI_API_KEY.
Нет ключа — молча пропускаем (фид не должен падать из-за необязательного источника).

API: https://polza.ai/docs/api-reference/history/generations
  GET /v1/history/generations?page=&limit=&dateFrom=&dateTo=
  limit: 1–100
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

API_BASE = "https://polza.ai/api/v1"
MSK = ZoneInfo("Europe/Moscow")


def _get(path: str, api_key: str, params: dict | None = None, timeout: int = 60):
    url = f"{API_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}" + (f" · {body}" if body else "")) from exc


def fetch_balance(api_key: str) -> float | None:
    try:
        data = _get("/balance", api_key)
    except Exception:
        return None
    for key in ("balance", "amount", "value", "rub"):
        if isinstance(data, dict) and data.get(key) is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                pass
    return None


def _row_dt(row: dict) -> date | None:
    """Дата генерации в МСК. Форматы у API плавают — пробуем известные поля."""
    raw = None
    for k in ("createdAt", "created_at", "date", "timestamp", "created"):
        if row.get(k) is not None and row.get(k) != "":
            raw = row[k]
            break
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e11:  # миллисекунды
            ts /= 1000.0
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(MSK).date()
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).date()


def _row_cost(row: dict) -> float:
    for k in ("clientCost", "cost", "price", "amount", "total", "cost_rub", "sum"):
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _row_kind(row: dict) -> str:
    """images vs chat — по requestType/модели, для разреза в дашборде."""
    rt = str(row.get("requestType") or row.get("request_type") or "").lower()
    if rt in ("image", "images", "video", "audio"):
        return "images" if rt.startswith("image") else rt
    blob = " ".join(
        str(row.get(k) or "") for k in ("type", "kind", "model", "modelDisplayName", "endpoint", "category", "requestType")
    ).lower()
    if any(w in blob for w in ("image", "img", "sd", "flux", "dalle", "midjourney", "video")):
        return "images"
    return "chat"


def fetch_polza_spend_by_day(
    api_key: str,
    date_since: date,
    date_until: date,
    page_limit: int = 50,
) -> dict:
    """→ {"by_day": {iso: rub}, "by_kind": {...}, "generations": n, "total": rub}."""
    by_day: dict[str, float] = {}
    by_kind: dict[str, float] = {"images": 0.0, "chat": 0.0}
    count = 0
    page = 1
    # API: limit 1–100; фильтры dateFrom / dateTo (ISO 8601).
    per_page = 100
    while page <= page_limit:
        try:
            data = _get(
                "/history/generations",
                api_key,
                {
                    "page": page,
                    "limit": per_page,
                    "dateFrom": date_since.isoformat(),
                    "dateTo": date_until.isoformat(),
                    "sortBy": "createdAt",
                    "sortOrder": "asc",
                },
            )
        except Exception as exc:
            if page == 1:
                raise RuntimeError(f"polza history: {exc}") from exc
            break
        rows = data if isinstance(data, list) else (
            data.get("data")
            or data.get("items")
            or data.get("generations")
            or data.get("results")
            or []
        )
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            d = _row_dt(row)
            if not d or d < date_since or d > date_until:
                continue
            cost = _row_cost(row)
            if not cost:
                continue
            by_day[d.isoformat()] = round(by_day.get(d.isoformat(), 0.0) + cost, 2)
            kind = _row_kind(row)
            by_kind[kind] = round(by_kind.get(kind, 0.0) + cost, 2)
            count += 1
        if len(rows) < per_page:
            break
        page += 1
    return {
        "by_day": by_day,
        "by_kind": by_kind,
        "generations": count,
        "total": round(sum(by_day.values()), 2),
    }
