# KV Capital — Calgary Comp-Analysis Assistant

Returns a ranked shortlist of plausible **comparable sales** for a Calgary subject
property. Python end-to-end, no n8n.

```
Streamlit UI  ──HTTP──▶  FastAPI  ──▶  CompRankingService  ──▶  MongoDB Atlas
 (app/)                  (scripts/api_main.py)  (scripts/comp_ranking_service.py)  (kv_comp_analysis)
```

- **Engine** (`scripts/comp_ranking_service.py`) — multi-pass retrieval (same-community → same-city)
  across tight/balanced/wide filter profiles, then deterministic scoring (distance, recency,
  assessed-value / land / year gaps, same-community bonus). Reused by both the CLI and the API.
- **API** (`scripts/api_main.py`) — `GET /health`, `GET /subject-search`, `POST /rank-comps`,
  `POST /ask` (grounded LLM Q&A / comp memo). See [`README_API.md`](README_API.md) for the contract.
- **LLM assistant** (`scripts/llm_service.py`) — Groq (OpenAI-compatible) grounded **only** on the
  subject + its ranked comps; answers value questions as ranges with caveats. Key stays server-side.
- **UI** (`app/streamlit_app.py`) — subject search → ranked table + map + implied value band +
  **property imagery** (aerial + Google Maps/Street View links) + **CSV export** + an
  **Ask-the-assistant** chat. A thin HTTP client of the API (does no ranking itself).
- **Data** — Atlas MVP dataset: 120,313 properties, 69,582 synthetic sales. (Large NDJSON/GeoJSON
  in `data/` is gitignored — see [`data/README.md`](data/README.md).)

## Project structure

```
kv-comp-analysis/
├── scripts/                 # runtime service
│   ├── api_main.py          #   FastAPI app (/health /subject-search /rank-comps /ask /ask/stream)
│   ├── api_models.py        #   Pydantic request/response models
│   ├── api_config.py        #   env-based settings
│   ├── comp_ranking_service.py  # the comp engine (retrieval + scoring) — also a CLI
│   ├── llm_service.py       #   grounded Groq LLM client (Q&A + memo, streaming)
│   └── smoke_test.py        #   live end-to-end API check
├── app/
│   └── streamlit_app.py     # demo UI (HTTP client of the API)
├── pipeline/                # one-off ETL that built the dataset (see data/README.md)
├── tests/                   # DB/network-free unit tests (pytest)
├── data/                    # gitignored datasets (documented in data/README.md)
├── Dockerfile.api · Dockerfile.ui · docker-compose.yml
├── requirements*.txt · pyproject.toml
└── README.md · README_API.md · LOOM_SCRIPT.md
```


## Quality-of-life features

**Search & input**
- 🔎 **Tolerant search** — partial words, any order, abbreviation-aware (`lynnwood dr` finds `… DR …`),
  word-anchored so `NW` no longer matches inside `LYNNWOOD`. Recent searches + ⭐ saved subjects.
- ✍️ **Manual / off-market subject** — rank comps for a property not in the dataset (typed traits).
- 🔗 **Shareable permalink** — the URL carries the subject (`?pid=…`) so a comp set can be re-opened.

**Analysis**
- ⚖️ **Tunable scoring weights** (distance / recency / value-gap / community bonus) with **live re-rank**.
- 💲 **$/sqm** and **time-adjusted "today's-equivalent" prices** (configurable market-trend rate).
- ✅ **Exclude-a-comp toggles** that recompute the value band; **IQR outlier flags**; **confidence score**
  (driven by comp count, price spread, recency). Bedrooms/bathrooms/garage feed scoring for manual subjects.

**Visualization & export**
- 🗺️ **Interactive map** (pydeck): subject pin, comps colored by recency, subject→comp lines, hover tooltips.
  The subject **aerial view is pinned** to the exact house. Charts: price histogram, price-vs-distance, timeline.
- 📄 **One-click PDF comp report** (subject + value band + comps table + memo + aerial) and CSV export.

**LLM assistant (Groq, grounded)**
- 🤖 **Streaming** Q&A + one-click **comp memo**; cites comps as `[#1]`, `[#2]` matching the table.
  Server-side via `POST /ask` and `POST /ask/stream`; grounded only on the live ranked comps.

**Engine / ops**
- 🛰️ **Geo-aware retrieval** (`$geoNear` on the sales `2dsphere` index) + a compound hot-query index
  (auto-ensured on startup) — better comp geography and speed.

## Setup

```bash
cd <repo>                     # this repo (dev machine: /d/Study/Project/Hackathon)
cp .env.example .env          # paste your MONGODB_URI (and GROQ_API_KEY for the assistant)

# Fresh clone on any OS — create a venv:
python -m venv .venv
# Windows: .venv\Scripts\python.exe   ·   macOS/Linux: .venv/bin/python
```

> The commands below use the dev machine's `.venv/Scripts/python.exe` (Windows). On macOS/Linux
> substitute `.venv/bin/python`, or just `python` inside an activated venv.

## Run locally (two processes)

```bash
# 1) API
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn api_main:app --app-dir scripts --host 0.0.0.0 --port 8000

# 2) UI (second terminal)
.venv/Scripts/python.exe -m pip install -r requirements-ui.txt
.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
```

API docs: http://localhost:8000/docs · UI: http://localhost:8501

