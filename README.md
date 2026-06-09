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
  in `data/` is gitignored.)

## Quality-of-life features

- 🤖 **LLM property Q&A** — ask "what's a fair offer range?", "why is comp #1 ranked first?";
  one-click **comp memo** for underwriting. Grounded on the live ranked comps via `POST /ask`.
- 🛰️ **Property imagery** — no-key aerial (Esri World Imagery) + clickable Google Maps / Street View
  links for the subject and every comp. Set `GOOGLE_MAPS_API_KEY` to also render inline Street View photos.
- 💲 **Implied value band** (median + min/max of comp prices) and a clickable comps table.
- ⬇️ **CSV export** of the shortlist for handoff.

## Setup

```bash
cd /d/Study/Project/Hackathon
cp .env.example .env          # paste your MONGODB_URI into .env
```

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

## Scope / notes

- Calgary MVP dataset only (Atlas free tier; the full 578k-property set is not loaded).
- Tuning flags (`max_distance_km`, `max_sale_age_days`, `same_community_only`) only ever
  *tighten* the engine's built-in profiles — callers can't widen past its safe ceilings.
- Auth is off by default; set `API_KEY` to require an `x-api-key` header on every request.
