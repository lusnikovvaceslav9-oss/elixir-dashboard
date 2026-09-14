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
    product_code: str = ""


@dataclass(frozen=True)
class SkuOffer:
    period: str  # weekly | monthly | yearly
    price: int
    trial_days: int


def _to_msk_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(MSK).date()


def _is_yearly(product_code: str | None) -> bool:
    code = (product_code or "").lower()
    return "yearly" in code or "year" in code or code == "planto_plus_yearly"


def _is_weekly(product_code: str | None) -> bool:
    code = (product_code or "").lower()
    return "week" in code or "7d" in code


def plan_of(product_code: str | None) -> str:
    """weekly / monthly / yearly. Раньше было бинарно (год vs «всё остальное»),
    из-за чего недельная подписка (premium_week) считалась месячной."""
    offer = sku_offer(product_code)
    if offer:
        return offer.period
    if _is_yearly(product_code):
        return "yearly"
    if _is_weekly(product_code):
        return "weekly"
    return "monthly"


# Цены задаются проектом (config.plans + config.sku_prices).
PLAN_PRICES = {"weekly": 0, "monthly": MONTHLY_PRICE, "yearly": YEARLY_PRICE}
SKU_PRICES: dict[str, int] = {}
SKU_CATALOG: dict[str, SkuOffer] = {}
UNKNOWN_SKUS: list[str] = []
# Planto: когорта = календарный день activated_at, счёт года = старт + lag.
# Не last_event − lag: last_event ползёт на ретраях RuStore.
BILL_COHORT_FROM_ACTIVATED_AT = False
# Касса с этой даты (MSK). ColorStylist: 2026-08-01 — чек 31.07 не в first.
CASH_FROM: date | None = None

# Лаг триала по тарифу: когортный день оплаты = pay_date − lag.
# ColorStylist: триал только на week (3д). Planto: триал на yearly (7д).
TRIAL_LAG_BY_PLAN = {"yearly": YEARLY_TRIAL_LAG_DAYS, "weekly": 0, "monthly": 0}


def set_plan_prices(plans: dict | None) -> None:
    if not plans:
        return
    for key, alias in (("weekly", "week"), ("monthly", "month"), ("yearly", "year")):
        v = plans.get(alias, plans.get(key))
        if v is not None:
            try:
                PLAN_PRICES[key] = int(v)
            except (TypeError, ValueError):
                pass


def _normalize_offer_period(raw: str | None) -> str:
    p = str(raw or "").strip().lower()
    if p in ("week", "weekly", "7d"):
        return "weekly"
    if p in ("year", "yearly"):
        return "yearly"
    return "monthly"


def _int_or_zero(raw) -> int:
    if raw is None or raw == "":
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def set_sku_catalog(mapping: dict[str, SkuOffer] | None) -> None:
    SKU_CATALOG.clear()
    UNKNOWN_SKUS.clear()
    if not mapping:
        return
    SKU_CATALOG.update(mapping)


def sku_offer(product_code: str | None) -> SkuOffer | None:
    return SKU_CATALOG.get(str(product_code or "").strip())


def catalog_active() -> bool:
    return bool(SKU_CATALOG)


def pop_unknown_skus() -> list[str]:
    out = sorted({c for c in UNKNOWN_SKUS if c})
    UNKNOWN_SKUS.clear()
    return out


def catalog_dump() -> dict[str, dict]:
    return {
        code: {"period": o.period, "price": o.price, "trial_days": o.trial_days}
        for code, o in sorted(SKU_CATALOG.items())
    }


_PAYWALL_OFFERS_SQL = """
    SELECT period, product_id, product_id_b,
           price_rub, price_rub_b,
           trial_days, trial_days_b
    FROM paywall_offers
    WHERE active
      AND store IN ('all', 'rustore');
"""


def fetch_paywall_offers(db_url: str) -> dict[str, SkuOffer]:
    """Live SKU catalog: product_id and product_id_b each get their own price/trial."""
    rows = _fetch_generic(db_url, _PAYWALL_OFFERS_SQL)
    out: dict[str, SkuOffer] = {}
    for period, pid, pid_b, price, price_b, trial, trial_b in rows:
        plan = _normalize_offer_period(period)
        code = str(pid or "").strip()
        if code:
            out[code] = SkuOffer(period=plan, price=_int_or_zero(price), trial_days=_int_or_zero(trial))
        code_b = str(pid_b or "").strip()
        if code_b:
            price_old = price_b if price_b is not None else price
            trial_old = trial_b if trial_b is not None else trial
            out[code_b] = SkuOffer(
                period=plan,
                price=_int_or_zero(price_old),
                trial_days=_int_or_zero(trial_old),
            )
    return out


