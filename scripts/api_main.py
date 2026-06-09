"""FastAPI service exposing the existing comp-ranking engine over HTTP.

Thin wrapper around ``CompRankingService`` (scripts/comp_ranking_service.py).
The ranking logic is reused as-is; this module only adds HTTP routing,
request/response shaping, and a singleton Mongo-backed service.

Run (from the repo root):
    .venv/Scripts/python.exe -m uvicorn api_main:app --app-dir scripts --port 8000
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from api_config import Settings, get_settings
from api_models import (
    AskRequest,
    AskResponse,
    HealthResponse,
    ManualSubject,
    RankCompsRequest,
    RankCompsResponse,
    SubjectSearchResponse,
)
from comp_ranking_service import CompRankingService, ScoringWeights
from llm_service import LLMService, LLMUnavailable

app = FastAPI(
    title="KV Comp Analysis API",
    version="1.1.0",
    description="Ranked comparable-sales shortlist + grounded LLM analysis (Calgary MVP).",
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
_llm: LLMService | None = None


def get_service() -> CompRankingService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = CompRankingService(uri=settings.mongodb_uri, db_name=settings.mongodb_db)
        _service.ensure_indexes()  # idempotent; powers geo retrieval + the hot query
    return _service


def get_llm() -> LLMService:
    global _llm
    if _llm is None:
        settings = get_settings()
        _llm = LLMService(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
        )
    return _llm


def require_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Enforce ``x-api-key`` only when API_KEY is configured (off by default)."""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing x-api-key header.")


def _resolve_subject(
    service: CompRankingService,
    *,
    subject_property_id: str | None,
    subject_address: str | None,
    subject_manual: ManualSubject | None,
) -> dict[str, Any]:
    """Resolve a subject from the DB, or build one from manual/off-market input."""
    if subject_manual is not None:
        return subject_manual.to_subject_doc()
    try:
        return service.get_subject_property(property_id=subject_property_id, address=subject_address)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _rank(service: CompRankingService, subject: dict[str, Any], payload: Any, max_per_pass: int) -> dict[str, Any]:
    try:
        return service.find_ranked_comps(
            subject=subject,
            limit=payload.limit,
            max_results_per_pass=max_per_pass,
            same_community_only=payload.same_community_only,
            max_distance_km=payload.max_distance_km,
            max_sale_age_days=payload.max_sale_age_days,
            weights=ScoringWeights.from_dict(payload.weights),
            annual_appreciation_rate=payload.annual_appreciation_rate,
            use_geo=payload.use_geo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
        llm_configured=bool(settings.groq_api_key),
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
    subject = _resolve_subject(
        service,
        subject_property_id=payload.subject_property_id,
        subject_address=payload.subject_address,
        subject_manual=payload.subject_manual,
    )
    result = _rank(service, subject, payload, payload.max_results_per_pass)
    return RankCompsResponse(**result)


@app.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_api_key)],
)
def ask(
    payload: AskRequest,
    service: CompRankingService = Depends(get_service),
    llm: LLMService = Depends(get_llm),
) -> AskResponse:
    """Grounded LLM Q&A (or comp summary) for a subject and its ranked comps."""
    if not llm.configured:
        raise HTTPException(status_code=503, detail="LLM is not configured. Set GROQ_API_KEY.")

    subject = _resolve_subject(
        service,
        subject_property_id=payload.subject_property_id,
        subject_address=payload.subject_address,
        subject_manual=payload.subject_manual,
    )
    result = _rank(service, subject, payload, 250)

    try:
        answer = llm.ask(
            subject=result["subject"],
            comparables=result["comparables"],
            question=payload.question,
            mode=payload.mode,
        )
    except LLMUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AskResponse(subject=result["subject"], **answer)


@app.post("/ask/stream", dependencies=[Depends(require_api_key)])
def ask_stream(
    payload: AskRequest,
    service: CompRankingService = Depends(get_service),
    llm: LLMService = Depends(get_llm),
) -> StreamingResponse:
    """Same as /ask but streams the answer token-by-token as text/plain."""
    if not llm.configured:
        raise HTTPException(status_code=503, detail="LLM is not configured. Set GROQ_API_KEY.")

    subject = _resolve_subject(
        service,
        subject_property_id=payload.subject_property_id,
        subject_address=payload.subject_address,
        subject_manual=payload.subject_manual,
    )
    result = _rank(service, subject, payload, 250)

    def token_stream():
        try:
            yield from llm.stream(
                subject=result["subject"],
                comparables=result["comparables"],
                question=payload.question,
                mode=payload.mode,
            )
        except LLMUnavailable as exc:
            yield f"\n\n⚠️ {exc}"

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")
