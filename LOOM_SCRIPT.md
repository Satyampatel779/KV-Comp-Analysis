# Loom walkthrough script (< 10 minutes)

KV weights the Loom heavily — clear communication is part of the role. Aim for ~8 minutes.
Have the API + Streamlit running and a subject ready (e.g. search `deercrest`).

---

### 0 · Hook (0:00–0:30)
"KV's real-estate-debt underwriters start every deal with one question: what's this property worth?
Today that means hand-assembling comps from a blank sheet. This tool gives them a ranked, explained
shortlist in seconds — they keep the final call." Show the running app.

### 1 · The job & what matters (0:30–1:30) — *Pragmatism, Communication*
- From the call: **retrieval quality first**, valuation second, and the pick must stay human.
- Scope I chose: Calgary residential MVP, one credible dataset, decision-support (not automation).
- One sentence on the stack: Python · FastAPI · MongoDB Atlas · Streamlit · Groq LLM.

### 2 · Retrieval & ranking — the core (1:30–3:30) — *ML/data thinking, retrieval quality*
- Search a subject (show tolerant search: `lynnwood dr`, lowercase, partial).
- Toggle **KV comp criteria** on: same type · ≤3 km · ≤12 months · ±10 yr · ±20% size.
- Show the **KV ✓** column and "X of N meet all criteria".
- Open **Score breakdown** — "ranking is transparent: here's exactly why comp #1 beats #6."
- Mention `$geoNear` geo retrieval + the `true_sales_only` guard (no unsold listings).

### 3 · Valuation & confidence (3:30–4:45) — *valuation quality*
- Implied value band + **confidence** (driven by comp count, spread, recency).
- **$/sqm** and **time-adjusted "today's-equivalent"** prices; adjust the market-trend slider live.
- Untick a comp → band + confidence recompute. Flag an **IQR outlier**.

### 4 · Explainability & the LLM (4:45–6:15) — *Agent & LLM design, reasoning transparency*
- Ask "What's a fair offer range?" → streamed answer citing `[#1]`, `[#3]`, with the disclaimer.
- Ask something **out of data** (school ratings / will prices rise?) → it refuses, names what's missing.
- "Why deterministic retrieval + a locked-down LLM? In lending, comps must be reproducible and
  auditable. Temperature 0, fenced DATA block, no fabrication. The model explains; it never decides."
- Show the **Verify manually** panel (backs-onto, walkout, legal suite, arms-length) — straight from the call.

### 5 · The handoff (6:15–7:00) — *output artifact*
- Download the **Underwriting CSV (41HP)** — comps + blank Adj % / Adj $ / notes columns.
- Show the **PDF handoff** (subject, value band, comps, memo, aerial). "Drops into the 41HP template."

### 6 · Code & honesty (7:00–8:00) — *Code quality, Pragmatism*
- Quick repo tour: engine / API / UI separation, 30+ tests, Docker, README.
- Honest tradeoff: "No living-area in open data, so size uses a land proxy + beds/baths — the engine
  takes real GLA in one line. I'd rather be explicit than fake the one criterion I can't fully meet."
- Close: "Retrieval-first, explainable, human-in-the-loop — a strong starting point an underwriter
  can trust, not a black box."

---

**Demo pitfalls to avoid:** have `.env` (Mongo + Groq) set; pre-warm one search so the first call
isn't cold; keep the manual-subject tab ready as a backup if search is slow.
