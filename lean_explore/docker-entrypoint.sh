#!/usr/bin/env bash
set -euo pipefail

cd /app

if [ ! -f ".venv/bin/activate" ]; then
    echo "[lean_explore] Creating virtual environment in /app/.venv"
    python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip

exec /app/start_local_api.sh