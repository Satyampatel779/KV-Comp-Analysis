# Loom walkthrough — read-aloud script (~8–9 min)

**How to use this:** read the normal lines out loud — but read them like you're talking to a colleague,
not presenting. The whole point is it shouldn't *sound* like a script. (pause) A few tips:
glance up at the camera between lines · let yourself slow down · and if you stumble a little, just keep
going — that actually makes you sound more real, not less.
`(pause)` = breathe. `(beat)` = tiny stop. `[SHOW: …]` = what to click — don't read those out.

**Before you record:** API + app running · `.env` has the Mongo URI and Groq key · do one search first
so it's warm · keep the manual-subject tab open as a backup.

---

### 0 · Hello  (0:00 – 0:45)
*[SHOW: the app open, a property and its comps already on screen]*

Hey — I'm [your name]. (beat) Thanks for taking a look at this. (pause)

So this whole thing really comes down to one question — (beat) the one every real estate loan starts with:
(beat) what's this place actually worth? (pause)

And right now? (beat) Somebody answers that by hand. (beat) Digging up similar sales, one at a time,
basically starting from a blank page. (pause)

What I built does that first chunk *for* you. (beat) You give it a property, and a couple seconds later —
(beat) you've got a ranked list of similar sales, each one with a quick reason next to it. (pause) So you
start from a real first draft instead of nothing. (pause) Here, let me just show you.

---

### 1 · What I built, and what I left out  (0:45 – 1:40)

Okay — before I dive in, (beat) let me be straight about what I chose to build. (beat) Because honestly,
knowing what to leave *out* is half the job here. (pause)

I kept it to homes, in Calgary, one solid set of data — (beat) and one clear goal: (beat) find good
comparable sales, fast. (beat) That's the part your team said matters most. (pause)

And there's one rule I stuck to the whole way: (beat) this thing *helps* the underwriter. (beat) It
doesn't replace them. (pause) It hands you the shortlist. (beat) You still make the call. (pause)

It's all built in Python, nothing fancy — (beat) a little web service, a database, a simple web page, and
an AI model that writes the plain-English parts. (pause) Alright — let's actually look at it.

---

### 1.5 · The data behind it  (1:40 – 2:30)

Real quick on the data — (beat) because honestly, that's most of the work you never see. (pause)

So I started with the City of Calgary's public property records. (beat) Same kind of public info your team
already pulls. (pause) And I cleaned the whole thing up — (beat) got it all into one tidy shape. (beat) The
type of home, the year it went up, the lot size, where it sits, what it's assessed at. (pause)

Now — actual sale prices, those aren't public. (beat) So I had to make up some realistic stand-in sales to
test against. (beat) Hold that thought, though — I'll come back to it. (pause)

Then I loaded all of it into a cloud database — (beat) call it a hundred and twenty thousand homes,
seventy thousand sales — (beat) and I indexed every location, so that "within three kilometres" check is
basically instant. (pause) It's a *lot* of data, so I trimmed it down to a free slice that still covers the
whole city. (pause) And the best part — (beat) the whole thing rebuilds from a handful of small scripts.
(beat) So none of this is a black box. (pause)

---

### 2 · Finding the comparable sales  (2:30 – 4:15)
*[SHOW: the search box]*

Okay, let me find a property. (pause) And the search is pretty forgiving — (beat) I can just type part of
the address, in any order, and it'll find it. *[SHOW: type `lynnwood dr`, pick a result, click "Use this subject"]*

Now — see this switch over here? (beat) **KV comp criteria.** *[SHOW: point to the sidebar checkbox]* (pause)
When that's on, it only keeps sales that match the rules your team gave me on the call. (beat) Same kind of
home. (beat) Within about three kilometres. (beat) Sold in the last year. (beat) Close in age. (beat) Close
in size. (pause)

And — there's the list. *[SHOW: the comps table]* (pause) That just searched the whole city — (beat) all
those homes and sales — in like, two seconds. (pause)

See this little tick column? (beat) A green tick means that sale clears *every single* one of those rules.
(pause) And right up top, it tells me how many of them clear the whole bar. (pause)

And look — I really didn't want this to be some mystery score. *[SHOW: open the "Score breakdown" expander]*
(pause) So this breaks it right down. (beat) How much came from distance, how much from how recently it
sold, how much from the price gap. (pause) So if anyone asks "well, why's *this* one ahead of *that* one?" —
(beat) it's right there. (pause)

Oh, and one thing happening quietly in the background — (beat) I only ever use real, completed sales. (beat)
Never something that was just listed and never actually sold. (pause) Your team told me that's one of the
big reasons people toss a comp out. (pause)

---

