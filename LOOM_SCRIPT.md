# Loom walkthrough — read-aloud script (~8–9 min)

**How to use this:** read the plain lines out loud, word for word.
`(pause)` = take a breath. `(beat)` = a tiny half-second stop. `[SHOW: …]` = what to do on
screen — don't read these out. Aim for a calm pace; pauses are your friend on camera.

**Before you hit record:** API + Streamlit running · `.env` has the Mongo URI and Groq key ·
do one search ahead of time so the first call is warm · keep the manual-subject tab open as a backup.

---

### 0 · Intro  (0:00 – 0:45)
*[SHOW: the running Streamlit app, subject + comps already on screen]*

Hi — I'm [your name], and this is my submission for the KV Capital comp-analysis challenge. (pause)

The problem I set out to solve is the one every real-estate-debt deal starts with: (beat) *what is
this property actually worth?* (pause) Today an underwriter answers that by hand — pulling
comparable sales, one at a time, from a blank sheet. (pause)

What I built gives them a ranked, explained shortlist of comparable sales in a few seconds — (beat)
so they start from a strong foundation instead of that blank sheet. (pause) Let me walk you through it.

---

### 1 · The job, and the scope I chose  (0:45 – 1:45)

I want to be upfront about scope, because pragmatism matters here. (pause)

I focused on **Calgary residential**, one credible dataset, and one clear job: (beat) fast,
trustworthy comp *retrieval* — which is what your team said matters most — with a defensible value
estimate second. (pause)

And one principle shaped every decision: (beat) this is **decision support, not automation.** (pause)
The tool produces the shortlist; (beat) the underwriter still makes the final pick. (pause)

The stack is simple and all Python: (beat) a FastAPI service, a MongoDB Atlas database, a Streamlit
front end, and a Groq language model for the explanations. (pause) Let's see it work.

---

### 2 · Retrieval and ranking — the core  (1:45 – 3:45)
*[SHOW: the search box]*

I'll start by finding a subject property. (pause) The search is deliberately forgiving — (beat) I can
type partial words, in any order, lowercase. *[SHOW: type `lynnwood dr`, pick a result, click "Use this subject"]*

Now, notice this toggle on the left — **KV comp criteria.** *[SHOW: point to the sidebar checkbox]*
(pause) When it's on, the engine pins the filters to the rules your team gave me on the call: (beat)
same property type, (beat) within about three kilometres, (beat) sold in the last twelve months,
(beat) age within ten years, (beat) and size within roughly twenty percent. (pause)

Here are the ranked comps. *[SHOW: the comps table]* (pause) See this **"KV check" column** — (beat)
a green tick means that comp meets *all* of those criteria. (pause) And up here it tells me how many
of the shortlist clear the full bar. (pause)

I also want the ranking to be transparent, not a black box. *[SHOW: open the "Score breakdown" expander]*
(pause) This shows exactly how each comp's score is built — (beat) how many points came from distance,
from recency, from the value gap, and so on. (pause) So if anyone asks *why* comp number one beats
comp number six, the answer is right here on the screen. (pause)

One more thing under the hood worth mentioning: (beat) I only ever rank genuine, closed sales — (beat)
no unsold listings — because your team told me that's a top reason humans reject a candidate. (pause)

---

### 3 · The value estimate, and how confident it is  (3:45 – 5:00)
*[SHOW: the value-band metrics and the implied value band line]*

From the included comps, the tool gives an **implied value band** — (beat) a low, a median, and a
high. (pause) Next to it is a **confidence score**, driven by how many comps I found, how tight the
prices are, and how recent the sales are. (pause)

And the underwriter stays in control. *[SHOW: untick one comp in the table]* (pause) If I think a comp
doesn't belong, I untick it — (beat) and the value band and the confidence recompute instantly. (pause)
Outliers are flagged automatically, too. (pause)

I'll be honest about one thing here. *[SHOW: keep talking over the screen]* (pause) Because the sale
prices in this dataset are synthetic — derived from the assessed values — the value band leans on
assessment more than it would in real life. (pause) With real arm's-length sale prices, the very same
engine surfaces genuine market signal. (beat) I flag that openly rather than dress it up as something
it isn't. (pause)

---

### 4 · Explaining the reasoning — the language model  (5:00 – 6:30)
*[SHOW: scroll to the "Ask the assistant" chat]*

Now the explanation layer. (pause) I can ask it a plain-English question. *[SHOW: click "What is a fair offer range?"]*

(pause — let it stream) Notice it answers in a *range*, (beat) it cites the specific comps in square
brackets — number one, number three — (beat) and it ends with a clear reminder that this is an
automated estimate, not a formal appraisal. (pause)

This part is locked down on purpose. (pause) The model is told to use *only* the comps in front of it —
(beat) no outside market knowledge, no invented numbers. (pause) And I don't just trust it to behave —
(beat) I *check* it: (beat) every figure it quotes has to fall inside the actual comp price range, and
every citation has to point at a real comp. (pause) If it ever steps outside that, the answer gets
flagged. (pause)

Let me show you the guardrail. *[SHOW: type something out-of-data, e.g. "what are the school ratings and crime rate here?"]*
(pause — let it answer) See — (beat) instead of making something up, it says plainly that that
information isn't in the data. (pause)

And that's the deliberate design choice I'd flag for the panel. (pause) I *didn't* build a free-roaming
autonomous agent. (beat) For a lending decision, I'd rather the retrieval be deterministic and
reproducible, and keep the model on a tight leash explaining it. (pause) Trust over flash.

---

### 5 · The handoff  (6:30 – 7:15)
*[SHOW: the export buttons]*

When the underwriter's happy, there are two handoffs. (pause) The **underwriting CSV** *[SHOW: hover the CSV button]*
drops the comps straight into your 41-H-P template, with blank adjustment columns ready to fill. (pause)
And the **PDF** *[SHOW: hover the PDF button]* is a one-page summary — (beat) subject, value band, the
comps, the memo, and an aerial of the property. (pause) Something you could attach to a file. (pause)

And every property has a real map and a pinned aerial view, *[SHOW: scroll up to the map / aerial briefly]*
(beat) so the comps aren't just rows in a table — you can see where they actually are. (pause)

---

### 6 · The code, and a bit of honesty  (7:15 – 8:15)
*[SHOW: the repo / project structure in your editor]*

Quickly, on the engineering. (pause) It's cleanly separated — (beat) the ranking engine, the API, the
UI, and the data pipeline each live in their own place. (pause) There are around forty automated tests,
it's Dockerised, and the README walks through the approach and the tradeoffs. (pause)

And on tradeoffs — (beat) the honest one is square footage. (pause) Your team mentioned size within
twenty percent, but the open data only has *land* size, not finished floor area. (pause) So I use a
land-size proxy plus beds, baths, and assessed value — (beat) and I labelled it honestly rather than
pretend I'm matching on living area I don't have. (pause) The engine takes a real floor-area field in
about one line the day it's available. (pause)

---

### 7 · Close  (8:15 – 8:45)
*[SHOW: back to the app, the ranked comps]*

So that's it. (pause) Retrieval-first, (beat) fully explainable, (beat) with the underwriter keeping the
final call. (pause) A strong starting point your team could actually trust — (beat) not a black box. (pause)

Thanks very much for watching. (beat) I'd love to talk through any of it.

---

*Total target: ~8 and a half minutes. If you're running long, the section you can trim is 6 (the code
tour) — the demo itself is what carries the score.*
