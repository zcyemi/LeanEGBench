# lean_server

Lean verification HTTP server for LeanBenchEnv.

## Run locally

```bash
python lean_server.py --help
```

## Docker entrypoint

The Docker image starts the service through `docker-entrypoint.sh`, which prepares the Lean workspace and then launches `lean_server.py`.
