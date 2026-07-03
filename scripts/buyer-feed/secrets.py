"""Load secrets from environment or secrets.env file."""

from __future__ import annotations

import os
import re
from pathlib import Path


def read_secret(name: str, secrets_path: Path | None = None) -> str | None:
    env_val = os.environ.get(name, "").strip()
    if env_val:
        return env_val
    if secrets_path is None:
        return None
    if not secrets_path.is_file():
        return None
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*(.+)\s*$")
    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def load_secrets(work_dir: Path) -> dict[str, str | None]:
    work_dir = work_dir.resolve()
    repo_guess = work_dir
    for _ in range(5):
        if (repo_guess / "supabase" / "secrets.env").is_file():
            break
        if repo_guess.parent == repo_guess:
            break
        repo_guess = repo_guess.parent
    candidates = [
        work_dir / "secrets.env",
        work_dir / "supabase" / "secrets.env",
        repo_guess / "secrets.env",
        repo_guess / "supabase" / "secrets.env",
        Path(__file__).resolve().parents[2] / "supabase" / "secrets.env",
    ]
    # Уникальные существующие файлы в порядке приоритета.
    seen: set[Path] = set()
    secret_files: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp in seen or not p.is_file():
            continue
        seen.add(rp)
        secret_files.append(p)
    keys = (
        "APPMETRICA_OAUTH_TOKEN",
        "APPMETRICA_APPLICATION_ID",
        "DIRECT_OAUTH_TOKEN",
        "DIRECT_CLIENT_LOGIN",
        "SUPABASE_DB_URL",
    )
    out: dict[str, str | None] = {}
    for key in keys:
        # Первое непустое значение среди env → всех файлов-кандидатов.
        value = os.environ.get(key, "").strip() or None
        if value is None:
            for path in secret_files:
                value = read_secret(key, path)
                if value:
                    break
        out[key] = value
    return out
