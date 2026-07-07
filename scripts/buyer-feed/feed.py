"""Orchestrate Planto buyer feed update."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from appmetrica import fetch_event_by_day, fetch_installs_by_day, fetch_window
from cohort import analyze_cohort_from_daily, default_anchor
from daily import estimate_today_spend, load_daily_csv, merge_daily, write_daily_csv
from direct import fetch_spend_by_day
from payments import (
    bills_by_day as csv_bills_by_day,
    load_payments,
    paid_net_by_cohort_day as csv_paid_net_by_cohort_day,
    payments_breakdown,
    sold_trials_by_cohort_day as csv_sold_by_cohort_day,
    sold_trials_by_day as csv_sold_by_day,
)
from secrets import load_secrets
from supabase import (
    bills_breakdown,
    bills_by_day,
    fetch_bills,
    fetch_trial_starts,
    paid_net_by_cohort_day,
    sold_by_cohort_day,
    sold_by_day,
    trials_by_day_from_starts,
)


def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def run_feed(work_dir: Path, config_path: Path | None = None) -> int:
    work_dir = work_dir.resolve()
    cfg_path = config_path or (work_dir / "config" / "planto.json")
    if not cfg_path.is_file():
        cfg_path = Path(__file__).resolve().parent / "config" / "planto.json"
    cfg = load_config(cfg_path)

    secrets = load_secrets(work_dir)
    anchor = date.fromisoformat(cfg.get("anchor") or default_anchor().isoformat())
    until = datetime.now(ZoneInfo("Europe/Moscow")).date()
    refresh_days = int(cfg.get("refresh_days") or 7)
    lag = int(cfg.get("attribution_lag_days") or 2)
    window_start = max(anchor, until - timedelta(days=refresh_days + lag))

    daily_path = work_dir / cfg.get("daily_csv", "data/planto-daily.csv")
    cohort_path = work_dir / cfg.get("cohort_json", "data/planto-cohort.json")
    meta_path = work_dir / cfg.get("meta_json", "data/planto-meta.json")
    payments_path = work_dir / cfg.get("payments_csv", "data/rustore-payments.csv")

    existing = load_daily_csv(daily_path)
    old_trials_total = sum(int(v.get("trials") or 0) for v in existing.values())

    spend: dict[str, float] = {}
    installs: dict[str, int] = {}
    trials: dict[str, int] = {}
    trials_am_crosscheck: dict[str, int] = {}
    trial_starts = []
    bills: dict[str, int] = {}
    sold: dict[str, int] = {}
    paid_by_cohort_day: dict[str, int] = {}
    sold_by_cohort_day_map: dict[str, int] = {}
    bills_by_plan: dict[str, dict[str, int]] | None = None
    sources: dict[str, str] = {}
    errors: list[str] = []

    direct_token = secrets.get("DIRECT_OAUTH_TOKEN")
    client_login = secrets.get("DIRECT_CLIENT_LOGIN") or cfg.get("direct_client_login") or ""
    if direct_token:
        try:
            spend = fetch_spend_by_day(direct_token, client_login, window_start, until)
            sources["spend"] = "direct_api"
            print(f"  Direct: {len(spend)} days")
        except Exception as exc:
            errors.append(f"direct: {exc}")
            print(f"  Direct failed: {exc}")
    else:
        errors.append("direct: DIRECT_OAUTH_TOKEN missing")

    am_token = secrets.get("APPMETRICA_OAUTH_TOKEN")
    app_id = secrets.get("APPMETRICA_APPLICATION_ID") or cfg.get("appmetrica_application_id") or "6305902"
    if am_token:
        try:
            inst_win, _ = fetch_window(am_token, str(app_id), anchor, until, refresh_days, lag)
            installs.update(inst_win)
            sources["installs"] = "appmetrica_reporting"
            try:
                trials_am_crosscheck = fetch_event_by_day(
                    am_token, str(app_id), "trial_started", window_start, until
                )
                if trials_am_crosscheck:
                    sources["trials_am_crosscheck"] = "appmetrica_reporting"
            except Exception as exc:
                errors.append(f"appmetrica_trials_crosscheck: {exc}")
            print(f"  AppMetrica: {len(installs)} install-days")
        except Exception as exc:
            errors.append(f"appmetrica: {exc}")
            print(f"  AppMetrica failed: {exc}")
    else:
        errors.append("appmetrica: APPMETRICA_OAUTH_TOKEN missing")

    db_url = secrets.get("SUPABASE_DB_URL")
    if db_url:
        try:
            trial_starts = fetch_trial_starts(db_url, anchor, until)
            trials_sb = trials_by_day_from_starts(trial_starts)
            trials = trials_sb
            if trials_sb:
                sources["trials"] = "supabase_trial_start"
            print(
                f"  Supabase trials: {len(trial_starts)} starts (daily = cohort), "
                f"{len(trials_sb)} days"
            )
        except Exception as exc:
            errors.append(f"supabase: {exc}")
            print(f"  Supabase failed: {exc}")
        try:
            bills_list = fetch_bills(db_url, anchor, until)
            bills = bills_by_day(bills_list)
            sold = sold_by_day(bills_list)
            paid_by_cohort_day = paid_net_by_cohort_day(bills_list)
            sold_by_cohort_day_map = sold_by_cohort_day(bills_list)
            bills_by_plan = bills_breakdown(bills_list)
            sources["bills"] = "supabase_main_active"
            print(f"  Supabase bills: {len(bills_list)} charges (MAIN active)")
        except Exception as exc:
            errors.append(f"supabase_bills: {exc}")
            print(f"  Supabase bills failed: {exc}")
    else:
        errors.append("supabase: SUPABASE_DB_URL missing")

    # CSV — fallback (если Supabase недоступен) + ручные корректировки/возвраты.
    payments = load_payments(payments_path)
    if payments and sources.get("bills") != "supabase_main_active":
        bills = csv_bills_by_day(payments)
        sold = csv_sold_by_day(payments)
        paid_by_cohort_day = csv_paid_net_by_cohort_day(payments)
        sold_by_cohort_day_map = csv_sold_by_cohort_day(payments)
        bills_by_plan = payments_breakdown(payments)
        sources["bills"] = "rustore_payments_csv"
        print(f"  Payments (CSV fallback): {len(payments)} records")

    full_spend = spend
    full_installs = installs
    full_trials = trials

    if anchor < window_start and direct_token:
        try:
            full_spend = fetch_spend_by_day(direct_token, client_login, anchor, until)
            # Wide date ranges return stale spend for recent days; window fetch is fresher.
            ws = window_start.isoformat()
            for day_key, value in spend.items():
                if day_key >= ws:
                    full_spend[day_key] = value
        except Exception:
            full_spend = spend

    if anchor < window_start and am_token:
        try:
            full_installs = fetch_installs_by_day(am_token, str(app_id), anchor, until)
        except Exception:
            full_installs = installs

    if anchor < window_start and db_url:
        try:
            trial_starts = fetch_trial_starts(db_url, anchor, until)
            full_trials = trials_by_day_from_starts(trial_starts)
        except Exception:
            full_trials = trials

    # Supabase — источник истины по триалам на весь диапазон: дни без стартов = 0,
    # иначе в CSV остаются старые (до-Supabase) значения и итог не сходится с когортами.
    if sources.get("trials") == "supabase_trial_start":
        filled = dict(full_trials)
        d = anchor
        while d <= until:
            filled.setdefault(d.isoformat(), 0)
            d += timedelta(days=1)
        full_trials = filled

    # Аналогично для биллов: Supabase — источник истины, дни без списаний = 0,
    # иначе в CSV висят старые (ручные) значения fb/sold.
    if sources.get("bills") == "supabase_main_active":
        filled_bills = dict(bills)
        filled_sold = dict(sold)
        d = anchor
        while d <= until:
            key = d.isoformat()
            filled_bills.setdefault(key, 0)
            filled_sold.setdefault(key, 0)
            d += timedelta(days=1)
        bills = filled_bills
        sold = filled_sold

    merged = merge_daily(
        existing,
        spend=full_spend,
        installs=full_installs,
        trials=full_trials,
        bills=bills,
        sold=sold,
        anchor=anchor,
        until=until,
    )
    spend_today_estimated = estimate_today_spend(merged, until)
    if spend_today_estimated:
        print(f"  Direct: estimated spend for {until.isoformat()} from recent CPI")
    write_daily_csv(daily_path, merged)
    print(f"  Daily CSV: {daily_path} ({len(merged)} days)")

    new_trials_total = sum(int(v.get("trials") or 0) for v in merged.values())

    daily_for_cohort = {
        k: {
            "spend": v.get("spend", 0),
            "installs": v.get("installs", 0),
            "trials": v.get("trials", 0),
        }
        for k, v in merged.items()
    }
    cohort = analyze_cohort_from_daily(
        anchor=anchor,
        until=until,
        report_date=until,
        daily=daily_for_cohort,
        paid_by_cohort_day=paid_by_cohort_day,
        sold_by_cohort_day=sold_by_cohort_day_map,
        trial_starts=trial_starts if db_url else None,
    )
    cohort_path.parent.mkdir(parents=True, exist_ok=True)
    cohort_path.write_text(json.dumps(cohort, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Cohort JSON: {cohort_path}")

    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "anchor": anchor.isoformat(),
        "until": until.isoformat(),
        "window_start": window_start.isoformat(),
        "trial_attribution": "supabase_trial_start",
        "sources": sources,
        "errors": errors,
        "days": len(merged),
        "reconcile_diff": {
            "trials_total_old_csv": old_trials_total,
            "trials_total_new": new_trials_total,
            "delta": new_trials_total - old_trials_total,
        },
    }
    if bills_by_plan:
        meta["payments_by_plan"] = bills_by_plan
    if trials_am_crosscheck:
        meta["trials_am_crosscheck_total"] = sum(trials_am_crosscheck.values())
    if spend_today_estimated:
        meta["spend_today_estimated"] = True
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return 0 if merged else 1
