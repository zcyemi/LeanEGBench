# bench-env-code

Benchmark runner for LeanEGBench.

## Artifact Instructions

This directory assumes the local services from the repository root README are already running:

- Lean verification API at `http://localhost:8578/verify`
- LeanExplore API at `http://localhost:8580`

### 1. Prepare the local runner environment

Install the local Python environment with `uv`:

```bash
uv sync
```

### 2. Fill in `env.toml`

Create the runtime config from the checked-in template:

```bash
cp env.example.toml env.toml
```

Then edit `env.toml` and set the model entry you plan to use.

Required fields:

- `[[model]].api_key`
- `[[model]].url` when your provider endpoint differs from the default
- `[[model]].model_id` when the provider model id differs from `name`

When using the Dockerized local services, keep:

```toml
lean_explore.url = "http://localhost:8580"
```

### 3. Run the benchmark

The standard command is:

```bash
./run.sh
```

This runs the benchmark with the defaults encoded in `run.sh`:

- dataset: `./dataset/lean-eg-bench.jsonl`
- model: `deepseek-v4-flash`
- mode: `tool`
- pass count: `1`
- batch size: `1`
- output database directory: `./output`

Common variants:

```bash
./run.sh --model gpt-5.4
./run.sh --mode single
uv run python -m runner.main --verify --dataset ./dataset/lean-eg-bench.jsonl
```

### 4. Collect outputs

- result databases are written to `./output`
- logs are written to `./logs`

To inspect all available CLI options:

```bash
uv run python -m runner.main --help
```
