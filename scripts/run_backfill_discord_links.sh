#!/usr/bin/env bash
# Run legacy profiles.discord_id → alphapy_discord_links backfill with project venv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating .venv and installing requirements.txt …"
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

export PYTHONPATH=.
exec .venv/bin/python scripts/backfill_discord_links_from_profiles.py "$@"
