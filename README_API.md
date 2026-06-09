# KV Comp Analysis API

A thin FastAPI layer over the existing Calgary comp-ranking engine
(`scripts/comp_ranking_service.py`). It exposes the **same** MongoDB-Atlas-backed
ranking logic over HTTP so a frontend, CLI, or any HTTP client can call it reliably.

- Data: Atlas DB `kv_comp_analysis` (MVP dataset — 120,313 properties, 69,582 sales).
- The ranking engine is reused as-is; the API only adds routing + request/response shaping.
- Scope: Calgary MVP only. No Edmonton, no full-dataset scaling, no UI.

---

## Endpoints

| Method | Path              | Purpose                                              |
|--------|-------------------|------------------------------------------------------|
| GET    | `/health`         | Service status + Mongo connectivity + LLM readiness. |
| GET    | `/subject-search` | Resolve a subject by address/property_id.            |
| POST   | `/rank-comps`     | Ranked comparable-sales shortlist for a subject.     |
| POST   | `/ask`            | Grounded LLM Q&A / comp memo for a subject.          |
| POST   | `/ask/stream`     | Same as `/ask` but streams the answer (text/plain).  |

Interactive docs once running: `http://localhost:8000/docs`.

### `GET /health`
```json
{ "status": "ok", "db": "kv_comp_analysis", "mongo_connected": true, "llm_configured": true }
```

### `GET /subject-search?q=<address-or-id>&limit=10`
Exact (normalized) address match first, then normalized "contains", then property_id.
```json
{
  "query": "10 EXAMPLE ST NW",
  "count": 1,
  "results": [
    {
      "property_id": "PROP-123",
      "address": "10 EXAMPLE ST NW",
      "city": "CALGARY",
      "community": "EXAMPLE COMMUNITY",
      "property_type_normalized": "detached",
      "assessed_value": 615000,
      "year_built": 2004,
      "land_size_sqm": 480.0
    }
  ]
}
```

### `POST /rank-comps`
Provide **exactly one** of `subject_property_id` or `subject_address`. Optional tuning
flags only ever *tighten* the engine's built-in filter profiles (deterministic).

Request:
```json
{
  "subject_property_id": "PROP-123",
  "limit": 10,
  "same_community_only": false,
  "max_distance_km": 5,
  "max_sale_age_days": 540
}
```

Response (truncated):
```json
{
  "subject": {
    "property_id": "PROP-123",
    "address": "10 EXAMPLE ST NW",
    "city": "CALGARY",
    "community": "EXAMPLE COMMUNITY",
    "property_type_normalized": "detached",
    "assessed_value": 615000,
    "year_built": 2004,
    "land_size_sqm": 480.0
  },
  "candidate_count": 27,
  "returned_count": 10,
  "applied_filters": {
    "limit": 10,
    "same_community_only": false,
    "max_distance_km": 5,
    "max_sale_age_days": 540,
    "max_results_per_pass": 250
  },
  "comparables": [
    {
      "sale_id": "SALE-987",
      "property_id": "PROP-456",
      "address": "22 EXAMPLE ST NW",
      "community": "EXAMPLE COMMUNITY",
      "sale_date": "2025-09-14T00:00:00Z",
      "sale_price": 628000,
      "score": 91.42,
      "distance_km": 0.18,
      "recency_days": 120,
      "assessed_value_gap_ratio": 0.021,
      "year_built_gap": 1,
      "matched_profile": "tight",
      "matched_query_pass": "same_community",
      "reasons": ["0.18 km away", "sold 120 days ago", "value gap 2.1%", "same community", "year built gap 1"]
    }
  ]
}
```

Errors: `400` (both/neither subject provided), `404` (subject not found), `422` (subject
missing required fields like `assessed_value`).

#### v1.1 optional fields (all backward-compatible)
`/rank-comps` and `/ask` also accept:
- `subject_manual` — an off-market subject instead of a DB lookup. Provide **exactly one** of
  `subject_property_id` / `subject_address` / `subject_manual`. Shape:
  `{ "property_type_normalized": "detached", "assessed_value": 600000, "year_built": 2005,
  "land_size_sqm": 500, "bedrooms": 4, "bathrooms": 3, "garage_count": 2,
  "latitude": 51.045, "longitude": -114.06, "community_name": "…" }`
- `weights` — override scoring knobs, e.g. `{"distance_per_km": 12, "recency_per_month": 1.5,
  "value_gap_factor": 0.5, "same_community_bonus": 10}` (any subset; unset knobs keep defaults).
