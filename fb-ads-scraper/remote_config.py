"""Загрузка конфига FB scraper из Elixir dashboard (JSONBin)."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

SHEET_ID_RE = re.compile(r"spreadsheets/d/([a-zA-Z0-9_-]+)")

logger = logging.getLogger(__name__)


def extract_sheet_id_from_urls(urls: list[str]) -> str:
    for url in urls or []:
        match = SHEET_ID_RE.search(url or "")
        if match:
            return match.group(1)
    return ""


def first_sheet_label(project: dict) -> str:
    sources = project.get("sheetSources") or []
    for src in sources:
        label = (src.get("label") or "").strip()
        if label:
            return label
    return ""


def build_ads_manager_url(project: dict | None, config: dict) -> str:
    base = config.get(
        "fb_ads_manager_url",
        "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
    )
    if not project:
        return base

    custom = (project.get("fb_ads_manager_url") or "").strip()
    if custom:
        return custom

    act = (project.get("ad_account_id") or project.get("act") or "").strip()
    bm = (project.get("bm_id") or project.get("business_manager_id") or "").strip()
    if not act and not bm:
        return base

    parsed = urlparse(base)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if act:
        query["act"] = [act.replace("act_", "")]
    if bm:
        query["business_id"] = [bm]
    flat = {k: v[0] for k, v in query.items() if v}
    return urlunparse(parsed._replace(query=urlencode(flat)))


def fetch_dashboard_projects(config: dict) -> list[dict]:
    bin_id = (config.get("jsonbin_bin_id") or os.environ.get("JSONBIN_BIN_ID") or "").strip()
    master_key = (
        config.get("jsonbin_master_key") or os.environ.get("JSONBIN_MASTER_KEY") or ""
    ).strip()
    if not bin_id or not master_key:
        raise RuntimeError(
            "JSONBin не настроен: укажите jsonbin_bin_id и jsonbin_master_key в config.json "
            "или переменные JSONBIN_BIN_ID / JSONBIN_MASTER_KEY"
        )

    api_base = (config.get("jsonbin_api_base") or "https://api.jsonbin.io/v3").rstrip("/")
    response = requests.get(
        f"{api_base}/b/{bin_id}/latest",
        headers={"X-Master-Key": master_key},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    record = payload.get("record")
    if isinstance(record, list):
        return record
    if isinstance(record, dict) and isinstance(record.get("projects"), list):
        return record["projects"]
    raise RuntimeError("JSONBin: неожиданный формат record")


def dashboard_to_scraper_projects(dashboard_projects: list[dict]) -> list[dict]:
    out = []
    for project in dashboard_projects:
        fb = project.get("fbScraper") or {}
        if not fb.get("enabled"):
            continue

        profile_id = (fb.get("profileId") or "").strip()
        if not profile_id:
            logger.warning(
                "Проект %s: fbScraper включён, но не задан profileId — пропуск",
                project.get("name") or project.get("id"),
            )
            continue

        sheet_id = extract_sheet_id_from_urls(project.get("urls") or [])
        if not sheet_id:
            logger.warning(
                "Проект %s: нет sheet_id в urls — пропуск",
                project.get("name") or project.get("id"),
            )
            continue

        out.append(
            {
                "enabled": True,
                "dashboard_id": project.get("id", ""),
                "name": project.get("name", ""),
                "icon": project.get("icon", ""),
                "currency": project.get("currency", "$"),
                "profile_id": profile_id,
                "bm_id": (fb.get("bmId") or "").strip(),
                "ad_account_id": (fb.get("adAccountId") or "").strip(),
                "sheet_id": sheet_id,
                "dashboard_sheet": (
                    (fb.get("dashboardSheet") or "").strip()
                    or first_sheet_label(project)
                    or "Лист 1"
                ),
                "detail_sheet": (fb.get("detailSheet") or "FB Кампании").strip(),
                "export_mode": (fb.get("exportMode") or "campaign").strip(),
            }
        )
    return out


def load_local_projects(projects_file: Path) -> list[dict]:
    if not projects_file.exists():
        raise FileNotFoundError(f"Файл проектов не найден: {projects_file}")

    with projects_file.open(encoding="utf-8") as f:
        payload = json.load(f)

    projects = []
    for item in payload.get("projects", []):
        if not item.get("enabled"):
            continue
        profile_id = (item.get("profile_id") or "").strip()
        sheet_id = (item.get("sheet_id") or "").strip()
        dashboard_id = (item.get("dashboard_id") or "").strip()
        if not profile_id:
            raise RuntimeError(
                f"Проект {item.get('name') or dashboard_id}: не задан profile_id в projects.json"
            )
        if not sheet_id:
            raise RuntimeError(
                f"Проект {item.get('name') or dashboard_id}: не задан sheet_id в projects.json"
            )
        projects.append(item)

    if not projects:
        raise RuntimeError("Нет enabled-проектов в projects.json")
    return projects


def load_projects(config: dict, script_dir: Path) -> tuple[list[dict], str]:
    """Возвращает (projects, source_name). source: jsonbin | local."""
    projects_file = script_dir / config.get("projects_file", "projects.json")
    source = (config.get("projects_source") or "auto").strip().lower()

    if source in ("auto", "jsonbin", "remote"):
        try:
            dashboard_projects = fetch_dashboard_projects(config)
            remote_projects = dashboard_to_scraper_projects(dashboard_projects)
            if remote_projects:
                logger.info("Конфиг загружен с JSONBin: %s проектов", len(remote_projects))
                return remote_projects, "jsonbin"
            if source in ("jsonbin", "remote"):
                raise RuntimeError("JSONBin: нет проектов с включённым fbScraper")
        except Exception as exc:
            if source in ("jsonbin", "remote"):
                raise
            logger.warning("JSONBin недоступен, fallback на projects.json: %s", exc)

    projects = load_local_projects(projects_file)
    logger.info("Конфиг загружен из %s: %s проектов", projects_file.name, len(projects))
    return projects, "local"
