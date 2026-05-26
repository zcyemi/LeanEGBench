# LeanBenchEnv

LeanBenchEnv packages the local services needed to run the Lean benchmark environment used by `bench-env-code`:

- `lean_server`: Lean verification service at `http://localhost:8578`
- `lean_explore`: LeanExplore local search service at `http://localhost:8580`

## Artifact Instructions

These steps assume you start from the repository root.

### 1. Prepare the environment

Requirements:

- Docker Desktop or Docker Engine with the Compose plugin
- `uv` for running `bench-env-code`
- Internet access on the first startup, because the services populate Lean packages, Python dependencies, and LeanExplore search data

Optional environment variables for `lean_explore` can be placed in a root `.env` file or exported in the shell before startup:

```bash
HF_TOKEN=hf_xxx
LEAN_EXPLORE_VERSION=20260213_050002
```

- `HF_TOKEN`: enables authenticated Hugging Face downloads and higher rate limits
- `LEAN_EXPLORE_VERSION`: pins the LeanExplore data snapshot to fetch

An example file is available at [.env.example](.env.example).

### 2. Start the local services

Build and start the two required services:

```bash
docker compose up --build -d
```

The first cold start can take a while because:

- `lean_server` runs `lake update --keep-toolchain` inside its workspace
- `lean_explore` creates a virtual environment, installs local dependencies, downloads search data, and warms its indices

Wait until both health checks pass, then verify the endpoints:

```bash
curl -fsS http://localhost:8578/health | python -m json.tool
curl -fsS "http://localhost:8580/search?q=Nat&limit=10&rerank_top=0" | python -m json.tool
```

Default endpoints exposed by this repository:

- verify API: `http://localhost:8578/verify`
- LeanExplore API: `http://localhost:8580`

### 3. Fill in the benchmark configuration

Create the runner configuration file and edit the model entry you want to use:

```bash
cd bench-env-code
cp env.example.toml env.toml
```

In `env.toml`, set at least:

- `[[model]].api_key`
- `[[model]].url` if your provider does not use the checked-in default
- `[[model]].model_id` if it differs from the local model name

When using the Dockerized local services, keep:

```toml
lean_explore.url = "http://localhost:8580"
```

### 4. Run the benchmark

The default benchmark command is:

```bash
cd bench-env-code
./run.sh
```

`run.sh` bootstraps the local `uv` environment if needed and then runs the benchmark with these defaults:

- dataset: `./dataset/lean-eg-bench.jsonl`
- model: `deepseek-v4-flash`
- mode: `tool`
- pass count: `1`
- batch size: `1`
- output database directory: `./output`

Common variants:

```bash
cd bench-env-code
./run.sh --model gpt-5.4
./run.sh --mode single
uv run python -m runner.main --verify --dataset ./dataset/lean-eg-bench.jsonl
```

Runner outputs are written to `bench-env-code/output`, and logs are written to `bench-env-code/logs`.

## Cached Data and Cleanup

The Compose stack uses named Docker volumes so expensive setup is not repeated on every restart:

- `lean_server_elan`
- `lean_server_lake`
- `lean_explore_venv`
- `lean_explore_data`
- `lean_explore_hf_cache`

Stop the services while keeping cached data:

```bash
docker compose down
```

Remove containers and all cached volumes for a full reset:

```bash
docker compose down -v
```

## Service Notes

- `lean_server` exposes `GET /health` and `POST /verify`
- `lean_explore` exposes `GET /health`, `GET /search`, and `GET /declarations/{id}`
- `GET /search` accepts `rerank_top`; the smoke test above uses `rerank_top=0` to avoid cross-encoder reranking during startup checks

Useful operational commands:

```bash
docker compose logs -f lean_server
docker compose logs -f lean_explore
docker compose restart lean_server
docker compose restart lean_explore
```