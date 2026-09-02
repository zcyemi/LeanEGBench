# LeanEGBench

LeanEGBench packages the local services needed to run the Lean benchmark environment used by `bench-env-code`:

- `lean_server`: Lean verification service at `http://localhost:8578`
- `lean_explore`: LeanExplore local search service at `http://localhost:8580`

## Collaborators

- @Scarlett-le
- @wangying11123

## About LeanEGBench

LeanEGBench is a benchmark for evaluating large language models on Euclidean geometry theorem proving in Lean. Its core dataset contains 130 manually formalized and fully verified problems. Every problem preserves the semantics of its original geometry statement and is expressed directly with native Lean 4 and Mathlib4 concepts, without a separate geometry DSL, custom axioms, or placeholder propositions. Each formalization was independently reviewed, and complete public Lean proofs available before the evaluation period were excluded to reduce proof-level data contamination.

The checked-in runner dataset is [`bench-env-code/dataset/lean-eg-bench.jsonl`](bench-env-code/dataset/lean-eg-bench.jsonl), which contains the 130-problem core evaluation set:

| Subset | Source | Problems |
| --- | --- | ---: |
| Basic | Author-selected elementary geometry | 22 |
| Textbook | *Advanced Euclidean Geometry* | 9 |
| Textbook | Evan Chen's geometry notes | 20 |
| Textbook | *Geometry Revisited* | 15 |
| Competition | IMO | 22 |
| Competition | National and regional competitions | 42 |
| **Total** |  | **130** |

The problems cover goals such as metric and angle equalities, collinearity, concurrence, perpendicularity, parallelism, concyclicity, similarity, and tangency, across topics including triangles, circles, incenters, circumcenters, orthocenters, projections, and angle bisectors.

### Evaluation

The paper evaluates `gpt-5.4-mini`, `deepseek-v4-flash`, `gemini-3.0-flash`, `o4-mini`, and `gpt-oss-120b` using Lean 4.29.0, Mathlib4 commit `8a178386ffc0f5fef0b77738bb5449d50efeea95`, and LeanExplore data version `20260213_050002`. Each model receives four independent attempts per problem with a 32,768-token output budget under two conditions:

- **Single:** closed-book, single-shot proof generation with no theorem search or verifier feedback.
- **Tool:** single-shot proof generation with up to 25 LeanExplore theorem queries, but still without an iterative verifier-repair loop.

A proof counts as successful only when it preserves the theorem statement exactly, introduces no `sorry`, `admit`, new axioms, or bypass declarations, closes every goal, and compiles in the fixed environment. The evaluation reports task-level pass@1, pass@2, pass@4, valid submissions, failure stages, and Lean error categories.

### Results

All five models score **0/130 on Single pass@4**, showing a strong floor effect without theorem retrieval. Tool access separates the models, but absolute completion remains low:

| Model | Tool pass@1 | Tool pass@2 | Tool pass@4 | Elementary solved at pass@4 | Competition / IMO solved |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gpt-5.4-mini` | 6/130 | 7/130 | **7/130** | 5/22 | 0/64 |
| `deepseek-v4-flash` | 2/130 | 3/130 | 6/130 | 4/22 | 0/64 |
| `gemini-3.0-flash` | 1/130 | 3/130 | 5/130 | 2/22 | 0/64 |
| `o4-mini` | 0/130 | 0/130 | 1/130 | 1/22 | 0/64 |
| `gpt-oss-120b` | 0/130 | 0/130 | 1/130 | 0/22 | 0/64 |

Successful proofs are concentrated in the elementary subset; none of the 64 competition and IMO problems is solved. Tool use improves the number of submissions that reach Lean verification for some models, but does not help uniformly. The remaining failures expose different bottlenecks in theorem retrieval, type and instance construction, proof planning, syntax, and closing incomplete proofs.

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
