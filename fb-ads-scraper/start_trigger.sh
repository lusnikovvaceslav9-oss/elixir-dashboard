#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
python3 trigger_server.py
