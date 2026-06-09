"""Streamlit demo UI for the KV Calgary comp-analysis assistant.

A thin HTTP client of the FastAPI service (scripts/api_main.py). It resolves a
subject (DB lookup, tolerant search, or manual/off-market entry), calls the
ranking + LLM endpoints, and presents an interactive analysis: live re-ranking
with tunable weights, an implied value band with confidence, an interactive map,
charts, a PDF comp report, and a streaming grounded chat.

Run (API must be running separately):
    .venv/Scripts/python.exe -m streamlit run app/streamlit_app.py

Config (env or .env): API_BASE_URL (default http://localhost:8000),
API_KEY (optional), GOOGLE_MAPS_API_KEY (optional, enables Street View photos +
satellite pins).
"""

from __future__ import annotations

import io
import json
import math
import os
import statistics
from typing import Any

import altair as alt
import httpx
import pandas as pd
import pydeck as pdk
import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    _HAS_FPDF = True
except ImportError:
    _HAS_FPDF = False

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY") or None
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or None
HEADERS = {"x-api-key": API_KEY} if API_KEY else {}
TIMEOUT = httpx.Timeout(30.0)
LLM_TIMEOUT = httpx.Timeout(120.0)

st.set_page_config(page_title="KV Comp Analysis", page_icon="🏠", layout="wide")


# --------------------------------------------------------------------------- #
# API client (cached where it helps responsiveness)
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
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), False


@st.cache_data(ttl=300, show_spinner=False)
def cached_search(query: str, limit: int) -> list[dict[str, Any]]:
    return _get("/subject-search", {"q": query, "limit": limit}).get("results", [])


@st.cache_data(ttl=120, show_spinner=False)
def cached_rank(payload_json: str) -> dict[str, Any]:
    return _post("/rank-comps", json.loads(payload_json))


def ask_llm_stream(payload: dict[str, Any]):
    with httpx.Client(timeout=LLM_TIMEOUT, headers=HEADERS) as client:
        with client.stream("POST", f"{API_BASE_URL}/ask/stream", json=payload) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_text():
                if chunk:
                    yield chunk


def fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------- #
# Imagery — no-key aerial + pin; Street View / satellite pin if a Google key set
# --------------------------------------------------------------------------- #
def google_maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def street_view_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"


def esri_aerial_url(lat: float, lon: float, meters: float = 130.0, size: int = 500) -> str:
    dlat = meters / 111_320.0
    dlon = meters / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    minx, miny, maxx, maxy = lon - dlon, lat - dlat, lon + dlon, lat + dlat
    return (
        "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/"
        f"MapServer/export?bbox={minx},{miny},{maxx},{maxy}"
        f"&bboxSR=4326&imageSR=3857&size={size},{size}&format=jpg&f=image"
    )


def google_satellite_pin_url(lat: float, lon: float, size: str = "600x400") -> str | None:
    if not GOOGLE_MAPS_API_KEY:
        return None
    return (
        f"https://maps.googleapis.com/maps/api/staticmap?center={lat},{lon}&zoom=19"
        f"&size={size}&maptype=satellite&markers=color:red%7C{lat},{lon}&key={GOOGLE_MAPS_API_KEY}"
    )


def street_view_static_url(lat: float, lon: float, size: str = "600x300") -> str | None:
    if not GOOGLE_MAPS_API_KEY:
        return None
    return (
        f"https://maps.googleapis.com/maps/api/streetview?size={size}"
        f"&location={lat},{lon}&fov=80&key={GOOGLE_MAPS_API_KEY}"
    )


def render_aerial_with_pin(lat: float, lon: float, caption: str) -> None:
    """Aerial image with a pin marking the (centered) subject house."""
    google = google_satellite_pin_url(lat, lon)
    if google:  # Google satellite already draws a real red marker
        st.image(google, caption=f"{caption} (satellite)", width="stretch")
        return
    # No-key Esri aerial: overlay a centered pin (the property is bbox-centered)
    url = esri_aerial_url(lat, lon)
    st.markdown(
        f"""
        <div style="position:relative;width:100%;max-width:500px;">
          <img src="{url}" style="width:100%;border-radius:8px;display:block;" alt="aerial"/>
          <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-100%);
                      font-size:36px;line-height:1;filter:drop-shadow(0 1px 2px #000);">📍</div>
        </div>
        <div style="color:#888;font-size:0.85em;margin-top:4px;">{caption} — 📍 marks the subject</div>
        """,
        unsafe_allow_html=True,
    )


