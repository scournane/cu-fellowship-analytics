# Implementation Prompt: Part B — End-of-Session Check-in

**This builds on Part A.** See `docs/implementation/part-a-passphrase-checkin.md`.
Part A's console, Google OAuth, template-and-copy provisioning, Supabase schema,
adjudication tiers, and test harness already exist. Extend them. Do not rebuild
them, and do not fork a parallel set of tables or a second web app.

**Read Part A's "Three verified Google API traps" section before writing any
Google code.** All four apply here unchanged: API-created forms are unpublished by
default, `emailCollectionType: VERIFIED` is unreliable via `batchUpdate`, the REST
API cannot link a response spreadsheet, and service accounts cannot own Workspace
assets. Part B needs its own template form, which means its own one-time
human-verified Verified-email step.

---

## What Part B is

Part A proves presence. Part B measures what landed.

It is a second, separate form released at the **end** of the session — not the
same form as Part A, because the two are released at different moments and a
single form cannot be both. A session therefore has two forms.

Six fields, deliberately ordered:

| # | Field | Type | Why this position |
|---|---|---|---|
| 1 | Email + timestamp | auto | — |
| 2 | Confidence: *"I could explain today's topic to someone"* | 7-point scale | opens with a click, not a text box |
| 3 | One-sentence takeaway | short text | core processing artifact |
| 4 | **Rotating slot** | varies by week | see below |
| 5 | Peer shoutout: *"who helped you today?"* | optional text | — |
| 6 | ☐ *"I'd like someone to check in with me"* | checkbox | sensitive → last |

**The order is load-bearing, not cosmetic.** Surveys opening with a simple
multiple-choice complete at **89% versus 83% for open-ended**, and sensitive items
placed early measurably raise abandonment. The help checkbox goes last, after
rapport is built. Do not reorder these fields for visual balance or because a
different order seems tidier.

### Why a 7-point scale, not 5

Preston & Colman found scales under 5 points lose reliability while 7–10 perform
best; Krosnick & Presser converge on 5–7 as optimal. Critically, **5-point scales
induce interpolation** — respondents try to answer *between* two values. This
field gets graphed, so the extra resolution matters.

Use `scaleQuestion` with `low: 1`, `high: 7`, and labels on the endpoints only.

### The rotating slot

One question, changing by week. This is the survey-fatigue mitigation and it is
not optional: going from **3 questions to 4 drops completion by 18%**, response
rates fall roughly **60% past 8 minutes**, and fatigued respondents **straight-line
about 30% more**. Over ten weeks the rotation collects all three dimensions while
no single fellow ever faces more than four questions.

| Week | Question |
|---|---|
| 1, 4, 7, 10 | Teacher's custom content question |
| 2, 5, 8 | *"What's still unclear?"* (muddiest point) |
| 3, 6, 9 | Application prompt — *"one way you'd use this in your project"* |

The teacher's question appears most often because it is **the only genuinely
unfakeable one** — it depends on content that only someone present would know.

The schedule lives in `config/rotation.json`, owned and editable by the Director
of Programs. Ship the table above as the default. Weeks beyond the configured
schedule wrap; document the wrap behavior.

---

## Design invariants

Part A's invariants all still hold. These are additional, and they are ethical
constraints rather than engineering preferences.

1. **Asking for help must never lower any participation signal.** The help
   checkbox is excluded from every count, score, rate, and aggregate, forever. If
   a fellow can suspect that checking the box costs them something, the field
   stops working and the program loses its only self-reported distress channel.
   Enforce this in code, not in a comment.
2. **The help checkbox cannot be provisioned without a named recipient.** If
   `config/help_routing.json` has no configured recipient, the field is omitted
   from the form entirely and provisioning logs why. A system that invites people
   to ask for help and routes it nowhere is worse than one that never asks. CU
   confirmed on 2026-08-10 that no Fellow-support responder role exists yet, but
   the Director of Programs does — so a named human is available today. Require
   one.
