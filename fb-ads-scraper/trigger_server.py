#!/usr/bin/env python3
"""Локальный HTTP-триггер: один POST = один проект = один запуск скрипта."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATUS_PATH = SCRIPT_DIR / "status.json"

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"

logger = logging.getLogger("fb_trigger")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def running_path(project_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)
    return SCRIPT_DIR / f".running_{safe}"


def cors_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Trigger-Token")


def json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    cors_headers(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_status(project_id: str | None = None) -> dict:
    data = {"updated_at": None, "projects": []}
    if STATUS_PATH.exists():
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    if project_id:
        data["projects"] = [
            p for p in data.get("projects") or []
            if p.get("dashboard_id") == project_id
        ]

    last_run_path = SCRIPT_DIR / "last_run.json"
    if last_run_path.exists():
        try:
            data["last_run"] = json.loads(last_run_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    running_projects = []
    for path in SCRIPT_DIR.glob(".running_*"):
        name = path.name.removeprefix(".running_")
        running_projects.append(name)
    data["running_projects"] = running_projects
    data["running"] = bool(running_projects)
    if project_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)
        data["running"] = running_path(project_id).exists() or safe in running_projects
    return data


def run_scraper(project_id: str) -> None:
    lock = running_path(project_id)
    lock.write_text("1", encoding="utf-8")
    last_run_path = SCRIPT_DIR / "last_run.json"
    started_at = datetime.now().isoformat(timespec="seconds")
    exit_code = 1
    try:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "fb_ads_scraper.py"),
            "--manual",
            "--project-id",
            project_id,
        ]
        logger.info("Запуск отдельного прогона: %s", " ".join(cmd))
        result = subprocess.run(cmd, cwd=str(SCRIPT_DIR), check=False)
        exit_code = int(result.returncode)
    finally:
        payload = {
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "project_id": project_id,
            "exit_code": exit_code,
            "ok": exit_code == 0,
        }
        last_run_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if lock.exists():
            lock.unlink()


class TriggerHandler(BaseHTTPRequestHandler):
    server_version = "FBScraperTrigger/2.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        cors_headers(self)
        self.end_headers()

    def do_GET(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        project_id = (qs.get("project_id") or [None])[0]
        project_id = (project_id or "").strip() or None
        path = urlparse(self.path).path

        if path == "/status":
            json_response(self, 200, {"ok": True, **read_status(project_id)})
            return
        if path == "/health":
            json_response(self, 200, {"ok": True, "service": "fb-scraper-trigger"})
            return
        json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        config = load_config()
        expected = (config.get("trigger_token") or "").strip()
        if expected:
            got = (self.headers.get("X-Trigger-Token") or "").strip()
            if got != expected:
                json_response(self, 401, {"ok": False, "error": "invalid token"})
                return

        path = urlparse(self.path).path
        if path != "/trigger":
            json_response(self, 404, {"ok": False, "error": "not found"})
            return

        qs = parse_qs(urlparse(self.path).query)
        project_id = (qs.get("project_id") or [None])[0]
        project_id = (project_id or "").strip()
        if not project_id:
            json_response(
                self,
                400,
                {"ok": False, "error": "project_id обязателен — каждый проект парсится отдельно"},
            )
            return

        lock = running_path(project_id)
        if lock.exists():
            json_response(
                self,
                409,
                {"ok": False, "error": f"проект {project_id} уже парсится", **read_status(project_id)},
            )
            return

        threading.Thread(target=run_scraper, args=(project_id,), daemon=True).start()
        json_response(
            self,
            202,
            {"ok": True, "message": "scraper started", "project_id": project_id},
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    config = load_config()
    host = (config.get("trigger_host") or DEFAULT_HOST).strip()
    port = int(config.get("trigger_port") or DEFAULT_PORT)

    server = HTTPServer((host, port), TriggerHandler)
    logger.info("FB scraper trigger: http://%s:%s", host, port)
    logger.info("POST /trigger?project_id=ID  GET /status?project_id=ID")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка")
        server.server_close()


if __name__ == "__main__":
    main()