def set_sku_prices(mapping: dict | None) -> None:
    SKU_PRICES.clear()
    if not mapping:
        return
    for key, raw in mapping.items():
        code = str(key or "").strip()
        if not code:
            continue
        try:
            SKU_PRICES[code] = int(raw)
        except (TypeError, ValueError):
            continue


def set_bill_cohort_from_activated_at(enabled: bool) -> None:
    global BILL_COHORT_FROM_ACTIVATED_AT
    BILL_COHORT_FROM_ACTIVATED_AT = bool(enabled)


def set_cash_from(raw) -> None:
    """Нижняя граница кассы по pay_date (MSK). None / пусто — без отсечки."""
    global CASH_FROM
    CASH_FROM = None
    if raw is None or raw == "":
        return
    if isinstance(raw, date):
        CASH_FROM = raw
        return
    try:
        CASH_FROM = date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        CASH_FROM = None


def amount_for_sku(product_code: str | None) -> int | None:
    code = str(product_code or "").strip()
    offer = sku_offer(code)
    if offer:
        return int(offer.price)
    if catalog_active():
        return None
    if code in SKU_PRICES:
        return int(SKU_PRICES[code])
    return int(PLAN_PRICES.get(plan_of(code), MONTHLY_PRICE))


def trial_days_for_sku(product_code: str | None) -> int:
    offer = sku_offer(product_code)
    if offer:
        return int(offer.trial_days or 0)
    if catalog_active():
        return 0
    return trial_lag_for_plan(plan_of(product_code))


def set_trial_config(cfg: dict | None) -> None:
    """trial_plan + trial_days из конфига проекта.

    ColorStylist: trial_plan=week, trial_days=3 → лаг только у weekly.
    Planto (без trial_plan): trial_lag_days=7 → лаг у yearly.
    """
    if not cfg:
        return
    trial_days = cfg.get("trial_days")
    if trial_days is None:
        trial_days = cfg.get("trial_lag_days")
    try:
        lag = int(trial_days) if trial_days is not None else None
    except (TypeError, ValueError):
        lag = None
    plan = str(cfg.get("trial_plan") or "").strip().lower()
    if plan in ("week", "weekly", "7d"):
        TRIAL_LAG_BY_PLAN["weekly"] = lag if lag is not None else 0
        TRIAL_LAG_BY_PLAN["yearly"] = 0
        TRIAL_LAG_BY_PLAN["monthly"] = 0
    elif plan in ("year", "yearly"):
        TRIAL_LAG_BY_PLAN["yearly"] = lag if lag is not None else YEARLY_TRIAL_LAG_DAYS
        TRIAL_LAG_BY_PLAN["weekly"] = 0
        TRIAL_LAG_BY_PLAN["monthly"] = 0
    elif lag is not None:
        # Legacy: trial_lag_days без trial_plan → yearly (Planto).
        TRIAL_LAG_BY_PLAN["yearly"] = lag