3. **No AI ever judges an individual fellow's free text.** Gemini's only role in
   Part B is clustering *muddiest-point* answers into themes for the teacher —
   aggregate, about content, never about a person. Do not classify takeaway
   quality, do not score effort, do not flag anyone.
4. **Free text is counted, never graded.** Record that a substantive response
   exists; never rate how well written it is. Grading writing penalizes ESL and
   neurodivergent fellows for reasons unrelated to engagement.
5. **A peer shoutout is data about a third party** who did not submit it. It gets
   the same protection as the submitter's own data, and it is never surfaced to
   the person named without an explicit decision by the data owner.
6. **The help checkbox is never processed by the AI tier, never exported to a
   general report, and never included in a CSV a staffer might email.**

---

## Deliverable 1 — Form structure and provisioning

Part B needs its own template form, provisioned by the same
template-and-copy mechanism Part A already uses. Extend, don't duplicate:

- `form_template` gains a `part` column (`a` | `b`). Each part has its own
  template and its own one-time human Verified-email confirmation.
- `session_form` gains a `part` column. A session has at most one form per part.
- The console's template setup screen handles both parts, showing which are
  verified and which are not.

### Exact question shapes

Build via `forms.batchUpdate` → `createItem`. Verified field shapes:

**Confidence (index 0):**
```json
{ "createItem": { "item": { "title": "I could explain today's topic to someone",
  "questionItem": { "question": { "required": true,
    "scaleQuestion": { "low": 1, "high": 7,
      "lowLabel": "Not at all", "highLabel": "Easily" } } } },
  "location": { "index": 0 } } }
```

**Takeaway (index 1):** `textQuestion` with `paragraph: false`, `required: true`

**Rotating slot (index 2):** `textQuestion`, `paragraph: false`, `required: true`.
Title is the resolved question text for that week.

**Peer shoutout (index 3):** `textQuestion`, `paragraph: false`,
**`required: false`**

**Help checkbox (index 4):** `choiceQuestion` with `type: "CHECKBOX"`, one option,
**`required: false`**

**Only the confidence, takeaway, and rotating fields are required.** Forcing
responses measurably hurts completion, and forcing the shoutout or help fields
would be actively wrong.

### ⚠️ Question ID mapping — the new trap

Part A had one question, so the answer was unambiguous. Part B has five, and
`forms.responses.list` returns answers keyed by **`questionId`**, not by title or
position.

**I could not verify whether Drive `files.copy` preserves question IDs across
copies.** Do not assume either way, and do not hardcode IDs.

After provisioning each Part B form, **read the form back with `forms.get` and
record the mapping** of `questionId` → semantic slot (`confidence`, `takeaway`,
`rotating`, `shoutout`, `help`) into a `form_question_map` table, keyed by form.
Resolve every response through that table.

Match slots by **item index**, which the app controls at creation time, not by
title text — the rotating slot's title changes every week and the teacher may
edit others.

If a form's map is missing or incomplete at ingest time, **fail loudly and refuse
to ingest that form**. Silently mis-keyed answers would attribute a confidence
score to a takeaway field and corrupt every downstream number in a way that looks
plausible.

### Preserving rotating-question history

The rotating slot's text changes weekly. Snapshot the **exact question text shown**
into `form_question_map.question_text` at provisioning time. Never reconstruct it
later from config — the config may have changed since. "What was actually asked in
week 3" must be answerable from the database alone.

---

## Deliverable 2 — Rotation engine

`config/rotation.json`:

```json
{
  "version": "1.0.0",
  "owner": "adiah@civicsunplugged.org",
  "status": "DRAFT — NOT APPROVED",
  "schedule": {
    "teacher_question": [1, 4, 7, 10],
    "muddiest_point":   [2, 5, 8],
    "application":      [3, 6, 9]
  },
  "fixed_text": {
    "muddiest_point": "What's still unclear?",
    "application": "One way you'd use this in your project"
  },
  "wrap": true
}
```

Week comes from an explicit `session.week_index`, set in the console — **not
derived from calendar dates**. Sessions get rescheduled, skipped, and doubled up;
a calendar-derived week silently desynchronizes the whole rotation.

