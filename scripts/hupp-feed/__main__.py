#!/usr/bin/env python3
"""Run the Hupp dashboard feed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from feed import run_feed


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Hupp-like dashboard feed (Metrika)")
    parser.add_argument("--work-dir", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config/hupp.json"))
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    config_path = args.config if args.config.is_absolute() else work_dir / args.config
    try:
        return run_feed(work_dir, config_path)
    except Exception as error:
        print(f"Feed failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
