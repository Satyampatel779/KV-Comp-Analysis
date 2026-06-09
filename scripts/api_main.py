"""FastAPI service exposing the existing comp-ranking engine over HTTP.

Thin wrapper around ``CompRankingService`` (scripts/comp_ranking_service.py).
The ranking logic is reused as-is; this module only adds HTTP routing,
request/response shaping, and a singleton Mongo-backed service.

Run (from the repo root):
    .venv/Scripts/python.exe -m uvicorn api_main:app --app-dir scripts --port 8000
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api_config import Settings, get_settings
from api_models import (
    HealthResponse,
    RankCompsRequest,
    RankCompsResponse,
    SubjectSearchResponse,
)
from comp_ranking_service import CompRankingService

app = FastAPI(
    title="KV Comp Analysis API",
    version="1.0.0",
    description="Ranked comparable-sales shortlist for a subject property (Calgary MVP).",
)

# Permissive CORS so browser clients and other HTTP callers can use the service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazily-built singletons. MongoClient is thread-safe and connection-pooled,
# so one service instance is reused across all requests.
_service: CompRankingService | None = None


def get_service() -> CompRankingService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = CompRankingService(uri=settings.mongodb_uri, db_name=settings.mongodb_db)
    return _service


def require_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Enforce ``x-api-key`` only when API_KEY is configured (off by default)."""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing x-api-key header.")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    connected = False
    try:
        service = get_service()
        service.client.admin.command("ping")
        connected = True
    except Exception:  # noqa: BLE001 - health must never raise
        connected = False
    return HealthResponse(
        status="ok" if connected else "degraded",
        db=settings.mongodb_db,
        mongo_connected=connected,
    )


@app.get(
    "/subject-search",
    response_model=SubjectSearchResponse,
    dependencies=[Depends(require_api_key)],
)
def subject_search(
    q: str = Query(..., min_length=1, description="Address or property_id to search."),
    limit: int = Query(10, ge=1, le=50),
    service: CompRankingService = Depends(get_service),
) -> SubjectSearchResponse:
    results = service.search_subjects(q, limit=limit)
    return SubjectSearchResponse(query=q, count=len(results), results=results)


@app.post(
    "/rank-comps",
    response_model=RankCompsResponse,
    dependencies=[Depends(require_api_key)],
)
def rank_comps(
    payload: RankCompsRequest,
    service: CompRankingService = Depends(get_service),
) -> RankCompsResponse:
    try:
        subject = service.get_subject_property(
            property_id=payload.subject_property_id,
            address=payload.subject_address,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = service.find_ranked_comps(
            subject=subject,
            limit=payload.limit,
            max_results_per_pass=payload.max_results_per_pass,
            same_community_only=payload.same_community_only,
            max_distance_km=payload.max_distance_km,
            max_sale_age_days=payload.max_sale_age_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RankCompsResponse(**result)