When the resolved slot is `teacher_question`, the text comes from
`session.teacher_question`. **Provisioning must refuse** if that week calls for a
teacher question and the field is empty — with a clear console error naming the
session. Do not fall back to a generic question; a silent substitution destroys
the one unfakeable signal.

Validate the config on load: every week 1..N covered exactly once, no overlaps, no
gaps. Reject a malformed schedule at startup rather than mid-provisioning.

---

## Deliverable 3 — Schema additions

Migrations only. Do not modify Part A's tables destructively.

**`form_template`** — add `part` (`a` | `b`)

**`session_form`** — add `part` (`a` | `b`); unique on `(session_id, part)`

**`session`** — add `week_index` int, `teacher_question` text nullable

**`form_question_map`**

| Column | Notes |
|---|---|
| `id` | uuid PK |
| `form_id` | text |
| `question_id` | text |
| `slot` | `confidence` \| `takeaway` \| `rotating` \| `shoutout` \| `help` |
| `rotating_kind` | `teacher_question` \| `muddiest_point` \| `application`, nullable |
| `question_text` | text — **exact text shown to fellows** |
| `item_index` | int |
| `recorded_at` | timestamptz |

Unique on `(form_id, question_id)` and on `(form_id, slot)`.

**`checkin_b`** — one row per Part B submission. **Immutable**, mirroring Part A's
shape.

| Column | Notes |
|---|---|
| `checkin_b_id` | uuid PK |
| `source_event_id` | text UNIQUE |
| `source` | `forms_api` \| `csv` |
| `submitted_email` | normalized |
| `submitted_at_utc` | timestamptz |
| `session_id` | FK nullable |
| `session_match` | `matched` \| `none` \| `ambiguous` |
| `confidence_raw` | int 1–7, nullable |
| `takeaway_text` | text, nullable |
| `rotating_kind` | text, nullable |
| `rotating_text` | text, nullable |
| `shoutout_text` | text, nullable |
| `latency_seconds` | int, nullable |
| `extra_fields` | jsonb |
| `load_id` | FK |
| `ingested_at` | timestamptz |

**Note the help checkbox is not in this table.** See Deliverable 5.

**`peer_shoutout`**

| Column | Notes |
|---|---|
| `shoutout_id` | uuid PK |
| `checkin_b_id` | FK |
| `raw_text` | text as typed |
| `named_fellow_id` | FK nullable |
| `match_method` | `exact_name` \| `manual` \| `unresolved` |
| `confidence` | numeric |
| `resolved_by` | text |
| `resolved_at` | timestamptz |

One row per name extracted; a fellow may name several people.

**`muddiest_theme`** and **`muddiest_theme_member`** — AI clustering output, per
session. Theme label, summary, member `checkin_b_id`s, model, prompt version,
generated_at. Regenerating supersedes rather than overwrites.

### Row Level Security

Extend Part A's RLS. `peer_shoutout` and `checkin_b` follow the same
`TODO(access)` stub. **The help table gets stricter treatment — see Deliverable 5.**

---

## Deliverable 4 — Ingestion

Extend `cufa pull` with `--part b`. Same mechanics as Part A: incremental
`forms.responses.list` with `filter=timestamp > <watermark>`, pagination, watermark
advanced only after a complete successful pass, identical `source_event_id` hashing
so cross-path re-import cannot duplicate.

Per response:

1. Resolve every answer through `form_question_map`. Missing map → refuse the form.
2. **Confidence:** parse to int, validate 1–7. Out of range → store NULL, record
   the raw value in `extra_fields`, warn. Never clamp silently.
3. **Takeaway / rotating:** store verbatim, including whitespace. Store
   `rotating_kind` from the map so the question type travels with the answer.
4. **Shoutout:** store raw, then resolve (Deliverable 6).
5. **Help checkbox:** route separately and immediately (Deliverable 5).
6. Session match, identity resolution, and latency follow Part A exactly.