def has_coords(obj: dict[str, Any]) -> bool:
    return obj.get("latitude") is not None and obj.get("longitude") is not None


# --------------------------------------------------------------------------- #
# Analysis helpers (recompute on the user's included subset)
# --------------------------------------------------------------------------- #
def recompute_band(comps: list[dict[str, Any]]) -> dict[str, Any] | None:
    prices = [c["sale_price"] for c in comps if isinstance(c.get("sale_price"), (int, float))]
    n = len(prices)
    if n == 0:
        return None
    mean = sum(prices) / n
    stdev = statistics.pstdev(prices) if n > 1 else 0.0
    cv = (stdev / mean) if mean else 0.0
    rec = [c.get("recency_days") for c in comps if isinstance(c.get("recency_days"), (int, float))]
    med_rec = statistics.median(rec) if rec else None
    taj = [c["time_adjusted_price"] for c in comps if isinstance(c.get("time_adjusted_price"), (int, float))]
    score = 100
    factors = []
    if n < 3:
        score -= 45; factors.append(f"only {n} comp(s)")
    elif n < 6:
        score -= 20; factors.append(f"{n} comps")
    if cv > 0.25:
        score -= 30; factors.append(f"wide spread ({cv*100:.0f}% CV)")
    elif cv > 0.15:
        score -= 15; factors.append(f"moderate spread ({cv*100:.0f}% CV)")
    if med_rec and med_rec > 365:
        score -= 20; factors.append("stale sales")
    elif med_rec and med_rec > 270:
        score -= 10; factors.append("aging sales")
    score = max(0, score)
    level = "high" if score >= 75 else "medium" if score >= 50 else "low"
    return {
        "count": n,
        "median": statistics.median(prices),
        "min": min(prices),
        "max": max(prices),
        "time_adjusted_median": statistics.median(taj) if taj else None,
        "cv": cv,
        "confidence": score,
        "level": level,
        "factors": factors or ["solid comp set"],
    }


def outlier_flags(prices: list[float]) -> list[bool]:
    if len(prices) < 4:
        return [False] * len(prices)
    q1, _, q3 = statistics.quantiles(prices, n=4)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [(p < lo or p > hi) for p in prices]


def recency_color(days: Any) -> list[int]:
    d = min(max(int(days or 730), 0), 730) / 730.0
    return [int(40 + 215 * d), int(180 * (1 - d)), 70, 200]  # green(recent)->red(old)


# --------------------------------------------------------------------------- #
# PDF comp report
# --------------------------------------------------------------------------- #
def _latin(text: str) -> str:
    return (text or "").encode("latin-1", "replace").decode("latin-1")


