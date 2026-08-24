# The end-of-session form (Part B)

Part A goes out **mid-lesson** and proves someone was there. Part B goes out at
the **end** and measures what landed. They are two different forms because they
are released at two different moments, and one form cannot be both.

This page covers the Part B template, its one-time manual step, the rotating
question, and what a teacher prepares each week.

---

## The six fields

| # | Field | Type | Required |
|---|---|---|---|
| 1 | Email + timestamp | collected automatically by Google | — |
| 2 | *"I could explain today's topic to someone"* | 1–7 scale | yes |
| 3 | *"In one sentence, what are you taking away from today?"* | short text | yes |
| 4 | **The rotating question** — changes weekly | short text | yes |
| 5 | *"Who helped you today?"* | short text | **no** |
| 6 | ☐ *"I'd like someone to check in with me"* | checkbox | **no** |

**The order is load-bearing, not cosmetic.** Do not reorder these because a
different order looks tidier:

* Field 2 opens the form with a **click, not a text box**. Surveys that open
  with a simple multiple-choice complete at **89%** against **83%** for
  open-ended. The cheapest possible first action is what gets someone into the
  form at all.
* Field 6 is **last**, after rapport is built. A sensitive item placed early
  measurably raises abandonment of the whole form, not just of that item.
* Fields 5 and 6 are **optional**. Forcing either would be actively wrong: an
  optional field that must be answered is not optional, and a compulsory "do you
  need help?" is a different question from a voluntary one.

### Why six fields and not seven

Somebody will want to add one more question. The answer is a number, not a
preference:

* Three questions → four: completion drops **18%**.
* Past eight minutes: response rates fall roughly **60%**.
* Fatigued respondents repeat the same answer about **a third more often** —
  which corrupts the confidence trend rather than merely shortening it.

The rotating slot exists precisely so a seventh field is never needed. If a new
question genuinely has to be asked, put it in the rotating slot for one week.

This text is shown in the console anywhere a staff member might be tempted to
add a field.

### Why a 7-point scale and not a 5-point one

Preston & Colman found scales under 5 points lose reliability, with 7–10
performing best; Krosnick & Presser converge on 5–7 as optimal. Critically,
**5-point scales induce interpolation** — respondents try to answer *between*
two values. This field gets graphed, so the extra resolution is the difference
between a readable trend and a stepped one.

Labels are on the endpoints only ("Not at all" / "Easily"). Labelling every
point invites people to read the words instead of the position.

---

## One-time setup

Part B needs **its own template form and its own manual Verified-email step.**
Part A's being verified says nothing about Part B's: email collection is a
property of a *form*, and it is carried onto session forms by the Drive copy —
not by having been set on some other form in the same account.

So the manual step happens twice, once. That is the honest cost of the trap, not
a shortcut around it. See [`docs/google-api-traps.md`](../google-api-traps.md),
trap 2.

> **You may not need the manual step.** As of August 2026 the live API accepts
> `emailCollectionType: VERIFIED` directly, so pressing **Verify** straight after
> **Create** may simply go green. That is fine and expected — verification reads
> the setting back from the API either way, so a green tick means the API said
> `VERIFIED`, never that a call returned 200. If it does not go green, do the
> manual step below; it has not gone anywhere.

### From the console

1. Open **Templates**. Both parts are shown side by side, including one that
   does not exist yet.
2. Under **Part B — end-of-session check-in**, press **Create the Part B
   template**.
3. Open the template in Google Forms and set
   **Settings → Responses → Collect email addresses → Verified**.
4. Press **Verify the Part B template**. It reads the setting back from the API
   and believes only what the API says.

### From a terminal

```
cufa template create --part b
# do the manual step in the Forms editor
cufa template verify --part b
cufa template status              # shows both parts
```

Provisioning any Part B form is blocked until step 4 passes, and step 4 is
re-run before every provisioning run — so a template someone edits back to
responder-input fails loudly instead of quietly producing forms that collect a
typed address.

---

## The rotating question

One question, changing by week. The schedule lives in `config/rotation.json` and
is **owned and edited by the Director of Programs**, not by this code.

| Week | What it asks |
|---|---|
| 1, 4, 7, 10 | The teacher's own question for that lesson |
| 2, 5, 8 | *"What's still unclear?"* — the muddiest point |
| 3, 6, 9 | *"One way you'd use this in your project"* — application |

