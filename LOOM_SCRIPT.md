# Loom walkthrough — read-aloud script (~8 min)

**How to use this:** read the normal lines out loud, just as they're written.
`(pause)` = take a breath. `(beat)` = a tiny stop. `[SHOW: …]` = what to click — don't read these out.
Go slow. The pauses make you sound calm and sure of yourself.

**Before you record:** API + app running · `.env` has the Mongo URI and Groq key · do one search first
so it's warm · keep the manual-subject tab open as a backup.

---

### 0 · Hello  (0:00 – 0:45)
*[SHOW: the app open, a property and its comps already on screen]*

Hi — I'm [your name], and this is my project for the KV Capital challenge. (pause)

Every real estate loan starts with one question: (beat) what is this property worth? (pause) Right now,
someone answers that by hand — (beat) digging up similar recent sales, one by one, starting from an
empty page. (pause)

What I built does that first part for them. (beat) You give it a property, and a few seconds later you
get a ranked list of similar sales — (beat) with a short reason for each one. (pause) So the work starts
from a strong first draft instead of a blank page. (pause) Let me show you.

---

### 1 · What I built, and what I left out  (0:45 – 1:45)

First, let me be honest about what I chose to build — (beat) because knowing what to leave out matters
just as much. (pause)

I kept it to **homes in Calgary**, one good set of data, and one clear job: (beat) find good comparable
sales, fast. (pause) That's the part your team said matters most. (pause)

And one rule guided everything: (beat) this tool **helps** the underwriter — it doesn't replace them. (pause)
It hands you the shortlist. (beat) You still make the final call. (pause)

The whole thing is built in Python — (beat) a small web service, a database, a simple web page, and an
AI model that writes the explanations. (pause) Let's see it run.

---

### 2 · Finding the comparable sales  (1:45 – 3:45)
*[SHOW: the search box]*

I'll start by finding a property. (pause) The search is easy on you — (beat) you can type part of the
address, in any order, and it still finds it. *[SHOW: type `lynnwood dr`, pick a result, click "Use this subject"]*

Now look at this switch on the left — **KV comp criteria.** *[SHOW: point to the sidebar checkbox]* (pause)
When it's on, the tool only keeps sales that match the rules your team gave me: (beat) same kind of home,
(beat) within about three kilometres, (beat) sold in the last year, (beat) close in age, (beat) and
close in size. (pause)

Here's the list. *[SHOW: the comps table]* (pause) This **tick column** is the quick read — (beat) a green
tick means that sale passes *every* one of those rules. (pause) And up top, it tells me how many on the
list pass all of them. (pause)

I also wanted to show *why* each sale is ranked where it is — (beat) no mystery. *[SHOW: open the "Score breakdown" expander]*
(pause) This breaks the score down: (beat) how much came from distance, how much from how recently it
sold, how much from the price gap, and so on. (pause) So if someone asks why this sale beats that one,
(beat) the answer's right here. (pause)

And one quiet thing in the background: (beat) I only ever use real, completed sales — (beat) never homes
that were just listed and didn't sell. (pause) Your team told me that's one of the main reasons people
throw a comp out. (pause)

---

### 3 · The value estimate, and how sure it is  (3:45 – 5:00)
*[SHOW: the value-band numbers and the line below]*

From those sales, the tool gives a **value range** — (beat) a low end, a middle, and a high end. (pause)
Next to it is a **confidence score.** (beat) That goes up when there are plenty of sales, when the prices
are close together, and when they're recent. (pause)

And you stay in charge. *[SHOW: untick one comp in the table]* (pause) If a sale doesn't feel right, you
untick it — (beat) and the range and the confidence update right away. (pause) It also flags the odd
ones out for you. (pause)

Let me be straight about one thing. *[SHOW: stay on screen]* (pause) The sale prices in this data are
made up — (beat) they're built from the city's assessed values. (pause) So the range leans on those
assessments more than it would with real sales. (pause) With real sale prices, the exact same tool gives
a real market answer. (beat) I'd rather say that plainly than pretend otherwise. (pause)

---

### 4 · How it explains itself  (5:00 – 6:30)
*[SHOW: scroll down to the "Ask the assistant" chat]*

Now the part that explains things in plain English. (pause) I can just ask it a question. *[SHOW: click "What is a fair offer range?"]*

(pause — let it type out) Notice three things. (beat) It gives a *range*, not a single number. (beat) It
points to the exact sales it used — see the little numbers in brackets. (beat) And it always reminds you
this is an estimate, not an official appraisal. (pause)

This part is kept on a short leash on purpose. (pause) The model can only use the sales right in front
of it — (beat) no guessing, no made-up numbers. (pause) And I don't just hope it behaves — (beat) I
check it. (pause) Every dollar figure it gives has to fit inside the real range of those sales, and every
sale it points to has to actually exist. (pause) If it ever slips, (beat) the answer gets a warning. (pause)

Let me show you that. *[SHOW: type an out-of-data question, e.g. "what are the school ratings and crime rate here?"]*
(pause — let it answer) See — (beat) instead of making something up, it just says that's not in the
data. (pause)

And that was a real choice on my part. (pause) I *didn't* build some free-roaming AI that does whatever
it wants. (beat) For a loan, I'd rather the search be steady and repeatable, (beat) and keep the AI on a
tight leash just to explain it. (pause) I went for trust over flash. (pause)

---

### 5 · Handing it off  (6:30 – 7:15)
*[SHOW: the export buttons]*

When you're happy with the list, there are two ways to hand it off. (pause) The **spreadsheet** *[SHOW: hover the CSV button]*
drops the sales straight into your 41-H-P template, with blank columns ready for your adjustments. (pause)
And the **PDF** *[SHOW: hover the PDF button]* is a clean one-pager — (beat) the property, the value
range, the sales, the write-up, and a photo from above. (pause) Something you can drop into a file. (pause)

And every property has a real map and an aerial photo with a pin on it, *[SHOW: scroll up to the map / aerial for a second]*
(beat) so the sales aren't just rows in a table — (beat) you can see exactly where they are. (pause)

---

### 6 · The code, and one honest catch  (7:15 – 8:00)
*[SHOW: the project folders in your editor]*

A quick word on the code. (pause) It's tidy and split up sensibly — (beat) the engine, the web service,
the page, and the data steps each have their own spot. (pause) There are about forty tests, it runs in
Docker, and the README explains the thinking. (pause)

And the one honest catch: (beat) square footage. (pause) Your team mentioned matching within twenty
percent on size, but the public data only has *lot* size — (beat) not the size of the house itself. (pause)
So I use lot size plus bedrooms, bathrooms, and value as a stand-in — (beat) and I named it honestly
instead of pretending. (pause) The day real floor-area data shows up, (beat) it slots in with about one
line of code. (pause)

---

### 7 · Wrap up  (8:00 – 8:30)
*[SHOW: back on the app, the ranked list]*

So that's it. (pause) Good comps first, (beat) everything explained, (beat) and the underwriter still
makes the final call. (pause) A solid starting point your team could trust — (beat) not a black box. (pause)

Thanks so much for watching. (beat) I'd be happy to walk through any part of it.

---

*Aim for about eight minutes. If you're running long, section 6 (the code bit) is the safe one to cut —
the demo is what counts.*