**Part A and Part B are independent observations.** A fellow may submit one and
not the other; both cases are valid data, not errors. Part B is not evidence for
Part A and must never be used to backfill it. Join on `(fellow, session)` when
reading; never merge on write.

---

## Deliverable 5 — The help request path

The most sensitive thing this system touches. Treat it accordingly.

**Separate table, `help_request`:**

| Column | Notes |
|---|---|
| `help_request_id` | uuid PK |
| `fellow_id` | FK nullable — unresolved email still recorded |
| `submitted_email` | text |
| `session_id` | FK nullable |
| `submitted_at_utc` | timestamptz |
| `status` | `open` \| `acknowledged` \| `closed` |
| `acknowledged_by` | text nullable |
| `acknowledged_at` | timestamptz nullable |
| `note` | text nullable |

**Requirements:**

- **Routed immediately on ingest**, not on a batch schedule. A fellow asking for
  contact should not wait for a weekly pipeline run. Send an email to the
  configured recipient the moment the request lands.
- `config/help_routing.json` names the recipient. **Empty config → the checkbox is
  omitted from the form at provisioning time**, and the console shows why. This is
  invariant 2 and it is not negotiable.
- **Stricter RLS than any other table.** Explicitly not covered by the general
  "visible to every full-time team member" default — that question is unresolved
  for ordinary attendance data and is definitely not resolved for this.
- **Never touched by the AI tier.** No classification, no summarization, no
  clustering.
- **Never appears** in `cufa report`, any CSV export, or any aggregate. Add a test
  asserting the help table is absent from every export path.
- **Never feeds a participation signal.** Add a test asserting no code path reads
  `help_request` while computing any count or rate.
- The console gets a dedicated, access-gated screen listing open requests with
  acknowledge and close actions.
- Notification emails contain **the fellow's name and the session only** — never
  the takeaway text, the confidence score, or anything else from the submission.
  Minimum necessary.

Leave a `TODO(retention)` on this table specifically. CU has not defined a
retention period for anything, and a record that someone asked for help is the
most sensitive thing here. Do not invent a number.

---

## Deliverable 6 — Peer shoutout resolution

Free-typed names → `fellow_id`s. Deliberately conservative.

1. Split multi-name answers on commas, `and`, `&`, and newlines. Store each
   separately with its raw fragment.
2. Normalize: trim, collapse whitespace, casefold.
3. Match against roster full names, then first names, **within the cohort only**.
4. An unambiguous exact match → `match_method='exact_name'`, confidence 1.0.
5. **Ambiguity is not resolved automatically.** Two fellows named Jordan means
   `unresolved` and a review-queue entry — never a coin flip. Guessing wrong
   attributes praise to the wrong person.
6. A name matching nobody is **legal, not an error** — guest speakers and staff
   get thanked. Record `unresolved` and move on.
7. Console review screen for manual linking, writing `match_method='manual'` with
   the resolving human's identity.

**Out of scope here:** any leaderboard, ranking, points, or public display.
Collect and resolve only. When gamification is designed later, the research is
explicit that recognition should be ranked by **giving, not receiving** — ranking
on received recognition builds a popularity contest. Note that in the ADR so the
finding is not lost, but build none of it now.

---

## Deliverable 7 — Confidence storage and trend

Store `confidence_raw` as the integer 1–7. Never rescale, normalize, or convert to
a percentage on write.

Provide a read-time view exposing, per fellow per session, the raw value, and per
cohort per week, the **median and interquartile range**. Median, not mean —
7-point Likert data is ordinal and a mean of ordinal data is not meaningful.

`cufa report --confidence` prints the cohort trend by week.

**Interpretation guidance, for the docs and the console:** absolute self-rated
confidence is noisy and weakly calibrated. **The signal is the trend and the
dip** — a fellow moving 6 → 3 across two sessions is informative; a fellow sitting
flat at 4 mostly is not. Write that next to the chart, so nobody reads a single
low score as a finding.

