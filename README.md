# BenchEnv Docker Setup

This repository can be started as two Docker services:

- `lean_server`: a Lean benchmark verification server that exposes a REST API for submitting Lean code.
- `lean_explore`: a local LeanExplore REST API that downloads search data on first start and serves declaration search endpoints.

## Services and ports

- `lean_server`: `http://localhost:8578`
  - Health: `GET /health`
  - Verify: `POST /verify`
- `lean_explore`: `http://localhost:8580`
  - Health: `GET /health`
  - Search: `GET /search?q=Nat&limit=10&rerank_top=0`
  - Declaration: `GET /declarations/{id}`

## Prerequisites

- Docker Desktop or Docker Engine with the Compose plugin
- Internet access on first startup:
  - `lean_server` runs `lake update --keep-toolchain` inside `lean_server/workspace`
  - `lean_explore` creates a Python virtual environment and runs `lean-explore data fetch`

## Start the stack

From the repository root:

```bash
docker compose up --build
```

To run the containers in the background:

```bash
docker compose up --build -d
```

## What happens on startup

### `lean_server`

The container entrypoint performs these steps before exposing the API:

1. Changes into `lean_server/workspace`
2. Runs `lake update --keep-toolchain`
3. Starts the verification REST server on `0.0.0.0:8578`

### `lean_explore`

The container entrypoint performs these steps before exposing the API:

1. Creates `/app/.venv` if it does not exist
2. Upgrades `pip` in that virtual environment
3. Runs `start_local_api.sh`
4. `start_local_api.sh` installs `lean-explore[local]` if needed, fetches remote data if missing, and starts the REST API on `0.0.0.0:8580`

The first `lean_explore` startup can take a long time because it downloads several GB of data.

## Persistent data

The Compose file uses named Docker volumes so expensive initialization is not repeated on every restart:

- `lean_server_elan`: Lean toolchains managed by `elan`
- `lean_server_lake`: Lean workspace package cache under `lean_server/workspace/.lake`
- `lean_explore_venv`: Python virtual environment used by `lean_explore`
- `lean_explore_data`: LeanExplore cache and downloaded search data
- `lean_explore_hf_cache`: Hugging Face model cache used by local embedding and reranking models

Stopping the stack with `docker compose down` keeps these volumes, so data and model files are not downloaded again on the next `up`.
Only `docker compose down -v` removes them and forces a fresh download.

To stop the services while keeping cached data:

```bash
docker compose down
```

To remove the containers and all named volumes:

```bash
docker compose down -v
```

## Quick checks

After the services are up, test them from the repository root:

```bash
python verify_check/test_conn.py
python verify_check/verify.py
```

You can override the default endpoints with environment variables:

```bash
LEAN_EXPLORE_BASE_URL=http://localhost:8580 python verify_check/test_conn.py
LEAN_VERIFY_URL=http://localhost:8578/verify python verify_check/verify.py
```

## Run `bench-env-code`

`bench-env-code` is the benchmark runner in this repository. It reads tasks from `bench-env-code/dataset/lean-eg-bench.jsonl`, calls a configured model, optionally uses the local `lean_explore` search API in `tool` mode, and sends generated Lean code to the local verification service.

### Default local endpoints

The checked-in `bench-env-code` defaults now match the Docker services started by this repository:

- Lean verify endpoint: `http://localhost:8578/verify`
- LeanExplore endpoint: `http://localhost:8580`

That means after `docker compose up --build`, you can run `bench-env-code` locally without changing the server URLs.

### Prepare model config

Edit `bench-env-code/env.toml` and set the model entry you want to use:

- fill in `api_key`
- adjust `url` and `model_id` if your provider requires it
- keep `lean_explore.url = "http://localhost:8580"` when using the Docker `lean_explore` service

### Install and run

From the repository root:

```bash
cd bench-env-code
./run.sh
```

`run.sh` uses `uv` to create the local environment if needed, then starts the runner with these defaults:

- dataset: `./dataset/lean-eg-bench.jsonl`
- model: `deepseek-v4-flash`
- mode: `tool`
- pass count: `1`
- batch size: `1`
- result database directory: `./output`

### Common commands

Run with the default tool workflow:

```bash
cd bench-env-code
./run.sh
```

Run a different model defined in `env.toml`:

```bash
cd bench-env-code
./run.sh --model gpt-5.4
```

Run without LeanExplore tools:

```bash
cd bench-env-code
./run.sh --mode single
```

Only verify the source dataset without calling a model:

```bash
cd bench-env-code
uv run python -m runner.main --verify --dataset ./dataset/lean-eg-bench.jsonl
```

Override the service endpoints if your containers are exposed elsewhere:

```bash
cd bench-env-code
./run.sh --verify-url http://localhost:8578/verify
```

If you also need a different LeanExplore endpoint, update `bench-env-code/env.toml` before running.

### Output files

Runner outputs are written under `bench-env-code/output` and logs are written under `bench-env-code/logs`.

## Optional LeanExplore parameters

You can provide these variables through your shell environment or a root `.env` file before starting Compose:

```bash
HF_TOKEN=hf_xxx
LEAN_EXPLORE_VERSION=20260507_203639
```

An example file is available at [.env.example](d:/git/BenchEnv/.env.example).

- `HF_TOKEN`: passed into the `lean_explore` container so Hugging Face downloads use authenticated requests and higher rate limits.
- `LEAN_EXPLORE_VERSION`: selects the exact LeanExplore data version to use. If the requested version is missing from the persisted cache volume, the container runs `lean-explore data fetch --version <value>`.

For local API calls, `GET /search` now accepts `rerank_top`. The default smoke-check path uses `rerank_top=0` so the endpoint responds quickly without cross-encoder reranking. If you want higher-quality reranked results, call `/search` with a positive `rerank_top` value.

The `lean_explore` container now warms the search indices and Hugging Face models during application startup. This makes the first `/search` request much more predictable, but it also means the container can stay in `starting` state for longer on a cold boot.

## Useful commands

Show logs:

```bash
docker compose logs -f lean_server
docker compose logs -f lean_explore
```

Restart a single service:

```bash
docker compose restart lean_server
docker compose restart lean_explore
```