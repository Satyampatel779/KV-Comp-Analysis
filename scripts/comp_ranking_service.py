from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank comparable sales for a subject property from MongoDB Atlas."
    )
    parser.add_argument(
        "--uri",
        default=os.environ.get("MONGODB_URI"),
        help="MongoDB Atlas URI. Defaults to MONGODB_URI.",
    )
    parser.add_argument(
        "--db",
        default="kv_comp_analysis",
        help="MongoDB database name.",
    )
    parser.add_argument(
        "--subject-property-id",
        help="Subject property_id to rank comps for.",
    )
    parser.add_argument(
        "--subject-address",
        help="Exact subject address.full value to rank comps for.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of ranked comparable sales to return.",
    )
    parser.add_argument(
        "--max-results-per-pass",
        type=int,
        default=250,
        help="Max Mongo candidates to inspect per widening filter pass.",
    )
    return parser.parse_args()


@dataclass(frozen=True)
class FilterProfile:
    label: str
    max_sale_age_days: int
    max_value_gap_ratio: float
    max_land_gap_ratio: float
    max_year_gap: int
    max_distance_km: float


FILTER_PROFILES = [
    FilterProfile(
        label="tight",
        max_sale_age_days=365,
        max_value_gap_ratio=0.20,
        max_land_gap_ratio=0.30,
        max_year_gap=10,
        max_distance_km=4.0,
    ),
    FilterProfile(
        label="balanced",
        max_sale_age_days=540,
        max_value_gap_ratio=0.35,
        max_land_gap_ratio=0.45,
        max_year_gap=18,
        max_distance_km=8.0,
    ),
    FilterProfile(
        label="wide",
        max_sale_age_days=730,
        max_value_gap_ratio=0.50,
        max_land_gap_ratio=0.60,
        max_year_gap=25,
        max_distance_km=15.0,
    ),
]


@dataclass(frozen=True)
class QueryPass:
    label: str
    same_community_only: bool


QUERY_PASSES = [
    QueryPass(label="same_community", same_community_only=True),
    QueryPass(label="same_city", same_community_only=False),
]


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def parse_sale_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def haversine_km(point_a: list[float] | None, point_b: list[float] | None) -> float | None:
    if not point_a or not point_b:
        return None

    lon1, lat1 = point_a
    lon2, lat2 = point_b
    radius_km = 6371.0088

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    hav = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(hav))


def safe_ratio_gap(subject_value: float | None, candidate_value: float | None) -> float | None:
    if subject_value in (None, 0) or candidate_value is None:
        return None
    return abs(candidate_value - subject_value) / abs(subject_value)


def abs_gap(subject_value: Any, candidate_value: Any) -> float | None:
    """Absolute numeric gap, or None when either side is missing/non-numeric."""
    if subject_value is None or candidate_value is None:
        return None
    try:
        return abs(float(candidate_value) - float(subject_value))
    except (TypeError, ValueError):
        return None


def clamp_penalty(value: float, maximum: float) -> float:
    return min(value, maximum)


def lat_lon(coordinates: list[float] | None) -> tuple[float | None, float | None]:
    """Split a GeoJSON ``[lon, lat]`` pair into ``(latitude, longitude)``.

    Additive helper so engine output carries plottable coordinates for map UIs.
    Returns ``(None, None)`` when coordinates are missing/malformed.
    """
    if not coordinates or len(coordinates) != 2:
        return None, None
    return coordinates[1], coordinates[0]


@dataclass(frozen=True)
class ScoringWeights:
    """Tunable scoring knobs. Defaults reproduce the engine's original scoring.

    Penalties are subtracted from ``base``; each is capped. Bonuses are added.
    Callers (API/UI) may override any subset; unspecified knobs keep defaults.
    """

    base: float = 100.0
    distance_per_km: float = 9.0
    distance_cap: float = 35.0
    distance_missing_penalty: float = 20.0
    recency_per_month: float = 2.0
    recency_cap: float = 22.0
    value_gap_factor: float = 0.55
    value_gap_cap: float = 22.0
    land_gap_factor: float = 0.22
    land_gap_cap: float = 12.0
    year_gap_per_year: float = 0.6
    year_gap_cap: float = 12.0
    bed_gap_per: float = 3.0
    bed_gap_cap: float = 9.0
    bath_gap_per: float = 3.0
    bath_gap_cap: float = 9.0
    garage_gap_per: float = 2.0
    garage_gap_cap: float = 6.0
    same_community_bonus: float = 12.0
    same_community_pass_bonus: float = 4.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScoringWeights":
        if not data:
            return cls()
        names = {f.name for f in dataclasses.fields(cls)}
        clean = {k: float(v) for k, v in data.items() if k in names and v is not None}
        return dataclasses.replace(cls(), **clean)


