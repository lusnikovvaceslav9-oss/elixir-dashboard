"""Catalog-mode CPT and first MAIN prices (ColorStylist)."""

from datetime import date, datetime, timezone

from supabase import (
    SkuOffer,
    amount_for_sku,
    derive_bill,
    derive_trial_start,
    set_bill_cohort_from_activated_at,
    set_sku_catalog,
)


def _ts(y, m, d, h=12):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def setup_function():
    set_bill_cohort_from_activated_at(True)
    set_sku_catalog({
        "premium_week_399": SkuOffer("weekly", 399, 3),
        "premium_week": SkuOffer("weekly", 199, 3),
        "premium_month_799": SkuOffer("monthly", 799, 0),
        "premium_month": SkuOffer("monthly", 399, 0),
        "premium_year_4999": SkuOffer("yearly", 4999, 0),
        "premium_year": SkuOffer("yearly", 2490, 0),
    })


def test_old_week_stays_199():
    setup_function()
    assert amount_for_sku("premium_week") == 199
    assert amount_for_sku("premium_week_399") == 399


def test_unknown_sku_not_199():
    setup_function()
    assert amount_for_sku("premium_mystery") is None


def test_trial_from_trial_period():
    setup_function()
    d = derive_trial_start(
        period="TRIAL",
        product_code="premium_week",
        last_event_time=_ts(2026, 9, 10),
        activated_at=_ts(2026, 9, 8, 8),
    )
    assert d == date(2026, 9, 8)


def test_converted_week_still_cpt():
    setup_function()
    d = derive_trial_start(
        period="MAIN",
        product_code="premium_week_399",
        last_event_time=_ts(2026, 9, 14),
        activated_at=_ts(2026, 9, 11, 8),
    )
    assert d == date(2026, 9, 11)


def test_month_main_not_in_cpt():
    setup_function()
    d = derive_trial_start(
        period="MAIN",
        product_code="premium_month",
        last_event_time=_ts(2026, 8, 20),
        activated_at=_ts(2026, 8, 20),
    )
    assert d is None


def test_hold_not_in_cpt():
    setup_function()
    d = derive_trial_start(
        period="HOLD",
        product_code="premium_week",
        last_event_time=_ts(2026, 9, 10),
        activated_at=_ts(2026, 9, 8),
    )
    assert d is None


def test_week_first_pay_plus_trial_days():
    setup_function()
    bill = derive_bill(
        purchase_id="p1",
        user_id="u1",
        product_code="premium_week",
        last_event_time=_ts(2026, 8, 15),
        activated_at=_ts(2026, 8, 10, 8),
        period="MAIN",
        status="ACTIVE",
    )
    assert bill is not None
    assert bill.amount == 199
    assert bill.pay_date == date(2026, 8, 13)  # activated 10 Aug + 3


def test_new_week_first_is_399():
    setup_function()
    bill = derive_bill(
        purchase_id="p2",
        user_id="u2",
        product_code="premium_week_399",
        last_event_time=_ts(2026, 9, 14, 12),
        activated_at=_ts(2026, 9, 11, 8),
        period="MAIN",
        status="ACTIVE",
    )
    assert bill is not None
    assert bill.amount == 399


def test_month_first_pay_same_day():
    setup_function()
    bill = derive_bill(
        purchase_id="p3",
        user_id="u3",
        product_code="premium_month",
        last_event_time=_ts(2026, 8, 5),
        activated_at=_ts(2026, 8, 5, 6),
        period="MAIN",
        status="ACTIVE",
    )
    assert bill is not None
    assert bill.amount == 399
    assert bill.pay_date == date(2026, 8, 5)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            setup_function()
            fn()
            print("ok", name)
