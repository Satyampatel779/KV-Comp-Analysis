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
class ManualSubject(BaseModel):
    """An off-market / hypothetical subject typed in by the user (not in the DB)."""

    address: str | None = Field(default="Off-market subject")
    city: str = Field(default="Calgary")
    community_name: str | None = None
    community_code: str | None = None
    property_type_normalized: str = Field(..., description="e.g. detached, semi, condo, townhouse.")
    assessed_value: float = Field(..., gt=0)
    year_built: int | None = Field(default=None, ge=1800, le=2100)
    land_size_sqm: float | None = Field(default=None, gt=0)
    bedrooms: float | None = Field(default=None, ge=0)
    bathrooms: float | None = Field(default=None, ge=0)
    garage_count: float | None = Field(default=None, ge=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    def to_subject_doc(self) -> dict[str, Any]:
        """Render into the nested document shape the engine expects."""
        doc: dict[str, Any] = {
            "property_id": None,
            "city": self.city,
            "address": {"full": self.address},
            "community": {"code": self.community_code, "name": self.community_name},
            "assessment": {"assessed_value": self.assessed_value},
            "property": {
                "property_type_normalized": self.property_type_normalized,
                "year_built": self.year_built,
                "land_size_sqm": self.land_size_sqm,
                "bedrooms": self.bedrooms,
                "bathrooms": self.bathrooms,
                "garage_count": self.garage_count,
            },
        }
        if self.latitude is not None and self.longitude is not None:
            doc["location"] = {"type": "Point", "coordinates": [self.longitude, self.latitude]}
        return doc


class RankCompsRequest(BaseModel):
    subject_property_id: str | None = Field(
        default=None, description="Subject property_id (provide exactly one subject)."
    )
    subject_address: str | None = Field(
        default=None, description="Exact subject address (provide exactly one subject)."
    )
    subject_manual: ManualSubject | None = Field(
        default=None, description="Off-market subject details (provide exactly one subject)."
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
    weights: dict[str, float] | None = Field(
        default=None, description="Override scoring knobs (e.g. distance_per_km, recency_per_month)."
    )
    annual_appreciation_rate: float = Field(
        default=0.0, ge=-0.5, le=0.5, description="Annual market trend for time-adjusted prices."
    )
    use_geo: bool = Field(default=True, description="Use $geoNear retrieval when coords exist.")

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> "RankCompsRequest":
        provided = sum(
            bool(x) for x in (self.subject_property_id, self.subject_address, self.subject_manual)
        )
        if provided != 1:
            raise ValueError(
                "Provide exactly one of subject_property_id, subject_address, or subject_manual."
            )
        return self


class AskRequest(BaseModel):
    """Grounded LLM Q&A / summary over a subject and its ranked comps."""

    subject_property_id: str | None = Field(default=None)
    subject_address: str | None = Field(default=None)
    subject_manual: ManualSubject | None = Field(default=None)
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
    weights: dict[str, float] | None = Field(default=None)
    annual_appreciation_rate: float = Field(default=0.0, ge=-0.5, le=0.5)
    use_geo: bool = Field(default=True)
    stream: bool = Field(default=False, description="(POST /ask only) ignored; use /ask/stream.")

    @model_validator(mode="after")
    def _validate(self) -> "AskRequest":
        provided = sum(
            bool(x) for x in (self.subject_property_id, self.subject_address, self.subject_manual)
        )
        if provided != 1:
            raise ValueError(
                "Provide exactly one of subject_property_id, subject_address, or subject_manual."
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
    time_adjusted_price: float | None = None
    price_per_land_sqm: float | None = None
    score: float | None = None
    distance_km: float | None = None
    recency_days: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    reasons: list[str] = Field(default_factory=list)


class ValueAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")

    count: int = 0
    median_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    price_per_land_sqm_median: float | None = None
    time_adjusted_median: float | None = None
    confidence: dict[str, Any] = Field(default_factory=dict)


class RankCompsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    subject: dict[str, Any]
    candidate_count: int
    returned_count: int
    applied_filters: dict[str, Any]
    analysis: ValueAnalysis = Field(default_factory=ValueAnalysis)
    comparables: list[Comparable]


class AskResponse(BaseModel):
    answer: str
    model: str
    mode: str
    used_comps: int
    subject: dict[str, Any]
    verification: dict[str, Any] = Field(default_factory=dict)
    comparables: list[Comparable] = Field(default_factory=list)
