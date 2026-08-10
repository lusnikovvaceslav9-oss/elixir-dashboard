"""Orchestrate Planto buyer feed update."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from appmetrica import (
    fetch_event_by_day,
    fetch_installs_by_day,
    fetch_product_metrics,
    fetch_product_metrics_spec,
    fetch_window,
)
from cohort import analyze_cohort_from_daily, default_anchor
from daily import estimate_today_spend, load_daily_csv, merge_daily, write_daily_csv
from direct import fetch_direct_by_day
from payments import (
    bills_by_cohort_day as csv_bills_by_day,
    load_payments,
    paid_net_by_cohort_day as csv_paid_net_by_cohort_day,
    payments_breakdown,
    sold_trials_by_cohort_day as csv_sold_by_cohort_day,
    sold_trials_by_cohort_day as csv_sold_by_day,
)
from polza import fetch_polza_spend_by_day
from secrets import load_secrets
from supabase import (
    bills_breakdown,
    bills_by_cohort_day,
    fetch_bills,
    fetch_trial_cancellations_by_day,
    fetch_trial_starts,
    fetch_unit_economics_snapshot,
    paid_net_by_cohort_day,
    sold_by_cohort_day,
    trials_by_day_from_starts,
)


def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


DASHBOARD_API = "https://elixir-ua-bot.lusnikovvaceslav9.workers.dev/api/projects"


def fetch_admin_campaign_ids(project_id: str) -> list[str] | None:
    """ID кампаний Директа, заданные админом в дашборде для этого проекта.

    Источник правды — админка (поле directCampaignIds в projects[]), чтобы
    менять привязку кампаний к проектам без правки кода. Сеть недоступна /
    поля нет — возвращаем None, и решает конфиг (direct_campaign_ids).
    """
    import urllib.request

    try:
        req = urllib.request.Request(DASHBOARD_API, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  admin campaign ids: fetch failed ({exc}) — using config")
        return None
    for proj in data if isinstance(data, list) else []:
        if isinstance(proj, dict) and proj.get("id") == project_id:
            ids = proj.get("directCampaignIds")
            if isinstance(ids, list) and ids:
                return [str(c).strip() for c in ids if str(c).strip()]
            return None
    return None


def other_projects_campaign_ids(project_id: str, config_dir: Path) -> list[str]:
    """Кампании, заявленные ДРУГИМИ проектами (тот же Direct-логин).

    Проект без своего include-фильтра (напр. Planto) должен исключить их, иначе
    затянет чужой спенд. Источники: конфиги config/*.json и админка дашборда.
    """
    ids: set[str] = set()
    # Из соседних конфигов.
    try:
        for cfg_file in config_dir.glob("*.json"):
            try:
                other = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if other.get("id") == project_id:
                continue
            for c in other.get("direct_campaign_ids") or []:
                if str(c).strip():
                    ids.add(str(c).strip())
    except Exception:
        pass
    # Из админки (projects[]).
    import urllib.request

    try:
        req = urllib.request.Request(DASHBOARD_API, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for proj in data if isinstance(data, list) else []:
            if not isinstance(proj, dict) or proj.get("id") == project_id:
                continue
            for c in proj.get("directCampaignIds") or []:
                if str(c).strip():
                    ids.add(str(c).strip())
    except Exception:
        pass
    return sorted(ids)


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
    clicks: dict[str, int] = {}
    impressions: dict[str, int] = {}
    installs: dict[str, int] = {}
    trials: dict[str, int] = {}
    trials_am_crosscheck: dict[str, int] = {}
    trial_starts = []
    bills: dict[str, int] = {}
    sold: dict[str, int] = {}
    paid_by_cohort_day: dict[str, int] = {}
    sold_by_cohort_day_map: dict[str, int] = {}
    bills_by_plan: dict[str, dict[str, int]] | None = None
    trial_cancels_by_day: dict[str, int] = {}
    unit_snap: dict | None = None
    product_metrics: dict | None = None
    sources: dict[str, str] = {}
    errors: list[str] = []

    def _split_direct(raw: dict[str, dict]) -> tuple[dict[str, float], dict[str, int], dict[str, int]]:
        sp: dict[str, float] = {}
        cl: dict[str, int] = {}
        im: dict[str, int] = {}
        for day, vals in raw.items():
            sp[day] = float(vals.get("spend") or 0)
            cl[day] = int(vals.get("clicks") or 0)
            im[day] = int(vals.get("impressions") or 0)
        return sp, cl, im

    def _fetch_direct_range(date_since: date, date_until: date, *, chunk_days: int = 10) -> tuple[dict[str, dict], list[str]]:
        """Direct sometimes returns empty for wide ranges — fetch in chunks.

        A chunk that raises is skipped, not fatal for the whole range — otherwise
        one transient failure blanks out every previously-fetched chunk too.
        """
        out: dict[str, dict] = {}
        chunk_errors: list[str] = []
        d = date_since
        while d <= date_until:
            e = min(d + timedelta(days=chunk_days - 1), date_until)
            try:
                part = fetch_direct_by_day(
                    direct_token, client_login, d, e, direct_campaign_ids, direct_exclude_ids
                )
                out.update(part)
            except Exception as exc:
                chunk_errors.append(f"{d.isoformat()}..{e.isoformat()}: {exc}")
            d = e + timedelta(days=1)
        return out, chunk_errors

    direct_token = secrets.get("DIRECT_OAUTH_TOKEN")
    client_login = secrets.get("DIRECT_CLIENT_LOGIN") or cfg.get("direct_client_login") or ""
    # Несколько проектов под одним client_login → фильтр по кампаниям, иначе спенды
    # смешаются. Свой include: админка дашборда (directCampaignIds), затем конфиг.
    # Нет своего — исключаем кампании, заявленные другими проектами (напр. Planto
    # без своего фильтра исключает кампанию ColorStylist из конфига colorstylist.json).
    proj_id = cfg.get("id") or ""
    direct_campaign_ids = fetch_admin_campaign_ids(proj_id) or cfg.get("direct_campaign_ids") or None
    direct_exclude_ids: list[str] | None = None
    if direct_campaign_ids:
        print(f"  Direct campaign filter (only): {', '.join(direct_campaign_ids)}")
    else:
        direct_exclude_ids = other_projects_campaign_ids(proj_id, cfg_path.parent) or None
        if direct_exclude_ids:
            print(f"  Direct campaign filter (exclude): {', '.join(direct_exclude_ids)}")
    if direct_token:
        try:
            direct_win, chunk_errors = _fetch_direct_range(window_start, until)
            spend, clicks, impressions = _split_direct(direct_win)
            if direct_win:
                sources["spend"] = "direct_api"
                sources["clicks"] = "direct_api"
                sources["impressions"] = "direct_api"
            if chunk_errors:
                errors.append(f"direct: {len(chunk_errors)} chunk(s) failed: " + "; ".join(chunk_errors))
            print(
                f"  Direct: {len(spend)} days (spend+clicks+impressions)"
                + (f" · {len(chunk_errors)} chunk(s) failed" if chunk_errors else "")
            )
        except Exception as exc:
            errors.append(f"direct: {exc}")
            print(f"  Direct failed: {exc}")
    else:
        errors.append("direct: DIRECT_OAUTH_TOKEN missing")

    am_token = secrets.get("APPMETRICA_OAUTH_TOKEN")
    app_id = secrets.get("APPMETRICA_APPLICATION_ID") or cfg.get("appmetrica_application_id") or "6305902"
    # Событие начала триала различается по приложениям: Planto шлёт trial_started,
    # ColorStylist — purchase_success (подписка premium_week с 3-дневным триалом).
    trial_event = cfg.get("trial_event") or "trial_started"
    trials_sb_crosscheck: dict[str, int] = {}
    # Отдельный флаг успеха запроса — пустой словарь (0 трайлов за окно) не должен
    # выглядеть как сбой AppMetrica и триггерить fallback на Supabase.
    trials_am_fetch_ok = False
    if am_token:
        try:
            inst_win, _ = fetch_window(am_token, str(app_id), anchor, until, refresh_days, lag)
            installs.update(inst_win)
            sources["installs"] = "appmetrica_reporting"
            try:
                # Daily trials = AppMetrica event count (колонка «События» в UI).
                trials_am = fetch_event_by_day(
                    am_token, str(app_id), trial_event, anchor, until
                )
                trials_am_fetch_ok = True
                trials = trials_am
                trials_am_crosscheck = trials_am
                sources["trials"] = f"appmetrica_{trial_event}"
                sources["trials_am_crosscheck"] = "appmetrica_reporting_events"
            except Exception as exc:
                errors.append(f"appmetrica_trials: {exc}")
            print(
                f"  AppMetrica: {len(installs)} install-days · "
                f"{sum(trials.values())} trial events"
            )
        except Exception as exc:
            errors.append(f"appmetrica: {exc}")
            print(f"  AppMetrica failed: {exc}")
    else:
        errors.append("appmetrica: APPMETRICA_OAUTH_TOKEN missing")

    db_url = secrets.get("SUPABASE_DB_URL")
    if db_url:
        try:
            # RuStore — сверка и fallback, если AM недоступна.
            trial_starts = fetch_trial_starts(db_url, anchor, until)
            trials_sb_crosscheck = trials_by_day_from_starts(trial_starts)
            if not trials_am_fetch_ok and trials_sb_crosscheck:
                trials = trials_sb_crosscheck
                sources["trials"] = "supabase_trial_start"
            print(
                f"  Supabase trials (crosscheck): {sum(trials_sb_crosscheck.values())} · "
                f"{len(trials_sb_crosscheck)} days"
            )
            if trials and trials_sb_crosscheck:
                yday = (until - timedelta(days=1)).isoformat()
                am_y = int(trials.get(yday) or 0)
                sb_y = int(trials_sb_crosscheck.get(yday) or 0)
                if am_y or sb_y:
                    print(
                        f"  Trials {yday}: dashboard/AM={am_y} · "
                        f"RuStore={sb_y} · delta={am_y - sb_y:+d}"
                    )
        except Exception as exc:
            errors.append(f"supabase: {exc}")
            print(f"  Supabase failed: {exc}")
        try:
            # Daily fb/sold = по дню когорты (yearly: оплата − 7д = старт триала).
            bills_list = fetch_bills(db_url, anchor, until)
            bills = bills_by_cohort_day(bills_list)
            sold = sold_by_cohort_day(bills_list)
            paid_by_cohort_day = paid_net_by_cohort_day(bills_list)
            sold_by_cohort_day_map = sold_by_cohort_day(bills_list)
            bills_by_plan = bills_breakdown(bills_list)
            sources["bills"] = "supabase_main_active_cohort_day"
            print(
                f"  Supabase bills: {len(bills_list)} charges "
                f"(daily = cohort day, yearly −{7}d)"
            )
        except Exception as exc:
            errors.append(f"supabase_bills: {exc}")
            print(f"  Supabase bills failed: {exc}")
        try:
            trial_cancels_by_day = fetch_trial_cancellations_by_day(db_url, anchor, until)
            unit_snap = fetch_unit_economics_snapshot(db_url)
            sources["trial_cancellations"] = "supabase_trial_cancelled"
            sources["unit_economics"] = "supabase_entitlements"
            print(
                f"  Supabase cancels: {sum(trial_cancels_by_day.values())} · "
                f"active payers: {(unit_snap or {}).get('active_payers', 0)}"
            )
        except Exception as exc:
            errors.append(f"supabase_unit: {exc}")
            print(f"  Supabase unit/cancels failed: {exc}")
    else:
        errors.append("supabase: SUPABASE_DB_URL missing")

    # CSV — fallback (если Supabase недоступен) + ручные корректировки/возвраты.
    payments = load_payments(payments_path)
    if payments and sources.get("bills") not in (
        "supabase_main_active",
        "supabase_main_active_cohort_day",
    ):
        bills = csv_bills_by_day(payments)
        sold = csv_sold_by_day(payments)
        paid_by_cohort_day = csv_paid_net_by_cohort_day(payments)
        sold_by_cohort_day_map = csv_sold_by_cohort_day(payments)
        bills_by_plan = payments_breakdown(payments)
        sources["bills"] = "rustore_payments_csv_cohort_day"
        print(f"  Payments (CSV fallback, cohort day): {len(payments)} records")

    full_spend = spend
    full_clicks = clicks
    full_impressions = impressions
    full_installs = installs
    full_trials = trials

    if anchor < window_start and direct_token:
        try:
            direct_full, chunk_errors = _fetch_direct_range(anchor, until)
            if direct_full:
                full_spend, full_clicks, full_impressions = _split_direct(direct_full)
                # Window fetch is fresher for recent days.
                ws = window_start.isoformat()
                for day_key, value in spend.items():
                    if day_key >= ws:
                        full_spend[day_key] = value
                        full_clicks[day_key] = clicks.get(day_key, 0)
                        full_impressions[day_key] = impressions.get(day_key, 0)
                print(
                    f"  Direct full range: {len(full_spend)} days"
                    + (f" · {len(chunk_errors)} chunk(s) failed" if chunk_errors else "")
                )
            else:
                print("  Direct full range: no data, keeping window-only spend")
            if chunk_errors:
                errors.append(f"direct_full: {len(chunk_errors)} chunk(s) failed: " + "; ".join(chunk_errors))
        except Exception as exc:
            errors.append(f"direct_full: {exc}")
            full_spend = spend
            full_clicks = clicks
            full_impressions = impressions
            print(f"  Direct full range failed: {exc}")

    if anchor < window_start and am_token:
        try:
            full_installs = fetch_installs_by_day(am_token, str(app_id), anchor, until)
        except Exception:
            full_installs = installs
        if sources.get("trials") == f"appmetrica_{trial_event}":
            try:
                full_trials = fetch_event_by_day(
                    am_token, str(app_id), trial_event, anchor, until
                )
            except Exception:
                full_trials = trials

    if anchor < window_start and db_url and sources.get("trials") == "supabase_trial_start":
        try:
            trial_starts = fetch_trial_starts(db_url, anchor, until)
            full_trials = trials_by_day_from_starts(trial_starts)
        except Exception:
            full_trials = trials

    # Источник истины по триалам: дни без событий = 0 (не оставляем старый CSV).
    if sources.get("trials") in (f"appmetrica_{trial_event}", "supabase_trial_start"):
        filled = dict(full_trials)
        d = anchor
        while d <= until:
            filled.setdefault(d.isoformat(), 0)
            d += timedelta(days=1)
        full_trials = filled

    # Аналогично для биллов: дни без списаний (по когорте) = 0.
    if sources.get("bills") in ("supabase_main_active", "supabase_main_active_cohort_day"):
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

    # Polza (ИИ) — фактический burn по ключам. Необязательный источник: нет ключа
    # или упал запрос — фид продолжает работать, просто без polza_spend.
    polza_by_day: dict[str, float] = {}
    polza_summary: dict | None = None
    polza_key = secrets.get("POLZA_API_KEY")
    if polza_key:
        try:
            polza_summary = fetch_polza_spend_by_day(polza_key, anchor, until)
            polza_by_day = polza_summary.get("by_day") or {}
            sources["polza_spend"] = "polza_api"
            print(
                f"  Polza: {polza_summary.get('total')} ₽ "
                f"({polza_summary.get('generations')} ген.) · {polza_summary.get('by_kind')}"
            )
        except Exception as exc:
            errors.append(f"polza: {exc}")
            print(f"  Polza failed: {exc}")

    merged = merge_daily(
        existing,
        spend=full_spend,
        installs=full_installs,
        trials=full_trials,
        bills=bills,
        sold=sold,
        clicks=full_clicks,
        impressions=full_impressions,
        polza_spend=polza_by_day,
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
    # Когорты считают trials из daily (тот же источник, что в таблице по дням).
    cohort = analyze_cohort_from_daily(
        anchor=anchor,
        until=until,
        report_date=until,
        daily=daily_for_cohort,
        paid_by_cohort_day=paid_by_cohort_day,
        sold_by_cohort_day=sold_by_cohort_day_map,
        trial_starts=None,
    )
    cohort_path.parent.mkdir(parents=True, exist_ok=True)
    cohort_path.write_text(json.dumps(cohort, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Cohort JSON: {cohort_path}")

    installs_total = sum(int(v.get("installs") or 0) for v in merged.values())
    spend_total = sum(float(v.get("spend") or 0) for v in merged.values())
    revenue_total = int((bills_by_plan or {}).get("total", {}).get("rub") or cohort["totals"].get("paid_net") or 0)
    paid_count = int((bills_by_plan or {}).get("total", {}).get("count") or cohort["totals"].get("sold") or 0)

    if am_token:
        try:
            product_spec = cfg.get("product_section")
            if product_spec:
                # Проект описал свои события/KPI (ColorStylist) — считаем по спецификации.
                product_metrics = fetch_product_metrics_spec(
                    am_token, str(app_id), anchor, until, installs_total, product_spec
                )
                shown = " · ".join(
                    f"{k['label']} {k['value_pct']}%"
                    for k in product_metrics.get("kpis") or [] if k.get("value_pct") is not None
                )
                print(f"  Product: {shown or 'нет посчитанных KPI'}")
            else:
                product_metrics = fetch_product_metrics(
                    am_token, str(app_id), anchor, until, installs_total
                )
                print(
                    f"  Product: garden {product_metrics.get('garden_activation_pct')}% · "
                    f"paywall CTA {product_metrics.get('paywall_cta_pct')}%"
                )
            sources["product"] = "appmetrica_reporting"
        except Exception as exc:
            errors.append(f"product: {exc}")
            print(f"  Product metrics failed: {exc}")

    # Unit economics proxies from current revenue + active base.
    active_users = int((product_metrics or {}).get("active_users") or 0)
    active_payers = int((unit_snap or {}).get("active_payers") or paid_count or 0)
    arpu = round(revenue_total / active_users, 2) if active_users and revenue_total else None
    arppu = round(revenue_total / active_payers, 2) if active_payers and revenue_total else None
    # Simple LTV proxy: ARPPU × expected renewals. Yearly dominates; use 1.3x as early estimate
    # until we have full rebill curves (renewals already observed boost it).
    renewals = int((unit_snap or {}).get("renewals") or 0)
    ltv = None
    if arppu is not None:
        renew_factor = 1.0 + (renewals / active_payers) if active_payers else 1.0
        ltv = round(arppu * max(1.0, min(renew_factor, 3.0)), 2)

    unit_economics = {
        "revenue_total": revenue_total,
        "spend_total": round(spend_total, 2),
        "active_users": active_users,
        "active_payers": active_payers,
        "plus_active_users": int((unit_snap or {}).get("plus_active_users") or 0),
        "trial_cancellations": int((unit_snap or {}).get("trial_cancellations") or sum(trial_cancels_by_day.values())),
        "trial_cancellations_by_day": trial_cancels_by_day,
        "renewals": renewals,
        "arpu": arpu,
        "arppu": arppu,
        "ltv": ltv,
        "ltv_note": "прокси: ARPPU × (1 + renewals/payers), пока нет полной кривой ребиллов",
        "roas_d7": cohort["totals"].get("roas_d7"),
        "roas_d14": cohort["totals"].get("roas_d14"),
        "roas_d30": cohort["totals"].get("roas_d30"),
    }

    product_path = work_dir / cfg.get("product_json", "data/planto-product.json")
    product_payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "anchor": anchor.isoformat(),
        "until": until.isoformat(),
        "product": product_metrics,
        "unit_economics": unit_economics,
    }
    product_path.parent.mkdir(parents=True, exist_ok=True)
    product_path.write_text(json.dumps(product_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Product JSON: {product_path}")

    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "anchor": anchor.isoformat(),
        "until": until.isoformat(),
        "window_start": window_start.isoformat(),
        "trial_attribution": sources.get("trials") or f"appmetrica_{trial_event}",
        # Копирайт дашборда: длительность триала и подпись колонки продаж различаются
        # по проектам (Planto — 7 дней/yearly, ColorStylist — 3 дня/week-month).
        "ai_spend": {
            "provider": "polza",
            "total": (polza_summary or {}).get("total"),
            "generations": (polza_summary or {}).get("generations"),
            "by_kind": (polza_summary or {}).get("by_kind"),
            "per_install": round((polza_summary or {}).get("total", 0) / installs_total, 2)
                if polza_summary and installs_total else None,
            "per_trial": round((polza_summary or {}).get("total", 0) / new_trials_total, 2)
                if polza_summary and new_trials_total else None,
            "total_burn": round(spend_total + ((polza_summary or {}).get("total") or 0), 2)
                if polza_summary else None,
        } if polza_summary else None,
        "trial_days": int(cfg.get("trial_days") or cfg.get("trial_lag_days") or 7),
        "trial_lag_days": int(cfg.get("trial_lag_days") or 7),
        "sold_label": cfg.get("sold_label"),
        "plans": cfg.get("plans"),
        "sources": sources,
        "errors": errors,
        "days": len(merged),
        "reconcile_diff": {
            "trials_total_old_csv": old_trials_total,
            "trials_total_new": new_trials_total,
            "delta": new_trials_total - old_trials_total,
        },
        "unit_economics": unit_economics,
        "roas_config": {
            "revenue_source": cfg.get("revenue_source", "actual"),
            "ltv_metric": cfg.get("ltv_metric"),
            "fixed_ltv_value": cfg.get("fixed_ltv_value"),
            "assumed_avg_check": cfg.get("assumed_avg_check"),
            "funnel": cfg.get("funnel"),
            "mature_cohort_days": cfg.get("mature_cohort_days"),
            "predict_horizon_days": cfg.get("predict_horizon_days"),
        },
        "product": {
            "garden_activation_pct": (product_metrics or {}).get("garden_activation_pct"),
            "garden_avg_plants": (product_metrics or {}).get("garden_avg_plants"),
            "garden_depth_icp_pct": (product_metrics or {}).get("garden_depth_icp_pct"),
            "paywall_cta_pct": (product_metrics or {}).get("paywall_cta_pct"),
            "care_engagement_pct": (product_metrics or {}).get("care_engagement_pct"),
            "feature_activation": (product_metrics or {}).get("feature_activation"),
            "retention": (product_metrics or {}).get("retention"),
            # Проектная спецификация (ColorStylist): KPI/фичи с собственными лейблами.
            "subtitle": (product_metrics or {}).get("subtitle"),
            "count_mode": (product_metrics or {}).get("count_mode"),
            "kpis": (product_metrics or {}).get("kpis"),
            "features": (product_metrics or {}).get("features"),
            "quality": (product_metrics or {}).get("quality"),
        } if product_metrics else None,
    }
    if bills_by_plan:
        meta["payments_by_plan"] = bills_by_plan
    if trials_sb_crosscheck:
        meta["trials_sb_crosscheck_total"] = sum(int(v) for v in trials_sb_crosscheck.values())
        meta["trials_sb_crosscheck_by_day"] = {
            k: int(v) for k, v in sorted(trials_sb_crosscheck.items())
        }
    if trials_am_crosscheck:
        meta["trials_am_crosscheck_total"] = sum(int(v) for v in trials_am_crosscheck.values())
        meta["trials_am_crosscheck_by_day"] = {
            k: int(v) for k, v in sorted(trials_am_crosscheck.items())
        }
    yday = (until - timedelta(days=1)).isoformat()
    meta["trials_crosscheck_yesterday"] = {
        "day": yday,
        "appmetrica": int((trials_am_crosscheck or full_trials).get(yday) or 0),
        "supabase": int(trials_sb_crosscheck.get(yday) or 0),
        "dashboard": int((merged.get(yday) or {}).get("trials") or 0),
        "note": f"В дашборде trials = AppMetrica {trial_event} (события ym:ce:allEvents, как колонка «События» в UI). RuStore — сверка оплат.",
    }
    if spend_today_estimated:
        meta["spend_today_estimated"] = True
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return 0 if merged else 1