- `annual_appreciation_rate` — e.g. `0.03`; drives each comp's `time_adjusted_price`.
- `use_geo` — `true` (default) uses `$geoNear` retrieval when the subject has coordinates.

Each comp now also includes `time_adjusted_price`, `price_per_sqm`, `bedrooms`/`bathrooms`/
`garage_count` (+ gaps). The response carries an `analysis` block: `median_price`, `min/max_price`,
`price_per_sqm_median`, `time_adjusted_median`, and a `confidence` `{score, level, factors}`.

### `POST /ask`
Grounded LLM Q&A (Groq, OpenAI-compatible) over the subject + its ranked comps. The model is
instructed to use **only** the provided data and to frame value opinions as ranges with caveats.
Requires `GROQ_API_KEY` (returns `503` if unset). Provide **exactly one** subject. `question` is
required when `mode="qa"`; `mode="summary"` writes a short underwriting comp memo.

Request:
```json
{ "subject_property_id": "PROP-123", "question": "What is a fair offer range?", "mode": "qa", "limit": 8 }
```
Response:
```json
{
  "answer": "Based on 159 LYNNWOOD DR SE ($581,000)…, a fair range is $535,000–$575,000. (Automated estimate, not an appraisal.)",
  "model": "llama-3.3-70b-versatile",
  "mode": "qa",
  "used_comps": 8,
  "subject": { "property_id": "PROP-123", "address": "…", "latitude": 50.98, "longitude": -114.0 }
}
```
Errors: `503` (LLM not configured), `502` (upstream LLM error), `404`/`400`/`422` as for `/rank-comps`.

---

## Local setup (exact commands)

From the repo root (`D:\Study\Project\Hackathon`), using the existing `.venv`:

```bash
cd /d/Study/Project/Hackathon

# 1. Install deps into the existing venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. Configure env (option A: .env file)
cp .env.example .env        # then edit .env and paste your MONGODB_URI

#    (option B: export directly — same URI the CLI already uses)
export MONGODB_URI="mongodb+srv://...:...@cluster.mongodb.net/?retryWrites=true&w=majority"
export MONGODB_DB="kv_comp_analysis"

# 3. Start the API
.venv/Scripts/python.exe -m uvicorn api_main:app --app-dir scripts --host 0.0.0.0 --port 8000
```

Leave it running; use a second terminal for the tests below.

---

## Test / validate (exact commands)

```bash
# Health
curl http://localhost:8000/health

# Resolve a subject (grab a property_id from the results)
curl "http://localhost:8000/subject-search?q=NW&limit=5"

# Rank comps by property_id
curl -X POST http://localhost:8000/rank-comps \
  -H "Content-Type: application/json" \
  -d '{"subject_property_id":"<id-from-search>","limit":10}'

# Rank comps by address (skips the search step)
curl -X POST http://localhost:8000/rank-comps \
  -H "Content-Type: application/json" \
  -d '{"subject_address":"10 EXAMPLE ST NW","limit":10,"same_community_only":true}'

# Automated end-to-end smoke test (PASS/FAIL across all 3 endpoints)
.venv/Scripts/python.exe scripts/smoke_test.py
```

Cross-check against the original CLI for the same subject (should match):
```bash
.venv/Scripts/python.exe scripts/comp_ranking_service.py --subject-property-id "<id>" --limit 10
```

---

## Client integration notes

Any HTTP-capable client can integrate with the service.

1. Resolve a subject with `GET /subject-search?q=<address-or-id>&limit=1`.
2. Call `POST /rank-comps` with either `subject_property_id` or `subject_address`.
3. Consume the response JSON directly:
  - `subject` for the resolved property summary
  - `comparables[]` for ranked comp rows
  - `returned_count` and `candidate_count` for summary metadata

If `API_KEY` is set in `.env`, include header `x-api-key: <value>` in client requests.

---

## Assumptions / known limitations

- Reuses the existing **MVP Atlas dataset** only (no full 578k load, no Edmonton, no UI).
- Tuning flags (`max_distance_km`, `max_sale_age_days`, `same_community_only`) only
  *tighten* the built-in filter profiles — they cannot widen past the engine's ceilings.
- Auth is **off by default** for local development; enable by setting `API_KEY`.
- One shared `CompRankingService` (pooled `MongoClient`) is reused across requests.
- API files live alongside the existing scripts in `scripts/` to match the repo pattern;
  hence `uvicorn ... --app-dir scripts`.
