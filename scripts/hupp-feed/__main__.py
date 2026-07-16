#!/usr/bin/env python3
"""Run the Hupp dashboard feed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from feed import run_feed


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Hupp dashboard data")
    parser.add_argument("--work-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        return run_feed(args.work_dir.resolve())
    except Exception as error:
        print(f"Hupp feed failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