## Run with Docker (both services)

```bash
# .env must contain MONGODB_URI
docker compose up --build
```

UI on :8501, API on :8000. The UI reaches the API at `http://api:8000` inside the compose network.

## Test

```bash
# Unit tests (no DB needed)
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q

# Live end-to-end smoke test (API must be running against Atlas)
.venv/Scripts/python.exe scripts/smoke_test.py
```

## How this maps to KV's brief

The job (from the brief + the underwriting call): *given a subject property, retrieve and rank
plausible comparable sales to support a valuation — and explain the reasoning*. Sam ranked
**retrieval quality first**, valuation quality second, and warned that fully automating the pick
would be naive — the final call stays human.

| KV "trustworthy comp" criterion | How we implement it |
|---|---|
| Same property **type** | Hard filter on `property_type_normalized` |
| Within **~3 km** | `$geoNear` retrieval; KV preset pins max distance to 3 km |
| Sold in the **last 6–12 months** | KV preset pins max sale age to 365 days; recency penalty in score |
| **Size** within ~20% | Land-size gap (documented proxy — see tradeoffs) + beds/baths/value |
| **Age** within 10 years | `max_year_gap = 10` in the tightest profile |
| **True sale** only (no unsold listings) | `true_sales_only` guard (positive sale price; closed sales only) |
| **Key fields pre-filled** for 10–20 comps | Each comp returns price, $/sqm, time-adjusted price, distance, recency, beds/baths/garage, score + reasons |
| **Decision stays human** | Exclude-comp toggles, "verify manually" panel, LLM disclaims and never makes the pick |
| Output → **41HP template** | Underwriting CSV (blank Adj % / Adj $ / notes) + PDF handoff |
| Per-comp **KV ✓** | `kv_criteria` checklist + `meets_kv_criteria`; UI shows "X of N meet all criteria" |

## Approach

1. **Deterministic retrieval + ranking** (`comp_ranking_service.py`): a widening multi-pass search
   (same-community → same-city × tight/balanced/wide profiles) seeded by `$geoNear`, then a
   transparent additive score (distance, recency, value/size/age/bed-bath gaps, community bonus).
   Every comp carries a `score_breakdown` and a `kv_criteria` checklist.
2. **Grounded LLM for explanation, not decisions** (`llm_service.py`, Groq): temperature 0, a fenced
   `DATA` block, and hard rules — use only the provided comps, never invent numbers, cite `[#n]`,
   and end memos with the qualitative factors a human must verify.
3. **Thin clients**: a FastAPI contract (`/subject-search`, `/rank-comps`, `/ask`, `/ask/stream`) and
   a Streamlit UI that does no ranking itself.

## Why deterministic retrieval, not an autonomous agent

The brief says "agent" and rewards tool use, but in *lending* the comp set must be reproducible and
auditable. So the retrieval/ranking is deterministic code (same inputs → same comps, with a visible
score breakdown), and the LLM is confined to explaining that output under strict anti-fabrication
rules. That's a deliberate trade: less "agentic flash," more trust — which is what an underwriter
signing off on a loan actually needs. Swapping in an LLM tool-calling loop over the same endpoints
is a small change if desired.

## Tradeoffs & what we cut

- **No living-area (GLA/sqft).** The open assessment data has *land* size, not finished floor area —
  so "size within 20%" uses a land-size proxy plus beds/baths/assessed-value. The weighted engine
  ingests true GLA in ~one line if the dataset provides it; we chose to be explicit rather than fake it.
- **Calgary MVP only** on the Atlas free tier (120k properties / 69k synthetic sales) — not the full
  578k set, no Edmonton, no MLS feed.
- **Synthetic sales** (mirroring the brief's anonymized-data approach); the engine is source-agnostic.
  ⚠️ **Known validity caveat:** the synthetic `sale_price` is derived from each property's
  `assessed_value` (× a per-property multiplier), so the implied value band is partly *assessment
  re-derived* rather than independent market signal. With real arm's-length MLS sale prices this
  circularity disappears and the same engine surfaces genuine market evidence — we flag it rather
  than let it read as a real valuation.
- **No full automation** — by design; the underwriter makes the final pick.
- **Breadth was a deliberate scope choice.** The interactive UI, imagery, PDF/CSV handoff, permalinks
  and Docker are demo/handoff polish; given more time we'd reinvest it in comp-selection depth (true
  GLA, a per-feature dollar-adjustment grid, weight calibration) rather than more surface area.

## Edge cases handled

- Subject missing required fields → `422`; subject not found → `404`.
- Subject not in the dataset (e.g. a builder's new build) → **manual/off-market entry**.
- No comps after the widest profile → UI explains and suggests loosening.
- Price outliers flagged (IQR); thin/wide/stale comp sets lower the **confidence** score.
- Non-true-sales excluded via the `true_sales_only` guard.

## Scope / notes

- Tuning flags only ever *tighten* the engine's built-in profiles — callers can't widen past its ceilings.
- Auth is off by default; set `API_KEY` to require an `x-api-key` header on every request.
- See [`LOOM_SCRIPT.md`](LOOM_SCRIPT.md) for the < 10-min walkthrough outline.

## License / IP

Built for the KV Capital AI-Engineer hackathon. Per the challenge terms, KV Capital owns the IP of
this submission. Synthetic/anonymized data only — no proprietary or personal records.
