"""Push baked daily CSVs to the dashboard Worker (Supabase via /api/project-days)."""
from __future__ import annotations

import csv
import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

DASH = Path("/Users/vaceslavlusnikov/Desktop/дашборд")
WORKER = os.environ.get("DASHBOARD_API_BASE", "https://elixir-ua-bot.lusnikovvaceslav9.workers.dev")
WRITE_KEY = os.environ.get("DASHBOARD_WRITE_KEY", "7a4bca97c10ccb1ffcd04c03b43dc9f3abbc57d7")

PROJECT_SHEETS = {
    "bezdna": [("total", "bezdna-daily.csv"), ("leads", "bezdna-leads.csv"), ("reg", "bezdna-reg.csv")],
    "skinai": [("total", "skinai-daily.csv"), ("phi", "skinai-phi.csv"), ("mauve", "skinai-mauve.csv")],
    "quadcode": [
        ("total", "quadcode-daily.csv"),
        ("games", "quadcode-games.csv"),
        ("site", "quadcode-site.csv"),
        ("discord", "quadcode-discord.csv"),
    ],
    "tablet": [
        ("total", "tablet-daily.csv"),
        ("h1", "tablet-h1.csv"),
        ("h2", "tablet-h2.csv"),
        ("h3", "tablet-h3.csv"),
        ("h4", "tablet-h4.csv"),
    ],
}


def parse_num(val) -> float:
    s = str(val or "").strip().replace("\xa0", "").replace(" ", "")
    if not s or s in {"-", "—", "–"}:
        return 0.0
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return 0.0


def ru_to_iso(date: str) -> str:
    s = str(date or "").strip()
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    try:
        return datetime.strptime(s, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return s


def read_sheet(path: Path) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return [], []
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        return [], []
    fields = [k for k in rows[0].keys() if k]
    out = []
    for r in rows:
        date = ru_to_iso(r.get("date") or "")
        if not date:
            continue
        rec = {"date": date, "dateStr": datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")}
        for k in fields:
            if k == "date":
                continue
            rec[k] = parse_num(r.get(k))
        if any(rec.get(k) for k in rec if k not in {"date", "dateStr"}):
            out.append(rec)
    return fields, out


def load_project(project: str, data_dir: Path | None = None) -> dict | None:
    sheets_cfg = PROJECT_SHEETS.get(project)
    if not sheets_cfg:
        raise ValueError(f"Unknown project: {project}")
    root = data_dir or (DASH / "data")
    sheets: dict[str, list[dict]] = {}
    columns: dict[str, list[str]] = {}
    for sheet, filename in sheets_cfg:
        fields, rows = read_sheet(root / filename)
        if not rows:
            continue
        sheets[sheet] = rows
        columns[sheet] = ["date"] + [f for f in fields if f != "date"]
    if not sheets:
        return None
    return {"project": project, "sheets": sheets, "columns": columns}


def push_project(project: str, data_dir: Path | None = None) -> dict:
    payload = load_project(project, data_dir)
    if not payload:
        return {"ok": False, "project": project, "error": "no_rows"}
    req = urllib.request.Request(
        f"{WORKER.rstrip('/')}/api/project-days",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Dashboard-Key": WRITE_KEY,
            "User-Agent": "elixir-dashboard-import/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        text = ""
        if isinstance(e, urllib.error.HTTPError):
            text = e.read().decode("utf-8", errors="replace")
        if "1010" in text or "CERTIFICATE" in str(e).upper() or getattr(e, "code", None) == 403:
            import subprocess
            proc = subprocess.run(
                [
                    "curl", "-sS", "-X", "POST",
                    "-H", "Content-Type: application/json",
                    "-H", f"X-Dashboard-Key: {WRITE_KEY}",
                    "-H", "User-Agent: elixir-dashboard-import/1.0",
                    "--data-binary", "@-",
                    f"{WORKER.rstrip('/')}/api/project-days",
                ],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"project-days curl: {proc.stderr[:300]}") from e
            body = json.loads(proc.stdout)
        else:
            raise RuntimeError(f"project-days {getattr(e, 'code', '?')}: {text[:300] or e}") from e
    days = {k: len(v) for k, v in payload["sheets"].items()}
    spend = round(sum(r.get("spend") or 0 for r in payload["sheets"].get("total") or []), 2)
    return {"ok": True, "project": project, "days": days, "spend": spend, "remote": body}


def push_all(projects: list[str] | None = None) -> list[dict]:
    return [push_project(p) for p in (projects or list(PROJECT_SHEETS))]


if __name__ == "__main__":
    print(json.dumps(push_all(), ensure_ascii=False, indent=2))