def trial_lag_for_plan(plan: str) -> int:
    return int(TRIAL_LAG_BY_PLAN.get(plan) or 0)


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
    Catalog mode (paywall_offers): CPT = TRIAL or MAIN/GRACE у SKU с trial_days>0.
    """
    period = (period or "").upper()
    status = (status or "").upper()
    event_type = (event_type or "").upper()
    start_time = activated_at or last_event_time
    if start_time is None:
        return None
    start_day = _to_msk_date(activated_at) if activated_at is not None else _to_msk_date(start_time)

    if catalog_active():
        offer = sku_offer(product_code)
        has_trial_sku = bool(offer and offer.trial_days > 0)
        # HOLD/PAUSED без списания — не CPT. CLOSED/TERMINATED после триала — да:
        # иначе первая неделя РК обнуляется, как только все сконвертились.
        if period in ("HOLD", "PAUSED"):
            return None
        if period == "TRIAL" or (has_trial_sku and period in ("MAIN", "GRACE", "CLOSED", "TERMINATED", "EXPIRED")):
            if activated_at is not None:
                return start_day
            lag = int(offer.trial_days) if offer else 0
            if lag and last_event_time is not None and period in ("CLOSED", "TERMINATED", "EXPIRED"):
                return _to_msk_date(last_event_time) - timedelta(days=lag)
            return start_day
        return None

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
        # Годовая MAIN = сконвертированный триал. Только activated_at — last_event ползёт.
        if _is_yearly(product_code):
            if activated_at is not None:
                return _to_msk_date(activated_at)
            if last_event_time is None:
                return None
            return _to_msk_date(last_event_time) - timedelta(days=YEARLY_TRIAL_LAG_DAYS)
        # Месяц: триала нет, activated_at = старт оплаты (входит в «старты» когорты).
        if plan_of(product_code) == "monthly" and activated_at is not None:
            return _to_msk_date(activated_at)
        return None

    # HOLD/PAUSED/CLOSED/TERMINATED: старт = activated_at; если вебхук его
    # не заполнил (оборванный месяц W2) — last_event, иначе строка пропадает.
    if activated_at is not None:
        return _to_msk_date(activated_at)
    if plan_of(product_code) == "monthly" and start_time is not None:
        return start_day

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
      AND coalesce(activated_at, last_event_time) IS NOT NULL;
"""


def _product_matches(product_code: str | None, needles: list[str] | None) -> bool:
    if not needles:
        return True
    code = (product_code or "").lower()
    return any(n.lower() in code for n in needles if n)


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
    product_code_includes: list[str] | None = None,
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
        if not _product_matches(product_code, product_code_includes):
            continue
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
                product_code=str(product_code or ""),
            )
        )
    return starts


def fetch_trial_starts(
    db_url: str,
    date_since: date,
    date_until: date,
    product_code_includes: list[str] | None = None,
) -> list[TrialStart]:
    """Cohort attribution: TRIAL starts + yearly MAIN/GRACE backdated by 7d."""
    return _rows_to_starts(
        _fetch_rows(db_url), date_since, date_until, for_daily=False,
        product_code_includes=product_code_includes,
    )


def fetch_new_trial_starts(
    db_url: str,
    date_since: date,
    date_until: date,
    product_code_includes: list[str] | None = None,
) -> list[TrialStart]:
    """Daily column: новые триалы (ACTIVATED / CLIENT_SYNC / CANCELLED autorenew), без CLOSED и MAIN."""
    return _rows_to_starts(
        _fetch_rows(db_url), date_since, date_until, for_daily=True,
        product_code_includes=product_code_includes,
    )


def dedupe_trial_starts_by_user(starts: list[TrialStart]) -> list[TrialStart]:
    """One trial per user_id (fallback purchase_id) — earliest start wins."""
    best: dict[str, TrialStart] = {}
    for row in starts:
        key = str(row.user_id or row.purchase_id)
        prev = best.get(key)
        if prev is None or row.trial_start < prev.trial_start:
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.trial_start, key_user(r)))


def key_user(row: TrialStart) -> str:
    return str(row.user_id or row.purchase_id)


def trials_by_day_from_starts(starts: list[TrialStart]) -> dict[str, int]:
    """Один purchase_id в день — не схлопывать разные SKU одного user_id."""
    seen: set[tuple[str, str]] = set()
    out: dict[str, int] = {}
    for row in starts:
        pid = str(row.purchase_id or row.user_id)
        key = row.trial_start.isoformat()
        sig = (key, pid)
        if sig in seen:
            continue
        seen.add(sig)
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


# ── Bills (успешные списания) из entitlements ────────────────────────────────
# Касса = каталожная цена SKU × факт списания, до комиссии RuStore.
# Когорта Planto = день activated_at (MSK). Счёт года в консоли = старт + 7д.
# Не last_event − 7: last_event ползёт на ретраях.
# MAIN + HOLD/TERMINATED после денег — входит. HOLD без списания (не MAIN) — нет.
# Возврат: год CLOSED вскоре после первого счёта.