DEFAULT_WEIGHTS = ScoringWeights()


# Calgary street-type + quadrant abbreviations used in the stored addresses.
# Used to build tolerant search regexes (full word OR its abbreviation match).
ADDRESS_ABBREV = {
    "STREET": "ST", "AVENUE": "AV", "AVE": "AV", "DRIVE": "DR", "ROAD": "RD",
    "BOULEVARD": "BV", "CRESCENT": "CR", "CLOSE": "CL", "COURT": "CT", "PLACE": "PL",
    "LANE": "LN", "WAY": "WY", "GATE": "GT", "HEIGHTS": "HT", "TERRACE": "TC",
    "POINT": "PT", "SQUARE": "SQ", "VIEW": "VW", "GREEN": "GR", "GROVE": "GV",
    "BAY": "BA", "MANOR": "MR", "RISE": "RI", "HILL": "HL", "COMMON": "CO",
    "NORTHWEST": "NW", "NORTHEAST": "NE", "SOUTHWEST": "SW", "SOUTHEAST": "SE",
}


def address_tokens(text: str) -> list[str]:
    """Split a query into normalized tokens (upper-cased, whitespace-collapsed)."""
    return [t for t in normalize_whitespace(text).split(" ") if t]


def token_regex(token: str) -> str:
    """Word-prefix regex for one token, tolerant of full-word/abbreviation forms.

    ``\\b`` anchors to a word start so e.g. ``NW`` matches the quadrant token but
    NOT the ``nw`` inside ``LYNNWOOD``. A full street-type word also matches its
    stored abbreviation (``DRIVE`` -> ``DR``) and vice-versa (``DR`` prefix
    matches ``DRIVE``).
    """
    esc = re.escape(token)
    abbr = ADDRESS_ABBREV.get(token)
    if abbr:
        return rf"\b({esc}|{re.escape(abbr)})"
    return rf"\b{esc}"


def summarize_value(comparables: list[dict[str, Any]]) -> dict[str, Any]:
    """Implied value band + confidence. Re-exported from the shared module so the
    engine and the UI compute it identically (single source of truth)."""
    from comp_analysis import summarize_value as _summarize

    return _summarize(comparables)


