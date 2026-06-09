"""DB-free unit tests for the comp-ranking engine.

These exercise the pure helpers and the scoring/filter logic of
``comp_ranking_service`` without any MongoDB connection. ``_rank_candidate`` is
an instance method that never touches ``self``/the DB, so we build an instance
via ``object.__new__`` to bypass the Mongo-connecting ``__init__``.
"""

from __future__ import annotations

import math

import pytest

from comp_ranking_service import (
    FILTER_PROFILES,
    CompRankingService,
    QueryPass,
    clamp_penalty,
    haversine_km,
    lat_lon,
    normalize_whitespace,
    parse_sale_datetime,
    safe_ratio_gap,
)

TIGHT = FILTER_PROFILES[0]
SAME_COMMUNITY = QueryPass(label="same_community", same_community_only=True)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_haversine_known_distance():
    # One degree of latitude at the equator ≈ 111.195 km (radius 6371.0088).
    assert haversine_km([0.0, 0.0], [0.0, 1.0]) == pytest.approx(111.195, abs=0.5)


def test_haversine_identical_points_is_zero():
    assert haversine_km([-114.0, 51.0], [-114.0, 51.0]) == pytest.approx(0.0, abs=1e-9)


def test_haversine_none_inputs():
    assert haversine_km(None, [0.0, 1.0]) is None
    assert haversine_km([0.0, 1.0], None) is None


def test_safe_ratio_gap():
    assert safe_ratio_gap(100.0, 110.0) == pytest.approx(0.1)
    assert safe_ratio_gap(100.0, 90.0) == pytest.approx(0.1)
    assert safe_ratio_gap(0, 50.0) is None  # zero subject guards against div-by-zero
    assert safe_ratio_gap(None, 50.0) is None
    assert safe_ratio_gap(100.0, None) is None


def test_clamp_penalty():
    assert clamp_penalty(50.0, 35.0) == 35.0
    assert clamp_penalty(10.0, 35.0) == 10.0


def test_normalize_whitespace():
    assert normalize_whitespace("  10   main  st ") == "10 MAIN ST"


def test_parse_sale_datetime():
    dt = parse_sale_datetime("2025-06-16T00:00:00Z")
    assert dt is not None and dt.year == 2025 and dt.tzinfo is not None
    assert parse_sale_datetime(None) is None


def test_lat_lon_splits_geojson_pair():
    assert lat_lon([-114.0, 51.0]) == (51.0, -114.0)
    assert lat_lon(None) == (None, None)
    assert lat_lon([1.0]) == (None, None)


# --------------------------------------------------------------------------- #
# _apply_overrides: may only tighten, never widen
# --------------------------------------------------------------------------- #
def test_apply_overrides_noop_returns_base():
    assert (
        CompRankingService._apply_overrides(TIGHT, max_distance_km=None, max_sale_age_days=None)
        is TIGHT
    )


def test_apply_overrides_tightens():
    out = CompRankingService._apply_overrides(TIGHT, max_distance_km=2.0, max_sale_age_days=100)
    assert out.max_distance_km == 2.0
    assert out.max_sale_age_days == 100


def test_apply_overrides_cannot_widen():
    out = CompRankingService._apply_overrides(TIGHT, max_distance_km=999.0, max_sale_age_days=99999)
    assert out.max_distance_km == TIGHT.max_distance_km
    assert out.max_sale_age_days == TIGHT.max_sale_age_days


# --------------------------------------------------------------------------- #
# _subject_summary: flattens nested property doc
# --------------------------------------------------------------------------- #
def test_subject_summary_flattens():
    doc = {
        "property_id": "p1",
        "address": {"full": "10 MAIN ST"},
        "city": "Calgary",
        "community": {"code": "ABC", "name": "ALPHA"},
        "assessment": {"assessed_value": 600000.0},
        "property": {"property_type_normalized": "detached", "year_built": 2000, "land_size_sqm": 500.0},
    }
    out = CompRankingService._subject_summary(doc)
    assert out["property_id"] == "p1"
    assert out["address"] == "10 MAIN ST"
    assert out["community"] == "ALPHA"
    assert out["community_code"] == "ABC"
    assert out["assessed_value"] == 600000.0
    assert out["property_type_normalized"] == "detached"


# --------------------------------------------------------------------------- #
# _rank_candidate: filters + scoring (no DB)
# --------------------------------------------------------------------------- #
@pytest.fixture
def engine() -> CompRankingService:
    return object.__new__(CompRankingService)  # bypass Mongo-connecting __init__


SUBJECT = {"property_id": "subj", "address": {"full": "1 SUBJECT RD"}}
SUBJECT_LOCATION = [-114.0, 51.0]
SUBJECT_COMMUNITY = {"code": "ABC", "name": "ALPHA"}


def _make_sale(**overrides):
    sale = {
        "sale_id": "s1",
        "property_id": "p2",
        "sale_date": "2026-05-01T00:00:00Z",
        "sale_price": 605000,
        "community": {"code": "ABC", "name": "ALPHA"},
        "location": {"type": "Point", "coordinates": [-114.0, 51.0]},
        "property_snapshot": {
            "property_type_normalized": "detached",
            "year_built": 2001,
            "land_size_sqm": 510.0,
            "assessed_value": 605000.0,
            "address": "2 COMP RD",
        },
    }
    sale["property_snapshot"].update(overrides.pop("snapshot", {}))
    sale.update(overrides)
    return sale


def _rank(engine, sale, profile=TIGHT):
    return engine._rank_candidate(
        subject=SUBJECT,
        subject_location=SUBJECT_LOCATION,
        subject_assessed_value=600000.0,
        subject_year_built=2000,
        subject_land_size=500.0,
        subject_community=SUBJECT_COMMUNITY,
        sale=sale,
        profile=profile,
        query_pass=SAME_COMMUNITY,
    )


def test_rank_good_candidate(engine):
    out = _rank(engine, _make_sale())
    assert out is not None
    assert out["sale_id"] == "s1"
    assert out["latitude"] == 51.0 and out["longitude"] == -114.0
    # A near-perfect same-community comp scores high. Note: scores are NOT capped
    # at 100 — the same-community (+12) and same-community-pass (+4) bonuses are
    # additive on the 100 base, so a strong comp can exceed 100. Only the floor
    # is clamped (max(0.0, ...)).
    assert out["score"] > 90.0
    assert "same community" in out["reasons"]
    assert out["distance_km"] == pytest.approx(0.0, abs=1e-6)


def test_rank_rejects_value_gap_over_profile(engine):
    assert _rank(engine, _make_sale(snapshot={"assessed_value": 800000.0})) is None


def test_rank_rejects_too_far(engine):
    far = _make_sale(location={"type": "Point", "coordinates": [-114.3, 51.3]})
    assert _rank(engine, far) is None


def test_rank_rejects_year_gap_over_profile(engine):
    assert _rank(engine, _make_sale(snapshot={"year_built": 1985})) is None


def test_rank_rejects_land_gap_over_profile(engine):
    assert _rank(engine, _make_sale(snapshot={"land_size_sqm": 900.0})) is None


def test_rank_rejects_missing_sale_date(engine):
    assert _rank(engine, _make_sale(sale_date=None)) is None


def test_rank_same_community_scores_higher(engine):
    same = _rank(engine, _make_sale())
    diff = _rank(engine, _make_sale(community={"code": "XYZ", "name": "OTHER"}))
    assert same is not None and diff is not None
    assert same["score"] > diff["score"]
