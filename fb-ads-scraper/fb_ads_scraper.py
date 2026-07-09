#!/usr/bin/env python3
"""
FB Ads Manager Scraper via AdsPower → Google Sheets → Elixir Dashboard
========================================================================
Каждый проект из projects.json:
  AdsPower profile → парсинг FB Ads Manager → лист дашборда + лист детализации
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import gspread
import requests
from dashboard_export import (
    build_dashboard_rows,
    build_detail_rows,
    normalized_campaign_rows,
)
from remote_config import build_ads_manager_url, load_projects as load_scraper_projects
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
PROJECTS_PATH = SCRIPT_DIR / "projects.json"
STATUS_PATH = SCRIPT_DIR / "status.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SERVICE_ACCOUNT_PATH = SCRIPT_DIR / "service_account.json"
OAUTH_CREDENTIALS_PATH = SCRIPT_DIR / "credentials.json"
TOKEN_PATH = SCRIPT_DIR / "token.pickle"

COLUMN_MAP = {
    "campaign name": "Название кампании",
    "название кампании": "Название кампании",
    "campaign": "Название кампании",
    "кампания": "Название кампании",
    "delivery": "Статус",
    "статус": "Статус",
    "status": "Статус",
    "доставка": "Статус",
    "results": "Результаты",
    "результаты": "Результаты",
    "reach": "Охват",
    "охват": "Охват",
    "impressions": "Показы",
    "показы": "Показы",
    "amount spent": "Расходы",
    "потрачено": "Расходы",
    "расходы": "Расходы",
    "spend": "Расходы",
    "сумма расходов": "Расходы",
    "cpc (cost per link click)": "CPC",
    "cpc (all)": "CPC",
    "cpc": "CPC",
    "цена за клик": "CPC",
    "cpm (cost per 1,000 impressions)": "CPM",
    "cpm": "CPM",
    "цена за 1000 показов": "CPM",
    "ctr (link click-through rate)": "CTR",
    "ctr (all)": "CTR",
    "ctr": "CTR",
    "кликабельность": "CTR",
    "link clicks": "Клики",
    "clicks (all)": "Клики",
    "клики": "Клики",
    "clicks": "Клики",
    "cost per result": "Цена за результат",
    "цена за результат": "Цена за результат",
}

SKIP_ROW_RE = re.compile(
    r"^(итого|total|all campaigns|все кампании|результаты|results|—|--)$",
    re.I,
)


def setup_logging(log_file: str) -> logging.Logger:
    log_path = SCRIPT_DIR / log_file
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    return logging.getLogger(__name__)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Конфиг не найден: {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = json.load(f)

    api_key = os.getenv("FB_ADSPOWER_API_KEY") or config.get("adspower_api_key")
    if not api_key:
        raise RuntimeError(
            "Не задан AdsPower API key. Укажите adspower_api_key в config.json "
            "или переменную FB_ADSPOWER_API_KEY."
        )
    config["adspower_api_key"] = api_key
    return config


def get_gspread_client(logger: logging.Logger) -> gspread.Client:
    if SERVICE_ACCOUNT_PATH.exists():
        logger.info("Google Sheets: service_account.json")
        creds = ServiceAccountCredentials.from_service_account_file(
            str(SERVICE_ACCOUNT_PATH), scopes=SCOPES
        )
        return gspread.authorize(creds)

    if OAUTH_CREDENTIALS_PATH.exists():
        logger.info("Google Sheets: OAuth credentials.json")
        creds = None
        if TOKEN_PATH.exists():
            with TOKEN_PATH.open("rb") as f:
                creds = pickle.load(f)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(OAUTH_CREDENTIALS_PATH), SCOPES
                )
                creds = flow.run_local_server(port=0)
            with TOKEN_PATH.open("wb") as f:
                pickle.dump(creds, f)
        return gspread.authorize(creds)

    raise FileNotFoundError(
        "Нет авторизации Google Sheets. Положите credentials.json или service_account.json"
    )


class GoogleSheetsClient:
    def __init__(self, gc: gspread.Client, sheet_id: str, sheet_name: str, logger: logging.Logger):
        self.gc = gc
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        self.logger = logger
        self._worksheet = None

    @property
    def worksheet(self) -> gspread.Worksheet:
        if self._worksheet is None:
            spreadsheet = self.gc.open_by_key(self.sheet_id)
            try:
                self._worksheet = spreadsheet.worksheet(self.sheet_name)
            except gspread.WorksheetNotFound:
                self._worksheet = spreadsheet.add_worksheet(self.sheet_name, rows=2000, cols=26)
        return self._worksheet

    def get_all_values(self) -> list[list]:
        return self.worksheet.get_all_values()

    def set_headers(self, headers: list[str]):
        end_col = chr(ord("A") + len(headers) - 1)
        self.worksheet.update(f"A1:{end_col}1", [headers])

    def append_rows(self, rows: list[list]):
        if rows:
            self.worksheet.append_rows(rows, value_input_option="USER_ENTERED")

    def update_row(self, row_number: int, values: list):
        end_col = chr(ord("A") + len(values) - 1)
        self.worksheet.update(f"A{row_number}:{end_col}{row_number}", [values])

    def delete_rows(self, row_numbers: list[int]):
        for row_num in sorted(row_numbers, reverse=True):
            self.worksheet.delete_rows(row_num)

    def write_upsert(
        self,
        headers: list[str],
        data_rows: list[list],
        *,
        scope_date: str,
        match_cols: list[int],
        label: str,
    ):
        """Обновляет строки за scope_date (колонка 0), ключ = match_cols."""
        existing = self.get_all_values()
        if not existing:
            self.set_headers(headers)
            self.append_rows(data_rows)
            self.logger.info(f"{label}: первая запись ({len(data_rows)} строк)")
            return

        if existing[0] != headers:
            self.set_headers(headers)

        def row_key(row: list) -> tuple:
            return tuple((row[i] if i < len(row) else "").strip() for i in match_cols)

        existing_map: dict[tuple, int] = {}
        stale_rows: list[int] = []
        for i, row in enumerate(existing[1:], start=2):
            if not row or (row[0] or "").strip() != scope_date:
                continue
            key = row_key(row)
            if any(key):
                existing_map[key] = i

        seen: set[tuple] = set()
        to_append: list[list] = []

        for data_row in data_rows:
            key = row_key(data_row)
            if not any(key):
                to_append.append(data_row)
                continue
            seen.add(key)
            row_num = existing_map.get(key)
            if row_num:
                self.update_row(row_num, data_row)
            else:
                to_append.append(data_row)

        for key, row_num in existing_map.items():
            if key not in seen:
                stale_rows.append(row_num)

        if stale_rows:
            self.delete_rows(stale_rows)

        if to_append:
            self.append_rows(to_append)

        self.logger.info(
            f"{label}: дата {scope_date}, кампаний {len(data_rows)}, "
            f"добавлено {len(to_append)}, удалено {len(stale_rows)}"
        )


class AdsPowerClient:
    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def open_browser(self, profile_id: str) -> dict:
        url = f"{self.api_base}/api/v1/browser/start"
        params = {"user_id": profile_id, "open_tabs": "1", "ip_tab": "0"}
        resp = requests.get(url, params=params, headers=self.headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data["code"] != 0:
            raise RuntimeError(f"AdsPower: {data['msg']}")
        return data["data"]

    def close_browser(self, profile_id: str):
        url = f"{self.api_base}/api/v1/browser/stop"
        try:
            requests.get(url, params={"user_id": profile_id}, headers=self.headers, timeout=15)
        except Exception as e:
            logging.warning(f"Не удалось закрыть браузер {profile_id}: {e}")


def connect_selenium(browser_data: dict, timeout: int = 30) -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", browser_data["ws"]["selenium"])
    service = Service(executable_path=browser_data["webdriver"])
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(timeout)
    return driver


def scrape_fb_ads(
    driver: webdriver.Chrome,
    logger: logging.Logger,
    config: dict,
    project: dict | None = None,
) -> list[dict]:
    timeout = int(config.get("wait_timeout", 30))
    target_url = build_ads_manager_url(project, config)

    logger.info(f"Открываю Facebook Ads Manager: {target_url}")
    try:
        driver.get(target_url)
    except Exception:
        pass

    time.sleep(6)
    current_url = driver.current_url
    logger.info(f"URL: {current_url}")

    if any(x in current_url for x in ["login", "checkpoint", "recover"]):
        raise RuntimeError(
            "Профиль не авторизован в Facebook. "
            "Войдите в FB вручную в этом профиле AdsPower."
        )

    time.sleep(8)

    for attempt in range(6):
        for parser in (_parse_js, _parse_selenium):
            try:
                rows = parser(driver, logger, timeout)
                rows = _normalize_raw_rows(rows)
                rows = _filter_campaign_rows(rows)
                if rows:
                    return rows
            except Exception as e:
                logger.warning(f"{parser.__name__} (попытка {attempt + 1}): {e}")
        if attempt < 5:
            logger.info(f"Строк пока нет, жду 5с… ({attempt + 1}/6)")
            time.sleep(5)

    shot = SCRIPT_DIR / f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    try:
        driver.save_screenshot(str(shot))
        logger.info(f"Скриншот: {shot}")
    except Exception:
        pass

    return []


def _normalize_raw_rows(raw_rows: list[dict]) -> list[dict]:
    normalized = []
    for raw in raw_rows:
        norm = {}
        for key, value in raw.items():
            mapped = COLUMN_MAP.get(str(key).lower().strip())
            norm[mapped or key] = value
        normalized.append(norm)
    return normalized


def _filter_campaign_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        name = (row.get("Название кампании") or "").strip()
        if not name or SKIP_ROW_RE.match(name):
            continue
        out.append(row)
    return out


def _parse_js(driver: webdriver.Chrome, logger: logging.Logger, timeout: int) -> list[dict]:
    data = driver.execute_script(
        """
        var res = {headers: [], rows: []};
        document.querySelectorAll('div[role="columnheader"]').forEach(function(h) {
            var t = h.innerText.trim();
            if (t) res.headers.push(t);
        });
        document.querySelectorAll('div[role="row"]').forEach(function(row, idx) {
            if (idx === 0) return;
            var cells = row.querySelectorAll('div[role="gridcell"], div[role="cell"]');
            if (!cells.length) return;
            var r = [];
            cells.forEach(function(c) { r.push(c.innerText.trim().replace(/\\n/g,' ')); });
            if (r.some(function(x){ return x !== ''; })) res.rows.push(r);
        });
        return res;
        """
    )
    headers = [h for h in data.get("headers", []) if h]
    rows = data.get("rows", [])
    logger.info(f"JS: заголовков={len(headers)}, строк={len(rows)}")
    result = []
    for row in rows:
        d = {}
        for i, v in enumerate(row):
            if v:
                d[headers[i] if i < len(headers) else f"col_{i}"] = v
        if d:
            result.append(d)
    return result


def _parse_selenium(driver: webdriver.Chrome, logger: logging.Logger, timeout: int) -> list[dict]:
    for sel in ["div[role='grid']", "div[role='table']", "table"]:
        try:
            WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            break
        except TimeoutException:
            continue

    headers = []
    for sel in ["div[role='columnheader']", "th"]:
        cells = driver.find_elements(By.CSS_SELECTOR, sel)
        if cells:
            headers = [c.text.strip() for c in cells if c.text.strip()]
            break

    result = []
    for row in driver.find_elements(By.CSS_SELECTOR, "div[role='row']")[1:]:
        cells = row.find_elements(By.CSS_SELECTOR, "div[role='gridcell'], div[role='cell']")
        if not cells:
            continue
        d = {}
        for i, c in enumerate(cells):
            t = c.text.strip()
            if t:
                d[headers[i] if i < len(headers) else f"col_{i}"] = t
        if d:
            result.append(d)
    logger.info(f"Selenium: строк={len(result)}")
    return result


def write_status(project_results: list[dict]):
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "projects": project_results,
    }
    STATUS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def process_project(
    project: dict,
    adspower: AdsPowerClient,
    gc: gspread.Client,
    logger: logging.Logger,
    config: dict,
) -> dict:
    pid = project["profile_id"]
    name = project.get("name", pid)
    dashboard_id = project.get("dashboard_id", "")
    sheet_id = project["sheet_id"]
    dashboard_sheet = project.get("dashboard_sheet", "Лист 1")
    detail_sheet = project.get("detail_sheet", "FB Кампании")
    export_mode = project.get("export_mode", "campaign")

    iso_date = date.today().isoformat()
    display_date = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    now = datetime.now().strftime("%H:%M:%S")

    result = {
        "dashboard_id": dashboard_id,
        "name": name,
        "profile_id": pid,
        "sheet_id": sheet_id,
        "status": "error",
        "campaigns": 0,
        "spend_total": 0,
        "error": "",
    }

    logger.info("=" * 60)
    logger.info(f"Проект: {name} ({dashboard_id})")
    logger.info(f"AdsPower: {pid} → Sheet: {sheet_id}")
    logger.info(f"Ads Manager: {build_ads_manager_url(project, config)}")
    logger.info("=" * 60)

    browser_data = driver = None
    try:
        browser_data = adspower.open_browser(pid)
        time.sleep(3)

        driver = connect_selenium(browser_data, timeout=config.get("wait_timeout", 30))
        raw_rows = scrape_fb_ads(driver, logger, config, project)
        logger.info(f"Получено кампаний: {len(raw_rows)}")

        if not raw_rows:
            raise RuntimeError("Нет данных из FB Ads Manager")

        campaigns = normalized_campaign_rows(raw_rows)
        if not campaigns:
            raise RuntimeError("Не удалось нормализовать кампании")

        dash_headers, dash_rows = build_dashboard_rows(iso_date, campaigns, export_mode)
        detail_headers, detail_rows = build_detail_rows(iso_date, now, campaigns)

        dash_client = GoogleSheetsClient(gc, sheet_id, dashboard_sheet, logger)
        dash_match_cols = [0] if export_mode == "daily" else [0, 1]
        dash_client.write_upsert(
            dash_headers,
            dash_rows,
            scope_date=display_date,
            match_cols=dash_match_cols,
            label=f"Dashboard/{dashboard_sheet}",
        )

        if detail_sheet and detail_sheet != dashboard_sheet:
            detail_client = GoogleSheetsClient(gc, sheet_id, detail_sheet, logger)
            detail_client.write_upsert(
                detail_headers,
                detail_rows,
                scope_date=display_date,
                match_cols=[0, 2],
                label=f"Detail/{detail_sheet}",
            )

        result["status"] = "ok"
        result["campaigns"] = len(campaigns)
        result["spend_total"] = round(sum(c["spend"] for c in campaigns), 2)
        logger.info(f"✅ {name}: {result['campaigns']} кампаний, spend {result['spend_total']}")
        return result

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"❌ {name}: {e}", exc_info=not isinstance(e, RuntimeError))
        return result

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        if browser_data and config.get("close_browser_after", True):
            adspower.close_browser(pid)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FB Ads Scraper")
    parser.add_argument("--project-id", default=None, help="Только один проект (dashboard_id)")
    args = parser.parse_args()

    try:
        config = load_config()
    except Exception as e:
        print(e)
        sys.exit(1)

    logger = setup_logging(config.get("log_file", "fb_ads_scraper.log"))
    logger.info("#" * 60)
    logger.info(f"# FB Ads Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("#" * 60)

    try:
        projects, projects_source = load_scraper_projects(config, SCRIPT_DIR)
    except Exception as e:
        logger.error(e)
        sys.exit(1)
    logger.info(f"Источник конфига: {projects_source}")

    if args.project_id:
        needle = args.project_id.strip()
        projects = [
            p for p in projects
            if p.get("dashboard_id") == needle
            or p.get("name", "").strip().lower() == needle.lower()
        ]
        if not projects:
            msg = f"Проект не найден или не включён на Mac: {needle}"
            logger.error(msg)
            write_status([{
                "dashboard_id": needle,
                "name": needle,
                "status": "error",
                "campaigns": 0,
                "spend_total": 0,
                "error": msg,
            }])
            sys.exit(1)
        logger.info(f"Фильтр по проекту: {needle}")

    gc = get_gspread_client(logger)
    adspower = AdsPowerClient(
        api_base=config["adspower_api_base"],
        api_key=config["adspower_api_key"],
    )

    logger.info(f"Проектов к обработке: {len(projects)}")
    results = []

    for i, project in enumerate(projects):
        results.append(process_project(project, adspower, gc, logger, config))
        if i < len(projects) - 1:
            time.sleep(5)

    write_status(results)
    ok = sum(1 for r in results if r["status"] == "ok")
    err = len(results) - ok
    logger.info(f"Итого: успешно={ok}, ошибок={err}")
    if err and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
