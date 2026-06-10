# Loom walkthrough — read-aloud script (~7–8 min)

**How to use this:** read the lines out loud, just as written. Keep it slow and clear.
`(pause)` = take a breath. `[SHOW: …]` = what to click — don't read these out.

**Before you record:** API + app running · `.env` has the Mongo URI and Groq key · do one search first
so it's warm · keep the manual-subject tab open as a backup.

---

### 0 · Start  (0:00 – 0:40)
*[SHOW: the app open, a home and its matches on screen]*

Hi, I'm [your name]. (pause)

Every home loan starts with one question: (pause) what is this home worth? (pause)

Right now, someone answers that by hand. They look up similar sales, one by one. (pause)

My tool does that first step for them. (pause) You pick a home, and in a few seconds you get a ranked list
of similar sales — each one with a short reason. (pause)

So you start with a real first draft, not a blank page. (pause) Let me show you.

---

### 1 · What I built  (0:40 – 1:30)

First, let me be clear about what I built. (pause)

I kept it simple. Homes in Calgary, one set of data, one job: (pause) find good similar sales, fast. (pause)
That's the part your team said matters most. (pause)

And one rule the whole way through: (pause) this tool *helps* the underwriter. It does not replace them.
(pause) It gives you the short list. You still make the final call. (pause)

It's all built in Python. A small web service, a database, a simple web page, and an AI model for the
write-ups. (pause) Let's look at it.

---

### 1.5 · The data  (1:30 – 2:15)

A quick note on the data, because that was a lot of the work. (pause)

I started with the City of Calgary's public home records — (pause) the same public info your team already
uses. (pause) I cleaned it all up and put it in one simple shape: (pause) the type of home, the year it was
built, the lot size, where it sits, and the city's value for it. (pause)

Real sale prices are not public. So I made realistic stand-in sales to test with. (pause) I'll come back to
that in a minute. (pause)

Then I loaded it all into a cloud database — (pause) about 120,000 homes and 70,000 sales. (pause) And I
set it up so the "within 3 kilometres" search is instant. (pause)

It's a lot of data, so I kept a free slice that still covers the whole city. (pause) And it all rebuilds
from a few small scripts. Nothing here is hidden. (pause)

---

### 2 · Finding similar sales  (2:15 – 3:45)
*[SHOW: the search box]*

Let me find a home. (pause) The search is easy — I can type part of the address, in any order. *[SHOW: type `lynnwood dr`, pick a result, click "Use this subject"]*

Now look at this switch — **KV comp criteria.** *[SHOW: point to the sidebar checkbox]* (pause) When it's
on, it only keeps sales that match the rules your team gave me. (pause) Same type of home. Within about 3
kilometres. Sold in the last year. Close in age. Close in size. (pause)

And here's the list. *[SHOW: the table]* (pause) That searched the whole city in about two seconds. (pause)

See this tick column? (pause) A green tick means that sale passes *all* of those rules. (pause) And up top,
it shows how many pass all of them. (pause)

I also wanted to show *why* each sale ranks where it does. *[SHOW: open the "Score breakdown" expander]*
(pause) This breaks the score down — (pause) how much came from distance, from how recently it sold, from
the price gap. (pause) So if someone asks why one sale beats another, the answer is right here. (pause)

One more thing: I only use real, finished sales. (pause) Never a home that was just listed and never sold.
(pause) Your team said that's a top reason people drop a sale. (pause)

---

### 3 · The value, and how sure it is  (3:45 – 4:45)
*[SHOW: the value range]*

From those sales, you get a value range — (pause) a low, a middle, and a high. (pause) Next to it is a
score for how sure it is. (pause) It goes up with more sales, with prices that are close together, and with
recent sales. (pause)

And you stay in control. *[SHOW: untick one comp]* (pause) If a sale looks wrong, untick it — (pause) and
the range and the score update right away. (pause) It also flags the odd ones for you. (pause)

Now let me be honest about one thing. *[SHOW: stay on screen]* (pause) Remember those stand-in sale prices?
(pause) They come from the city's values. (pause) So this range leans on those city values more than it
would in real life. (pause) With real sale prices, the same tool gives a real market answer. (pause) I'd
rather just tell you that plainly. (pause)

---

### 4 · How it explains itself  (4:45 – 6:00)
*[SHOW: scroll to the "Ask the assistant" chat]*

Now the part that explains things in plain words. (pause) I can just ask it a question. *[SHOW: click "What is a fair offer range?"]*

(pause — let it answer) Look at what it does. (pause) It gives a range, not one magic number. (pause) It
points to the exact sales it used — see the small numbers in brackets. (pause) And it always says: this is
an estimate, not an official appraisal. (pause)

This part is kept on a tight leash, on purpose. (pause) It can only use the sales in front of it. No
guessing. No made-up numbers. (pause)

And I don't just trust it — I check it. (pause) Every dollar amount has to fit inside the range of those
sales. (pause) Every sale it points to has to be real. (pause) If it breaks that, the answer gets a
warning. (pause)

Let me show you. *[SHOW: type "what are the school ratings and crime rate here?"]* (pause — let it answer)
See? (pause) Instead of making something up, it says plainly: that's not in the data. (pause)

And that was on purpose. (pause) I did not build an AI that does whatever it wants. (pause) For a loan, I
want the search to be steady and repeatable — (pause) and the AI on a tight leash, just explaining. (pause)
Trust over flash. (pause)

---

### 5 · Handing it off  (6:00 – 6:40)
*[SHOW: the export buttons]*

When you're happy with the list, there are two ways to take it. (pause) The spreadsheet *[SHOW: hover the CSV button]*
drops the sales into your 41-H-P template, with blank columns for your adjustments. (pause) The PDF *[SHOW: hover the PDF button]*
is a clean one-page summary — (pause) the home, the range, the sales, the write-up, and a photo from above.
(pause)

And every home has a map and an aerial photo with a pin on it. *[SHOW: scroll up to the map / aerial]* (pause)
So the sales aren't just rows. You can see where they are. (pause)

---

### 6 · The code, and one honest catch  (6:40 – 7:20)
*[SHOW: the project folders in your editor]*

A quick word on the code. (pause) It's tidy and split up well — (pause) the engine, the web service, the
page, and the data steps each have their own place. (pause) There are about forty tests, it runs in Docker,
and the README explains the thinking. (pause)

And one honest catch: square footage. (pause) Your team mentioned size within twenty percent. (pause) But
the public data only has *lot* size — not the size of the house itself. (pause) So I use lot size, plus
beds, baths, and value, as a stand-in. (pause) And I named it honestly. (pause) The day real floor-size
data shows up, it slots in with about one line of code. (pause)

---

### 7 · Wrap up  (7:20 – 7:45)
*[SHOW: back on the app, the list]*

So that's the tool. (pause) Good similar sales first. Everything explained. And the underwriter still makes
the final call. (pause) A solid place to start — and one you can trust. (pause) Not a black box. (pause)

Thanks for watching. (pause) I'd be happy to walk through any part of it.

---

*Aim for about seven and a half minutes. If you run long, section 6 (the code bit) is the safe one to cut.*
