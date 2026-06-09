"""Pydantic request/response models for the KV comp-analysis API.

Response models mirror the dicts already produced by
``CompRankingService`` so the engine output passes through cleanly and stays
easy for clients to parse. ``extra="allow"`` keeps any additional engine fields
(e.g. nested ``property_snapshot``) without forcing schema churn.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class RankCompsRequest(BaseModel):
    subject_property_id: str | None = Field(
        default=None, description="Subject property_id (provide this OR subject_address)."
    )
    subject_address: str | None = Field(
        default=None, description="Exact subject address (provide this OR subject_property_id)."
    )
    limit: int = Field(default=10, ge=1, le=100, description="Number of comps to return.")
    same_community_only: bool = Field(
        default=False, description="Restrict comps to the subject's community."
    )
    max_distance_km: float | None = Field(
        default=None, gt=0, description="Tighten the max comp distance (km)."
    )
    max_sale_age_days: int | None = Field(
        default=None, gt=0, description="Tighten the max sale age (days)."
    )
    max_results_per_pass: int = Field(
        default=250, ge=1, le=2000, description="Mongo candidates inspected per filter pass."
    )

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> "RankCompsRequest":
        if bool(self.subject_property_id) == bool(self.subject_address):
            raise ValueError(
                "Provide exactly one of subject_property_id or subject_address."
            )
        return self


class AskRequest(BaseModel):
    """Grounded LLM Q&A / summary over a subject and its ranked comps."""

    subject_property_id: str | None = Field(
        default=None, description="Subject property_id (provide this OR subject_address)."
    )
    subject_address: str | None = Field(
        default=None, description="Exact subject address (provide this OR subject_property_id)."
    )
    question: str | None = Field(
        default=None, description="Question to answer (required when mode='qa')."
    )
    mode: Literal["qa", "summary"] = Field(
        default="qa", description="'qa' answers a question; 'summary' writes a comp memo."
    )
    limit: int = Field(default=10, ge=1, le=50, description="Comps to ground the answer on.")
    same_community_only: bool = Field(default=False)
    max_distance_km: float | None = Field(default=None, gt=0)
    max_sale_age_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate(self) -> "AskRequest":
        if bool(self.subject_property_id) == bool(self.subject_address):
            raise ValueError(
                "Provide exactly one of subject_property_id or subject_address."
            )
        if self.mode == "qa" and not (self.question and self.question.strip()):
            raise ValueError("question is required when mode='qa'.")
        return self


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    status: str
    db: str
    mongo_connected: bool
    llm_configured: bool = False


class SubjectSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    property_id: str | None = None
    address: str | None = None
    city: str | None = None
    community: str | None = None
    property_type_normalized: str | None = None
    assessed_value: float | None = None
    year_built: int | None = None
    land_size_sqm: float | None = None
    latitude: float | None = None
    longitude: float | None = None


class SubjectSearchResponse(BaseModel):
    query: str
    count: int
    results: list[SubjectSummary]


class Comparable(BaseModel):
    model_config = ConfigDict(extra="allow")

    sale_id: str | None = None
    property_id: str | None = None
    address: str | None = None
    community: str | None = None
    sale_date: str | None = None
    sale_price: float | None = None
    score: float | None = None
    distance_km: float | None = None
    recency_days: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    reasons: list[str] = Field(default_factory=list)


class RankCompsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    subject: dict[str, Any]
    candidate_count: int
    returned_count: int
    applied_filters: dict[str, Any]
    comparables: list[Comparable]


class AskResponse(BaseModel):
    answer: str
    model: str
    mode: str
    used_comps: int
    subject: dict[str, Any]