Over ten weeks all three dimensions get collected, and no fellow ever faces more
than four questions in one sitting.

**The teacher's question comes up most often because it is the only genuinely
unfakeable one** — it depends on content that only someone present would know.

### Weeks past the tenth

The schedule **wraps**. Week 11 asks what week 1 asked, week 12 what week 2
asked, and so on around the cycle. Week *N* maps onto week `((N − 1) mod 10) + 1`.

The console marks a wrapped week as "repeats wk 3" so it is visible rather than
implied. To stop wrapping instead, set `"wrap": false` in the config — a week
past the end then becomes an error rather than a repeat.

### The week is typed in, not worked out from the date

Every session has a **week number**, set on the session form in the console.
Nothing derives it from the calendar.

That is deliberate. Sessions get rescheduled, skipped and doubled up. A
calendar-derived week desynchronises the whole rotation the first time that
happens, and nothing announces it — the forms just start asking the wrong
questions.

A session with **no** week number is legal: it simply has no Part B form. A
makeup session or a one-off is not a week of the rotation. `cufa provision
--cohort <id> --part b` skips those and says so; naming one explicitly with
`--session` fails loudly, because there the operator asked for that one.

### What the teacher prepares each week

On a **teacher-question week**, one sentence: their own question about that
lesson's content. Set it on the session, either in the console (the field
appears when the week calls for it) or:

```
cufa session edit --session <id> --week 4 \
  --teacher-question "Which question from the practice interviews would you ask differently?"
```

**Provisioning refuses if it is missing.** No generic question is substituted —
a stand-in would produce data that looks identical and is worth nothing.

To see which weeks still need one:

```
cufa rotation --cohort <id>
```

or open **Rotation** in the console, which lists every upcoming week, what it
will ask, and which sessions are missing a question, with a link straight to the
one that needs it.

On a muddiest-point or application week the teacher prepares nothing — the
wording is fixed in the config.

---

## Provisioning a session's Part B form

From the console: open the session and press **Provision Part B**. From a
terminal:

```
cufa provision --session <id> --part b            # one session
cufa provision --cohort  <id> --part b            # every numbered session
cufa provision --session <id> --part b --dry-run  # what would happen
```

Safe to press twice: an existing form is shown, never duplicated. A dry run
still verifies the template and still resolves the rotation, so "this would be
blocked" is what it reports rather than a clean plan.

### If a template cannot be opened

If the console says a template **cannot be opened by the connected account**, the
stored form id belongs to something this account cannot see. Almost always that
means the database still holds forms from `make demo`, which are simulated and
have never existed in Drive.

The screen offers **Create a replacement template**, or from a terminal:

```
cufa template replace --part a
cufa template replace --part b
```

Each retires the old row — it is not deleted, so forms copied from it keep their
provenance — and creates a fresh form. It is a **new** form, so its Verified
state has to be confirmed again on it.

Session forms left over from the demo need no action: provisioning notices a
simulated form and copies a real one in its place, recording that it did.

### The question-id map

After the form is built, it is **read back** and the mapping from Google's
question ids to the six fields is recorded.

This is not belt-and-braces. Responses come back keyed by question id — not by
title, not by position — and whether a Drive copy preserves those ids across
copies is **not something anyone has confirmed**. Assuming either way produces
answers filed under the wrong field, with no error, and numbers that look
entirely plausible.

So: nothing is hardcoded, nothing is matched by title (the rotating slot's title
changes weekly by design), and **a form whose map is missing or incomplete
refuses to ingest**.

If you see that refusal, re-provision the session's Part B form. Provisioning
reads the form back and records the map again, and on an already-published form
it does that *without touching a single question* — so it is safe to press on a
form that is already collecting.

The map is visible on the session screen under "Question id map", and on the
responses screen. It also records **the exact wording each field showed**, so
"what was actually asked in week 3" is answerable from the database alone, even
after the config has changed.

---

## Collecting responses

```
cufa pull --session <id> --part b
cufa pull --cohort  <id> --part b
```

or **Pull Part B responses** on the session screen. Incremental: each form has a
watermark, so pulling repeatedly costs one request per form rather than
re-reading everything. Safe to re-run — a second pull writes nothing.