@dataclass(frozen=True)
class Bill:
    user_id: str
    purchase_id: str
    plan: str  # 'yearly' | 'monthly' | 'weekly'
    amount: int
    pay_date: date  # ожидаемый/первый счёт (год: activated_at + lag)
    cohort_day: date  # неделя набора: activated_at
    product_code: str = ""
    kind: str = "first"  # first | rebill


def _is_refund_closed(
    *,
    plan: str,
    status: str | None,
    event_type: str | None,
    last_event_time: datetime | None,
    first_invoice: date,
) -> bool:
    status_u = (status or "").upper()
    event_u = (event_type or "").upper()
    if event_u in ("REFUNDED", "REFUND", "CHARGEBACK"):
        return True
    # Месяц, который уже списали и потом оборвали, — не возврат.
    if plan != "yearly":
        return False
    if status_u != "CLOSED":
        return False
    if not last_event_time:
        return True
    closed = _to_msk_date(last_event_time)
    # Год закрылся вскоре после списания → деньги уехали.
    return closed <= first_invoice + timedelta(days=14)


def derive_bill(
    *,
    purchase_id: str,
    user_id: str,
    product_code: str | None,
    last_event_time: datetime | None,
    activated_at: datetime | None,
    period: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
) -> Bill | None:
    plan = plan_of(product_code)
    period_u = (period or "").upper()
    if catalog_active():
        amount = amount_for_sku(product_code)
        if amount is None:
            code = str(product_code or "").strip()
            if code:
                UNKNOWN_SKUS.append(code)
            return None
        if period_u != "MAIN":
            return None
        lag = trial_days_for_sku(product_code)
    else:
        if plan == "weekly" and int(PLAN_PRICES.get("weekly") or 0) <= 0:
            return None
        # Год в кассе только после MAIN. HOLD/GRACE без списания — нет.
        if plan == "yearly" and period_u != "MAIN":
            return None
        # У месяца триала нет: TRIAL не касса. Любой другой период после денег — да.
        if plan == "monthly" and period_u == "TRIAL":
            return None
        amount = amount_for_sku(product_code) or 0
        lag = trial_lag_for_plan(plan)

    if BILL_COHORT_FROM_ACTIVATED_AT:
        start_src = activated_at
        if start_src is None and plan == "monthly":
            start_src = last_event_time
        if start_src is None:
            return None
        start = _to_msk_date(start_src)
        pay_date = start + timedelta(days=lag) if lag else start
        cohort_day = start
    else:
        charge = last_event_time or activated_at
        if charge is None:
            return None
        pay_date = _to_msk_date(charge)
        cohort_day = pay_date - timedelta(days=lag) if lag else pay_date
        start = cohort_day

    if _is_refund_closed(
        plan=plan,
        status=status,
        event_type=event_type,
        last_event_time=last_event_time,
        first_invoice=pay_date,
    ):
        return None

    return Bill(
        user_id=str(user_id),
        purchase_id=str(purchase_id),
        plan=plan,
        amount=int(amount),
        pay_date=pay_date,
        cohort_day=cohort_day,
        product_code=str(product_code or ""),
        kind="first",
    )


def _add_calendar_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _n_period_steps(first_pay: date, last: date, plan: str) -> int:
    """Полные шаги тарифа между first_pay и last, без дня first."""
    if last <= first_pay:
        return 0
    if plan == "weekly":
        return (last - first_pay).days // 7
    if plan == "monthly":
        n = (last.year - first_pay.year) * 12 + (last.month - first_pay.month)
        if last.day < first_pay.day:
            n -= 1
        return max(0, n)
    if plan == "yearly":
        n = last.year - first_pay.year
        if (last.month, last.day) < (first_pay.month, first_pay.day):
            n -= 1
        return max(0, n)
    return 0


def _step_pay_date(first_pay: date, k: int, plan: str) -> date:
    if plan == "weekly":
        return first_pay + timedelta(days=7 * k)
    if plan == "monthly":
        return _add_calendar_months(first_pay, k)
    if plan == "yearly":
        return _add_calendar_months(first_pay, 12 * k)
    return first_pay + timedelta(days=7 * k)


