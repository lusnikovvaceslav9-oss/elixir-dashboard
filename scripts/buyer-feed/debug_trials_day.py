#!/usr/bin/env python3
"""List trial_start attribution for a single day (debug)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from secrets import load_secrets
from supabase import (
    TRIAL_START_EVENTS,
    _to_msk_date,
    derive_trial_start,
    fetch_new_trial_starts,
    fetch_trial_starts,
    fetch_trials_by_day_legacy_updated_at,
    trials_by_day_from_starts,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=Path.cwd())
    parser.add_argument("--day", type=str, required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    target = date.fromisoformat(args.day)
    secrets = load_secrets(args.work_dir.resolve())
    db_url = secrets.get("SUPABASE_DB_URL")
    if not db_url:
        print("No SUPABASE_DB_URL", file=sys.stderr)
        return 1

    import psycopg2

    sql = """
        SELECT purchase_id, user_id::text, product_code, period, status,
               last_event_time, updated_at, last_subscription_event_type, plus_active,
               activated_at
        FROM rustore_subscription_entitlements
        WHERE user_id IS NOT NULL
          AND coalesce(activated_at, last_event_time) IS NOT NULL
          AND period IN ('TRIAL', 'MAIN', 'GRACE', 'CLOSED')
        ORDER BY coalesce(activated_at, last_event_time);
    """
    all_rows: list[tuple] = []
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            all_rows = cur.fetchall()

    daily_rows = []
    cohort_rows = []
    legacy_trial_event_day = []
    excluded = []

    for r in all_rows:
        purchase_id, user_id, product_code, period, status, last_event_time, updated_at, ev_type, plus_active, activated_at = r
        daily_ts = derive_trial_start(
            period=period,
            product_code=product_code,
            last_event_time=last_event_time,
            status=status,
            event_type=ev_type,
            activated_at=activated_at,
            for_daily=True,
        )
        cohort_ts = derive_trial_start(
            period=period,
            product_code=product_code,
            last_event_time=last_event_time,
            status=status,
            event_type=ev_type,
            activated_at=activated_at,
            for_daily=False,
        )
        row_info = {
            "user_id": user_id[:8],
            "purchase_id": purchase_id[:12],
            "period": period,
            "status": status,
            "product": product_code,
            "last_event_msk": _to_msk_date(last_event_time).isoformat(),
            "activated_msk": _to_msk_date(activated_at).isoformat() if activated_at else "—",
            "event_type": ev_type,
            "plus_active": plus_active,
        }
        if daily_ts == target:
            daily_rows.append(row_info)
        if cohort_ts == target:
            cohort_rows.append(row_info)
        if period == "TRIAL" and _to_msk_date(last_event_time) == target:
            legacy_trial_event_day.append({**row_info, "daily": daily_ts, "cohort": cohort_ts})
            if daily_ts != target:
                excluded.append(row_info)

    new_starts = fetch_new_trial_starts(db_url, target, target)
    cohort_starts = fetch_trial_starts(db_url, target, target)
    legacy = fetch_trials_by_day_legacy_updated_at(db_url, target, target)

    print(f"=== {target} — daily new trials (for_daily): {len(daily_rows)} rows, "
          f"deduped {trials_by_day_from_starts(new_starts).get(target.isoformat(), 0)} ===")
    print(f"    events counted: {sorted(TRIAL_START_EVENTS)}")
    for row in daily_rows:
        print(row)

    print(f"\n=== excluded from daily (TRIAL event on day but not start): {len(excluded)} ===")
    for row in excluded:
        print(row)

    print(f"\n=== cohort trial_start = {target}: {len(cohort_rows)} ===")
    for row in cohort_rows:
        print(row)

    print(f"\n=== legacy period=TRIAL & last_event day = {target}: {legacy.get(target.isoformat(), 0)} rows ===")
    for row in legacy_trial_event_day:
        print(row)

    print(f"\nfetch_new_trial_starts: {len(new_starts)}")
    print(f"fetch_trial_starts (cohort): {len(cohort_starts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
