"""Streamlit demo UI for the KV Calgary comp-analysis assistant.

A thin HTTP client of the existing FastAPI service (scripts/api_main.py). It does
no ranking itself — it resolves a subject, calls ``POST /rank-comps``, and
presents the ranked shortlist as a table, a map, a price band, and a CSV export.

Run (API must be running separately):
    .venv/Scripts/python.exe -m streamlit run app/streamlit_app.py

Config (env or .env): API_BASE_URL (default http://localhost:8000), API_KEY (optional).
"""

from __future__ import annotations

import os
from statistics import median
from typing import Any

import httpx
import pandas as pd
import streamlit as st

try:  # optional: load .env for local dev so API_BASE_URL/API_KEY are picked up
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY") or None
HEADERS = {"x-api-key": API_KEY} if API_KEY else {}
TIMEOUT = httpx.Timeout(30.0)

st.set_page_config(page_title="KV Comp Analysis", page_icon="🏠", layout="wide")


# --------------------------------------------------------------------------- #
# API client helpers
# --------------------------------------------------------------------------- #
def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = client.get(f"{API_BASE_URL}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = client.post(f"{API_BASE_URL}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()


def check_health() -> tuple[bool, str]:
    try:
        body = _get("/health", {})
        return bool(body.get("mongo_connected")), body.get("db", "?")
    except Exception as exc:  # noqa: BLE001 - surface any connectivity issue in the badge
        return False, str(exc)


def search_subjects(query: str, limit: int) -> list[dict[str, Any]]:
    return _get("/subject-search", {"q": query, "limit": limit}).get("results", [])


def rank_comps(payload: dict[str, Any]) -> dict[str, Any]:
    return _post("/rank-comps", payload)


def fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------- #
# Sidebar: connection + tuning controls
# --------------------------------------------------------------------------- #
st.sidebar.title("KV Comp Analysis")
st.sidebar.caption(f"API: `{API_BASE_URL}`")

healthy, db_or_err = check_health()
if healthy:
    st.sidebar.success(f"API connected · db `{db_or_err}`")
else:
    st.sidebar.error("API unreachable")
    st.sidebar.caption(db_or_err)

st.sidebar.divider()
st.sidebar.subheader("Ranking controls")
limit = st.sidebar.slider("Number of comps", min_value=1, max_value=50, value=10)
same_community_only = st.sidebar.checkbox("Same community only", value=False)
use_max_distance = st.sidebar.checkbox("Cap distance (km)", value=False)
max_distance_km = (
    st.sidebar.number_input("Max distance (km)", min_value=0.5, max_value=15.0, value=5.0, step=0.5)
    if use_max_distance
    else None
)
use_max_age = st.sidebar.checkbox("Cap sale age (days)", value=False)
max_sale_age_days = (
    st.sidebar.number_input("Max sale age (days)", min_value=30, max_value=730, value=365, step=30)
    if use_max_age
    else None
)
st.sidebar.caption("Tuning flags only ever *tighten* the engine's built-in profiles.")


# --------------------------------------------------------------------------- #
# Main: subject selection
# --------------------------------------------------------------------------- #
st.title("🏠 Calgary Comparable-Sales Assistant")
st.caption("Resolve a subject property, then retrieve a ranked shortlist of plausible comparable sales.")

if "subject" not in st.session_state:
    st.session_state.subject = None

col_q, col_btn = st.columns([4, 1])
with col_q:
    query = st.text_input(
        "Search subject by address or property_id",
        placeholder="e.g. 120 DEERCREST CL SE   or   calgary_2026...",
    )
with col_btn:
    st.write("")
    st.write("")
    search_clicked = st.button("Search", use_container_width=True)

if search_clicked and query.strip():
    try:
        results = search_subjects(query.strip(), limit=10)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Search failed: {exc}")
        results = []
    st.session_state.search_results = results
    if not results:
        st.warning("No matching subject properties found.")

results = st.session_state.get("search_results", [])
if results:
    def _label(r: dict[str, Any]) -> str:
        return (
            f"{r.get('address') or '(no address)'} · {r.get('community') or '—'} · "
            f"{r.get('property_type_normalized') or '—'} · {fmt_money(r.get('assessed_value'))}"
        )

    chosen = st.selectbox(
        "Select the subject property",
        options=results,
        format_func=_label,
    )
    if st.button("Use this subject", type="primary"):
        st.session_state.subject = chosen


# --------------------------------------------------------------------------- #
# Subject card + comp retrieval
# --------------------------------------------------------------------------- #
subject = st.session_state.get("subject")
if subject:
    st.divider()
    st.subheader("Subject property")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Assessed value", fmt_money(subject.get("assessed_value")))
    c2.metric("Year built", subject.get("year_built") or "—")
    c3.metric("Land size (sqm)", subject.get("land_size_sqm") or "—")
    c4.metric("Type", subject.get("property_type_normalized") or "—")
    st.caption(
        f"**{subject.get('address') or '(no address)'}** · {subject.get('community') or '—'} · "
        f"{subject.get('city') or '—'} · `{subject.get('property_id')}`"
    )

    if st.button("🔍 Find comparable sales", type="primary"):
        payload: dict[str, Any] = {
            "subject_property_id": subject.get("property_id"),
            "limit": int(limit),
            "same_community_only": bool(same_community_only),
            "max_results_per_pass": 250,
        }
        if max_distance_km is not None:
            payload["max_distance_km"] = float(max_distance_km)
        if max_sale_age_days is not None:
            payload["max_sale_age_days"] = int(max_sale_age_days)

        try:
            with st.spinner("Ranking comparable sales…"):
                st.session_state.result = rank_comps(payload)
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = exc.response.text
            st.error(f"Ranking failed ({exc.response.status_code}): {detail}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Ranking failed: {exc}")


# --------------------------------------------------------------------------- #
# Results: metrics, price band, table, map, export
# --------------------------------------------------------------------------- #
result = st.session_state.get("result")
if result and result.get("subject", {}).get("property_id") == (subject or {}).get("property_id"):
    comps = result.get("comparables", [])
    st.divider()
    st.subheader("Ranked comparable sales")

    m1, m2, m3 = st.columns(3)
    m1.metric("Candidates found", result.get("candidate_count", 0))
    m2.metric("Comps returned", result.get("returned_count", 0))

    prices = [c["sale_price"] for c in comps if isinstance(c.get("sale_price"), (int, float))]
    if prices:
        m3.metric("Median comp price", fmt_money(median(prices)))
        st.info(
            f"**Implied value band** from {len(prices)} comps: "
            f"{fmt_money(min(prices))} – {fmt_money(max(prices))} "
            f"(median {fmt_money(median(prices))})"
        )

    if not comps:
        st.warning("No comparable sales matched even the widest filter profile.")
    else:
        table_cols = [
            "score",
            "address",
            "community",
            "sale_date",
            "sale_price",
            "distance_km",
            "recency_days",
            "assessed_value_gap_ratio",
            "year_built_gap",
            "matched_profile",
            "reasons",
        ]
        df = pd.DataFrame(comps)
        df_display = df[[c for c in table_cols if c in df.columns]].copy()
        if "reasons" in df_display.columns:
            df_display["reasons"] = df_display["reasons"].apply(
                lambda r: ", ".join(r) if isinstance(r, list) else r
            )
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        # Map: subject (one point) + comps. st.map wants latitude/longitude columns.
        map_rows: list[dict[str, Any]] = []
        subj = result.get("subject", {})
        if subj.get("latitude") is not None and subj.get("longitude") is not None:
            map_rows.append(
                {"latitude": subj["latitude"], "longitude": subj["longitude"],
                 "color": "#1f77b4", "size": 80}
            )
        for c in comps:
            if c.get("latitude") is not None and c.get("longitude") is not None:
                map_rows.append(
                    {"latitude": c["latitude"], "longitude": c["longitude"],
                     "color": "#d62728", "size": 40}
                )
        if map_rows:
            st.caption("Map · blue = subject, red = comparable sales")
            st.map(pd.DataFrame(map_rows), latitude="latitude", longitude="longitude",
                   color="color", size="size")

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download shortlist (CSV)",
            data=csv,
            file_name=f"comps_{subject.get('property_id')}.csv",
            mime="text/csv",
        )

        with st.expander("Raw API response (JSON)"):
            st.json(result)