### 3 · The value estimate, and how sure it is  (4:15 – 5:20)
*[SHOW: the value-band numbers and the line below]*

So from those sales, you get a value *range* — (beat) a low end, a middle, and a high end. (pause) And
right next to it, a confidence score. (beat) That climbs when there's plenty of sales, when the prices sit
close together, and when they're recent. (pause)

And you stay in the driver's seat the whole time. *[SHOW: untick one comp in the table]* (pause) Don't like
one of these sales? (beat) Untick it — (beat) and watch, the range and the confidence just... update.
(pause) Right there. (beat) It even flags the odd ones out for you. (pause)

Okay — and here's where I want to be totally straight with you. *[SHOW: stay on screen]* (pause) Remember
those made-up sale prices? (beat) They're built off the city's assessed values. (beat) So this range leans
on those assessments a bit more than it would in real life. (pause) Plug in real sale prices, though — (beat)
and the exact same tool gives you a real market answer. (beat) I'd just rather tell you that straight than
dress it up. (pause)

---

### 4 · How it explains itself  (5:20 – 6:45)
*[SHOW: scroll to the "Ask the assistant" chat]*

Right — now the part that explains itself, in plain English. (pause) I can literally just ask it. *[SHOW: click "What is a fair offer range?"]*

(pause — let it type out) Okay, watch what it does here. (beat) It gives a *range* — not some magic single
number. (beat) It points to the exact sales it used — (beat) see those little numbers in brackets? (beat)
And it always reminds you: (beat) this is an estimate, not an official appraisal. (pause)

And this is on a *short* leash, on purpose. (pause) It's only allowed to use the sales sitting right in
front of it. (beat) No guessing. (beat) No inventing numbers. (pause) And here's the thing — (beat) I don't
just *trust* it to behave. (beat) I check it. (pause) Every dollar amount it gives has to actually fit
inside the range of those sales. (beat) Every sale it points to has to be real. (pause) If it ever steps
outside that — (beat) the answer gets a warning slapped right on it. (pause)

Let me just prove that to you. *[SHOW: type an out-of-data question, e.g. "what are the school ratings and crime rate here?"]*
(pause — let it answer) See that? (beat) Instead of just... making something up — (beat) it tells me
straight: that's not in the data. (pause)

And that was a real decision on my end. (pause) I *didn't* build some AI that goes off and does whatever it
feels like. (beat) For a loan? (beat) I want the search steady and repeatable — (beat) and the AI kept on a
short leash, just explaining it. (pause) Trust over flash. (pause)

---

### 5 · Handing it off  (6:45 – 7:30)
*[SHOW: the export buttons]*

Okay — once you're happy with the list, (beat) there's two ways to take it with you. (pause) The
spreadsheet *[SHOW: hover the CSV button]* drops the sales straight into your 41-H-P template — (beat) with
blank columns sitting there, ready for your adjustments. (pause) And the PDF *[SHOW: hover the PDF button]*
is a clean one-pager. (beat) The property, the range, the sales, the write-up, and a shot from above.
(pause) Something you can just drop in the file. (pause)

Oh — and every property's got a real map, and an aerial photo with a pin right on it. *[SHOW: scroll up to the map / aerial for a second]*
(beat) So these aren't just rows in a table — (beat) you can actually *see* where everything is. (pause)

---

### 6 · The code, and one honest catch  (7:30 – 8:10)
*[SHOW: the project folders in your editor]*

Quick word on the code itself. (pause) It's tidy — (beat) split up the way you'd want it. (beat) The
engine, the web service, the page, the data steps — (beat) each in its own spot. (pause) Around forty
automated tests, it runs in Docker, and the README walks through all the thinking. (pause)

And — okay, the one honest catch. (beat) Square footage. (pause) Your team mentioned matching within twenty
percent on size. (beat) Thing is — the public data only gives me *lot* size. (beat) Not the size of the
actual house. (pause) So I use lot size, plus beds, baths, and value, as a stand-in — (beat) and I *named*
it honestly, instead of pretending it's something it isn't. (pause) The day real floor-area data shows up?
(beat) It slots in with about one line of code. (pause)

---

### 7 · Wrap up  (8:10 – 8:45)
*[SHOW: back on the app, the ranked list]*

So... yeah. (beat) That's the tool. (pause) Good comps first, (beat) everything explained, (beat) and the
underwriter still makes the final call. (pause) A solid place to *start* from — (beat) something you could
actually trust. (beat) Not a black box. (pause)

Thanks so much for watching. (beat) And honestly — I'd love to walk through any piece of it with you.

---

*Aim for about eight and a half minutes. If you're running long, section 6 (the code bit) is the safe one
to trim — the demo itself is what carries it.*
