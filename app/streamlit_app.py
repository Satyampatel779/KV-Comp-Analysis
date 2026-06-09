"""Streamlit demo UI for the KV Calgary comp-analysis assistant.

A thin HTTP client of the existing FastAPI service (scripts/api_main.py). It does
no ranking itself — it resolves a subject, calls ``POST /rank-comps``, and
presents the ranked shortlist as a table, a map, a price band, and a CSV export.

Run (API must be running separately):
    .venv/Scripts/python.exe -m streamlit run app/streamlit_app.py

Config (env or .env): API_BASE_URL (default http://localhost:8000), API_KEY (optional).
"""

from __future__ import annotations

import math
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
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or None
HEADERS = {"x-api-key": API_KEY} if API_KEY else {}
TIMEOUT = httpx.Timeout(30.0)
LLM_TIMEOUT = httpx.Timeout(90.0)  # LLM calls can take several seconds

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


def check_health() -> tuple[bool, str, bool]:
    try:
        body = _get("/health", {})
        return bool(body.get("mongo_connected")), body.get("db", "?"), bool(body.get("llm_configured"))
    except Exception as exc:  # noqa: BLE001 - surface any connectivity issue in the badge
        return False, str(exc), False


def search_subjects(query: str, limit: int) -> list[dict[str, Any]]:
    return _get("/subject-search", {"q": query, "limit": limit}).get("results", [])


def rank_comps(payload: dict[str, Any]) -> dict[str, Any]:
    return _post("/rank-comps", payload)


def ask_llm(payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=LLM_TIMEOUT, headers=HEADERS) as client:
        resp = client.post(f"{API_BASE_URL}/ask", json=payload)
        resp.raise_for_status()
        return resp.json()


def fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------- #
# Imagery helpers — no-key by default (aerial + clickable links); Street View
# Static photo only if GOOGLE_MAPS_API_KEY is set.
# --------------------------------------------------------------------------- #
def google_maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def street_view_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"


def aerial_image_url(lat: float, lon: float, meters: float = 130.0, size: int = 500) -> str:
    """Esri World Imagery static export (no API key). Square bbox in meters."""
    dlat = meters / 111_320.0
    dlon = meters / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    minx, miny, maxx, maxy = lon - dlon, lat - dlat, lon + dlon, lat + dlat
    return (
        "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/"
        f"MapServer/export?bbox={minx},{miny},{maxx},{maxy}"
        f"&bboxSR=4326&imageSR=3857&size={size},{size}&format=jpg&f=image"
    )


def street_view_static_url(lat: float, lon: float, size: str = "600x300") -> str | None:
    if not GOOGLE_MAPS_API_KEY:
        return None
    return (
        f"https://maps.googleapis.com/maps/api/streetview?size={size}"
        f"&location={lat},{lon}&fov=80&key={GOOGLE_MAPS_API_KEY}"
    )


def has_coords(obj: dict[str, Any]) -> bool:
    return obj.get("latitude") is not None and obj.get("longitude") is not None


# --------------------------------------------------------------------------- #
# Sidebar: connection + tuning controls
# --------------------------------------------------------------------------- #
st.sidebar.title("KV Comp Analysis")
st.sidebar.caption(f"API: `{API_BASE_URL}`")

