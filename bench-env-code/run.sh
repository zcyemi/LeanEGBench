#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
	echo "uv is required but was not found in PATH" >&2
	exit 1
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
	cd "$ROOT_DIR"
	uv sync
fi

if [[ ! -f "$ROOT_DIR/env.toml" ]]; then
	echo "Missing $ROOT_DIR/env.toml" >&2
	echo "Copy env.example.toml to env.toml and fill in your model configuration before running." >&2
	exit 1
fi

cd "$ROOT_DIR"

exec uv run python -m runner.main \
	--model deepseek-v4-flash \
	--dataset ./dataset/lean-eg-bench.jsonl \
	--pass 1 \
	--batch 1 \
	--mode tool \
	--db-path ./output \
	"$@"