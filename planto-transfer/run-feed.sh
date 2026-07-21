#!/usr/bin/env bash
# Прогон Planto auto feed из корня этого пакета.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -f secrets.env ]]; then
  echo "Создай secrets.env из secrets.env.example и заполни 5 ключей." >&2
  exit 1
fi

python3 -m pip install -r scripts/buyer-feed/requirements.txt -q
python3 scripts/buyer-feed/__main__.py --work-dir "$ROOT"

echo ""
echo "Готово. Проверь data/planto-meta.json → errors должно быть []."
