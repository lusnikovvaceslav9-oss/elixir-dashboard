"""RuStore payments registry — bills (fb) per day."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

YEARLY_PRICE = 2490
MONTHLY_PRICE = 399
TRIAL_LAG_DAYS = 7


def load_payments(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pay_date = (row.get("pay_date") or "")[:10]
            if len(pay_date) != 10:
                continue
            status = (row.get("status") or "paid").strip().lower()
            if status == "refunded":
                continue
            plan = (row.get("plan") or "yearly").strip().lower()
            amount = int(float(row.get("amount_rub") or 0))
            if not amount:
                amount = YEARLY_PRICE if plan == "yearly" else MONTHLY_PRICE
            rows.append({"pay_date": pay_date, "amount": amount, "plan": plan})
    return rows


def bills_by_day(payments: list[dict]) -> dict[str, int]:
    """Count paid bills per calendar pay_date."""
    out: dict[str, int] = defaultdict(int)
    for p in payments:
        out[p["pay_date"]] += 1
    return dict(sorted(out.items()))


def bills_by_cohort_day(payments: list[dict]) -> dict[str, int]:
    """Bills by cohort day: yearly pay−7d, monthly same day."""
    out: dict[str, int] = defaultdict(int)
    for p in payments:
        d = date.fromisoformat(p["pay_date"])
        if p.get("plan") == "yearly":
            d = d - timedelta(days=TRIAL_LAG_DAYS)
        out[d.isoformat()] += 1
    return dict(sorted(out.items()))


def sold_trials_by_day(payments: list[dict]) -> dict[str, int]:
    """Проданные триалы (годовая конверсия trial→paid) по дате оплаты. Месячные не в счёт."""
    out: dict[str, int] = defaultdict(int)
    for p in payments:
        if p["plan"] == "yearly":
            out[p["pay_date"]] += 1
    return dict(sorted(out.items()))


def sold_trials_by_cohort_day(payments: list[dict]) -> dict[str, int]:
    """Годовые конверсии, отнесённые к дню когорты (оплата − 7д). Месячные не в счёт."""
    out: dict[str, int] = defaultdict(int)
    for p in payments:
        if p["plan"] != "yearly":
            continue
        d = date.fromisoformat(p["pay_date"])
        cohort_day = d - timedelta(days=TRIAL_LAG_DAYS)
        out[cohort_day.isoformat()] += 1
    return dict(out)


def payments_breakdown(payments: list[dict]) -> dict[str, dict[str, int]]:
    """Split paid bills into monthly vs yearly: count + rub per plan."""
    out: dict[str, dict[str, int]] = {
        "yearly": {"count": 0, "rub": 0},
        "monthly": {"count": 0, "rub": 0},
        "total": {"count": 0, "rub": 0},
    }
    for p in payments:
        plan = "yearly" if p["plan"] == "yearly" else "monthly"
        out[plan]["count"] += 1
        out[plan]["rub"] += p["amount"]
        out["total"]["count"] += 1
        out["total"]["rub"] += p["amount"]
    return out


def paid_net_by_cohort_day(payments: list[dict]) -> dict[str, int]:
    """Map payment to cohort day (yearly: pay_date - 7d)."""
    out: dict[str, int] = defaultdict(int)
    for p in payments:
        d = date.fromisoformat(p["pay_date"])
        cohort_day = d - timedelta(days=TRIAL_LAG_DAYS) if p["plan"] == "yearly" else d
        out[cohort_day.isoformat()] += p["amount"]
    return dict(out)