def expand_weekly_rebills(
    bill: Bill,
    *,
    last_event_time: datetime | None,
    activated_at: datetime | None,
    until: date,
) -> list[Bill]:
    """First MAIN + extra списания по шагу тарифа (неделя 7д / месяц / год)."""
    if int(bill.amount or 0) <= 0:
        return [bill]
    if last_event_time is None:
        return [bill]
    last = _to_msk_date(last_event_time)
    n_extra = _n_period_steps(bill.pay_date, last, bill.plan)
    if n_extra < 1:
        return [bill]
    out = [bill]
    for k in range(1, n_extra + 1):
        d = _step_pay_date(bill.pay_date, k, bill.plan)
        if d > until:
            break
        out.append(
            Bill(
                user_id=bill.user_id,
                purchase_id=bill.purchase_id,
                plan=bill.plan,
                amount=bill.amount,
                pay_date=d,
                cohort_day=bill.cohort_day,
                product_code=bill.product_code,
                kind="rebill",
            )
        )
    return out


_BILLS_SQL = """
    SELECT purchase_id,
           user_id::text,
           product_code,
           period,
           status,
           last_subscription_event_type,
           last_event_time,
           activated_at
    FROM rustore_subscription_entitlements
    WHERE coalesce(activated_at, last_event_time) IS NOT NULL
      AND (
        period = 'MAIN'
        OR (
          product_code ILIKE '%month%'
          AND product_code NOT ILIKE '%year%'
          AND coalesce(period, '') NOT IN ('TRIAL')
        )
      );
"""


def fetch_bills(
    db_url: str,
    date_since: date,
    date_until: date,
    product_code_includes: list[str] | None = None,
) -> list[Bill]:
    """Первые успешные списания MAIN. Статус не только ACTIVE: оборванный
    уже оплаченный месяц тоже касса. Фильтр дат — когорта (activated_at) или счёт."""
    rows = _fetch_generic(db_url, _BILLS_SQL)
    bills: list[Bill] = []
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
        if not _product_matches(product_code, product_code_includes):
            continue
        bill = derive_bill(
            purchase_id=purchase_id,
            user_id=user_id,
            product_code=product_code,
            last_event_time=last_event_time,
            activated_at=activated_at,
            period=period,
            status=status,
            event_type=event_type,
        )
        if bill is None:
            continue
        for item in expand_weekly_rebills(
            bill,
            last_event_time=last_event_time,
            activated_at=activated_at,
            until=date_until,
        ):
            if CASH_FROM and item.pay_date < CASH_FROM:
                continue
            in_cohort = date_since <= item.cohort_day <= date_until
            in_invoice = date_since <= item.pay_date <= date_until
            if not in_cohort and not in_invoice:
                continue
            bills.append(item)
    return sorted(bills, key=lambda b: (b.cohort_day, b.user_id, b.pay_date, b.kind))


def first_bills(bills: list[Bill]) -> list[Bill]:
    return [b for b in bills if (b.kind or "first") != "rebill"]


def rebill_bills(bills: list[Bill]) -> list[Bill]:
    return [b for b in bills if b.kind == "rebill"]


def rebill_stats(bills: list[Bill]) -> dict:
    extras = rebill_bills(bills)
    firsts = first_bills(bills)
    by_pid: dict[str, int] = {}
    for b in extras:
        by_pid[b.purchase_id] = by_pid.get(b.purchase_id, 0) + 1
    return {
        "week_events": len(extras),
        "rub": sum(int(b.amount or 0) for b in extras),
        "users_ge1": len(by_pid),
        "max": max(by_pid.values()) if by_pid else 0,
        "first_count": len(firsts),
        "first_rub": sum(int(b.amount or 0) for b in firsts),
        "total_rub": sum(int(b.amount or 0) for b in bills),
    }


