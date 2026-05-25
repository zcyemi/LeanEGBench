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
  - Search: `GET /search?q=Nat&limit=10`
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