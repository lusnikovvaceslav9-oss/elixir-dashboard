#!/usr/bin/env python3
"""Planto buyer feed — lightweight auto-update for elixir dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from feed import run_feed


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Planto buyer dashboard data")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path.cwd(),
        help="Repo root (contains data/, config/, secrets.env)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Project config JSON")
    args = parser.parse_args()
    try:
        return run_feed(args.work_dir, args.config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Feed failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
