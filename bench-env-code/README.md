# bench-env-code

Benchmark runner for LeanBenchEnv.

## Run locally

```bash
uv sync
uv run python -m runner.main --help
```

## Configuration

Copy `env.example.toml` to `env.toml`, then edit `env.toml` with the model endpoint, model id, and API key you want to use.

```bash
cp env.example.toml env.toml
```

Key fields:

- `[[model]].name`: local name used by `./run.sh --model ...`
- `[[model]].model_id`: provider model id when it differs from `name`
- `[[model]].url`: provider base URL
- `[[model]].api_key`: provider API key
- `lean_explore.url`: keep `http://localhost:8580` when using the local Docker service

If you only use one provider, leave the other model entries blank or remove them.

## Default run

```bash
./run.sh
```
