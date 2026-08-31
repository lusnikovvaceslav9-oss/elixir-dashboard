"""Yandex Direct Reports API — daily spend, clicks, impressions (no VAT)."""

from __future__ import annotations

import csv
import io
import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import date


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"
MAX_POLL = 12
POLL_SLEEP = 5


def fetch_spend_by_day(
    token: str,
    client_login: str,
    date_since: date,
    date_until: date,
    campaign_ids: list[str] | None = None,
) -> dict[str, float]:
    """Backward-compatible: day → spend only."""
    full = fetch_direct_by_day(token, client_login, date_since, date_until, campaign_ids)
    return {day: vals["spend"] for day, vals in full.items()}


def fetch_direct_by_day(
    token: str,
    client_login: str,
    date_since: date,
    date_until: date,
    campaign_ids: list[str] | None = None,
    exclude_campaign_ids: list[str] | None = None,
    campaign_name_includes: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Day → {spend, clicks, impressions}, агрегировано по дате.

    Несколько проектов под одним client_login не должны смешиваться:
    - ``campaign_ids`` — тянуть ТОЛЬКО эти кампании (include);
    - ``exclude_campaign_ids`` — тянуть все, кроме этих (проект без своего
      include исключает кампании, заявленные другими проектами);
    - ``campaign_name_includes`` — если нет include по ID: только кампании,
      в названии которых есть любая из подстрок (Planto не должен забирать
      чужие РК того же логина);
    - ни фильтров — все кампании логина.

    Разбивка по кампаниям делается на нашей стороне (CampaignId + CampaignName).
    """
    include = {str(c) for c in (campaign_ids or [])}
    exclude = {str(c) for c in (exclude_campaign_ids or [])}
    name_needles = [str(n).lower() for n in (campaign_name_includes or []) if str(n).strip()]
    selection: dict = {
        "DateFrom": date_since.isoformat(),
        "DateTo": date_until.isoformat(),
    }
    payload = {
        "params": {
            "SelectionCriteria": selection,
            "FieldNames": ["Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost"],
            "OrderBy": [{"Field": "Date"}],
            "ReportName": f"BuyerCamp_{date_since.isoformat()}_{date_until.isoformat()}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "NO",
            "IncludeDiscount": "NO",
        }
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "ru",
        "returnMoneyInMicros": "false",
        "processingMode": "auto",
        "skipReportHeader": "true",
        "skipReportSummary": "true",
        "Content-Type": "application/json; charset=utf-8",
    }
    if client_login:
        headers["Client-Login"] = client_login

    body = json.dumps(payload).encode("utf-8")
    text = ""
    for attempt in range(1, MAX_POLL + 1):
        req = urllib.request.Request(REPORTS_URL, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
                text = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
                break
        except urllib.error.HTTPError as err:
            retry_after = err.headers.get("Retry-After")
            if err.code in (201, 202, 500) or retry_after:
                wait = int(retry_after or POLL_SLEEP)
                print(f"  Direct report pending (HTTP {err.code}), retry in {wait}s")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Direct HTTP {err.code}: {err.read()[:300]}") from err
    else:
        raise RuntimeError("Direct report timeout")

    return _parse_tsv(text, include, exclude, name_needles)


def _parse_num(raw: str) -> float:
    try:
        return float(str(raw).replace(",", ".").replace("\xa0", "").replace(" ", ""))
    except ValueError:
        return 0.0


def _parse_tsv(
    text: str,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
    name_needles: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Колонки: Date, CampaignId, [CampaignName], Impressions, Clicks, Cost."""
    out: dict[str, dict[str, float]] = {}
    needles = [n for n in (name_needles or []) if n]
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    for row in reader:
        if len(row) < 5:
            continue
        day_raw = row[0].strip()
        if not day_raw or day_raw.lower() in ("date", "дата", "--"):
            continue
        day = day_raw[:10]
        if len(day) != 10 or day[4] != "-":
            continue
        camp = str(row[1]).strip()
        has_name = len(row) >= 6
        name = str(row[2]).strip() if has_name else ""
        if include and camp not in include:
            continue
        if exclude and camp in exclude:
            continue
        # Name filter only when there is no include-ID list (Planto: не тащить
        # соседние приложения с того же Direct-логина).
        if needles and not include:
            if not name or not any(n in name.lower() for n in needles):
                continue
        if has_name:
            impressions = _parse_num(row[3])
            clicks = _parse_num(row[4])
            cost = _parse_num(row[5])
        else:
            impressions = _parse_num(row[2])
            clicks = _parse_num(row[3])
            cost = _parse_num(row[4])
        prev = out.get(day) or {"spend": 0.0, "clicks": 0.0, "impressions": 0.0}
        prev["spend"] = round(prev["spend"] + cost, 2)
        prev["clicks"] = round(prev["clicks"] + clicks, 0)
        prev["impressions"] = round(prev["impressions"] + impressions, 0)
        out[day] = prev
    return out
