"""Supabase trial starts via Postgres (RuStore entitlements)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
YEARLY_TRIAL_LAG_DAYS = 7
YEARLY_PRICE = 2490
MONTHLY_PRICE = 399

# RuStore webhook types that mean «новый триал в этот день» (не закрытие и не MAIN).
TRIAL_START_EVENTS = frozenset({"ACTIVATED", "CLIENT_SYNC", "RECOVERED", "CANCELLED"})


@dataclass(frozen=True)
class TrialStart:
    user_id: str
    purchase_id: str
    trial_start: date


def _to_msk_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(MSK).date()


def _is_yearly(product_code: str | None) -> bool:
    code = (product_code or "").lower()
    return "yearly" in code or "year" in code or code == "planto_plus_yearly"


def derive_trial_start(
    *,
    period: str | None,
    product_code: str | None,
    last_event_time: datetime | None,
    status: str | None = None,
    event_type: str | None = None,
    activated_at: datetime | None = None,
    for_daily: bool = False,
) -> date | None:
    """Trial start date (MSK).

    Prefers activated_at (stable activation date), falls back to last_event_time.
    for_daily=True — только новые старты триала в этот день.
    """
    period = (period or "").upper()
    status = (status or "").upper()
    event_type = (event_type or "").upper()
    start_time = activated_at or last_event_time
    if start_time is None:
        return None
    start_day = _to_msk_date(start_time)

    if period == "TRIAL":
        # activated_at — надёжный старт, берём как есть (в т.ч. для закрытых).
        if activated_at is not None:
            return start_day
        # Истёкший триал закрывается ровно на старт + 7 дней → старт = закрытие − 7.
        # Так возвращаем в когорту триалы, которые не сконвертились (иначе теряются).
        if status == "CLOSED" or event_type == "CLOSED":
            return _to_msk_date(last_event_time) - timedelta(days=YEARLY_TRIAL_LAG_DAYS)
        # Активный триал без activated_at: фолбэк на last_event_time, фильтруем по типу
        # события, чтобы не считать поздние CANCELLED/CLIENT_SYNC чужим днём.
        if for_daily and event_type not in TRIAL_START_EVENTS:
            return None
        if not for_daily and event_type and event_type not in TRIAL_START_EVENTS:
            return None
        return start_day

    if period in ("MAIN", "GRACE"):
        # Месячная MAIN — сразу платная, не триал; не считаем стартом триала.
        if for_daily or not _is_yearly(product_code):
            return None
        # Годовая MAIN = сконвертированный триал. С activated_at — точный старт,
        # иначе оценка last_event − 7 дней (лаг годового триала).
        if activated_at is not None:
            return start_day
        return _to_msk_date(last_event_time) - timedelta(days=YEARLY_TRIAL_LAG_DAYS)

    return None


_ENTITLEMENTS_SQL = """
    SELECT purchase_id,
           user_id::text,
           product_code,
           period,
           status,
           last_subscription_event_type,
           last_event_time,
           activated_at
    FROM rustore_subscription_entitlements
    WHERE user_id IS NOT NULL
      AND period IS NOT NULL
      AND period IN ('TRIAL', 'MAIN', 'GRACE', 'CLOSED')
      AND coalesce(activated_at, last_event_time) IS NOT NULL;
