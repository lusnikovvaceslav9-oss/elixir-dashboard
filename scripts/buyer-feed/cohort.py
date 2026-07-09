"""Cohort P&L from daily aggregates — self-contained (no Logs API)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_ANCHOR = "2026-06-05"
TRIAL_LAG_DAYS = 7
ROAS_WINDOWS = (7, 14, 30)

MONTH_RU = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X", 11: "XI", 12: "XII"}


@dataclass
class CohortBucket:
    week: int
    start: date
    end: date
    month_key: str = ""

    def __post_init__(self) -> None:
        if not self.month_key:
            self.month_key = f"{self.start.year:04d}-{self.start.month:02d}"

    @property
    def id(self) -> str:
        return f"{self.month_key}-W{self.week}"

    @property
    def label(self) -> str:
        mo = MONTH_RU.get(self.start.month, str(self.start.month))
        if self.start.month == self.end.month:
            day_part = f"{self.start.day}–{self.end.day}"
        else:
            day_part = f"{self.start.day}.{self.start.month:02d}–{self.end.day}.{self.end.month:02d}"
        return f"{mo} · W{self.week} {day_part}"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def parse_day(s: str) -> date | None:
    from datetime import datetime

    s = (s or "").strip()[:10]
    if len(s) != 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def cohort_buckets(anchor: date, until: date) -> list[CohortBucket]:
    buckets: list[CohortBucket] = []
    week = 1
    y, m = anchor.year, anchor.month

    def add(start: date, end: date, month_key: str) -> None:
        nonlocal week
        if start > until:
            return
        s = max(start, anchor)
        e = min(end, until)
        if s > e:
            return
        buckets.append(CohortBucket(week=week, start=s, end=e, month_key=month_key))
        week += 1

    def month_slices(year: int, month: int, *, from_day: int = 1) -> None:
        nonlocal week
        week = 1
        last_dom = _last_day_of_month(year, month)
        for lo, hi in ((1, 7), (8, 14), (15, 21), (22, 28), (29, last_dom)):
            if hi < from_day:
                continue
            start_day = max(lo, from_day)
            mk = f"{year:04d}-{month:02d}"
            add(date(year, month, start_day), date(year, month, hi), mk)

    month_slices(y, m, from_day=anchor.day)
    cy, cm = y, m
    while True:
        cy, cm = _next_month(cy, cm)
        if date(cy, cm, 1) > until:
            break
        month_slices(cy, cm, from_day=1)
    return buckets


def bucket_for_day(d: date, buckets: list[CohortBucket]) -> CohortBucket | None:
    for b in buckets:
        if b.contains(d):
            return b
    return None


def fmt_rub(n: float | int) -> str:
    return f"{round(n):,}".replace(",", " ")


def fmt_checkpoint(d: date) -> str:
    nxt = d + timedelta(days=1)
    if d.month == nxt.month:
        return f"{d.day}–{nxt.day} {MONTH_RU[d.month]}"
    return f"{d.day} {MONTH_RU[d.month]}–{nxt.day} {MONTH_RU[nxt.month]}"


def count_trial_starts_in_bucket(trial_starts: list, start: date, end: date) -> int:
    """Distinct user_id with trial_start in [start, end]."""
    best: dict[str, date] = {}
    for row in trial_starts:
        uid = getattr(row, "user_id", None) or row.get("user_id")
        ts = getattr(row, "trial_start", None) or row.get("trial_start")
        if not uid or not ts:
            continue
        if isinstance(ts, str):
            ts = parse_day(ts)
        if ts is None:
            continue
        prev = best.get(uid)
        if prev is None or ts < prev:
            best[uid] = ts
    return sum(1 for ts in best.values() if start <= ts <= end)


def analyze_cohort_from_daily(
    *,
    anchor: date,
    until: date,
    report_date: date | None,
    daily: dict[str, dict],
    paid_by_cohort_day: dict[str, int],
    sold_by_cohort_day: dict[str, int] | None = None,
    trial_starts: list | None = None,
) -> dict:
    report_date = report_date or until
    sold_by_cohort_day = sold_by_cohort_day or {}
    buckets = cohort_buckets(anchor, until)

    rows: list[dict] = []
    total_spend = 0.0
    total_paid = 0
    total_inst = 0
    total_trials = 0
    total_sold = 0
    mature_pnl = 0.0
    immature_count = 0

    for b in buckets:
        spend = 0.0
        inst_n = 0
        d = b.start
        while d <= b.end:
            key = d.isoformat()
            row = daily.get(key) or {}
            spend += float(row.get("spend") or 0)
            inst_n += int(row.get("installs") or 0)
            d += timedelta(days=1)

        if trial_starts is not None:
            trial_n = count_trial_starts_in_bucket(trial_starts, b.start, b.end)
        else:
            trial_n = 0
            d = b.start
            while d <= b.end:
                key = d.isoformat()
                row = daily.get(key) or {}
                trial_n += int(row.get("trials") or 0)
                d += timedelta(days=1)

        paid = 0
        sold = 0
        d = b.start
        while d <= b.end:
            paid += int(paid_by_cohort_day.get(d.isoformat(), 0))
            sold += int(sold_by_cohort_day.get(d.isoformat(), 0))
            d += timedelta(days=1)

        cpi = spend / inst_n if inst_n else None
        cpt = spend / trial_n if trial_n else None
        install_to_trial_cr = (trial_n / inst_n * 100) if inst_n else None
        trial_to_paid_cr = (sold / trial_n * 100) if trial_n else None
        cac = spend / sold if sold else None
        roas_raw = (paid / spend * 100) if spend else None

        # Same paid_net attribution; maturity gates differ by window.
        # Until we have day-level revenue curves, ROAS Dn uses cohort paid_net
        # once report_date >= end + n days.
        roas_by_window: dict[str, float | None] = {}
        for n in ROAS_WINDOWS:
            mature_n = report_date >= (b.end + timedelta(days=n))
            key = f"roas_d{n}"
            if mature_n and roas_raw is not None:
                roas_by_window[key] = round(roas_raw, 1)
            else:
                roas_by_window[key] = None

        checkpoint = b.end + timedelta(days=TRIAL_LAG_DAYS)
        mature = report_date >= checkpoint

        if mature:
            pnl_val = paid - spend
            pnl_display = fmt_rub(pnl_val)
            when = "зрелая"
            mature_pnl += pnl_val
            trial_to_paid_display = round(trial_to_paid_cr, 1) if trial_to_paid_cr is not None else None
        else:
            pnl_val = None
            checkpoint_label = fmt_checkpoint(checkpoint)
            pnl_display = f"Рано · {checkpoint_label}"
            when = checkpoint_label
            immature_count += 1
            trial_to_paid_display = None

        rows.append(
            {
                "cohort": b.label,
                "cohort_id": b.id,
                "month_key": b.month_key,
                "start": b.start.isoformat(),
                "end": b.end.isoformat(),
                "days": b.days,
                "spend": round(spend),
                "installs_am": inst_n,
                "trials_sb": trial_n,
                "trials_am": trial_n,
                "cpi": round(cpi) if cpi is not None else None,
                "cpt": round(cpt) if cpt is not None else None,
                "install_to_trial_cr": round(install_to_trial_cr, 2) if install_to_trial_cr is not None else None,
                "trial_to_paid_cr": trial_to_paid_display,
                "trial_to_paid_cr_raw": round(trial_to_paid_cr, 2) if trial_to_paid_cr is not None else None,
                "cac": round(cac) if cac is not None else None,
                "roas_d7": roas_by_window["roas_d7"],
                "roas_d7_raw": round(roas_raw, 1) if roas_raw is not None else None,
                "roas_d14": roas_by_window["roas_d14"],
                "roas_d30": roas_by_window["roas_d30"],
                "sold": sold,
                "paid_net": paid,
                "pnl": pnl_val,
                "pnl_display": pnl_display,
                "mature": mature,
                "checkpoint": checkpoint.isoformat(),
                "when": when,
            }
        )
        total_spend += spend
        total_paid += paid
        total_inst += inst_n
        total_trials += trial_n
        total_sold += sold

    totals_install_to_trial = (total_trials / total_inst * 100) if total_inst else None
    totals_trial_to_paid = (total_sold / total_trials * 100) if total_trials else None
    totals_cac = total_spend / total_sold if total_sold else None
    totals_roas = (total_paid / total_spend * 100) if total_spend else None

    # Totals ROAS Dn: only buckets mature for that window.
    totals_roas_windows: dict[str, float | None] = {}
    for n in ROAS_WINDOWS:
        key = f"roas_d{n}"
        spend_m = 0.0
        paid_m = 0
        for r in rows:
            end = parse_day(r["end"])
            if end is None:
                continue
            if report_date >= end + timedelta(days=n):
                spend_m += float(r["spend"] or 0)
                paid_m += int(r["paid_net"] or 0)
        # Require meaningful spend; 0% with tiny early cohort is misleading.
        if spend_m >= 5000:
            totals_roas_windows[key] = round(paid_m / spend_m * 100, 1)
        else:
            totals_roas_windows[key] = None

    return {
        "anchor": anchor.isoformat(),
        "until": until.isoformat(),
        "report_date": report_date.isoformat(),
        "trial_lag_days": TRIAL_LAG_DAYS,
        "rows": rows,
        "totals": {
            "spend": round(total_spend),
            "installs_am": total_inst,
            "trials_sb": total_trials,
            "trials_am": total_trials,
            "sold": total_sold,
            "paid_net": total_paid,
            "pnl_mature_only": round(mature_pnl),
            "immature_buckets": immature_count,
            "install_to_trial_cr": round(totals_install_to_trial, 2) if totals_install_to_trial is not None else None,
            "trial_to_paid_cr": round(totals_trial_to_paid, 2) if totals_trial_to_paid is not None else None,
            "cac": round(totals_cac) if totals_cac is not None else None,
            "roas_d7": totals_roas_windows.get("roas_d7") if totals_roas_windows.get("roas_d7") is not None else (round(totals_roas, 1) if totals_roas is not None else None),
            "roas_d14": totals_roas_windows.get("roas_d14"),
            "roas_d30": totals_roas_windows.get("roas_d30"),
        },
    }


def default_anchor() -> date:
    return parse_day(DEFAULT_ANCHOR) or date(2026, 6, 5)
