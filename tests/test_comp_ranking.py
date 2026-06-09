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
    DEFAULT_WEIGHTS,
    FILTER_PROFILES,
    CompRankingService,
    QueryPass,
    ScoringWeights,
    abs_gap,
    address_tokens,
    clamp_penalty,
    haversine_km,
    lat_lon,
    normalize_whitespace,
    parse_sale_datetime,
    safe_ratio_gap,
    summarize_value,
    token_regex,
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


def _rank(engine, sale, profile=TIGHT, *, weights=DEFAULT_WEIGHTS, beds=None, baths=None,
          garage=None, rate=0.0):
    return engine._rank_candidate(
        subject=SUBJECT,
        subject_location=SUBJECT_LOCATION,
        subject_assessed_value=600000.0,
        subject_year_built=2000,
        subject_land_size=500.0,
        subject_bedrooms=beds,
        subject_bathrooms=baths,
        subject_garage=garage,
        subject_community=SUBJECT_COMMUNITY,
        sale=sale,
        profile=profile,
        query_pass=SAME_COMMUNITY,
        weights=weights,
        annual_appreciation_rate=rate,
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


# --------------------------------------------------------------------------- #
# New: weights, time-adjustment, $/sqm, bed/bath, search tokens, value summary
# --------------------------------------------------------------------------- #
def test_scoring_weights_from_dict_overrides_only_named():
    w = ScoringWeights.from_dict({"distance_per_km": 20.0, "bogus": 1.0})
    assert w.distance_per_km == 20.0
    assert w.recency_per_month == DEFAULT_WEIGHTS.recency_per_month  # untouched
    assert not hasattr(w, "bogus")


def test_higher_distance_weight_lowers_score(engine):
    far = _make_sale(location={"type": "Point", "coordinates": [-114.02, 51.0]})  # ~1.4 km
    base = _rank(engine, far)
    heavy = _rank(engine, far, weights=ScoringWeights.from_dict({"distance_per_km": 25.0}))
    assert heavy["score"] < base["score"]


def test_time_adjusted_price_and_ppsqm(engine):
    out = _rank(engine, _make_sale(), rate=0.10)
    assert out["price_per_land_sqm"] == pytest.approx(605000.0 / 510.0, abs=0.1)
    # positive appreciation over a positive age => time-adjusted >= nominal
    assert out["time_adjusted_price"] >= out["sale_price"]


def test_bedroom_gap_penalizes(engine):
    # subject 5 beds vs comp 4 beds (snapshot default) -> penalty when subject supplies beds
    with_beds = _rank(engine, _make_sale(snapshot={"bedrooms": 4}), beds=5)
    without = _rank(engine, _make_sale(snapshot={"bedrooms": 4}), beds=None)
    assert with_beds["score"] < without["score"]
    assert with_beds["bedrooms_gap"] == 1.0


def test_abs_gap():
    assert abs_gap(4, 6) == 2.0
    assert abs_gap(None, 6) is None
    assert abs_gap(4, None) is None


def test_address_tokens_and_token_regex():
    assert address_tokens("  120   deercrest  cl ") == ["120", "DEERCREST", "CL"]
    # full street-type word matches its abbreviation form
    assert "DR" in token_regex("DRIVE")
    # quadrant token is word-anchored (won't match inside LYNNWOOD)
    assert token_regex("NW").startswith(r"\b")


def test_summarize_value_confidence():
    comps = [
        {"sale_price": 600000, "recency_days": 30, "price_per_land_sqm": 1000, "time_adjusted_price": 605000},
        {"sale_price": 610000, "recency_days": 40, "price_per_land_sqm": 1010, "time_adjusted_price": 615000},
        {"sale_price": 590000, "recency_days": 50, "price_per_land_sqm": 990, "time_adjusted_price": 595000},
        {"sale_price": 605000, "recency_days": 60, "price_per_land_sqm": 1005, "time_adjusted_price": 610000},
        {"sale_price": 615000, "recency_days": 20, "price_per_land_sqm": 1020, "time_adjusted_price": 620000},
        {"sale_price": 600000, "recency_days": 35, "price_per_land_sqm": 1000, "time_adjusted_price": 606000},
    ]
    s = summarize_value(comps)
    assert s["count"] == 6
    assert s["min_price"] == 590000 and s["max_price"] == 615000
    assert s["confidence"]["level"] == "high"  # many comps, tight spread, recent


def test_summarize_value_empty():
    s = summarize_value([])
    assert s["count"] == 0 and s["confidence"]["level"] == "none"


# --------------------------------------------------------------------------- #
# New: KV criteria checklist + score breakdown
# --------------------------------------------------------------------------- #
def test_rank_includes_kv_criteria_and_breakdown(engine):
    out = _rank(engine, _make_sale())
    assert out["meets_kv_criteria"] is True
    kv = out["kv_criteria"]
    assert kv["type"] is True
    assert kv["within_3km"] is True
    assert kv["within_12mo"] is True
    assert kv["age_within_10yr"] is True
    # score_breakdown sums to the (un-floored) score
    assert "base" in out["score_breakdown"]
    assert abs(sum(out["score_breakdown"].values()) - out["score"]) < 0.1


def test_kv_criteria_fails_when_far(engine):
    far = _make_sale(location={"type": "Point", "coordinates": [-114.0, 51.03]})  # ~3.3 km
    out = _rank(engine, far)
    assert out is not None  # still inside the tight 4 km filter
    assert out["kv_criteria"]["within_3km"] is False
    assert out["meets_kv_criteria"] is False


def test_summarize_value_kv_match_count():
    comps = [
        {"sale_price": 600000, "recency_days": 30, "meets_kv_criteria": True},
        {"sale_price": 610000, "recency_days": 40, "meets_kv_criteria": False},
    ]
    assert summarize_value(comps)["kv_match_count"] == 1