"""


def _fetch_rows(db_url: str) -> list[tuple]:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary required for Supabase trials") from exc
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_ENTITLEMENTS_SQL)
            return cur.fetchall()


def _rows_to_starts(
    rows: list[tuple],
    date_since: date,
    date_until: date,
    *,
    for_daily: bool,
) -> list[TrialStart]:
    starts: list[TrialStart] = []
    for (
        purchase_id,
        user_id,
        product_code,
        period,
        status,
        event_type,
        last_event_time,
        activated_at,
    ) in rows:
        trial_start = derive_trial_start(
            period=period,
            product_code=product_code,
            last_event_time=last_event_time,
            status=status,
            event_type=event_type,
            activated_at=activated_at,
            for_daily=for_daily,
        )
        if trial_start is None:
            continue
        if trial_start < date_since or trial_start > date_until:
            continue
        starts.append(
            TrialStart(
                user_id=str(user_id),
                purchase_id=str(purchase_id),
                trial_start=trial_start,
            )
        )
    return starts


def fetch_trial_starts(db_url: str, date_since: date, date_until: date) -> list[TrialStart]:
    """Cohort attribution: TRIAL starts + yearly MAIN/GRACE backdated by 7d."""
    return _rows_to_starts(_fetch_rows(db_url), date_since, date_until, for_daily=False)


def fetch_new_trial_starts(db_url: str, date_since: date, date_until: date) -> list[TrialStart]:
    """Daily column: новые триалы (ACTIVATED / CLIENT_SYNC / CANCELLED autorenew), без CLOSED и MAIN."""
    return _rows_to_starts(_fetch_rows(db_url), date_since, date_until, for_daily=True)


def dedupe_trial_starts_by_user(starts: list[TrialStart]) -> list[TrialStart]:
    """One trial per user — earliest start wins."""
    best: dict[str, TrialStart] = {}
    for row in starts:
        prev = best.get(row.user_id)
        if prev is None or row.trial_start < prev.trial_start:
            best[row.user_id] = row
    return sorted(best.values(), key=lambda r: (r.trial_start, r.user_id))


def trials_by_day_from_starts(starts: list[TrialStart]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in dedupe_trial_starts_by_user(starts):
        key = row.trial_start.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def count_trials_in_range(starts: list[TrialStart], start: date, end: date) -> int:
    users: set[str] = set()
    for row in dedupe_trial_starts_by_user(starts):
        if start <= row.trial_start <= end:
            users.add(row.user_id)
    return len(users)


def fetch_trials_by_day(db_url: str, date_since: date, date_until: date) -> dict[str, int]:
    """Authoritative daily trials — distinct users, new trial events only."""
    starts = fetch_new_trial_starts(db_url, date_since, date_until)
    return trials_by_day_from_starts(starts)


# ── Bills (успешные списания) из состояния подписок ──────────────────────────
# Билл = подписка дошла до period='MAIN' и status='ACTIVE' (списание прошло).
# MAIN CLOSED (возврат/чарджбэк) сюда НЕ попадает → возвраты отсекаются сами.
# Годовой (2490) — конвертированный триал; месячный (399) — прямая покупка.


@dataclass(frozen=True)
class Bill:
    user_id: str
    purchase_id: str
    plan: str  # 'yearly' | 'monthly'
    amount: int
    pay_date: date  # день списания (last_event_time, MSK)
    cohort_day: date  # день когорты (годовой: pay_date − 7; месячный: pay_date)


_BILLS_SQL = """
    SELECT purchase_id,
           user_id::text,
           product_code,
           last_subscription_event_type,
           last_event_time,
           activated_at
    FROM rustore_subscription_entitlements
    WHERE period = 'MAIN'
      AND status = 'ACTIVE'
      AND coalesce(last_event_time, activated_at) IS NOT NULL;
"""


def derive_bill(
    *,
    purchase_id: str,
    user_id: str,
    product_code: str | None,
    last_event_time: datetime | None,
    activated_at: datetime | None,
) -> Bill | None:
    charge = last_event_time or activated_at
    if charge is None:
        return None
    pay_date = _to_msk_date(charge)
    if _is_yearly(product_code):
        return Bill(
            user_id=str(user_id),
            purchase_id=str(purchase_id),
            plan="yearly",
            amount=YEARLY_PRICE,
            pay_date=pay_date,
            cohort_day=pay_date - timedelta(days=YEARLY_TRIAL_LAG_DAYS),
        )
    return Bill(
        user_id=str(user_id),
        purchase_id=str(purchase_id),
        plan="monthly",
        amount=MONTHLY_PRICE,
        pay_date=pay_date,
        cohort_day=pay_date,
    )


def fetch_bills(db_url: str, date_since: date, date_until: date) -> list[Bill]:
    """Успешные списания (MAIN ACTIVE), отфильтрованные по дате оплаты в диапазоне."""
    rows = _fetch_generic(db_url, _BILLS_SQL)
    bills: list[Bill] = []
    for purchase_id, user_id, product_code, _ev, last_event_time, activated_at in rows:
        bill = derive_bill(
            purchase_id=purchase_id,
            user_id=user_id,
            product_code=product_code,
            last_event_time=last_event_time,
            activated_at=activated_at,
        )
        if bill is None:
            continue
        if bill.pay_date < date_since or bill.pay_date > date_until:
            continue
        bills.append(bill)
    return sorted(bills, key=lambda b: (b.pay_date, b.user_id))


def bills_by_day(bills: list[Bill]) -> dict[str, int]:
    """Все успешные списания (годовые + месячные) по дню оплаты."""
    out: dict[str, int] = {}
    for b in bills:
        key = b.pay_date.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def sold_by_day(bills: list[Bill]) -> dict[str, int]:
    """Проданные триалы (годовая конверсия) по дню оплаты. Месячные не в счёт."""
    out: dict[str, int] = {}
    for b in bills:
        if b.plan != "yearly":
            continue
        key = b.pay_date.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def sold_by_cohort_day(bills: list[Bill]) -> dict[str, int]:
    """Годовые конверсии, отнесённые к дню когорты (оплата − 7д)."""
    out: dict[str, int] = {}
    for b in bills:
        if b.plan != "yearly":
            continue
        key = b.cohort_day.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(out)


def paid_net_by_cohort_day(bills: list[Bill]) -> dict[str, int]:
    """Сумма ₽ по дню когорты (годовой: оплата − 7д; месячный: день оплаты)."""
    out: dict[str, int] = {}
    for b in bills:
        key = b.cohort_day.isoformat()
        out[key] = out.get(key, 0) + b.amount
    return out


def bills_breakdown(bills: list[Bill]) -> dict[str, dict[str, int]]:
    """Разбивка биллов на месячные/годовые: count + rub."""
    out: dict[str, dict[str, int]] = {
        "yearly": {"count": 0, "rub": 0},
        "monthly": {"count": 0, "rub": 0},
        "total": {"count": 0, "rub": 0},
    }
    for b in bills:
        plan = "yearly" if b.plan == "yearly" else "monthly"
        out[plan]["count"] += 1
        out[plan]["rub"] += b.amount
        out["total"]["count"] += 1
        out["total"]["rub"] += b.amount
    return out


_TRIAL_CANCEL_SQL = """
    SELECT (last_event_time AT TIME ZONE 'Europe/Moscow')::date AS day,
           count(*)::int AS count
    FROM rustore_subscription_entitlements
    WHERE period = 'TRIAL'
      AND last_subscription_event_type = 'CANCELLED'
      AND last_event_time IS NOT NULL
      AND (last_event_time AT TIME ZONE 'Europe/Moscow')::date >= %s
      AND (last_event_time AT TIME ZONE 'Europe/Moscow')::date <= %s
    GROUP BY 1
    ORDER BY 1;
