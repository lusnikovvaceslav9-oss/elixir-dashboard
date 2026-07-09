#!/usr/bin/env python3
"""Фоновый воркер: читает очередь из JSONBin (дашборд) и парсит проекты на Mac."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from remote_config import (
    fetch_dashboard_projects,
    save_dashboard_projects,
    upsert_worker_heartbeat,
)

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
POLL_SEC = 12
WORKER_STATUS_PATH = SCRIPT_DIR / "worker_status.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(SCRIPT_DIR / "fb_scraper_worker.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("fb_worker")


def set_project_fb(projects: list[dict], project_id: str, patch: dict) -> list[dict]:
    out = []
    for project in projects:
        if project.get("id") != project_id:
            out.append(project)
            continue
        fb = dict(project.get("fbScraper") or {})
        fb.update(patch)
        out.append({**project, "fbScraper": fb})
    return out


def run_project_scraper(project_id: str, logger: logging.Logger) -> int:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "fb_ads_scraper.py"),
        "--manual",
        "--project-id",
        project_id,
    ]
    logger.info("Запуск: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR), check=False)
    return int(result.returncode)


def read_local_status(project_id: str) -> dict | None:
    status_path = SCRIPT_DIR / "status.json"
    if not status_path.exists():
        return None
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for item in payload.get("projects") or []:
        if item.get("dashboard_id") == project_id:
            return item
    return None


def process_pending(config: dict, logger: logging.Logger) -> bool:
    WORKER_STATUS_PATH.write_text(
        json.dumps(
            {"heartbeat": datetime.now().isoformat(timespec="seconds"), "status": "online"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    projects = fetch_dashboard_projects(config)
    projects = [p for p in projects if p.get("id") not in ("_worker", "planto")]

    pending = []
    for project in projects:
        fb = project.get("fbScraper") or {}
        if fb.get("jobStatus") == "pending":
            pending.append(project.get("id"))

    if not pending:
        return False

    for project_id in pending:
        if not project_id:
            continue
        logger.info("Очередь: проект %s", project_id)
        now = datetime.now().isoformat(timespec="seconds")

        projects = fetch_dashboard_projects(config)
        projects = [p for p in projects if p.get("id") != "_worker"]
        projects = set_project_fb(
            projects,
            project_id,
            {
                "jobStatus": "running",
                "jobStartedAt": now,
            },
        )
        projects = upsert_worker_heartbeat(config, projects)
        save_dashboard_projects(config, projects)

        exit_code = run_project_scraper(project_id, logger)
        local = read_local_status(project_id) or {}

        projects = fetch_dashboard_projects(config)
        projects = [p for p in projects if p.get("id") != "_worker"]
        projects = set_project_fb(
            projects,
            project_id,
            {
                "jobStatus": "done" if exit_code == 0 else "error",
                "jobFinishedAt": datetime.now().isoformat(timespec="seconds"),
                "lastRun": {
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "ok": exit_code == 0,
                    "campaigns": local.get("campaigns", 0),
                    "spend": local.get("spend_total", 0),
                    "error": local.get("error", ""),
                },
            },
        )
        projects = upsert_worker_heartbeat(config, projects)
        save_dashboard_projects(config, projects)
        logger.info("Проект %s завершён, exit=%s", project_id, exit_code)

    return True


def main() -> None:
    logger = setup_logging()
    config = load_config()
    logger.info("FB Scraper Worker — JSONBin очередь, poll=%ss", POLL_SEC)
    while True:
        try:
            process_pending(config, logger)
        except Exception as exc:
            logger.error("Ошибка воркера: %s", exc, exc_info=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
