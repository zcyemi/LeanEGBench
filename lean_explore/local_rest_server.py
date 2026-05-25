"""Local REST API server for LeanExplore.

Exposes the same endpoints as openapi.yaml but runs entirely on local data
(no remote API calls). Requires the local backend dependencies and fetched data.

Usage:
    uvicorn local_rest_server:app
    uvicorn local_rest_server:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    GET /search?q=<query>&limit=<n>   - Search for Lean declarations
    GET /declarations/{id}            - Retrieve a declaration by ID

Run start_local_api.sh for a one-command startup with all dependency checks.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Global service instance, initialized on startup
_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service
    logger.info("Initializing local search engine (this may take a moment)...")
    try:
        from lean_explore.search import SearchEngine, Service

        # use_local_data=False → use CACHE_DIRECTORY (data downloaded via
        # `lean-explore data fetch`)
        engine = SearchEngine(use_local_data=False)
        _service = Service(engine=engine)
        logger.info("Local search engine ready.")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing data files: {exc}\n"
            "Run 'lean-explore data fetch' to download required data."
        ) from exc
    yield
    _service = None


app = FastAPI(
    title="LeanExplore Local API",
    description=(
        "Local REST API for searching Lean 4 declarations. "
        "Data is served from the local cache directory."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/search")
async def search_declarations(
    q: str = Query(..., description="Search query string"),
    limit: int = Query(20, ge=1, le=200, description="Maximum results to return"),
):
    """Search for Lean declarations using natural language or Lean syntax."""
    if _service is None:
        raise HTTPException(status_code=503, detail="Search service not initialized")

    response = await _service.search(query=q, limit=limit)
    return JSONResponse(content=response.model_dump())


@app.get("/declarations/{declaration_id}")
async def get_declaration(declaration_id: int):
    """Retrieve a specific Lean declaration by its unique ID."""
    if _service is None:
        raise HTTPException(status_code=503, detail="Search service not initialized")

    result = await _service.get_by_id(declaration_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Declaration {declaration_id} not found"
        )
    return JSONResponse(content=result.model_dump())


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service_ready": _service is not None}
