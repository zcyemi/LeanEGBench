#!/usr/bin/env bash
# start_local_api.sh — one-command startup for the LeanExplore local REST API server
#
# What this script does:
#   1. Verifies Python ≥ 3.10 is available
#   2. Installs lean-explore[local] (editable, from this repo) if not already installed
#   3. Installs fastapi and uvicorn if not already installed
#   4. Downloads local search data via `lean-explore data fetch` if not yet present
#   5. Starts the FastAPI server with uvicorn
#
# Options (env vars):
#   HOST       — bind address (default: 127.0.0.1)
#   PORT       — port number  (default: 8000)
#   LOG_LEVEL  — uvicorn log level: debug|info|warning|error (default: info)
#
# Example:
#   PORT=9000 ./start_local_api.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV_PATH="$SCRIPT_DIR/.venv/bin/activate"
if [ -f "$VENV_PATH" ]; then
    source "$VENV_PATH"
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# ── helpers ──────────────────────────────────────────────────────────────────

info()    { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
success() { printf '\033[1;32m[OK]\033[0m    %s\n' "$*"; }
warn()    { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
error()   { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }
die()     { error "$*"; exit 1; }

# ── 1. Python version check ───────────────────────────────────────────────────

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" &>/dev/null; then
    die "Python 3 not found. Install Python ≥ 3.10 and re-run this script."
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
    die "Python 3.10+ required, found $PY_VERSION. Set PYTHON=<path> to override."
fi

success "Python $PY_VERSION found at $(command -v "$PYTHON")"

PIP=("$PYTHON" -m pip)
# lean-explore CLI binary (installed alongside the Python interpreter)
LEAN_EXPLORE_BIN="$(dirname "$("$PYTHON" -c "import sys; print(sys.executable)")")/lean-explore"

# ── 2. Install lean-explore[local] if missing ─────────────────────────────────

if ! "$PYTHON" -c "import lean_explore" &>/dev/null 2>&1; then
    info "lean-explore not installed. Installing from local repo (editable)..."
    "${PIP[@]}" install -e "$SCRIPT_DIR[local]" --quiet
    success "lean-explore[local] installed."
else
    # Check that the local extras (sentence-transformers, torch) are present
    if ! "$PYTHON" -c "import sentence_transformers" &>/dev/null 2>&1; then
        info "lean-explore[local] extras missing. Installing..."
        "${PIP[@]}" install -e "$SCRIPT_DIR[local]" --quiet
        success "lean-explore[local] extras installed."
    else
        success "lean-explore[local] already installed."
    fi
fi

# ── 3. Install fastapi + uvicorn if missing ───────────────────────────────────

MISSING_WEB=()
"$PYTHON" -c "import fastapi" &>/dev/null 2>&1 || MISSING_WEB+=("fastapi")
"$PYTHON" -c "import uvicorn" &>/dev/null 2>&1 || MISSING_WEB+=("uvicorn[standard]")

if [[ ${#MISSING_WEB[@]} -gt 0 ]]; then
    info "Installing web server dependencies: ${MISSING_WEB[*]}"
    "${PIP[@]}" install "${MISSING_WEB[@]}" --quiet
    success "Web server dependencies installed."
else
    success "fastapi + uvicorn already installed."
fi

# ── 4. Fetch local data if not present ────────────────────────────────────────

CACHE_DIR="${LEAN_EXPLORE_CACHE_DIR:-$HOME/.lean_explore/cache}"
VERSION_FILE="$HOME/.lean_explore/active_version"

info "Checking for local data in cache directory: $CACHE_DIR"
info "Active version file: $VERSION_FILE"

DATA_READY=false
if [[ -f "$VERSION_FILE" ]]; then
    ACTIVE_VERSION="$(cat "$VERSION_FILE")"
    DB_PATH="$CACHE_DIR/$ACTIVE_VERSION/lean_explore.db"
    info "Checking for local data at $DB_PATH..."
    if [[ -f "$DB_PATH" ]]; then
        DATA_READY=true
    fi
fi

if [[ "$DATA_READY" == false ]]; then
    info "Local data not found. Running 'lean-explore data fetch'..."
    info "This downloads ~several GB and may take a few minutes..."
    "$LEAN_EXPLORE_BIN" data fetch
    success "Data downloaded."
else
    success "Local data found at $CACHE_DIR/$ACTIVE_VERSION"
fi

# ── 5. Start the REST API server ──────────────────────────────────────────────

info "Starting LeanExplore local REST API server..."
info "  Address : http://$HOST:$PORT"
info "  Docs    : http://$HOST:$PORT/docs"
info "  Health  : http://$HOST:$PORT/health"
info ""
info "Press Ctrl+C to stop."

exec "$PYTHON" -m uvicorn local_rest_server:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level "$LOG_LEVEL" \
    --app-dir "$SCRIPT_DIR"