def sku_chip_breakdown(
    bills: list[Bill],
    trial_starts: list[TrialStart] | None = None,
) -> dict[str, dict[str, int | str]]:
    """Чипы кассы: MAIN × цена SKU и отдельно число триалов этого SKU."""
    out: dict[str, dict[str, int | str]] = {}
    for code, offer in SKU_CATALOG.items():
        out[code] = {
            "period": offer.period,
            "price": offer.price,
            "trial_days": offer.trial_days,
            "main": 0,
            "trial": 0,
            "rub": 0,
        }
    for b in first_bills(bills):
        code = str(b.product_code or "").strip() or b.plan
        cell = out.setdefault(code, {
            "period": b.plan,
            "price": int(b.amount or 0),
            "trial_days": 0,
            "main": 0,
            "trial": 0,
            "rub": 0,
        })
        cell["main"] = int(cell["main"]) + 1
        cell["rub"] = int(cell["rub"]) + int(b.amount or 0)
    seen: set[tuple[str, str]] = set()
    for row in trial_starts or []:
        code = str(getattr(row, "product_code", "") or "")
        uid = str(row.user_id or row.purchase_id)
        sig = (code, uid)
        if sig in seen:
            continue
        seen.add(sig)
        cell = out.setdefault(code, {
            "period": plan_of(code),
            "price": int(amount_for_sku(code) or 0),
            "trial_days": trial_days_for_sku(code),
            "main": 0,
            "trial": 0,
            "rub": 0,
        })
        cell["trial"] = int(cell["trial"]) + 1
    return {k: v for k, v in out.items() if int(v["main"]) or int(v["trial"])}


def bills_by_day(bills: list[Bill]) -> dict[str, int]:
    """Первые успешные списания по дню оплаты (ребиллы не в CAC/fb)."""
    out: dict[str, int] = {}
    for b in first_bills(bills):
        key = b.pay_date.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def bills_by_cohort_day(bills: list[Bill]) -> dict[str, int]:
    """Первые списания по дню когорты: yearly = оплата − 7д (старт триала), monthly = день оплаты."""
    out: dict[str, int] = {}
    for b in first_bills(bills):
        key = b.cohort_day.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def sold_by_day(bills: list[Bill]) -> dict[str, int]:
    """Проданные триалы (годовая конверсия) по дню оплаты. Месячные не в счёт."""
    out: dict[str, int] = {}
    for b in first_bills(bills):
        if b.plan != "yearly":
            continue
        key = b.pay_date.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def sold_by_cohort_day(bills: list[Bill]) -> dict[str, int]:
    """Годовые конверсии, отнесённые к дню когорты (оплата − 7д)."""
    out: dict[str, int] = {}
    for b in first_bills(bills):
        if b.plan != "yearly":
            continue
        key = b.cohort_day.isoformat()
        out[key] = out.get(key, 0) + 1
    return dict(out)


def paid_net_by_cohort_day(bills: list[Bill]) -> dict[str, int]:
    """Сумма ₽ по дню когорты (годовой: оплата − lag; месячный/неделя: день оплаты)."""
    out: dict[str, int] = {}
    for b in bills:
        key = b.cohort_day.isoformat()
        out[key] = out.get(key, 0) + b.amount
    return out


def paid_net_by_pay_day(bills: list[Bill]) -> dict[str, int]:
    """Сумма ₽ по календарному дню оплаты — как в консоли RuStore."""
    out: dict[str, int] = {}
    for b in bills:
        key = b.pay_date.isoformat()
        out[key] = out.get(key, 0) + int(b.amount or 0)
    return dict(sorted(out.items()))


def bills_breakdown(bills: list[Bill]) -> dict[str, dict[str, int]]:
    """Разбивка первых списаний по тарифам (ребиллы — отдельно в meta.rebills)."""
    bills = first_bills(bills)
    out: dict[str, dict[str, int]] = {
        "yearly": {"count": 0, "rub": 0},
        "monthly": {"count": 0, "rub": 0},
        "weekly": {"count": 0, "rub": 0},
        "total": {"count": 0, "rub": 0},
    }
    for b in bills:
        plan = b.plan if b.plan in out else "monthly"
        out[plan]["count"] += 1
        out[plan]["rub"] += b.amount
        out["total"]["count"] += 1
        out["total"]["rub"] += b.amount
    return out


def bills_by_plan_by_day(bills: list[Bill]) -> dict[str, dict[str, dict[str, int]]]:
    """Оплаты по тарифу и календарному дню оплаты (как RuStore «за N дней»)."""
    out: dict[str, dict[str, dict[str, int]]] = {
        "yearly": {},
        "monthly": {},
        "weekly": {},
    }
    for b in bills:
        plan = b.plan if b.plan in out else "monthly"
        key = b.pay_date.isoformat()
        cell = out[plan].setdefault(key, {"count": 0, "rub": 0})
        cell["count"] += 1
        cell["rub"] += int(b.amount or 0)
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
