"""Compare trial counts across buyer-feed sources."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from appmetrica import fetch_event_by_day
from cohort import cohort_buckets, count_trial_starts_in_bucket, default_anchor
from daily import load_daily_csv
from secrets import load_secrets
from supabase import (
    fetch_new_trial_starts,
    fetch_trial_starts,
    fetch_trials_by_day_legacy_updated_at,
    trials_by_day_from_starts,
)


def _day_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _load_daily_trials(work_dir: Path) -> dict[str, int]:
    daily_path = work_dir / "data" / "planto-daily.csv"
    rows = load_daily_csv(daily_path)
    return {k: int(v.get("trials") or 0) for k, v in rows.items()}


def _trials_from_am_logs(repo_root: Path, since: date, until: date) -> dict[str, int]:
    analytics = repo_root / "backups" / "analytics"
    if not analytics.is_dir():
        return {}
    try:
        sys.path.insert(0, str(repo_root / "scripts" / "analytics"))
        from build_dashboard_json import trials_by_day as am_trials_by_day  # noqa: PLC0415
        from build_dashboard_json import load_json_rows  # noqa: PLC0415
    except ImportError:
        return {}

    event_rows: list[dict] = []
    for ddir in sorted(analytics.iterdir()):
        if not ddir.is_dir():
            continue
        try:
            day = date.fromisoformat(ddir.name[:10])
        except ValueError:
            continue
        if day < since or day > until:
            continue
        events_path = ddir / "events.json"
        if events_path.is_file():
            event_rows.extend(load_json_rows(events_path))
    return am_trials_by_day(event_rows)


def _cohort_pnl_trials(repo_root: Path, anchor: date, until: date) -> dict[str, int]:
    analytics = repo_root / "scripts" / "analytics"
    if not (analytics / "cohort_pnl.py").is_file():
        return {}
    sys.path.insert(0, str(analytics))
    try:
        from cohort_pnl import analyze_cohort_pnl  # noqa: PLC0415
    except ImportError:
        return {}
    result = analyze_cohort_pnl(anchor=anchor, until=until, report_date=until)
    out: dict[str, int] = {}
    for row in result.get("rows") or []:
        out[row.get("cohort_id") or row.get("cohort", "")] = int(row.get("trials_am") or 0)
    return out


def run_reconcile(work_dir: Path, until: date | None = None, repo_root: Path | None = None) -> Path:
    work_dir = work_dir.resolve()
    repo_root = (repo_root or work_dir.parents[1]).resolve()
    cfg_path = work_dir / "config" / "planto.json"
    if not cfg_path.is_file():
        cfg_path = Path(__file__).resolve().parent / "config" / "planto.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    anchor = date.fromisoformat(cfg.get("anchor") or default_anchor().isoformat())
    until = until or date.today()

    secrets = load_secrets(work_dir)
    db_url = secrets.get("SUPABASE_DB_URL")
    am_token = secrets.get("APPMETRICA_OAUTH_TOKEN")
    app_id = secrets.get("APPMETRICA_APPLICATION_ID") or cfg.get("appmetrica_application_id") or "6305902"

    daily_csv = _load_daily_trials(work_dir)
    sb_legacy: dict[str, int] = {}
    sb_start: dict[str, int] = {}
    trial_starts = []
    am_reporting: dict[str, int] = {}

    if db_url:
        sb_legacy = fetch_trials_by_day_legacy_updated_at(db_url, anchor, until)
        new_starts = fetch_new_trial_starts(db_url, anchor, until)
        trial_starts = fetch_trial_starts(db_url, anchor, until)
        sb_start = trials_by_day_from_starts(new_starts)

    if am_token:
        try:
            am_reporting = fetch_event_by_day(am_token, str(app_id), "trial_started", anchor, until)
        except Exception as exc:
            am_reporting = {"_error": str(exc)}  # type: ignore[assignment]

    am_logs = _trials_from_am_logs(repo_root, anchor, until)
    cohort_pnl = _cohort_pnl_trials(repo_root, anchor, until)

    lines = [
        "# Trials reconcile",
        "",
        f"Anchor: **{anchor.isoformat()}** · Until: **{until.isoformat()}**",
        "",
        "## By day",
        "",
        "| Day | daily_csv | sb_updated_at (legacy) | sb_new_trial_start | am_reporting | am_logs |",
        "|-----|----------:|-----------------------:|---------------:|-------------:|--------:|",
    ]

    for d in _day_range(anchor, until):
        key = d.isoformat()
        lines.append(
            "| {day} | {csv} | {leg} | {new} | {amr} | {aml} |".format(
                day=key,
                csv=daily_csv.get(key, 0),
                leg=sb_legacy.get(key, 0),
                new=sb_start.get(key, 0),
                amr=am_reporting.get(key, 0) if isinstance(am_reporting, dict) else "—",
                aml=am_logs.get(key, 0),
            )
        )

    lines += [
        "",
        "## Totals",
        "",
        f"- daily_csv: **{sum(daily_csv.values())}**",
        f"- sb_updated_at (legacy): **{sum(sb_legacy.values())}**",
        f"- sb_new_trial_start (daily): **{sum(sb_start.values())}**",
        f"- sb_trial_start (cohort): **{len({s.user_id for s in trial_starts})}** distinct users",
        f"- am_reporting: **{sum(v for k, v in am_reporting.items() if not str(k).startswith('_'))}**"
        if isinstance(am_reporting, dict)
        else "- am_reporting: error",
        f"- am_logs (backups): **{sum(am_logs.values())}**",
        "",
        "## Cohorts (trials)",
        "",
        "| Cohort | daily_sum | sb_trial_start distinct | cohort_pnl (AM) |",
        "|--------|----------:|------------------------:|----------------:|",
    ]

    buckets = cohort_buckets(anchor, until)
    for b in buckets:
        daily_sum = sum(daily_csv.get(d.isoformat(), 0) for d in _day_range(b.start, b.end))
        sb_distinct = count_trial_starts_in_bucket(trial_starts, b.start, b.end) if trial_starts else 0
        pnl = cohort_pnl.get(b.id, "—")
        lines.append(f"| {b.label} | {daily_sum} | {sb_distinct} | {pnl} |")

    out_path = work_dir / "data" / "trials-reconcile.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Reconcile: {out_path}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile Planto trial sources")
    parser.add_argument("--work-dir", type=Path, default=Path.cwd())
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--until", type=str, default=None)
    args = parser.parse_args()
    until = date.fromisoformat(args.until) if args.until else None
    try:
        run_reconcile(args.work_dir, until=until, repo_root=args.repo_root)
        return 0
    except Exception as exc:
        print(f"Reconcile failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
