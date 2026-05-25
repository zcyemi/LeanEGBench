#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="/app/workspace"

export ELAN_HOME="${ELAN_HOME:-/root/.elan}"
export PATH="$ELAN_HOME/bin:$PATH"

if ! command -v lake >/dev/null 2>&1; then
    echo "[lean_server] lake not found, bootstrapping elan into $ELAN_HOME"
    curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | bash -s -- -y --default-toolchain none
fi

cd "$WORKSPACE_DIR"
echo "[lean_server] Running lake update --keep-toolchain in $WORKSPACE_DIR"
lake update --keep-toolchain

cd /app
exec python lean_server.py \
    --workspace "$WORKSPACE_DIR" \
    --host "${LEAN_SERVER_HOST:-0.0.0.0}" \
    --port "${LEAN_SERVER_PORT:-8578}" \
    -n "${LEAN_SERVER_SLOTS:-4}" \
    --wait-timeout "${LEAN_SERVER_WAIT_TIMEOUT:-40}" \
    --diagnostic-timeout "${LEAN_SERVER_DIAGNOSTIC_TIMEOUT:-30}" \
    --warmup-diagnostic-timeout "${LEAN_SERVER_WARMUP_TIMEOUT:-300}"