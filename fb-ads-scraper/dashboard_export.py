"""Export FB scrape results into Elixir-dashboard compatible Google Sheets."""

from __future__ import annotations

import re
from datetime import datetime

DASHBOARD_CAMPAIGN_HEADERS = [
    "Дата",
    "Кампания",
    "Спенд",
    "Результаты",
    "Показы",
    "Клики",
]

DASHBOARD_DAILY_HEADERS = [
    "Дата",
    "Спенд",
    "Результаты",
    "Показы",
    "Клики",
]

DETAIL_HEADERS = [
    "Дата",
    "Время обновления",
    "Название кампании",
    "Статус",
    "Результаты",
    "Охват",
    "Показы",
    "Расходы",
    "CPC",
    "CPM",
    "CTR",
    "Клики",
    "Цена за результат",
]

NUM_RE = re.compile(r"[^\d,.\-]")


def _parse_number(value: str | float | int | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = NUM_RE.sub("", str(value)).replace(",", ".")
    if not s or s in {"-", ".", "-."}:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fmt_dashboard_date(iso_date: str) -> str:
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return dt.strftime("%d.%m.%Y")


def normalized_campaign_rows(normalized: list[dict]) -> list[dict]:
    rows = []
    for item in normalized:
        name = (item.get("Название кампании") or item.get("Campaign") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "campaign": name,
                "status": item.get("Статус", ""),
                "results": _parse_number(item.get("Результаты")),
                "reach": _parse_number(item.get("Охват")),
                "impressions": _parse_number(item.get("Показы")),
                "spend": _parse_number(item.get("Расходы")),
                "cpc": item.get("CPC", ""),
                "cpm": item.get("CPM", ""),
                "ctr": item.get("CTR", ""),
                "clicks": _parse_number(item.get("Клики")),
                "cost_per_result": item.get("Цена за результат", ""),
            }
        )
    return rows


def build_detail_rows(iso_date: str, update_time: str, campaigns: list[dict]) -> tuple[list[str], list[list]]:
    headers = list(DETAIL_HEADERS)
    display_date = _fmt_dashboard_date(iso_date)
    rows = []
    for c in campaigns:
        rows.append(
            [
                display_date,
                update_time,
                c["campaign"],
                c.get("status", ""),
                _fmt_num(c.get("results")),
                _fmt_num(c.get("reach")),
                _fmt_num(c.get("impressions")),
                _fmt_money(c.get("spend")),
                c.get("cpc", ""),
                c.get("cpm", ""),
                c.get("ctr", ""),
                _fmt_num(c.get("clicks")),
                c.get("cost_per_result", ""),
            ]
        )
    return headers, rows


def build_dashboard_rows(iso_date: str, campaigns: list[dict], export_mode: str) -> tuple[list[str], list[list]]:
    display_date = _fmt_dashboard_date(iso_date)

    if export_mode == "daily":
        spend = sum(c["spend"] for c in campaigns)
        results = sum(c["results"] for c in campaigns)
        impressions = sum(c["impressions"] for c in campaigns)
        clicks = sum(c["clicks"] for c in campaigns)
        return DASHBOARD_DAILY_HEADERS, [
            [display_date, _fmt_money(spend), _fmt_num(results), _fmt_num(impressions), _fmt_num(clicks)]
        ]

    rows = []
    for c in campaigns:
        rows.append(
            [
                display_date,
                c["campaign"],
                _fmt_money(c["spend"]),
                _fmt_num(c["results"]),
                _fmt_num(c["impressions"]),
                _fmt_num(c["clicks"]),
            ]
        )
    return DASHBOARD_CAMPAIGN_HEADERS, rows


def _fmt_money(value: float | int | str | None) -> str:
    n = _parse_number(value)
    if abs(n - round(n)) < 0.005:
        return str(int(round(n)))
    return f"{n:.2f}".replace(".", ",")


def _fmt_num(value: float | int | str | None) -> str:
    n = _parse_number(value)
    if abs(n - round(n)) < 0.005:
        return str(int(round(n)))
    return f"{n:.2f}".replace(".", ",")