"""


def fetch_trial_cancellations_by_day(
    db_url: str, date_since: date, date_until: date
) -> dict[str, int]:
    """Отмены триала до списания: TRIAL + CANCELLED (autorenew off)."""
    rows = _fetch_generic_params(db_url, _TRIAL_CANCEL_SQL, (date_since, date_until))
    out: dict[str, int] = {}
    for day, count in rows:
        key = day.isoformat()[:10] if hasattr(day, "isoformat") else str(day)[:10]
        out[key] = int(count)
    return out


def fetch_unit_economics_snapshot(db_url: str) -> dict:
    """ARPU/ARPPU/LTV proxies + active payer base from entitlements."""
    sql = """
        SELECT
          count(DISTINCT user_id) FILTER (WHERE period = 'MAIN' AND status = 'ACTIVE') AS active_payers,
          count(DISTINCT user_id) FILTER (WHERE plus_active = true) AS plus_active_users,
          count(*) FILTER (WHERE period = 'TRIAL' AND last_subscription_event_type = 'CANCELLED') AS trial_cancellations,
          count(*) FILTER (WHERE last_subscription_event_type = 'RENEWED') AS renewals,
          count(*) FILTER (WHERE period = 'MAIN' AND status = 'ACTIVE' AND product_code ILIKE '%year%') AS active_yearly,
          count(*) FILTER (WHERE period = 'MAIN' AND status = 'ACTIVE' AND product_code ILIKE '%month%') AS active_monthly
        FROM rustore_subscription_entitlements;
    """
    rows = _fetch_generic(db_url, sql)
    row = rows[0] if rows else (0, 0, 0, 0, 0, 0)
    active_payers = int(row[0] or 0)
    plus_active = int(row[1] or 0)
    trial_cancels = int(row[2] or 0)
    renewals = int(row[3] or 0)
    active_yearly = int(row[4] or 0)
    active_monthly = int(row[5] or 0)
    return {
        "active_payers": active_payers,
        "plus_active_users": plus_active,
        "trial_cancellations": trial_cancels,
        "renewals": renewals,
        "active_yearly": active_yearly,
        "active_monthly": active_monthly,
    }


def _fetch_generic_params(db_url: str, sql: str, params: tuple) -> list[tuple]:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary required for Supabase") from exc
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _fetch_generic(db_url: str, sql: str) -> list[tuple]:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary required for Supabase") from exc
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def fetch_trials_by_day_legacy_updated_at(
    db_url: str, date_since: date, date_until: date
) -> dict[str, int]:
    """Legacy SQL (period=TRIAL, updated_at) — for reconcile diff only."""
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary required for Supabase trials") from exc

    since_ts = f"{date_since.isoformat()} 00:00:00+00"
    until_ts = f"{date_until.isoformat()} 23:59:59+00"
    sql = """
        SELECT date_trunc('day', updated_at AT TIME ZONE 'Europe/Moscow')::date AS day,
               count(*)::int AS count
        FROM rustore_subscription_entitlements
        WHERE period = 'TRIAL'
          AND updated_at >= %s AND updated_at <= %s
        GROUP BY 1 ORDER BY 1;
    """
    out: dict[str, int] = {}
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (since_ts, until_ts))
            for row in cur.fetchall():
                day = row[0].isoformat()[:10]
                out[day] = int(row[1])
    return out
