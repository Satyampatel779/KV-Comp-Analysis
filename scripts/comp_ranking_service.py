from __future__ import annotations

import argparse
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
        # 2) normalized "contains" address match
        if len(ordered) < limit:
            add(
                self.db.properties.find(
                    {"address.full": {"$regex": re.escape(normalized), "$options": "i"}},
                    projection,
                ).limit(limit * 3)
            )
        # 3) exact property_id match
        if len(ordered) < limit:
            add(self.db.properties.find({"property_id": query_text}, projection).limit(limit))

        return ordered[:limit]

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
    ) -> dict[str, Any]:
        subject_property = subject.get("property") or {}
        subject_assessment = subject.get("assessment") or {}
        subject_community = subject.get("community") or {}
        subject_location = (subject.get("location") or {}).get("coordinates")
        subject_assessed_value = subject_assessment.get("assessed_value")
        subject_year_built = subject_property.get("year_built")
        subject_land_size = subject_property.get("land_size_sqm")
        subject_type = subject_property.get("property_type_normalized")

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

                sales_cursor = (
                    self.db.sales.find(query, {"_id": 0})
                    .sort("sale_date", -1)
                    .limit(max_results_per_pass)
                )
                for sale in sales_cursor:
                    sale_id = sale.get("sale_id")
                    if not sale_id or sale_id in seen_sale_ids:
                        continue

                    ranked = self._rank_candidate(
                        subject=subject,
                        subject_location=subject_location,
                        subject_assessed_value=subject_assessed_value,
                        subject_year_built=subject_year_built,
                        subject_land_size=subject_land_size,
                        subject_community=subject_community,
                        sale=sale,
                        profile=profile,
                        query_pass=query_pass,
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
        self._attach_property_addresses(ranked_candidates[:limit])
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
                "latitude": subject_lat,
                "longitude": subject_lon,
            },
            "candidate_count": len(ranked_candidates),
            "returned_count": min(limit, len(ranked_candidates)),
            "applied_filters": {
                "limit": limit,
                "same_community_only": same_community_only,
                "max_distance_km": max_distance_km,
                "max_sale_age_days": max_sale_age_days,
                "max_results_per_pass": max_results_per_pass,
            },
            "comparables": ranked_candidates[:limit],
        }

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
        subject_community: dict[str, Any],
        sale: dict[str, Any],
        profile: FilterProfile,
        query_pass: QueryPass,
    ) -> dict[str, Any] | None:
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

        score = 100.0
        if distance_km is not None:
            score -= clamp_penalty(distance_km * 9.0, 35.0)
        else:
            score -= 20.0

        score -= clamp_penalty(recency_days / 30.0 * 2.0, 22.0)
        score -= clamp_penalty(assessed_gap_ratio * 100.0 * 0.55, 22.0)

        if land_gap_ratio is not None:
            score -= clamp_penalty(land_gap_ratio * 100.0 * 0.22, 12.0)
        if year_gap is not None:
            score -= clamp_penalty(year_gap * 0.6, 12.0)

        same_community = (sale.get("community") or {}).get("code") == subject_community.get("code")
        if same_community:
            score += 12.0

        if query_pass.same_community_only:
            score += 4.0

        score = max(0.0, round(score, 2))

        reasons = []
        if distance_km is not None:
            reasons.append(f"{distance_km:.2f} km away")
        reasons.append(f"sold {recency_days} days ago")
        reasons.append(f"value gap {assessed_gap_ratio * 100:.1f}%")
        if same_community:
            reasons.append("same community")
        if year_gap is not None:
            reasons.append(f"year built gap {year_gap}")

        return {
            "sale_id": sale.get("sale_id"),
            "property_id": sale.get("property_id"),
            "address": snapshot.get("address"),
            "community": (sale.get("community") or {}).get("name"),
            "sale_date": sale.get("sale_date"),
            "sale_price": sale.get("sale_price"),
            "score": score,
            "distance_km": round(distance_km, 3) if distance_km is not None else None,
            "recency_days": recency_days,
            "latitude": candidate_lat,
            "longitude": candidate_lon,
            "assessed_value_gap_ratio": round(assessed_gap_ratio, 4),
            "land_size_gap_ratio": round(land_gap_ratio, 4) if land_gap_ratio is not None else None,
            "year_built_gap": year_gap,
            "matched_profile": profile.label,
            "matched_query_pass": query_pass.label,
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