def build_pdf(subject: dict[str, Any], band: dict[str, Any] | None,
              comps: list[dict[str, Any]], memo: str | None) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin("Comparable Sales — Underwriting Handoff (41HP)"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120)
    pdf.cell(0, 5, _latin("Automated comp shortlist — underwriter completes adjustments & final value. Not a formal appraisal."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, _latin("Subject"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _latin(
        f"{subject.get('address') or 'Off-market subject'}  |  {subject.get('community') or '-'}, "
        f"{subject.get('city') or '-'}\n"
        f"Type: {subject.get('property_type_normalized') or '-'}  |  "
        f"Assessed: {fmt_money(subject.get('assessed_value'))}  |  "
        f"Year built: {subject.get('year_built') or '-'}  |  "
        f"Land: {subject.get('land_size_sqm') or '-'} sqm"
    ))
    pdf.ln(2)

    if band:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, _latin("Implied value band"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _latin(
            f"{fmt_money(band['min'])} - {fmt_money(band['max'])}  (median {fmt_money(band['median'])})  "
            f"from {band['count']} comps\n"
            f"Confidence: {band['confidence']}/100 ({band['level']}) - {', '.join(band['factors'])}"
        ))
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _latin("Subject internal value (underwriter to complete): _________________"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        pdf.ln(2)

    # optional aerial of the subject
    if has_coords(subject):
        try:
            url = esri_aerial_url(subject["latitude"], subject["longitude"])
            with httpx.Client(timeout=20.0) as client:
                img = client.get(url)
            if img.status_code == 200:
                pdf.image(io.BytesIO(img.content), w=80)
                pdf.ln(2)
        except Exception:  # noqa: BLE001 - image is optional
            pass

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, _latin("Comparable sales  (complete Adj % / Adj $ in the 41HP template)"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    headers = ["#", "Address", "Sale $", "Today-adj $", "Score", "Adj %", "Adj $"]
    widths = [8, 56, 26, 30, 16, 18, 26]
    pdf.set_font("Helvetica", "B", 9)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, _latin(h), border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for i, c in enumerate(comps, start=1):
        row = [
            str(i),
            (c.get("address") or "-")[:34],
            fmt_money(c.get("sale_price")),
            fmt_money(c.get("time_adjusted_price")),
            str(c.get("score")),
            "",  # Adj % — underwriter completes
            "",  # Adj $ — underwriter completes
        ]
        for val, w in zip(row, widths):
            pdf.cell(w, 6, _latin(val), border=1)
        pdf.ln()
    pdf.ln(2)

    if memo:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, _latin("Assistant memo"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _latin(memo))

    return bytes(pdf.output())


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
ss = st.session_state
ss.setdefault("subject", None)
ss.setdefault("search_results", [])
ss.setdefault("recent", [])
ss.setdefault("saved", [])
ss.setdefault("chat", [])
ss.setdefault("chat_subject", None)
ss.setdefault("last_memo", None)


def select_subject(subj: dict[str, Any]) -> None:
    ss.subject = subj
    if subj.get("chat_subject") != ss.chat_subject:
        ss.chat = []
        ss.chat_subject = subj.get("property_id")
    pid = subj.get("property_id")
    if pid:
        st.query_params["pid"] = pid  # shareable permalink for DB subjects


# Resolve a permalinked subject on first load
if ss.subject is None and "pid" in st.query_params:
    hits = cached_search(st.query_params["pid"], 1)
    if hits:
        ss.subject = hits[0]
        ss.chat_subject = hits[0].get("property_id")


# --------------------------------------------------------------------------- #
# Sidebar — connection, ranking controls, weights, recents/saved
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
limit = st.sidebar.slider("Number of comps", 1, 50, 12)
kv_preset = st.sidebar.checkbox(
    "✅ KV comp criteria (≤3 km · ≤12 mo · ±10 yr · ±20% size)", value=True,
    help="Sam's trust rules from the underwriting call: same type, within ~3 km, sold within "
         "12 months, age within 10 years, size within ~20%.")
same_community_only = st.sidebar.checkbox("Same community only", value=False)
use_geo = st.sidebar.checkbox("Geo-aware retrieval ($geoNear)", value=True)
appr = st.sidebar.slider("Annual market trend (%)", -10.0, 15.0, 3.0, 0.5,
                         help="Applied to comp sale prices → time-adjusted 'today's equivalent'.")
if kv_preset:
    max_distance_km, max_sale_age_days = 3.0, 365
    st.sidebar.caption("Filters pinned to KV criteria (≤3 km, ≤12 mo). Uncheck to tune manually.")
else:
    use_max_distance = st.sidebar.checkbox("Cap distance (km)", value=False)
    max_distance_km = (
        st.sidebar.number_input("Max distance (km)", 0.5, 15.0, 5.0, 0.5) if use_max_distance else None
    )
    use_max_age = st.sidebar.checkbox("Cap sale age (days)", value=False)
    max_sale_age_days = (
        st.sidebar.number_input("Max sale age (days)", 30, 730, 365, 30) if use_max_age else None
    )

with st.sidebar.expander("⚖️ Scoring weights"):
    w_dist = st.slider("Distance penalty / km", 0.0, 25.0, 9.0, 0.5)
    w_rec = st.slider("Recency penalty / month", 0.0, 8.0, 2.0, 0.5)
    w_val = st.slider("Value-gap weight", 0.0, 2.0, 0.55, 0.05)
    w_comm = st.slider("Same-community bonus", 0.0, 30.0, 12.0, 1.0)
WEIGHTS = {
    "distance_per_km": w_dist,
    "recency_per_month": w_rec,
    "value_gap_factor": w_val,
    "same_community_bonus": w_comm,
}

if ss.recent:
    st.sidebar.divider()
    st.sidebar.caption("🕘 Recent searches")
    for i, q in enumerate(ss.recent[:6]):
        if st.sidebar.button(q, key=f"recent_{i}", width="stretch"):
            ss._run_query = q
            st.rerun()

if ss.saved:
    st.sidebar.divider()
    st.sidebar.caption("⭐ Saved subjects")
    for i, s in enumerate(ss.saved):
        if st.sidebar.button(s.get("address") or s.get("property_id"), key=f"saved_{i}",
                             width="stretch"):
            select_subject(s)
            st.rerun()


# --------------------------------------------------------------------------- #
# Main — search / manual entry
# --------------------------------------------------------------------------- #
st.title("🏠 Calgary Comparable-Sales Assistant")
st.caption("Resolve a subject property, then retrieve a ranked shortlist of plausible comparable sales.")

tab_search, tab_manual = st.tabs(["🔎 Search a property", "✍️ Manual / off-market subject"])

with tab_search:
    default_q = ss.pop("_run_query", "") if "_run_query" in ss else ""
    query = st.text_input(
        "Search by address or property_id (tolerant: partial words, any order)",
        value=default_q,
        placeholder="e.g. lynnwood dr   ·   120 deercrest   ·   calgary_2026...",
    )
    if query and query.strip():
        if not ss.recent or ss.recent[0] != query.strip():
            ss.recent = [query.strip()] + [r for r in ss.recent if r != query.strip()]
        try:
            results = cached_search(query.strip(), 12)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Search failed: {exc}")
            results = []
        if not results:
            st.warning("No matching subject properties found.")
        else:
            def _label(r: dict[str, Any]) -> str:
                return (f"{r.get('address') or '(no address)'} · {r.get('community') or '—'} · "
                        f"{r.get('property_type_normalized') or '—'} · {fmt_money(r.get('assessed_value'))}")
            chosen = st.selectbox("Select the subject property", results, format_func=_label)
            if st.button("Use this subject", type="primary"):
                select_subject(chosen)
                st.rerun()

with tab_manual:
    st.caption("Rank comps for a property that isn't in the dataset (typed characteristics).")
    with st.form("manual_subject"):
        c1, c2, c3 = st.columns(3)
        m_type = c1.selectbox("Property type", ["detached", "semi", "townhouse", "condo", "duplex"])
        m_assessed = c2.number_input("Assessed value ($)", 50_000, 10_000_000, 600_000, 10_000)
        m_year = c3.number_input("Year built", 1900, 2026, 2005)
        c4, c5, c6 = st.columns(3)
        m_land = c4.number_input("Land size (sqm)", 0.0, 5000.0, 500.0, 10.0)
        m_beds = c5.number_input("Bedrooms", 0.0, 20.0, 4.0, 1.0)
        m_baths = c6.number_input("Bathrooms", 0.0, 20.0, 3.0, 0.5)
        c7, c8, c9 = st.columns(3)
        m_garage = c7.number_input("Garage spaces", 0.0, 10.0, 2.0, 1.0)
        m_lat = c8.number_input("Latitude", 50.0, 52.0, 51.045, format="%.5f")
        m_lon = c9.number_input("Longitude", -115.0, -113.0, -114.06, format="%.5f")
        m_comm = st.text_input("Community name (optional)", "")
        if st.form_submit_button("Use this manual subject", type="primary"):
            select_subject({
                "property_id": None, "manual": True,
                "address": "Off-market subject", "city": "Calgary",
                "community": m_comm or None, "property_type_normalized": m_type,
                "assessed_value": float(m_assessed), "year_built": int(m_year),
                "land_size_sqm": float(m_land), "bedrooms": float(m_beds),
                "bathrooms": float(m_baths), "garage_count": float(m_garage),
                "latitude": float(m_lat), "longitude": float(m_lon),
            })
            st.rerun()


# --------------------------------------------------------------------------- #
# Subject card
# --------------------------------------------------------------------------- #
subject = ss.subject
if not subject:
    st.info("Search for a property above, or enter a manual subject, to begin.")
    st.stop()

st.divider()
hdr_l, hdr_r = st.columns([3, 1])
hdr_l.subheader("Subject property")
if hdr_r.button("⭐ Save subject", width="stretch"):
    if subject not in ss.saved:
        ss.saved.append(subject)
        st.toast("Saved.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Assessed value", fmt_money(subject.get("assessed_value")))
c2.metric("Year built", subject.get("year_built") or "—")
c3.metric("Land (sqm)", subject.get("land_size_sqm") or "—")
c4.metric("Type", subject.get("property_type_normalized") or "—")
st.caption(
    f"**{subject.get('address') or '(no address)'}** · {subject.get('community') or '—'} · "
    f"{subject.get('city') or '—'}"
    + (f" · `{subject.get('property_id')}`" if subject.get("property_id") else " · _(manual)_")
)

if has_coords(subject):
    ic1, ic2 = st.columns(2)
    with ic1:
        render_aerial_with_pin(subject["latitude"], subject["longitude"], "Aerial view")
    sv = street_view_static_url(subject["latitude"], subject["longitude"])
    with ic2:
        if sv:
            st.image(sv, caption="Street View", width="stretch")
        else:
            st.caption("Add GOOGLE_MAPS_API_KEY to show inline Street View photos.")
    st.markdown(
        f"[📍 Google Maps]({google_maps_link(subject['latitude'], subject['longitude'])}) · "
        f"[🏠 Street View]({street_view_link(subject['latitude'], subject['longitude'])})"
    )


# --------------------------------------------------------------------------- #
# Build the rank request (live — re-runs whenever controls change)
# --------------------------------------------------------------------------- #
payload: dict[str, Any] = {
    "limit": int(limit),
    "same_community_only": bool(same_community_only),
    "max_results_per_pass": 250,
    "weights": WEIGHTS,
    "annual_appreciation_rate": round(appr / 100.0, 4),
    "use_geo": bool(use_geo),
}
if max_distance_km is not None:
    payload["max_distance_km"] = float(max_distance_km)
if max_sale_age_days is not None:
    payload["max_sale_age_days"] = int(max_sale_age_days)

if subject.get("manual"):
    payload["subject_manual"] = {
        "address": subject.get("address"), "city": subject.get("city", "Calgary"),
        "community_name": subject.get("community"),
        "property_type_normalized": subject.get("property_type_normalized"),
        "assessed_value": subject.get("assessed_value"), "year_built": subject.get("year_built"),
        "land_size_sqm": subject.get("land_size_sqm"), "bedrooms": subject.get("bedrooms"),
        "bathrooms": subject.get("bathrooms"), "garage_count": subject.get("garage_count"),
        "latitude": subject.get("latitude"), "longitude": subject.get("longitude"),
    }
else:
    payload["subject_property_id"] = subject.get("property_id")

try:
    result = cached_rank(json.dumps(payload, sort_keys=True))
except httpx.HTTPStatusError as exc:
    detail = exc.response.json().get("detail", exc.response.text) if exc.response.content else ""
    st.error(f"Ranking failed ({exc.response.status_code}): {detail}")
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.error(f"Ranking failed: {exc}")
    st.stop()

comps_all = result.get("comparables", [])
st.divider()
st.subheader("Ranked comparable sales")
if not comps_all:
    st.warning("No comparable sales matched even the widest filter profile. Try loosening the controls.")
    st.stop()


# --------------------------------------------------------------------------- #
# Comps table with include toggles + outlier flags
# --------------------------------------------------------------------------- #
prices_all = [c.get("sale_price") for c in comps_all]
flags = outlier_flags([p for p in prices_all if isinstance(p, (int, float))])
# map flags back (only computed over numeric prices, which is all of them here)
df = pd.DataFrame(comps_all)
df.insert(0, "rank", range(1, len(df) + 1))
df.insert(1, "include", True)
df["outlier"] = flags if len(flags) == len(df) else [False] * len(df)
if {"latitude", "longitude"}.issubset(df.columns):
    ok = df["latitude"].notna() & df["longitude"].notna()
    df["map"] = None
    df.loc[ok, "map"] = df[ok].apply(lambda r: google_maps_link(r["latitude"], r["longitude"]), axis=1)

show_cols = ["rank", "include", "address", "community", "sale_date", "sale_price",
             "time_adjusted_price", "price_per_sqm", "distance_km", "recency_days",
             "assessed_value_gap_ratio", "score", "meets_kv_criteria", "outlier", "map"]
show_cols = [c for c in show_cols if c in df.columns]
edited = st.data_editor(
    df[show_cols],
    width="stretch",
    hide_index=True,
    disabled=[c for c in show_cols if c != "include"],
    column_config={
        "rank": st.column_config.NumberColumn("#", width="small"),
        "include": st.column_config.CheckboxColumn("✓", help="Untick to exclude from the value band"),
        "sale_price": st.column_config.NumberColumn("Sale $", format="$%d"),
        "time_adjusted_price": st.column_config.NumberColumn("Today-adj $", format="$%d"),
        "price_per_sqm": st.column_config.NumberColumn("$/sqm", format="$%.0f"),
        "meets_kv_criteria": st.column_config.CheckboxColumn("KV ✓", help="Meets ALL of KV's comp criteria"),
        "outlier": st.column_config.CheckboxColumn("⚠️", help="Price is an IQR outlier"),
        "map": st.column_config.LinkColumn("Map", display_text="📍"),
    },
    key="comps_editor",
)

included_mask = list(edited["include"]) if "include" in edited else [True] * len(comps_all)
included_with_rank = [
    (i + 1, c) for i, (c, keep) in enumerate(zip(comps_all, included_mask)) if keep
]
included = [c for _, c in included_with_rank]
band = recompute_band(included)


# --------------------------------------------------------------------------- #
# Value band + confidence
# --------------------------------------------------------------------------- #
if band:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Comps used", f"{band['count']} / {len(comps_all)}")
    m2.metric("Median price", fmt_money(band["median"]))
    m3.metric("Today-adjusted median", fmt_money(band["time_adjusted_median"]))
    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(band["level"], "⚪")
    m4.metric("Confidence", f"{conf_emoji} {band['confidence']}/100")
    st.info(
        f"**Implied value band:** {fmt_money(band['min'])} – {fmt_money(band['max'])} "
        f"(median {fmt_money(band['median'])}). Confidence {band['level']} — {', '.join(band['factors'])}."
    )
    kv_ok = sum(1 for c in included if c.get("meets_kv_criteria"))
    st.caption(
        f"✅ **{kv_ok} of {len(included)}** included comps meet *all* of KV's criteria "
        "(type · ≤3 km · ≤12 mo · ±10 yr · ±20% size-proxy)."
    )

with st.expander("⚠️ Factors to verify manually (not in the data)"):
    st.markdown(
        "These materially affect value but aren't in public records — an underwriter must confirm "
        "them before relying on any comp (per KV's underwriting call):\n"
        "- **What the property backs onto** — greenspace/park vs busy road or commercial\n"
        "- **Walkout basement** / lot grade\n"
        "- **Legal or secondary suite**\n"
        "- **Renovation quality & condition**\n"
        "- **Arms-length sale?** — exclude family transfers, builder inventory, distress sales\n\n"
        "_This tool produces the ranked shortlist — the final comp selection and value stay with "
        "the underwriter (decision support, not automation)._"
    )

with st.expander("🔍 Score breakdown — how each comp's score is built"):
    rows = []
    for rank, c in included_with_rank:
        b = c.get("score_breakdown") or {}
        rows.append({"#": rank, "address": c.get("address"), **b, "= score": c.get("score")})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(
            "Each column is the points a factor adds/subtracts from the base 100 "
            "(penalties negative, bonuses positive). Weights are tunable in the sidebar."
        )


# --------------------------------------------------------------------------- #
# Interactive map (pydeck): subject pin + comps by recency + lines + tooltips
# --------------------------------------------------------------------------- #
geo_comps = [c for c in included if has_coords(c)]
if has_coords(subject) and geo_comps:
    s_lat, s_lon = subject["latitude"], subject["longitude"]
    comp_rows = [{
        "lon": c["longitude"], "lat": c["latitude"],
        "address": c.get("address") or "—",
        "price": fmt_money(c.get("sale_price")),
        "dist": c.get("distance_km"), "score": c.get("score"),
        "age": c.get("recency_days"),
        "color": recency_color(c.get("recency_days")),
    } for c in geo_comps]
    line_rows = [{"from": [s_lon, s_lat], "to": [c["longitude"], c["latitude"]]} for c in geo_comps]
    subj_row = [{"lon": s_lon, "lat": s_lat, "address": subject.get("address") or "Subject"}]

    layers = [
        pdk.Layer("LineLayer", line_rows, get_source_position="from", get_target_position="to",
                  get_color=[150, 150, 150, 120], get_width=1.5),
        pdk.Layer("ScatterplotLayer", comp_rows, get_position=["lon", "lat"], get_fill_color="color",
                  get_radius=55, radius_min_pixels=5, pickable=True),
        pdk.Layer("ScatterplotLayer", subj_row, get_position=["lon", "lat"],
                  get_fill_color=[30, 110, 255, 230], get_radius=80, radius_min_pixels=9, pickable=True),
    ]
    tooltip = {"html": "<b>{address}</b><br/>{price}<br/>{dist} km · {age} d · score {score}",
               "style": {"backgroundColor": "#222", "color": "white"}}
    st.pydeck_chart(pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(latitude=s_lat, longitude=s_lon, zoom=12.5, pitch=0),
        tooltip=tooltip,
    ))
    st.caption("🔵 subject · dots = comps (🟢 recent → 🔴 older) · lines link comps to the subject")


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
cdf = pd.DataFrame(included)
if not cdf.empty:
    t1, t2, t3 = st.tabs(["Price distribution", "Price vs distance", "Sales timeline"])
    with t1:
        st.altair_chart(
            alt.Chart(cdf).mark_bar().encode(
                x=alt.X("sale_price:Q", bin=alt.Bin(maxbins=15), title="Sale price"),
                y=alt.Y("count()", title="Comps"),
                tooltip=["count()"],
            ).properties(height=280),
            width="stretch",
        )
    with t2:
        if "distance_km" in cdf.columns:
            st.altair_chart(
                alt.Chart(cdf).mark_circle(size=90).encode(
                    x=alt.X("distance_km:Q", title="Distance (km)"),
                    y=alt.Y("sale_price:Q", title="Sale price"),
                    color=alt.Color("score:Q", scale=alt.Scale(scheme="viridis")),
                    tooltip=["address", "sale_price", "distance_km", "score"],
                ).properties(height=280),
                width="stretch",
            )
    with t3:
        tdf = cdf.copy()
        tdf["sale_dt"] = pd.to_datetime(tdf["sale_date"], errors="coerce")
        st.altair_chart(
            alt.Chart(tdf).mark_circle(size=90).encode(
                x=alt.X("sale_dt:T", title="Sale date"),
                y=alt.Y("sale_price:Q", title="Sale price"),
                color=alt.Color("score:Q", scale=alt.Scale(scheme="viridis")),
                tooltip=["address", "sale_price", "sale_date"],
            ).properties(height=280),
            width="stretch",
        )


# --------------------------------------------------------------------------- #
# Exports: underwriting handoff (CSV with adjustment columns) + PDF
# --------------------------------------------------------------------------- #
ex1, ex2, _ = st.columns([1, 1, 2])
und_rows = []
for rank, c in included_with_rank:
    und_rows.append({
        "rank": rank,
        "address": c.get("address"),
        "community": c.get("community"),
        "sale_date": (c.get("sale_date") or "")[:10],
        "sale_price": c.get("sale_price"),
        "time_adjusted_price": c.get("time_adjusted_price"),
        "price_per_sqm": c.get("price_per_sqm"),
        "distance_km": c.get("distance_km"),
        "recency_days": c.get("recency_days"),
        "bedrooms": c.get("bedrooms"),
        "bathrooms": c.get("bathrooms"),
        "garage_count": c.get("garage_count"),
        "meets_kv_criteria": c.get("meets_kv_criteria"),
        "score": c.get("score"),
        # blank columns for the underwriter to complete in the 41HP template
        "adjustment_pct": "",
        "adjusted_value": "",
        "underwriter_notes": "",
    })
csv = pd.DataFrame(und_rows).to_csv(index=False).encode("utf-8")
ex1.download_button("⬇️ Underwriting CSV (41HP)", csv,
                    file_name=f"underwriting_{subject.get('property_id') or 'manual'}.csv",
                    mime="text/csv", width="stretch",
                    help="Comps + blank adjustment columns to drop into the 41HP template.")
if _HAS_FPDF:
    try:
        pdf_bytes = build_pdf(result.get("subject", subject), band, included, ss.get("last_memo"))
        ex2.download_button("📄 PDF handoff", pdf_bytes,
                            file_name=f"comp_handoff_{subject.get('property_id') or 'manual'}.pdf",
                            mime="application/pdf", width="stretch")
    except Exception as exc:  # noqa: BLE001
        ex2.caption(f"PDF unavailable: {exc}")
else:
    ex2.caption("Install fpdf2 for PDF export.")

with st.expander("Raw API response (JSON)"):
    st.json(result)


# --------------------------------------------------------------------------- #
# LLM assistant — streaming chat + comp memo (grounded; cites [#n] rank rows)
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("🤖 Ask the assistant")
st.caption("Answers are grounded only on the comps above and cite them as [#1], [#2] (matching the # column).")

if not llm_ready:
    st.info("LLM assistant isn't configured. Set GROQ_API_KEY in the API environment to enable Q&A.")
else:
    def _ask_payload(question: str | None, mode: str) -> dict[str, Any]:
        p = dict(payload)
        p.pop("max_results_per_pass", None)
        p["mode"] = mode
        if mode == "qa":
            p["question"] = question
        p["limit"] = min(int(limit), 50)
        return p

    def run_stream(question: str | None, mode: str) -> None:
        label = question if mode == "qa" else "📝 Generate comp memo"
        ss.chat.append(("user", label))
        with st.chat_message("user"):
            st.markdown(label)
        with st.chat_message("assistant"):
            try:
                full = st.write_stream(ask_llm_stream(_ask_payload(question, mode)))
            except Exception as exc:  # noqa: BLE001
                full = f"⚠️ {exc}"
                st.markdown(full)
        ss.chat.append(("assistant", full))
        ss.last_memo = full

    for role, msg in ss.chat:
        with st.chat_message(role):
            st.markdown(msg)

    suggestions = [
        "What is a fair offer range for the subject?",
        "Why is comp [#1] ranked first?",
        "Which comps are the weakest, and why?",
    ]
    cols = st.columns(len(suggestions) + 1)
    for i, sug in enumerate(suggestions):
        if cols[i].button(sug, key=f"sug_{i}", width="stretch"):
            run_stream(sug, "qa")
    if cols[-1].button("📝 Comp memo", key="memo_btn", width="stretch"):
        run_stream(None, "summary")

    if prompt := st.chat_input("Ask about this property…"):
        run_stream(prompt, "qa")
