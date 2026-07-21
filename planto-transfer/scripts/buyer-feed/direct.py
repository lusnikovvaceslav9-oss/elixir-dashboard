"""Yandex Direct Reports API — daily spend (no VAT)."""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"
MAX_POLL = 12
POLL_SLEEP = 5


def fetch_spend_by_day(
    token: str,
    client_login: str,
    date_since: date,
    date_until: date,
) -> dict[str, float]:
    payload = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": date_since.isoformat(),
                "DateTo": date_until.isoformat(),
            },
            "FieldNames": ["Date", "Clicks", "Cost"],
            "OrderBy": [{"Field": "Date"}],
            "ReportName": f"PlantoBuyer_{date_until.isoformat()}",
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
            with urllib.request.urlopen(req, timeout=120) as resp:
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

    return _parse_tsv(text)


def _parse_tsv(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    for row in reader:
        if len(row) < 3:
            continue
        day_raw = row[0].strip()
        if not day_raw or day_raw.lower() in ("date", "дата", "--"):
            continue
        day = day_raw[:10]
        if len(day) != 10 or day[4] != "-":
            continue
        try:
            cost = float(str(row[2]).replace(",", ".").replace("\xa0", "").replace(" ", ""))
        except ValueError:
            cost = 0.0
        out[day] = round(out.get(day, 0) + cost, 2)
    return out