healthy, db_or_err, llm_ready = check_health()
if healthy:
    st.sidebar.success(f"API connected · db `{db_or_err}`")
    st.sidebar.caption(f"LLM assistant: {'✅ ready' if llm_ready else '⚠️ not configured'}")
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

    if has_coords(subject):
        s_lat, s_lon = subject["latitude"], subject["longitude"]
        img_l, img_r = st.columns(2)
        img_l.image(aerial_image_url(s_lat, s_lon), caption="Aerial view", use_container_width=True)
        sv_url = street_view_static_url(s_lat, s_lon)
        if sv_url:
            img_r.image(sv_url, caption="Street View", use_container_width=True)
        else:
            img_r.caption("Add GOOGLE_MAPS_API_KEY to show an inline Street View photo.")
        st.markdown(
            f"[📍 Open in Google Maps]({google_maps_link(s_lat, s_lon)}) · "
            f"[🏠 Street View]({street_view_link(s_lat, s_lon)})"
        )
    else:
        st.caption("No coordinates available for this property — imagery unavailable.")

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
            "map",
            "street_view",
            "reasons",
        ]
        df = pd.DataFrame(comps)
        if {"latitude", "longitude"}.issubset(df.columns):
            coord_ok = df["latitude"].notna() & df["longitude"].notna()
            df["map"] = None
            df["street_view"] = None
            df.loc[coord_ok, "map"] = df[coord_ok].apply(
                lambda r: google_maps_link(r["latitude"], r["longitude"]), axis=1
            )
            df.loc[coord_ok, "street_view"] = df[coord_ok].apply(
                lambda r: street_view_link(r["latitude"], r["longitude"]), axis=1
            )
        df_display = df[[c for c in table_cols if c in df.columns]].copy()
        if "reasons" in df_display.columns:
            df_display["reasons"] = df_display["reasons"].apply(
                lambda r: ", ".join(r) if isinstance(r, list) else r
            )
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "sale_price": st.column_config.NumberColumn("Sale price", format="$%d"),
                "map": st.column_config.LinkColumn("Map", display_text="📍"),
                "street_view": st.column_config.LinkColumn("Street View", display_text="🏠"),
            },
        )

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

    # ----------------------------------------------------------------- #
    # LLM assistant: grounded Q&A + comp memo (calls POST /ask)
    # ----------------------------------------------------------------- #
    st.divider()
    st.subheader("🤖 Ask the assistant")

    subject_id = subject.get("property_id")
    if st.session_state.get("chat_subject") != subject_id:
        st.session_state.chat = []
        st.session_state.chat_subject = subject_id

    if not llm_ready:
        st.info(
            "LLM assistant isn't configured. Set GROQ_API_KEY in the API "
            "environment to enable grounded Q&A about this property."
        )
    else:
        def run_ask(question: str | None = None, mode: str = "qa") -> None:
            payload: dict[str, Any] = {
                "subject_property_id": subject_id,
                "mode": mode,
                "limit": int(limit),
                "same_community_only": bool(same_community_only),
            }
            if mode == "qa":
                payload["question"] = question
            if max_distance_km is not None:
                payload["max_distance_km"] = float(max_distance_km)
            if max_sale_age_days is not None:
                payload["max_sale_age_days"] = int(max_sale_age_days)
            label = question if mode == "qa" else "📝 Generate comp memo"
            try:
                with st.spinner("Thinking…"):
                    resp = ask_llm(payload)
                st.session_state.chat.append(("user", label))
                st.session_state.chat.append(("assistant", resp.get("answer", "")))
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get("detail", "")
                except Exception:  # noqa: BLE001
                    detail = exc.response.text
                st.session_state.chat.append(
                    ("assistant", f"⚠️ Error {exc.response.status_code}: {detail}")
                )
            except Exception as exc:  # noqa: BLE001
                st.session_state.chat.append(("assistant", f"⚠️ Error: {exc}"))

        for role, msg in st.session_state.get("chat", []):
            with st.chat_message(role):
                st.markdown(msg)

        suggestions = [
            "What is a fair offer range for the subject?",
            "Why is the top comp ranked first?",
            "Which comps are the weakest, and why?",
        ]
        cols = st.columns(len(suggestions) + 1)
        for i, sug in enumerate(suggestions):
            if cols[i].button(sug, key=f"sug_{i}", use_container_width=True):
                run_ask(question=sug, mode="qa")
                st.rerun()
        if cols[-1].button("📝 Comp memo", key="memo_btn", use_container_width=True):
            run_ask(mode="summary")
            st.rerun()

        if prompt := st.chat_input("Ask about this property…"):
            run_ask(question=prompt, mode="qa")
            st.rerun()