### What happens to each field

* **Confidence** is stored as the integer 1–7, exactly as submitted. Never
  rescaled, never converted to a percentage. A value outside 1–7 is stored as
  *nothing*, with the raw value kept alongside and a warning printed — it is
  **never clamped**, because a clamped 8 is a plausible number invented from a
  broken form.
* **Takeaway** and the **rotating answer** are stored verbatim, whitespace
  included. They are **counted, never graded**. Nothing rates how well a
  sentence is written: doing so would penalise fellows writing in a second
  language, or writing differently, for reasons unrelated to engagement.
* **Shoutouts** are split on commas, "and", "&" and newlines, then matched
  against the roster **within the cohort only**. One unambiguous match links
  automatically; anything else waits for a person. See below.
* **The help checkbox** is routed separately and immediately. See
  [`docs/safeguarding.md`](../safeguarding.md).

Part A and Part B are **independent observations**. A fellow may submit one and
not the other; both are valid data, not errors. Neither is ever used to fill in
the other.

---

## Shoutouts

Free-typed names, resolved conservatively. Open **Shoutouts** in the console, or:

```
cufa shoutouts review --cohort <id>
cufa shoutouts link --shoutout <id> --fellow <id> --by you@civicsunplugged.org
```

Two things end up in that queue, and only one is a problem:

* **A name matching two fellows** is never linked automatically. Attributing
  someone's praise to the wrong person is worse than leaving it unattached,
  because a wrong link is invisible and nobody ever finds out.
* **A name matching nobody is not an error.** Guest speakers, teachers and
  people outside the cohort get thanked too. Leave it.

There is deliberately **no leaderboard, ranking, points table or public
display**. Shoutouts are collected and resolved, and that is all — see
[`docs/decisions.md`](../decisions.md), ADR-028.

---

## Muddiest-point themes

On muddiest-point weeks, the *"what's still unclear"* answers can be clustered
into 2–5 themes for the teacher:

```
cufa themes --session <id>
cufa themes --session <id> --regenerate
```

or **Regenerate themes** on the session's responses screen.

* The model receives the answers as **anonymous text**. No names, no addresses,
  no ids, no counts per person, nothing from the help checkbox.
* Its only job is grouping by **subject matter**. It does not judge any
  individual answer, rate anyone's writing, or say anything about a person.
* **No API key means no themes and a clear message** — never a failed run. The
  answers are the data; themes are a convenience over them.
* Regenerating **supersedes** the previous set rather than overwriting it, so a
  teacher who planned a lesson around last week's themes can still see what they
  read.

Fewer than three answers produces a message rather than themes: three sentences
do not have a theme, and a model asked to find one will invent it.

**This is the feedback loop, and it matters more than it looks.** Showing
fellows what their confusion changed about the teaching is what makes the form
visibly *for* them rather than *about* them. The teacher-facing view is built.
The fellow-facing view is deliberately **not** — that is a decision for the data
owner, not a default.

---

## Reading the confidence numbers

```
cufa report --cohort <id> --confidence
```

Median and interquartile range per week. **Never a mean** — a 7-point Likert
scale is ordinal, and the mean of ordinal data is not a meaningful quantity.

**The signal is the trend and the dip, not the level.** Self-rated confidence is
noisy and weakly calibrated: some fellows never say 7 and some never say below
4. A fellow moving 6 → 3 across two sessions is worth a conversation; a fellow
sitting flat at 4 mostly is not. Do not read a single low score as a finding —
that sentence is printed next to every chart for a reason.

### Straight-lining

A fellow who submits the same confidence value four or more sessions running is
flagged on the **Review → Straight-lining** tab.

That is a **data-quality flag on the responses**, not a finding about the
person, and it enters no count, rate or score anywhere. Fatigued respondents
repeat an answer about a third more often; that is a fact about the survey.

---

## See also

* [`docs/safeguarding.md`](../safeguarding.md) — the help-request path, for
  non-engineers.
* [`docs/google-api-traps.md`](../google-api-traps.md) — including trap 5, the
  question-id mapping.
* [`docs/setup/console.md`](console.md) — running the console.
* [`docs/decisions.md`](../decisions.md) — ADR-021 to ADR-028.