class CompRankingService:
    def __init__(self, uri: str, db_name: str) -> None:
        from pymongo import MongoClient

        self.client = MongoClient(uri, appname="kv-comp-analysis-comps")
        self.db = self.client[db_name]

    def get_subject_property(
        self,
        *,
        property_id: str | None = None,
        address: str | None = None,
    ) -> dict[str, Any]:
        if bool(property_id) == bool(address):
            raise ValueError("Provide exactly one of subject property_id or subject address.")

        query: dict[str, Any]
        if property_id:
            query = {"property_id": property_id}
        else:
            normalized = normalize_whitespace(address or "")
            query = {"address.full": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"}}

        subject = self.db.properties.find_one(query, {"_id": 0})
        if not subject:
            raise LookupError("Subject property not found in Atlas properties collection.")
        return subject

    def search_subjects(self, query_text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find candidate subject properties by property_id or address.

        Exact (normalized) address match is returned first, then a normalized
        "contains" match, then a property_id match. Results are de-duplicated by
        property_id while preserving that priority order.
        """
        query_text = (query_text or "").strip()
        if not query_text:
            return []

        normalized = normalize_whitespace(query_text)
        projection = {
            "_id": 0,
            "property_id": 1,
            "address.full": 1,
            "city": 1,
            "community": 1,
            "property": 1,
            "assessment": 1,
            "location": 1,
        }

        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(documents: Any) -> None:
            for document in documents:
                property_id = document.get("property_id")
                if not property_id or property_id in seen:
                    continue
                seen.add(property_id)
                ordered.append(self._subject_summary(document))
                if len(ordered) >= limit:
                    break

        # 1) exact normalized address match
        add(
            self.db.properties.find(
                {"address.full": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"}},
                projection,
            ).limit(limit)
        )
        # 2) tolerant token match — every token must appear as a word-prefix
        #    (order-independent, abbreviation-aware typeahead). Avoids the
        #    "NW matches LYNNWOOD" substring false-positive via word anchoring.
        tokens = address_tokens(query_text)
        if len(ordered) < limit and tokens:
            token_query = {
                "$and": [
                    {"address.full": {"$regex": token_regex(tok), "$options": "i"}}
                    for tok in tokens
                ]
            }
            add(self.db.properties.find(token_query, projection).limit(limit * 4))
        # 3) normalized "contains" address match (last-resort substring)
        if len(ordered) < limit:
            add(
                self.db.properties.find(
                    {"address.full": {"$regex": re.escape(normalized), "$options": "i"}},
                    projection,
                ).limit(limit * 3)
            )
        # 4) exact property_id match
        if len(ordered) < limit:
            add(self.db.properties.find({"property_id": query_text}, projection).limit(limit))

        return ordered[:limit]

    def ensure_indexes(self) -> None:
        """Best-effort: create the indexes the hot paths rely on (idempotent).

        ``sales.location`` 2dsphere powers geo-aware retrieval; the compound
        index supports the non-geo filter pass. Safe to call on every startup.
        """
        try:
            self.db.sales.create_index([("location", "2dsphere")], name="location_2dsphere")
            self.db.sales.create_index(
                [("city", 1), ("property_snapshot.property_type_normalized", 1), ("sale_date", -1)],
                name="comp_hot",
            )
            self.db.properties.create_index([("location", "2dsphere")], name="location_2dsphere")
        except Exception:  # noqa: BLE001 - index creation must never break startup
            pass

    @staticmethod
    def _subject_summary(subject: dict[str, Any]) -> dict[str, Any]:
        subject_property = subject.get("property") or {}
        subject_assessment = subject.get("assessment") or {}
        subject_community = subject.get("community") or {}
        subject_lat, subject_lon = lat_lon((subject.get("location") or {}).get("coordinates"))
        return {
            "property_id": subject.get("property_id"),
            "address": (subject.get("address") or {}).get("full"),
            "city": subject.get("city"),
            "community": subject_community.get("name"),
            "community_code": subject_community.get("code"),
            "property_type_normalized": subject_property.get("property_type_normalized"),
            "assessed_value": subject_assessment.get("assessed_value"),
            "year_built": subject_property.get("year_built"),
            "land_size_sqm": subject_property.get("land_size_sqm"),
            "latitude": subject_lat,
            "longitude": subject_lon,
        }

    def find_ranked_comps(
        self,
        subject: dict[str, Any],
        limit: int,
        max_results_per_pass: int,
        *,
        same_community_only: bool = False,
        max_distance_km: float | None = None,
        max_sale_age_days: int | None = None,
        weights: ScoringWeights | None = None,
        annual_appreciation_rate: float = 0.0,
        use_geo: bool = True,
        true_sales_only: bool = True,
    ) -> dict[str, Any]:
        weights = weights or DEFAULT_WEIGHTS
        subject_property = subject.get("property") or {}
        subject_assessment = subject.get("assessment") or {}
        subject_community = subject.get("community") or {}
        subject_location = (subject.get("location") or {}).get("coordinates")
        subject_assessed_value = subject_assessment.get("assessed_value")
        subject_year_built = subject_property.get("year_built")
        subject_land_size = subject_property.get("land_size_sqm")
        subject_type = subject_property.get("property_type_normalized")
        subject_bedrooms = subject_property.get("bedrooms")
        subject_bathrooms = subject_property.get("bathrooms")
        subject_garage = subject_property.get("garage_count")

        if not subject_type or not subject_assessed_value:
            raise ValueError("Subject property is missing property_type_normalized or assessed_value.")

        seen_sale_ids: set[str] = set()
        ranked_candidates: list[dict[str, Any]] = []

        query_passes = (
            [QueryPass(label="same_community", same_community_only=True)]
            if same_community_only
            else QUERY_PASSES
        )

        for query_pass in query_passes:
            for base_profile in FILTER_PROFILES:
                profile = self._apply_overrides(
                    base_profile,
                    max_distance_km=max_distance_km,
                    max_sale_age_days=max_sale_age_days,
                )
                cutoff = datetime.now(UTC) - timedelta(days=profile.max_sale_age_days)
                value_floor = subject_assessed_value * (1 - profile.max_value_gap_ratio)
                value_ceiling = subject_assessed_value * (1 + profile.max_value_gap_ratio)

                query = {
                    "city": subject.get("city"),
                    "property_id": {"$ne": subject.get("property_id")},
                    "property_snapshot.property_type_normalized": subject_type,
                    "sale_date": {"$gte": cutoff.isoformat().replace("+00:00", "Z")},
                    "property_snapshot.assessed_value": {
                        "$gte": value_floor,
                        "$lte": value_ceiling,
                    },
                }
                if query_pass.same_community_only and subject_community.get("code"):
                    query["community.code"] = subject_community.get("code")
                if true_sales_only:
                    # Only genuine closed sales count as comps — exclude unsold listings
                    # and $0 placeholders (a top reason humans reject candidates). A real
                    # MLS feed would also filter transaction_type/status; this dataset
                    # holds closed sales, so a positive sale_price is the available guard.
                    query["sale_price"] = {"$gt": 0}

                for sale in self._iter_candidates(
                    query, profile, subject_location, max_results_per_pass, use_geo
                ):
                    sale_id = sale.get("sale_id")
                    if not sale_id or sale_id in seen_sale_ids:
                        continue

                    ranked = self._rank_candidate(
                        subject=subject,
                        subject_location=subject_location,
                        subject_assessed_value=subject_assessed_value,
                        subject_year_built=subject_year_built,
                        subject_land_size=subject_land_size,
                        subject_bedrooms=subject_bedrooms,
                        subject_bathrooms=subject_bathrooms,
                        subject_garage=subject_garage,
                        subject_community=subject_community,
                        sale=sale,
                        profile=profile,
                        query_pass=query_pass,
                        weights=weights,
                        annual_appreciation_rate=annual_appreciation_rate,
                    )
                    if ranked is None:
                        continue

                    seen_sale_ids.add(sale_id)
                    ranked_candidates.append(ranked)

                if len(ranked_candidates) >= limit * 4:
                    break
            if len(ranked_candidates) >= limit * 4:
                break

        ranked_candidates.sort(key=lambda item: item["score"], reverse=True)
        top = ranked_candidates[:limit]
        self._attach_property_addresses(top)
        subject_lat, subject_lon = lat_lon(subject_location)
        return {
            "subject": {
                "property_id": subject.get("property_id"),
                "address": (subject.get("address") or {}).get("full"),
                "city": subject.get("city"),
                "community": subject_community.get("name"),
                "property_type_normalized": subject_type,
                "assessed_value": subject_assessed_value,
                "year_built": subject_year_built,
                "land_size_sqm": subject_land_size,
                "bedrooms": subject_bedrooms,
                "bathrooms": subject_bathrooms,
                "garage_count": subject_garage,
                "latitude": subject_lat,
                "longitude": subject_lon,
            },
            "candidate_count": len(ranked_candidates),
            "returned_count": len(top),
            "applied_filters": {
                "limit": limit,
                "same_community_only": same_community_only,
                "max_distance_km": max_distance_km,
                "max_sale_age_days": max_sale_age_days,
                "max_results_per_pass": max_results_per_pass,
                "annual_appreciation_rate": annual_appreciation_rate,
                "geo_retrieval": bool(use_geo and subject_location),
                "true_sales_only": true_sales_only,
            },
            "analysis": summarize_value(top),
            "comparables": top,
        }

    def _iter_candidates(
        self,
        query: dict[str, Any],
        profile: FilterProfile,
        subject_location: list[float] | None,
        max_results_per_pass: int,
        use_geo: bool,
    ) -> list[dict[str, Any]]:
        """Fetch candidate sales for one filter pass.

        When the subject has coordinates and ``use_geo`` is set, use ``$geoNear``
        (nearest-first within the profile's max distance) over the sales 2dsphere
        index — better comp geography than recency-sorted scanning. Falls back to
        a recency-sorted ``find`` on any error (e.g. a missing geo index).
        """
        if use_geo and subject_location and len(subject_location) == 2:
            pipeline = [
                {
                    "$geoNear": {
                        "near": {"type": "Point", "coordinates": subject_location},
                        "distanceField": "_dist_m",
                        "spherical": True,
                        "maxDistance": profile.max_distance_km * 1000.0,
                        "key": "location",
                        "query": query,
                    }
                },
                {"$limit": max_results_per_pass},
                {"$project": {"_id": 0}},
            ]
            try:
                return list(self.db.sales.aggregate(pipeline))
            except Exception:  # noqa: BLE001 - fall back to non-geo retrieval
                pass
        return list(
            self.db.sales.find(query, {"_id": 0}).sort("sale_date", -1).limit(max_results_per_pass)
        )

    @staticmethod
    def _apply_overrides(
        base_profile: FilterProfile,
        *,
        max_distance_km: float | None,
        max_sale_age_days: int | None,
    ) -> FilterProfile:
        """Return a profile with caller overrides applied.

        Overrides only ever *tighten* the built-in profile (they take the
        ``min`` against the base value) so results stay deterministic and a
        caller can never accidentally widen past the engine's safe ceilings.
        """
        if max_distance_km is None and max_sale_age_days is None:
            return base_profile

        return FilterProfile(
            label=base_profile.label,
            max_sale_age_days=(
                min(base_profile.max_sale_age_days, max_sale_age_days)
                if max_sale_age_days is not None
                else base_profile.max_sale_age_days
            ),
            max_value_gap_ratio=base_profile.max_value_gap_ratio,
            max_land_gap_ratio=base_profile.max_land_gap_ratio,
            max_year_gap=base_profile.max_year_gap,
            max_distance_km=(
                min(base_profile.max_distance_km, max_distance_km)
                if max_distance_km is not None
                else base_profile.max_distance_km
            ),
        )

    def _attach_property_addresses(self, comparables: list[dict[str, Any]]) -> None:
        property_ids = [item.get("property_id") for item in comparables if item.get("property_id")]
        if not property_ids:
            return

        address_lookup: dict[str, str | None] = {}
        cursor = self.db.properties.find(
            {"property_id": {"$in": property_ids}},
            {"_id": 0, "property_id": 1, "address.full": 1},
        )
        for document in cursor:
            address_lookup[document.get("property_id")] = (document.get("address") or {}).get("full")

        for comparable in comparables:
            if comparable.get("address"):
                continue
            comparable["address"] = address_lookup.get(comparable.get("property_id"))

    def _rank_candidate(
        self,
        *,
        subject: dict[str, Any],
        subject_location: list[float] | None,
        subject_assessed_value: float,
        subject_year_built: int | None,
        subject_land_size: float | None,
        subject_bedrooms: float | None,
        subject_bathrooms: float | None,
        subject_garage: float | None,
        subject_community: dict[str, Any],
        sale: dict[str, Any],
        profile: FilterProfile,
        query_pass: QueryPass,
        weights: ScoringWeights,
        annual_appreciation_rate: float = 0.0,
    ) -> dict[str, Any] | None:
        w = weights
        snapshot = sale.get("property_snapshot") or {}
        candidate_location = (sale.get("location") or {}).get("coordinates")
        candidate_lat, candidate_lon = lat_lon(candidate_location)
        sale_datetime = parse_sale_datetime(sale.get("sale_date"))
        if sale_datetime is None:
            return None

        assessed_gap_ratio = safe_ratio_gap(
            subject_assessed_value,
            snapshot.get("assessed_value"),
        )
        if assessed_gap_ratio is None or assessed_gap_ratio > profile.max_value_gap_ratio:
            return None

        land_gap_ratio = safe_ratio_gap(subject_land_size, snapshot.get("land_size_sqm"))
        if land_gap_ratio is not None and land_gap_ratio > profile.max_land_gap_ratio:
            return None

        year_gap = None
        if subject_year_built is not None and snapshot.get("year_built") is not None:
            year_gap = abs(int(snapshot["year_built"]) - int(subject_year_built))
            if year_gap > profile.max_year_gap:
                return None

        distance_km = haversine_km(subject_location, candidate_location)
        if distance_km is not None and distance_km > profile.max_distance_km:
            return None

        recency_days = max(0, (datetime.now(UTC) - sale_datetime).days)

        # Optional structural gaps — only scored when the subject supplies them
        # (DB subjects have no bed/bath; manual/off-market subjects may).
        bed_gap = abs_gap(subject_bedrooms, snapshot.get("bedrooms"))
        bath_gap = abs_gap(subject_bathrooms, snapshot.get("bathrooms"))
        garage_gap = abs_gap(subject_garage, snapshot.get("garage_count"))

        score = w.base
        breakdown: dict[str, float] = {"base": w.base}

        def _pen(key: str, raw: float, cap: float) -> None:
            nonlocal score
            penalty = -clamp_penalty(raw, cap)
            breakdown[key] = round(penalty, 2)
            score += penalty

        if distance_km is not None:
            _pen("distance", distance_km * w.distance_per_km, w.distance_cap)
        else:
            breakdown["distance"] = -w.distance_missing_penalty
            score -= w.distance_missing_penalty
        _pen("recency", recency_days / 30.0 * w.recency_per_month, w.recency_cap)
        _pen("value_gap", assessed_gap_ratio * 100.0 * w.value_gap_factor, w.value_gap_cap)
        if land_gap_ratio is not None:
            _pen("land_gap", land_gap_ratio * 100.0 * w.land_gap_factor, w.land_gap_cap)
        if year_gap is not None:
            _pen("year_gap", year_gap * w.year_gap_per_year, w.year_gap_cap)
        if bed_gap is not None:
            _pen("bed_gap", bed_gap * w.bed_gap_per, w.bed_gap_cap)
        if bath_gap is not None:
            _pen("bath_gap", bath_gap * w.bath_gap_per, w.bath_gap_cap)
        if garage_gap is not None:
            _pen("garage_gap", garage_gap * w.garage_gap_per, w.garage_gap_cap)

        same_community = (sale.get("community") or {}).get("code") == subject_community.get("code")
        if same_community:
            breakdown["same_community"] = w.same_community_bonus
            score += w.same_community_bonus
        if query_pass.same_community_only:
            breakdown["same_community_pass"] = w.same_community_pass_bonus
            score += w.same_community_pass_bonus

        score = max(0.0, round(score, 2))

        # KV's trustworthy-comp checklist (from the underwriting call): type, within
        # ~3 km, sold within 12 months, age within 10 yr, size within 20%. We have no
        # living-area (GLA) field, so size uses the land-size gap as a documented proxy.
        kv_criteria = {
            "type": True,  # candidates are pre-filtered to the subject's type
            "within_3km": (distance_km <= 3.0) if distance_km is not None else None,
            "within_12mo": recency_days <= 365,
            "age_within_10yr": (year_gap <= 10) if year_gap is not None else None,
            "size_within_20pct_land_proxy": (land_gap_ratio <= 0.20) if land_gap_ratio is not None else None,
        }
        meets_kv_criteria = all(v for v in kv_criteria.values() if v is not None)

        sale_price = sale.get("sale_price")
        land = snapshot.get("land_size_sqm")
        price_per_land_sqm = round(sale_price / land, 2) if sale_price and land else None
        rate = annual_appreciation_rate or 0.0
        time_adjusted_price = (
            round(sale_price * ((1.0 + rate) ** (recency_days / 365.0))) if sale_price else None
        )

        reasons = []
        if distance_km is not None:
            reasons.append(f"{distance_km:.2f} km away")
        reasons.append(f"sold {recency_days} days ago")
        reasons.append(f"value gap {assessed_gap_ratio * 100:.1f}%")
        if same_community:
            reasons.append("same community")
        if year_gap is not None:
            reasons.append(f"year built gap {year_gap}")
        if bed_gap:
            reasons.append(f"{bed_gap:g} bd diff")
        if bath_gap:
            reasons.append(f"{bath_gap:g} ba diff")

        return {
            "sale_id": sale.get("sale_id"),
            "property_id": sale.get("property_id"),
            "address": snapshot.get("address"),
            "community": (sale.get("community") or {}).get("name"),
            "sale_date": sale.get("sale_date"),
            "sale_price": sale_price,
            "time_adjusted_price": time_adjusted_price,
            "price_per_land_sqm": price_per_land_sqm,
            "score": score,
            "distance_km": round(distance_km, 3) if distance_km is not None else None,
            "recency_days": recency_days,
            "latitude": candidate_lat,
            "longitude": candidate_lon,
            "assessed_value_gap_ratio": round(assessed_gap_ratio, 4),
            "land_size_gap_ratio": round(land_gap_ratio, 4) if land_gap_ratio is not None else None,
            "year_built_gap": year_gap,
            "bedrooms": snapshot.get("bedrooms"),
            "bathrooms": snapshot.get("bathrooms"),
            "garage_count": snapshot.get("garage_count"),
            "bedrooms_gap": bed_gap,
            "bathrooms_gap": bath_gap,
            "matched_profile": profile.label,
            "matched_query_pass": query_pass.label,
            "score_breakdown": breakdown,
            "kv_criteria": kv_criteria,
            "meets_kv_criteria": meets_kv_criteria,
            "reasons": reasons,
            "property_snapshot": snapshot,
        }


def main() -> None:
    args = parse_args()
    if not args.uri:
        raise SystemExit("MongoDB Atlas URI is required. Set MONGODB_URI or pass --uri.")

    service = CompRankingService(uri=args.uri, db_name=args.db)
    subject = service.get_subject_property(
        property_id=args.subject_property_id,
        address=args.subject_address,
    )
    result = service.find_ranked_comps(
        subject=subject,
        limit=args.limit,
        max_results_per_pass=args.max_results_per_pass,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()