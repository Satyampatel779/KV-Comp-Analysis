"""Shared, dependency-free comp-analysis helpers.

Pure functions (stdlib ``statistics`` only — no DB, no clock, no network) so that
**both** the engine (scripts/comp_ranking_service.py) and the Streamlit UI import
the *same* value-band + confidence logic. This is the single source of truth for
the implied value band and confidence scoring; nothing should re-implement it.
"""

from __future__ import annotations

import statistics
from typing import Any


def summarize_value(comparables: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute an implied value band + a heuristic confidence over a comp set.

    Driven entirely by the comp dicts passed in, so the UI can recompute it after
    a user excludes individual comps and get results identical to the engine.
    """
    prices = [c["sale_price"] for c in comparables if isinstance(c.get("sale_price"), (int, float))]
    n = len(prices)
    if n == 0:
        return {
            "count": 0, "median_price": None, "min_price": None, "max_price": None,
            "price_per_land_sqm_median": None, "time_adjusted_median": None,
            "median_recency_days": None, "kv_match_count": 0,
            "confidence": {"score": 0, "level": "none", "factors": ["no comparable sales"]},
        }

    mean = sum(prices) / n
    stdev = statistics.pstdev(prices) if n > 1 else 0.0
    cv = (stdev / mean) if mean else 0.0
    ppsqm = [c["price_per_land_sqm"] for c in comparables if isinstance(c.get("price_per_land_sqm"), (int, float))]
    taj = [c["time_adjusted_price"] for c in comparables if isinstance(c.get("time_adjusted_price"), (int, float))]
    recencies = [c["recency_days"] for c in comparables if isinstance(c.get("recency_days"), (int, float))]
    median_recency = statistics.median(recencies) if recencies else None

    score = 100.0
    factors: list[str] = []
    if n < 3:
        score -= 45; factors.append(f"only {n} comp(s)")
    elif n < 6:
        score -= 20; factors.append(f"{n} comps")
    if cv > 0.25:
        score -= 30; factors.append(f"wide price spread ({cv * 100:.0f}% CV)")
    elif cv > 0.15:
        score -= 15; factors.append(f"moderate spread ({cv * 100:.0f}% CV)")
    if median_recency is not None and median_recency > 365:
        score -= 20; factors.append("stale sales (>1yr median)")
    elif median_recency is not None and median_recency > 270:
        score -= 10; factors.append("aging sales")
    score = max(0, round(score))
    level = "high" if score >= 75 else "medium" if score >= 50 else "low"

    return {
        "count": n,
        "median_price": round(statistics.median(prices)),
        "min_price": round(min(prices)),
        "max_price": round(max(prices)),
        "price_per_land_sqm_median": round(statistics.median(ppsqm), 2) if ppsqm else None,
        "time_adjusted_median": round(statistics.median(taj)) if taj else None,
        "median_recency_days": int(median_recency) if median_recency is not None else None,
        "kv_match_count": sum(1 for c in comparables if c.get("meets_kv_criteria")),
        "confidence": {"score": score, "level": level, "factors": factors or ["solid comp set"]},
    }