**Straight-lining detection:** flag a fellow submitting an identical confidence
value across 4+ consecutive sessions. This is a **data-quality** flag on the
response, not a judgment about the person, and it never enters a participation
signal. Surface it in the review screen only.

---

## Deliverable 8 — AI: muddiest-point clustering only

The one place Gemini is used in Part B.

**Input:** all `rotating_text` answers for one session where
`rotating_kind = 'muddiest_point'`. **Text only — no names, no emails, no
`fellow_id`s, no counts per person.** The model sees anonymous strings.

**Output:** 2–5 themes, each with a short label, a one-sentence summary, and the
indices of the answers belonging to it. Structured JSON via response schema.

Same infrastructure as Part A: `google-genai` (`from google import genai`),
`gemini-2.5-flash` by default, `temperature=0`, versioned prompt string stored on
every row, backoff on 429, and **degrade rather than crash** — no key or no network
means no themes and a clear message, never a failed pipeline.

**This is the feedback loop, and it is the highest-leverage trust move in the
project.** Closing the loop — showing fellows what their confusion changed about
the teaching — is what makes the form visibly *for them* rather than *about* them,
and research on youth data collection is consistent that this drives honest
responding. It also maps directly onto the Director of Programs' instruction to
leave room for a model where information is shared with fellows.

Build the teacher-facing view. Do not build the fellow-facing view yet — that is a
decision for the data owner, not a default.

`cufa themes --session <id>` prints them; the console shows them on the session
detail screen.

---

## Deliverable 9 — Console additions

- **Session form:** `week_index`, `teacher_question` (shown only when that week's
  rotation calls for it, with a warning when required and empty)
- **Session detail:** provision Part B alongside Part A, each with its own status,
  URL, QR code, and publish-verified indicator
- **Rotation preview:** which question each upcoming week will ask, so the teacher
  can see what is coming and prepare the custom ones
- **Response view:** confidence distribution for the session, takeaways as a
  scannable list, muddiest-point themes
- **Shoutout review:** unresolved names with one-click linking
- **Help requests:** separate, access-gated screen; open requests, acknowledge and
  close. Visually distinct from the rest of the console — this is not routine data.

The console must display the survey-fatigue rationale where a staffer might be
tempted to add fields: a short note that the form is six fields **by design**, with
the completion-rate numbers. Someone will want to add "just one more question."

---

## Deliverable 10 — CLI

```
cufa provision --session <id> --part b [--dry-run]
cufa pull      --session <id> --part b
cufa themes    --session <id> [--regenerate]
cufa shoutouts review | link --shoutout <id> --fellow <id> --by <email>
cufa help-requests list | ack --id <id> --by <email> --note "<text>"
cufa report    --cohort <id> [--confidence] [--json]
```

Everything the console does is doable from the CLI.

---

## Deliverable 11 — Test harness

Extend Part A's. `FakeGoogleClient` gains Part B question types and a
**configurable question-ID scheme** so tests can simulate both possibilities:
IDs preserved across `files.copy`, and IDs regenerated. The mapping logic must be
correct under either.

`scripts/generate_fixtures.py` extends to Part B:

- Confidence values across the full 1–7 range, plus out-of-range (`0`, `8`,
  `"four"`) and blank
- Takeaways: substantive, one-word, whitespace-only, emoji-only, very long
- Rotating answers for all three kinds across ten weeks
- Shoutouts: single name, multiple names comma-separated, `"X and Y"`, a
  non-roster name, an ambiguous first name matching two fellows, blank
- Help checkbox: checked on a small number of submissions
- One fellow with identical confidence across 5 consecutive sessions
  (straight-lining)
- A fellow submitting Part B but not Part A, and the reverse
- A form whose `form_question_map` is deliberately incomplete

`make demo` extends through Part B end to end with no Google account and no
Gemini key.

---

## Deliverable 12 — Tests

Part A's tests all still pass. Additional:

**Mapping**
1. Question IDs resolve correctly when `files.copy` preserves them
2. And when it regenerates them — same result
3. A form with a missing or incomplete map **refuses to ingest** rather than
   guessing
