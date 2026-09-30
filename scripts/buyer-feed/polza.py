"""Polza.ai — фактический спенд на ИИ по дням (не оценка).

В приложении есть ИИ, поэтому в юнит-экономике нужен реальный burn по ключам:
total_burn = direct_spend + polza_spend. Тянем историю генераций и складываем
стоимость по дате (МСК, как и остальной фид).

Ключи: POLZA_API_KEY / POLZA_AI_API_KEY и опционально POLZA_API_KEY_FALLBACK
(второй ключ ColorStylist — суммируем spend по обоим).
Нет ключа — молча пропускаем (фид не должен падать из-за необязательного источника).

API: https://polza.ai/docs/api-reference/history/generations
  GET /v1/history/generations?page=&limit=&dateFrom=&dateTo=
  limit: 1–100
  dateFrom/dateTo — ISO 8601 datetime (не голая дата: иначе dateTo = полночь UTC
  и из каждого чанка выпадает последний календарный день + вечер МСК).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_BASE = "https://polza.ai/api/v1"
MSK = ZoneInfo("Europe/Moscow")


def _get(path: str, api_key: str, params: dict | None = None, timeout: int = 60):
    url = f"{API_BASE}{path}"
    if params:
        qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
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


def _msk_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=MSK)


def _iso_msk(d: date, *, end_exclusive: bool = False) -> str:
    dt = _msk_start(d)
    if end_exclusive:
        dt = dt + timedelta(days=1)
    return dt.isoformat()


def _row_dt(row: dict) -> date | None:
    """Дата генерации в МСК. Форматы у API плавают — пробуем известные поля."""
    raw = None
    for k in ("createdAt", "created_at", "completedAt", "date", "timestamp", "created"):
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
    """Первое ненулевое поле. clientCost=0 при живом cost не должен занулять строку."""
    best = 0.0
    for k in ("cost", "clientCost", "price", "amount", "total", "cost_rub", "sum"):
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if n > best:
            best = n
    return best


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


def _pagination_meta(data) -> tuple[int | None, int | None]:
    """→ (totalPages, totalItems)."""
    if not isinstance(data, dict):
        return None, None
    pages = None
    total = None
    for nest in (data, data.get("meta"), data.get("pagination"), data.get("pageInfo")):
        if not isinstance(nest, dict):
            continue
        if pages is None:
            for k in ("totalPages", "total_pages", "pages"):
                v = nest.get(k)
                if v is None:
                    continue
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    pages = n
                    break
        if total is None:
            for k in ("total", "totalItems", "total_items", "count"):
                if k in nest and nest.get(k) is not None and k not in ("totalPages",):
                    try:
                        total = int(nest[k])
                    except (TypeError, ValueError):
                        continue
                    break
    return pages, total


def _extract_rows(data) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    rows = (
        data.get("items")
        or data.get("data")
        or data.get("generations")
        or data.get("results")
        or []
    )
    return rows if isinstance(rows, list) else []


def _fetch_polza_range(
    api_key: str,
    date_since: date,
    date_until: date,
    page_limit: int,
    seen_ids: set[str] | None = None,
) -> dict:
    by_day: dict[str, float] = {}
    by_kind: dict[str, float] = {"images": 0.0, "chat": 0.0}
    count = 0
    skipped_cost = 0
    page = 1
    per_page = 100
    total_pages = None
    truncated = False
    seen_ids = seen_ids if seen_ids is not None else set()
    date_from = _iso_msk(date_since)
    date_to = _iso_msk(date_until, end_exclusive=True)

    while page <= page_limit:
        try:
            data = _get(
                "/history/generations",
                api_key,
                {
                    "page": page,
                    "limit": per_page,
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "sortBy": "createdAt",
                    "sortOrder": "asc",
                },
            )
        except Exception as exc:
            if page == 1:
                raise RuntimeError(f"polza history: {exc}") from exc
            break
        if total_pages is None:
            total_pages, _ = _pagination_meta(data)
        rows = _extract_rows(data)
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("id") or "").strip()
            if gid and gid in seen_ids:
                continue
            if gid:
                seen_ids.add(gid)
            d = _row_dt(row)
            if not d or d < date_since or d > date_until:
                continue
            cost = _row_cost(row)
            if cost <= 0:
                skipped_cost += 1
                continue
            by_day[d.isoformat()] = round(by_day.get(d.isoformat(), 0.0) + cost, 2)
            kind = _row_kind(row)
            by_kind[kind] = round(by_kind.get(kind, 0.0) + cost, 2)
            count += 1
        if total_pages and page >= total_pages:
            break
        if len(rows) < per_page:
            break
        page += 1
    else:
        truncated = True

    return {
        "by_day": by_day,
        "by_kind": by_kind,
        "generations": count,
        "skipped_zero_cost": skipped_cost,
        "truncated": truncated,
        "pages_fetched": min(page, page_limit),
        "total": round(sum(by_day.values()), 2),
    }


def fetch_polza_spend_by_day(
    api_key: str,
    date_since: date,
    date_until: date,
    page_limit: int = 200,
    chunk_days: int = 3,
) -> dict:
    """→ {"by_day": {iso: rub}, "by_kind": {...}, "generations": n, "total": rub}.

    Режем короткими чанками с datetime МСК. Чанки стыкуются вплотную
    (dateTo exclusive = начало следующего дня), id дедуплицируются.
    """
    by_day: dict[str, float] = {}
    by_kind: dict[str, float] = {"images": 0.0, "chat": 0.0}
    generations = 0
    skipped = 0
    truncated = False
    seen: set[str] = set()
    d = date_since
    while d <= date_until:
        e = min(d + timedelta(days=chunk_days - 1), date_until)
        part = _fetch_polza_range(api_key, d, e, page_limit, seen_ids=seen)
        generations += int(part.get("generations") or 0)
        skipped += int(part.get("skipped_zero_cost") or 0)
        truncated = truncated or bool(part.get("truncated"))
        for day, rub in (part.get("by_day") or {}).items():
            by_day[day] = round(by_day.get(day, 0.0) + float(rub or 0), 2)
        for kind, rub in (part.get("by_kind") or {}).items():
            by_kind[kind] = round(by_kind.get(kind, 0.0) + float(rub or 0), 2)
        d = e + timedelta(days=1)
    out = {
        "by_day": by_day,
        "by_kind": by_kind,
        "generations": generations,
        "skipped_zero_cost": skipped,
        "total": round(sum(by_day.values()), 2),
    }
    if truncated:
        out["truncated"] = True
    return out


def _merge_polza_summaries(parts: list[dict]) -> dict:
    by_day: dict[str, float] = {}
    by_kind: dict[str, float] = {"images": 0.0, "chat": 0.0}
    generations = 0
    skipped = 0
    by_key: list[dict] = []
    truncated = False
    for part in parts:
        label = part.get("key_label") or "key"
        total = float(part.get("total") or 0)
        gens = int(part.get("generations") or 0)
        by_key.append({"label": label, "total": total, "generations": gens})
        generations += gens
        skipped += int(part.get("skipped_zero_cost") or 0)
        truncated = truncated or bool(part.get("truncated"))
        for day, rub in (part.get("by_day") or {}).items():
            by_day[day] = round(by_day.get(day, 0.0) + float(rub or 0), 2)
        for kind, rub in (part.get("by_kind") or {}).items():
            by_kind[kind] = round(by_kind.get(kind, 0.0) + float(rub or 0), 2)
    out = {
        "by_day": by_day,
        "by_kind": by_kind,
        "generations": generations,
        "total": round(sum(by_day.values()), 2),
        "by_key": by_key,
        "keys_used": len(parts),
    }
    if skipped:
        out["skipped_zero_cost"] = skipped
    if truncated:
        out["truncated"] = True
    return out


def fetch_polza_spend_by_day_multi(
    api_keys: list[tuple[str, str]],
    date_since: date,
    date_until: date,
    page_limit: int = 200,
) -> dict:
    """Суммирует spend по нескольким ключам (Style + Style-emergency).

    api_keys: [(label, key), ...] — пустые ключи пропускаются.
    """
    parts: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for label, key in api_keys:
        key = (key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            part = fetch_polza_spend_by_day(key, date_since, date_until, page_limit=page_limit)
            part["key_label"] = label
            parts.append(part)
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    if not parts:
        if errors:
            raise RuntimeError("; ".join(errors))
        return {
            "by_day": {},
            "by_kind": {"images": 0.0, "chat": 0.0},
            "generations": 0,
            "total": 0.0,
            "by_key": [],
            "keys_used": 0,
        }
    out = _merge_polza_summaries(parts)
    if errors:
        out["errors"] = errors
    return out