4. `question_text` snapshots the text actually shown, and survives a later config
   change

**Rotation**
5. Weeks 1–10 resolve to the documented kinds
6. `week_index` drives rotation, not calendar dates — rescheduling a session does
   not change its question
7. A teacher-question week with an empty `teacher_question` **blocks provisioning**
8. A malformed rotation config is rejected at startup

**Fields**
9. Confidence out of range → NULL plus raw in `extra_fields`, never clamped
10. Blank optional fields are legal and produce no row in `peer_shoutout`
11. Multi-name shoutouts split into multiple rows
12. Ambiguous name → `unresolved`, never auto-linked
13. Non-roster name → `unresolved`, not an error
14. Straight-lining flagged after 4 consecutive identical values, and the flag
    enters no participation signal

**Help request — safeguarding**
15. Empty `help_routing.json` → the checkbox is **absent from the provisioned
    form**
16. A help request notifies immediately on ingest, not on a batch pass
17. Notification content contains name and session only — **assert takeaway,
    confidence, and shoutout text are absent**
18. `help_request` **never appears** in any export or report path
19. **No participation computation reads `help_request`** — assert by inspection
    of the query paths, not by convention
20. The AI tier never receives help-request data

**AI**
21. Clustering receives text only — **assert no email, name, or `fellow_id` is in
    the payload**
22. No key → no themes, clear message, pipeline completes
23. Regenerating themes supersedes rather than overwriting

**Independence**
24. Part A without Part B, and Part B without Part A, both ingest cleanly and
    neither backfills the other

No network in tests. Fakes for both Google and Gemini.

---

## Deliverable 13 — Documentation

- `docs/setup/part-b-form.md` — the Part B template, its one-time Verified step,
  the rotation, and what the teacher prepares each week
- `docs/safeguarding.md` — the help-request path: who is notified, what they
  receive, access restrictions, the no-recipient-no-field rule, and the open
  retention question. **Written for CU staff, not engineers.**
- `docs/decisions.md` — append ADRs: field-order-from-completion-research,
  7-point-over-5-point, rotation-over-every-question-every-week, question-ID-map,
  help-requires-named-recipient, help-excluded-from-all-signals,
  AI-clusters-content-not-people, shoutouts-collected-not-ranked (recording the
  give-not-receive finding for later)
- Update `README.md` and `docs/google-api-traps.md` with the question-ID mapping
  trap

---

## Constraints

Part A's constraints hold unchanged: Python 3.11+, the same dependency set,
Supabase CLI, no secrets committed, never log an email or key at INFO or above,
type hints on public functions, docstrings explaining *why*.

Additionally: **never log the content of a help request at any level**, including
DEBUG.

## Acceptance criteria

1. `make setup && make demo` runs Part A and Part B end to end on a clean machine
   with no Google account and no `GEMINI_API_KEY`
2. Re-running produces identical output — no duplicates in `checkin_b`,
   `peer_shoutout`, or `help_request`
3. Every fixture Part B response appears in `checkin_b` regardless of field
   validity
4. Provisioning **fails loudly** when a teacher-question week has no question set
5. Provisioning **omits the help checkbox** when no recipient is configured
6. Question-ID mapping is correct under both fake ID schemes
7. `help_request` appears in no export, report, or participation computation —
   asserted by test, not convention
8. The AI clustering payload contains no personally identifying field — asserted
   by test
9. `make test` passes with no network access
10. `docs/safeguarding.md` is legible to a non-engineer

## Out of scope

- Gamification, leaderboards, points, streaks, or any public shoutout display
- Participation scoring across Part A, Part B, Slack, and assignments — the
  weighting is a separate decision owned by the Director of Programs
- Any at-risk flag or struggling-fellow label
- Fellow-facing views of themes or their own data — a data-owner decision, not a
  default
- Slack or Zoom integration, auto-posting form links, reminder nudges
- Dashboards beyond the console screens and the terminal report
- Deploying to Supabase Cloud or hosting the console publicly

Land the six fields end to end first.
