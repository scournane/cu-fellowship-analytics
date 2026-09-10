# Architecture decision records

One record per decision that would be expensive or dangerous to reverse by accident.
Each says what the situation was, what was chosen, what was rejected, and why. If you
are about to change one of these, the "why" is the thing to argue with.

Status of ADR-001 to ADR-020: **accepted**, August 2026, Part A.

Two things are deliberately **not** decided and must not be invented — see ADR-020,
which Part B extends rather than resolves.

---

## ADR-001 — Attendance comes from a mid-session form, not the Zoom API

**Context.** Roughly 30 fellows attend live lessons over Zoom. Zoom's participants API
was the obvious source. Since a March 2023 API change, guest participants have `id` and
`participant_user_id` withheld as PII, and email is returned only for participants
signed into Zoom.

**Decision.** Attendance is recorded by a Google Form released **mid-session**, asking
for a passphrase the teacher says aloud and displays on screen. Google's *Verified*
email collection supplies the identity.

**Rejected.** (a) The Zoom participants API: for an unauthenticated joiner the entire
record is a self-typed display name and a duration, so renaming yourself, or joining and
walking away, produces a record identical to real attendance. (b) A form released at the
start of the lesson: it proves someone joined, nothing more. (c) A timestamp with no
passphrase: satisfied by an idle open tab.

**Why.** Released 15–25 minutes in, the form proves presence at a moment the fellow
could not have predicted, and the passphrase is what makes that provable. The passphrase
must be spoken *and* shown — audio-only excludes deaf and hard-of-hearing fellows, and
anyone whose audio drops. That widens the leak surface, which is precisely why the
passphrase is one signal among several and never proof on its own.

---

## ADR-002 — Google-Verified email collection, not a typed address

**Context.** The form needs to know who submitted it. Google Forms can collect email
addresses as *Verified* (Google confirms the signed-in account) or as *Responder input*
(the respondent types whatever they like).

**Decision.** Verified only. The system refuses to operate on a form whose settings do
not read back as `VERIFIED` (`template.verify_template`).

**Rejected.** (a) Responder input. (b) An email question on the form. (c) A roster
dropdown of names.

**Why.** A typed address is the self-reported identity this whole design replaces — it
reintroduces the exact weakness that makes Zoom's data useless, and it does so
invisibly, because a typed address and a verified one are the same string in the
database. Making the failure loud (`TemplateNotVerified` blocks provisioning) is the
only way to keep this property true over time.

---

## ADR-003 — Template-and-copy, not per-form API settings

**Context.** `forms.batchUpdate` → `updateSettings` → `emailCollectionType` has been
reported returning `400 INVALID_ARGUMENT` with no working enum value. Verified email
collection is the premise of the design, so it cannot depend on a call that may reject.

**Decision.** Create **one** template form; a human sets Verified by hand once; the app
reads `form.settings` back and refuses to proceed until Google says `VERIFIED`; every
session form is a Drive `files.copy` of that template. `batchUpdate` is used only for
title, description and question text. See `src/cufa/template.py` and
`docs/google-api-traps.md` trap 2.

**Rejected.** (a) Setting collection per form through the API — the call that does not
work. (b) Catching the 400 and continuing — produces forms that collect nothing, and
looks fine. (c) Asking a human to configure every session's form — CU has no data
manager; every manual step is a step that stops happening in November.

**Why.** One manual step, once, is affordable. Thirty of them per cohort is not. The
app still *attempts* the API call first (`try_set_verified_email`) and treats the 400 as
expected, so the manual step disappears for free if Google fixes the enum. The template
is re-verified before every provisioning run, not read from a stored flag, because the
realistic failure is a human editing the template months later.

---

## ADR-004 — Read `forms.responses.list`, not a linked spreadsheet

**Context.** `Form.setDestination()` exists in Apps Script with no REST equivalent, so
an API-provisioned form has no linked Sheet and no CSV to export.

**Decision.** Read responses directly from `forms.responses.list`, with incremental
polling via `filter=timestamp > <watermark>` and a per-form watermark that advances only
after a complete successful pass (`src/cufa/ingest/forms_api.py`). A CSV path is kept as
a documented fallback for manually created forms.

**Rejected.** (a) Having someone link a Sheet by hand and exporting CSV as the primary
path. (b) Polling without a watermark and relying on idempotency alone. (c) Advancing
the watermark per page.

**Why.** The API path is strictly better, not a workaround: it returns
`respondentEmail` and RFC3339 UTC timestamps, which **eliminates the Sheets timezone
trap entirely**. A Sheets export writes form timestamps in the spreadsheet's locale with
no offset marker, so parsing them as UTC shifts every check-in by hours while looking
entirely plausible. Advancing the watermark only after a full pass means a mid-pull
failure re-reads rather than skips — and re-reading is free, because `source_event_id`
makes writes idempotent.

---

## ADR-005 — User OAuth, not a service account

**Context.** The system creates and publishes forms in Google. A service account cannot
own a Google Form.

**Decision.** One CU staff member signs in once and grants exactly two scopes:
`forms.body` (create, update, publish) and `drive.file` (copy the template). The refresh
token is encrypted at rest in Postgres. Forms are owned by that staff member's account.

**Rejected.** (a) A service account owning the assets — not possible. (b) Domain-wide
delegation — requires Workspace super-admin access CU may not have on demand, and grants
a service account standing permission to impersonate users across the domain to
provision one form a week. Explicitly out of scope for Part A. (c) Broader Drive scope.

**Why.** `drive.file` suffices because the app creates the template itself, so the
template — and every copy of it — stays inside the app's own scope by construction.
Asking for `drive` would give this tool reach over a staff member's entire Drive to do a
job that never touches anything it did not create, and drags in Google's verification
process for no benefit. Owning the forms in a CU account rather than a contractor's is
the outcome CU wants: the work product stays with CU.

---

## ADR-006 — Session is derived from the timestamp; the form has no session dropdown

**Context.** Each check-in must be attributed to a lesson. The form could ask which
session it is.

**Decision.** The form has **exactly one question** ("Today's passphrase"). Session is
derived by matching `submitted_at_utc` against session windows
`[scheduled_at_utc − grace, scheduled_at_utc + duration + grace]`
(`ingest/common.py::assign_session`). On the API path the form itself already implies a
session, and that wins.

**Rejected.** A session dropdown or a hidden pre-filled session field.

**Why.** A dropdown is user input, and user input can contradict reality — a fellow
picks last week's session and the record now disagrees with its own timestamp, with no
way to tell which is right. A timestamp cannot be mis-selected. Every extra question is
also a chance to abandon the form mid-lesson. Where the timestamp and the form's own
session disagree, that is a **configuration error worth flagging** (a warning naming the
sessions), not a reason to reassign the row.

---

## ADR-007 — Never drop a submission

**Context.** Ingest sees wrong passphrases, blank answers, unknown addresses, timestamps
in no session's window, and timestamps in two.

**Decision.** Every one of those produces a `checkin` row, with the reason recorded in
`session_match` / `passphrase_match`. Nothing is filtered at the parser. Unrecognized
CSV columns are preserved into `extra_fields` rather than discarded.

**Rejected.** Rejecting malformed or unmatched rows at ingest; logging-and-skipping;
"clean" tables containing only decidable rows.

**Why.** A dropped row is an unrecoverable observation — the lesson is over and the
response cannot be resubmitted. Worse, a reject-bad-input parser deletes exactly the
cases worth looking at: the fellow who joined late, the one whose connection dropped,
the address that is not on the roster. Enforced by the `checkin_no_mutation` trigger
(`supabase/migrations/…_checkin_immutability.sql`), which blocks every DELETE and every
UPDATE to an observed column.

---

## ADR-008 — The observation is separate from the decision

**Context.** "Did this fellow attend?" is a judgment that can change: a definition
changes, a human overrides, a model improves. What arrived at 10:14 does not change.

**Decision.** Two tables. `checkin` stores what was observed and is **immutable**
(trigger-enforced; `latency_seconds` is the single exempt column, because it is derived
from session state that legitimately moves — see ADR-014).
`attendance_decision` stores the judgment, is **append-only and versioned**, and carries
its provenance: `decided_by`, `rule_name`, `ai_model`, `ai_prompt_version`,
`ai_reasoning`, `human_email`, `note`, `created_at`. Superseding is an UPDATE of
`superseded_at` plus an INSERT, never an in-place edit
(`src/cufa/decisions.py::record_decision`).

**Rejected.** (a) An `attended` column on `checkin`. (b) Mutating the decision in place.
(c) An audit log written alongside a mutable current-state table.

**Why.** This is what makes "why is this fellow marked absent?" answerable months later
without guessing, makes a human override auditable, and lets a changed definition of
"attended" be re-run over history as a real experiment rather than a fresh roll of the
dice. A **partial unique index** on `checkin_id WHERE superseded_at IS NULL` guarantees
exactly one current decision: code that forgets to supersede fails immediately on the
constraint instead of quietly producing two live decisions that disagree.

`checkin` also stores the **email, not a `fellow_id`** — identity resolves at read time
in `v_checkin_resolved`, so fixing a roster entry re-attributes all history with no
backfill.

---

## ADR-009 — Three tiers: deterministic rules, then a model, then a human

**Context.** Most check-ins are trivially decidable. A few are not:
`"the word was justice"`, `"justice i think?"`, `"jushtis"`, `"sorry I missed it"` — all
of which a human reads instantly and edit distance scores wrongly.

**Decision.** Tier 1 is deterministic rules over the recorded comparison
(`adjudicate/rules.py`). Only `mismatch`-inside-a-window escalates to tier 2, Gemini
(`adjudicate/ai.py`). Tier 3 is a human (`cufa decide`, or one click in the console's
review screen), whose decision has `confidence = 1.0` and is **never superseded** by a
later rule or AI pass — `adjudicate` skips any check-in whose current decision is
`decided_by='human'`, and `--force` overrides only while printing a warning naming
exactly what it will overwrite.

**Rejected.** (a) Rules only — cannot read a sentence. (b) A model on everything —
slower, costs money, rate limited, not reproducible, and no better than string equality
on the cases string equality already settles. (c) A human on everything — CU has no
data manager.

**Why.** Everything tier 1 can decide, tier 1 decides, because its outcomes are
reproducible from the row alone: no model, no clock, no network. That is what makes
re-running adjudication over history meaningful. A model call earns its place only where
edit distance genuinely cannot help. **Tier 2 never crashes the run** — no key, no
network, or exhausted quota writes `status='needs_review'`, `decided_by='rule'`,
`rule_name='ai_unavailable'` and the pipeline finishes, because a pipeline that stops
when an optional model is unreachable has made the model mandatory in practice.

---

## ADR-010 — `needs_review` is never collapsed into `not_attended`

**Context.** Cases reach the end of adjudication undecided: a wrong passphrase from a
verified address inside the window, a timestamp inside two overlapping windows, tier 2
unavailable.

**Decision.** They are written as `status='needs_review'` with `attended = NULL`, and
nothing downstream converts that to `not_attended`. Enforced in the schema by
`decision_status_matches_attended`, which requires `needs_review` to carry a NULL
`attended`.

**Rejected.** Defaulting undecided cases to absent; treating NULL as false in the
report; a two-state boolean.

**Why.** Absent evidence is not evidence of absence. A wrong answer from a Google-verified
address inside the session window is not proof the person was not there — the commonest
causes are mishearing the word, joining late, and a misconfigured session time. Marking
that fellow absent is a real consequence for a real young person, applied by a system
that just told you it does not know. Note the AI tier follows the same rule: a verdict of
"did not hear the passphrase" produces `needs_review`, not `not_attended`.

---

## ADR-011 — Fuzzy passphrase matching is on by default

**Context.** The passphrase is heard aloud, possibly over a lossy connection, and typed
on a phone.

**Decision.** Levenshtein distance ≤ `max_edit_distance` (config
`CUFA_MAX_EDIT_DISTANCE`, default **1**) counts as `fuzzy` and adjudicates as attended
with confidence 0.9, against 1.0 for `exact`. Normalization first: NFKC, casefold,
strip punctuation, collapse whitespace.

**Rejected.** (a) Exact match only. (b) A larger default distance. (c) Phonetic matching
(Soundex/Metaphone) as tier 1.

**Why.** Rejecting `justise` for `justice` penalizes someone who **was there and heard
it** — backwards from the intent of the whole mechanism. Distance 1 is deliberately
tight enough that it cannot bridge two different words. The confidence gap (0.9 vs 1.0)
records that this was a near-miss without punishing it. Anything wider than distance 1
belongs in tier 2, which can actually read the answer.

---

## ADR-012 — Levenshtein is implemented here, not imported

**Context.** Tier 1 needs edit distance. Several packages provide it.

**Decision.** ~20 lines in `src/cufa/text.py::levenshtein`, with a `max_distance`
short-circuit (the only case tier 1 cares about).

**Rejected.** `python-Levenshtein`, `rapidfuzz`, `jellyfish`.

**Why.** CU has no data manager; every dependency is maintenance CU inherits. A C
extension also complicates install on the machine of whoever picks this up next. The
algorithm is textbook, fully covered by tests, and will not change.

---

## ADR-013 — Gemini receives exactly two strings

**Context.** Tier 2 judges whether an answer indicates the person heard the passphrase.
It could be given names, roster context, attendance history, prior answers.

**Decision.** The prompt contains the expected passphrase and the submitted answer, and
nothing else. No names, no emails, no cohort, no history. `temperature=0`, structured
JSON response schema, `PROMPT_VERSION` stored on every decision, and a cache keyed on
`(expected_normalized, submitted_normalized, prompt_version, model)` checked before
every call.

**Rejected.** Sending the fellow's name or attendance record "for context"; free-form
text output; sending uncached duplicates.

**Why.** Better privacy **and** better accuracy at the same time. The model's only job
is judging whether an answer indicates someone heard a word — everything else is context
it could be wrong about, and context it could weigh against a fellow. `temperature=0`
matters because the cache would otherwise be lying about what a fresh call would return.
Versioning the prompt means a changed prompt invalidates the cache cleanly instead of
silently reusing verdicts produced by different instructions. `cufa review --status ai`
exists so the tier is auditable by a human sampling its judgments rather than trusted
because it is a model.

---

## ADR-014 — Latency is stored and never interpreted

**Context.** `latency_seconds` — the gap between announcement and submission — is
obviously suggestive. It is tempting to flag long ones.

**Decision.** Compute it, store it, stop. No thresholds, no "suspicious" marker, no
derived flag anywhere in the codebase or the report. T0 is `session.announced_at_utc`
when the teacher pressed "Announce now", otherwise the earliest submission matched to
that session; NULL when no session matched.

**Rejected.** A suspicion threshold; a percentile flag; excluding late submissions from
attendance; clamping negative values to zero. The clamp looks like tidying and is
actually interpretation — a submission that predates the announcement stamp (the teacher
pressed the button late) is a real observation, and a clamped one is invisible.

**Why.** A threshold would be a policy CU has not written, encoded by whoever happened
to implement this, and applied to young people. It is an input to an analysis that does
not exist yet. Under a derived T0 the first submitter always reads exactly 0 — documented
as expected, not a bug, because with no announcement stamp the first arrival *is* the
only evidence of when the form went out. Latency is recomputable
(`latency.recompute_for_session`), which is why it is the one column exempt from the
`checkin` immutability trigger: a teacher legitimately presses "Announce now" after the
first fellow has already submitted.

---

## ADR-015 — The provisioning row is written before publish, so a failure resumes

**Context.** Provisioning is: copy the template, set title and question, publish, verify
publish. Any step can fail. Retrying naively creates a second Google Form for the same
session, and the teacher shares whichever link they happen to have.

**Decision.** The `session_form` row is inserted as soon as the copy exists — including
in the error path of `batch_update` — with `publish_verified_at` NULL. "Ready" is
defined as `publish_verified_at IS NOT NULL`. A later `cufa provision` for the same
session finds the row and **resumes**: it publishes and verifies the form that already
exists rather than copying another. A session already ready is skipped and its existing
form shown. Every attempt writes a `provisioning_log` row with its outcome.

**Rejected.** (a) Writing the row only on full success — the copied form becomes an
orphan in Drive and the retry makes another. (b) Deleting the partial form on failure —
a delete needs permissions and can itself fail, and destroying evidence of a failed run
is the wrong instinct. (c) An in-memory retry loop.

**Why.** Retry safety is structural rather than careful: the invariant holds even if the
process is killed between two statements. And because "ready" is a column rather than a
return value, a half-provisioned form is never reported as ready in the meantime — the
`published_at` set / `publish_verified_at` NULL pair is exactly the state to look for.

---

## ADR-016 — psycopg 3 and plain SQL, not an ORM or `supabase-py`

**Context.** The system is batch ingest plus a small server against local Postgres.

**Decision.** `psycopg[binary]` with hand-written SQL in `src/cufa/db.py` and per-module
query functions. Schema lives in `supabase/migrations/*.sql`.

**Rejected.** (a) SQLAlchemy / an ORM. (b) `supabase-py` and PostgREST. (c) A query
builder.

**Why.** The interesting parts of this schema are things an ORM abstracts away and
`supabase-py` cannot express well: a partial unique index enforcing one current
decision, an immutability trigger with a column exemption, `ON CONFLICT DO NOTHING` as
the idempotency mechanism, `count(*) FILTER (WHERE …)` aggregates in the report. Plain
SQL is also the shortest handoff: whoever inherits this can paste any query in the docs
straight into Studio and see the same rows the application sees. RLS is enabled, and the
pipeline connects as the service role, which is a Postgres-level concept rather than a
PostgREST one.

---

## ADR-017 — FastAPI + Jinja2 + HTMX, not Next.js on Supabase

**Context.** Two or three non-technical CU staff need a small internal console:
connect Google, verify the template, manage sessions, provision a form, review the
queue.

**Decision.** Server-rendered HTML from the same Python codebase as the pipeline, with
HTMX for the few interactive bits. One language, one dependency set, one deploy, no
build step.

**Rejected.** Next.js on Supabase (the reasonable alternative if the team already ran
JS); a SPA against a JSON API; building both.

**Why.** CU inherits this without a data manager, and a Python-only codebase is the
shortest handoff. A JS front end would double the dependency surface and add a build
step to a project whose entire user base is three people on an internal tool.
Consequence accepted deliberately: **everything the console does is also a CLI command**
(`cufa provision`, `cufa pull`, `cufa decide`, `cufa review`, …), so the system stays
scriptable, testable without a browser, and usable on the day the web app breaks.

---

## ADR-018 — `--sheet-timezone` is mandatory with no default

**Context.** The CSV fallback path ingests a Google Sheets export. Sheets writes form
timestamps in the spreadsheet's locale as `2026-09-15 13:05:00` — a wall-clock string
with no offset marker.

**Decision.** `cufa ingest part-a` requires `--sheet-timezone <IANA>`. Omitting it
raises `MissingTimezone`, whose message names the flag, gives a complete example
command, and says where to find the value (Sheets → File → Settings → Time zone).
Conversion happens at the parser boundary with `zoneinfo`; both `submitted_at_raw` and
`source_timezone` are stored so the conversion can be re-derived.

**Rejected.** Defaulting to UTC; defaulting to the machine's local zone; inferring from
the cohort's sessions.

**Why.** Every default here is a guess that silently shifts every row by hours while
looking entirely plausible. Defaulting would make the most dangerous case — an operator
who never thought about timezones — the silent one. Failing loudly costs that operator
thirty seconds. (This trap does not exist on the API path at all, which is one of the
reasons that path is primary: see ADR-004.)

---

## ADR-019 — The refresh token is encrypted at rest, in the database

**Context.** A Google refresh token is a long-lived credential to a CU staff member's
Drive. It is the one value in this database worth stealing.

**Decision.** Fernet, key from `CUFA_ENCRYPTION_KEY` (generated with
`python -m cufa.crypto keygen`), ciphertext in `google_credential.refresh_token_enc`
(`bytea`). The key lives only in the environment. `credential_status()` reports the
connection without decrypting; `disconnect()` clears the ciphertext but keeps the row so
"who connected this, and when" survives. RLS is enabled on `google_credential` with
**no policy**, so nothing but the service role can read it. Logging redacts
token-shaped and key-shaped strings at every level, including DEBUG.

**Rejected.** (a) A token file on disk — moves the secret out of the one place that is
already backed up and access-controlled, and files get committed. (b) Plaintext in the
column — would satisfy every test in this repo while breaking the one security property
that matters. (c) `pgcrypto` in the database — the key would then live where the
ciphertext does. (d) A cloud KMS — infrastructure CU does not run.

**Why.** The threat is a database dump or a stray `select *`, and a key held outside the
database defeats both. A missing key is an error rather than a fallback to plaintext:
`Settings.require_encryption_key()` refuses to store a token without one.

---

## ADR-020 — Retention period and RLS access policy are deliberately undecided

**Context.** Two things a system like this normally specifies: how long check-in records
are kept, and who may read them.

**Decision.** Neither is set. `src/cufa/form_content.py` carries a literal
`TODO(retention)` marker where the retention period belongs, and the form's header
notice says so in plain language to fellows. `supabase/migrations/…_rls.sql` enables RLS
on `fellow`, `checkin`, `attendance_decision` and `google_credential`, and leaves
`TODO(access)` policy stubs whose predicate is `false` — non-permissive, documenting the
shape a real policy takes and granting nothing.

**Rejected.** Picking a plausible number (12 months, "end of cohort + 1 year"); writing
a permissive policy for authenticated users; disabling RLS until someone decides.

**Why.** An assumed retention period **silently becomes policy** — it is what fellows
were told, and nobody revisits it. CU has said the data should be visible to every
full-time team member but has not defined granular permissions, and a derived attendance
judgment ("marked absent") should not automatically be as open as a raw timestamp
("submitted at 10:14"). Writing either now would be inventing CU's policy on CU's
behalf, and an invented policy is indistinguishable from a considered one six months
later. RLS is enabled anyway so that anything reaching this database that is *not* the
service role is denied by default rather than allowed by default.

What has to be decided before the policies can be written is listed in the migration
itself: whether `fellow` is readable by the same people as `checkin`, whether a decision
is more restricted than an observation, and whether fellows ever read their own rows and
on what claim.

**Part B extends this rather than resolving it.** `checkin_b` and `peer_shoutout` get the
same non-permissive stub, and `peer_shoutout` adds a question of its own: a shoutout is
data about a third party who did not submit it, so whatever rule CU writes must also
answer whether the person *named* may read it. `help_request` is deliberately **outside**
the shared stub — RLS on, no policy, and grants revoked from `anon` and `authenticated`
outright — so that a future migration loosening the shared stub, which is the likely way
this leaks, does not reach it by accident. Its retention question is separate too, and
carries its own `TODO(retention)`: the right answer for "a young person asked to be
contacted" is very unlikely to be the right answer for "submitted at 10:14".

---

Status of ADR-021 to ADR-028: **accepted**, August 2026, Part B.

---

## ADR-021 — Field order comes from completion research, not from visual balance

**Context.** Part B has six fields, and their order is the kind of thing that gets
"tidied" by whoever edits the form next.

**Decision.** The order is fixed and documented as load-bearing: an easy click first, the
core processing artefact second, the rotating slot third, the optional shoutout fourth,
and the sensitive help checkbox last. Only the first three are required.

**Rejected.** Grouping the two free-text fields together for visual balance; putting the
help checkbox near the top so it is "not buried"; making every field required so no
response is partial.

**Why.** Surveys opening with a simple multiple-choice complete at **89% versus 83%** for
open-ended, so the cheapest possible first action is what gets someone into the form at
all. Sensitive items placed early measurably raise abandonment of the **whole form**, not
just of that item — so a help checkbox moved up would collect fewer help requests, not
more, while looking more prominent. Forcing responses measurably hurts completion, and
forcing the shoutout or the help field would be worse than that: an optional field that
must be answered is not optional, and a compulsory "do you need help?" is a different
question from a voluntary one.

The rationale is displayed in the console wherever a staff member might add a field,
because the numbers are the only durable answer to "just one more question".

---

## ADR-022 — A 7-point confidence scale, not a 5-point one

**Context.** The confidence field is the only quantitative item on the form and the only
one that gets graphed.

**Decision.** `scaleQuestion` with `low: 1`, `high: 7`, labels on the endpoints only.
Stored as the integer 1–7, never rescaled, never converted to a percentage on write.
Read-time views expose **median and interquartile range**, never a mean.

**Rejected.** Five points, because it is the familiar default; ten points, for finer
resolution; a 0–100 slider; storing a normalized 0–1 value.

**Why.** Preston & Colman found scales under 5 points lose reliability with 7–10
performing best, and Krosnick & Presser converge on 5–7 as optimal. Critically, **5-point
scales induce interpolation** — respondents try to answer between two values — and since
this field is graphed, the extra resolution is the difference between a readable trend and
a stepped one. Endpoint-only labels stop people reading the words instead of the position.

The mean is refused because a Likert scale is **ordinal**: the distance between 3 and 4 is
not known to equal the distance between 6 and 7, so summing and dividing produces a number
with no defined meaning however comfortable it looks. `percentile_disc` rather than
`percentile_cont` for the same reason — it returns an actual point on the scale rather
than interpolating a 4.5 nobody could have selected.

A percentage is refused because it implies a ratio scale: that 6 is twice 3.

---

## ADR-023 — One rotating question, not every question every week

**Context.** Three dimensions are worth collecting — the teacher's own content question,
the muddiest point, and an application prompt. Asking all three every week is the obvious
design.

**Decision.** One slot in position 4, rotating by week. Teacher's question on weeks 1, 4,
7 and 10; muddiest point on 2, 5 and 8; application on 3, 6 and 9. The schedule lives in
`config/rotation.json`, owned by the Director of Programs. Weeks past the end wrap.

**Rejected.** All three every week; letting the teacher choose each week; deriving the
question from the lesson title.

**Why.** Survey fatigue is the constraint, and it is measurable: three questions to four
drops completion by **18%**, response rates fall roughly **60%** past eight minutes, and
fatigued respondents **straight-line about a third more often** — which corrupts the
confidence trend rather than merely shortening the form. Over ten weeks the rotation
collects all three dimensions while no fellow ever faces more than four questions at once.

The teacher's question appears most often because it is the **only genuinely unfakeable
one**: it depends on content that only someone present would know.

Ownership sits with the Director of Programs rather than in code because which dimension
matters in which week is a programme decision, not an engineering one.

---

## ADR-024 — Answers are resolved through a recorded question-id map

**Context.** `forms.responses.list` returns answers keyed by `questionId`. Part A had one
question so the answer was unambiguous; Part B has five. **When this was decided, whether
Drive's `files.copy` preserves question ids across copies could not be verified.** It has
since been measured against a live account — it preserves them — which does not change the
decision and sharpens the reason for it; see the addendum.

**Decision.** After provisioning, every Part B form is read back with `forms.get` and the
mapping from `questionId` to semantic slot is recorded in `form_question_map`, keyed by
form. Every response is resolved through that table. Slots are matched by **item index**,
which the application controls at creation time. A form whose map is missing or incomplete
**refuses to ingest**. The exact question text shown is snapshotted at provisioning time.

**Rejected.** Assuming ids are preserved and reusing the template's; assuming they are
regenerated and reading them once per form into a cache; matching answers by question
title; matching by position in the response payload; skipping fields whose id is unknown.

**Why.** Both assumptions about `files.copy` are equally plausible descriptions of
reality, and code correct under only one of them produces answers filed against the wrong
field **with no error** — a confidence score stored as a takeaway, and every downstream
number looking entirely plausible. Reading the ids back costs one API call per form and
removes the question.

Titles are refused as a key because the rotating slot's title changes every week *by
design*, and a teacher can retitle any field in the Forms UI without telling anyone.
Position is refused because a teacher adding a question shifts it.

Refusing to ingest, rather than skipping the unmapped field, is invariant 1 inverted: a
dropped observation is recoverable by re-pulling, but a mis-attributed one is not
detectable at all.

The text snapshot exists because "what was actually asked in week 3" has to be answerable
from the database alone. Reconstructing it later from `config/rotation.json` would give
whatever the config says *now*.

The fake Google client takes a `question_id_scheme` argument and the test suite runs the
mapping tests under both settings. That is not an edge case being covered; it is the
unresolved question being made harmless.

**Addendum, August 2026 — measured.** `files.copy` **preserves** question ids. Every Part
B form copied from one template answers under the same ids, so the rotating slot carries
the *same* `questionId` in week 2 and week 5 while showing different text. That makes the
per-form map and its `question_text` snapshot **necessary rather than belt-and-braces**: a
map keyed on `questionId` alone would have merged ten weeks of different questions into
one entry and looked entirely correct doing it. The `regenerate` setting stays, and both
are still tested — one measurement on one account is evidence about current behaviour, not
a guarantee, and correctness under both costs one API call per form.

---

## ADR-025 — The help checkbox requires a named recipient, or it is not on the form

**Context.** The last field asks whether the fellow would like someone to check in with
them. CU confirmed on 2026-08-10 that no dedicated fellow-support responder role exists
yet.

**Decision.** `config/help_routing.json` names the recipient. If it names nobody, the
field is **omitted from the form entirely** at provisioning time, and the omission is
logged, shown on the session screen, and recorded in the provisioning history. The
repository ships with the Director of Programs named, because that is a real person who
exists today. Requests are emailed **immediately on ingest**, not on a batch schedule.

**Rejected.** Shipping the field with an unrouted destination and a TODO; collecting
requests into the database only, for someone to find later; defaulting to a shared inbox
nobody was asked about; a weekly digest.

**Why.** A system that invites a young person to ask for help and routes the request
nowhere is **worse than one that never asks**. The request gets recorded, nobody is told,
and everybody involved — the fellow especially — assumes it was handled. Omitting the
field is the honest state, and it is visible rather than silent.

Immediate rather than batched because a fellow asking for contact should not wait for a
weekly pipeline run, and because the gap between "raised their hand" and "someone replied"
is the only part of this a program can actually shorten.

The row is written **before** the email is attempted, so a mail failure never loses a
request — the console screen, not the email, is the durable channel.

---

## ADR-026 — The help checkbox is excluded from every signal, permanently

**Context.** The obvious implementation is a boolean column on the Part B response row.

**Decision.** It is a **separate table**, `help_request`, with stricter RLS than anything
else in the system, its own console access list, and no policy granting anything. It
appears in no report, no export, no aggregate and no participation computation. The AI
tier never receives it. Nothing about a request is logged at any level, DEBUG included.
Two tests enforce this: one runs every export and report path against data containing a
request and asserts nothing from it comes out; the other **inspects the SQL each
participation, report and export path actually executes** and fails if any of them
mentions the table.

**Rejected.** A boolean column on `checkin_b` with a note not to include it; excluding it
by convention and code review; a permissive RLS policy matching the other tables.

**Why.** A column travels with every `SELECT *` anyone ever writes, into every export and
every aggregate, and **nothing announces it**. A separate table makes inclusion a
deliberate act.

The exclusion has to be permanent and has to be believable, because the field only works
while fellows believe it. If a fellow can suspect that ticking the box costs them
something, they stop ticking it — and the programme loses its only self-reported distress
channel, while the numbers continue to look fine.

Convention was rejected as the enforcement mechanism for the same reason a comment was
rejected as the enforcement mechanism for observation immutability in ADR-008: the failure
is invisible, so it needs a test that fails rather than a rule someone remembers.

The runtime SQL inspection is kept alongside a source scan because they catch different
things — a query built at runtime escapes a source scan, and a path this suite does not
exercise escapes the runtime check.

---

## ADR-027 — The AI clusters content, never people

**Context.** Gemini is available and there is a great deal of free text.

**Decision.** The model's only role in Part B is clustering *muddiest-point* answers into
2–5 themes for the teacher. It receives **anonymous strings and nothing else** — no names,
no addresses, no fellow ids, no counts per person, no confidence scores, no takeaways, and
nothing from the help table. Its output is about content. Free text is **counted, never
graded**. Straight-lining is flagged as a data-quality property of the responses and
enters no participation signal. No API key means no themes and a clear message, never a
failed run. Regenerating supersedes rather than overwrites.

**Rejected.** Scoring takeaway quality; classifying engagement from free text; summarising
a fellow's answers across weeks; flagging at-risk fellows; sentiment analysis; ranking
answers.

**Why.** Grading writing penalises ESL and neurodivergent fellows for reasons unrelated to
engagement — a fellow who writes "ok" and one who writes a paragraph may have taken away
exactly the same thing. Recording that a substantive response exists is fair; rating how
well written it is, is not, and a model will happily produce the second while looking like
it produced the first.

Clustering is about the lesson rather than about a person, which is why it is the one
place a model is allowed near this data at all. Anonymity is better privacy *and* better
accuracy at the same time: the model's job is finding what a group of students found
confusing, and everything else is context it could be wrong about.

Degrading rather than crashing matters because an optional enrichment that can fail a run
has stopped being optional.

Superseding rather than overwriting matters because a teacher who planned a lesson around
last week's themes has to be able to see what they actually read.

**Closing the loop is the highest-leverage trust move in the project.** Showing fellows
what their confusion changed about the teaching is what makes the form visibly *for* them
rather than *about* them, and research on youth data collection is consistent that this
drives honest responding. It also maps onto the Director of Programs' instruction to leave
room for a model where information is shared with fellows. The teacher-facing view is
built; the fellow-facing view is deliberately **not**, because that is a decision for the
data owner rather than a default.

---

## ADR-028 — Shoutouts are collected and resolved, never ranked

**Context.** Field 5 asks who helped you today. The obvious next step is a leaderboard.

**Decision.** Names are split, normalized and matched against the roster within the cohort
only. One unambiguous match links automatically; **ambiguity is never resolved
automatically** and goes to a review queue with the resolving human's identity recorded.
A name matching nobody is **legal, not an error**. There is no leaderboard, ranking, points
total, streak or public display, and none is built.

**Rejected.** Picking the closest fuzzy match; picking the first of two matches; treating
an unmatched name as a parse failure; a "most thanked" list; points for giving or
receiving recognition.

**Why.** A wrong link is worse than no link, and asymmetrically so: an unlinked fragment
sits visibly in a queue, while a wrongly linked one is invisible — it attributes someone's
praise to a person who did not earn it and nothing ever surfaces the mistake. Two fellows
named Jordan is the normal case, not the edge case.

A name matching nobody is legal because guest speakers, teachers and people outside the
cohort get thanked, and treating that as an error would train people to stop naming them.

A peer shoutout is **data about a third party who did not submit it**. It gets the same
protection as the submitter's own data, and it is never surfaced to the person named
without an explicit decision by the data owner.

**Recorded for whenever gamification is designed:** the research is explicit that
recognition, if it is ever ranked at all, should be ranked by **giving, not receiving**.
Ranking on recognition received builds a popularity contest and rewards the
already-visible; ranking on recognition given rewards the behaviour the programme actually
wants. Nothing here builds either — this note exists so the finding is not lost when
somebody picks the question up.

---

## ADR-029 — Simulated state is detected before Google is asked, and the demo will not overwrite a real install

**Context.** `make demo` resets the working database and fills it with forms created by
`FakeGoogleClient`. Connect a real Google account afterwards — which is what anybody does
after trying the demo — and the console asks Google for `fake-form-0001`. Google answers
`404 Requested entity was not found`. This is not hypothetical; it is what the first real
install did, and the message named neither the form, the account, nor the fact that the id
had never been real.

**Decision.** Three things. Form ids carry their provenance (`fake-form-` is not a
possible Google id), and a stored id belonging to the other kind of client is detected
**before any API call**, producing a message that names the account and the two ways out.
`cufa template replace --part a|b` retires an unusable template and creates a fresh one,
deactivating rather than deleting so copied forms keep their provenance. And `make demo`
**refuses to run** when the database holds a connected account, a real template, or
recorded check-ins, unless `CUFA_DEMO_FORCE=1`.

**Rejected.** Letting the 404 through with a friendlier wrapper; auto-creating a
replacement template on detection; making the demo use a separate database by default;
deleting the stale rows automatically.

**Why.** A 404 from Google is indistinguishable from an outage, a permissions problem and
a deleted form. Detecting the case offline is what lets the message be specific, and being
specific is the whole difference between a dead end and a next step.

Replacing a template is **explicit** because a template carries a one-time human Verified
step. Creating one silently would drop that step on the floor while the screen still
looked green — the exact false reassurance trap 2 exists to prevent.

A stale *session* form is different and is healed automatically: an id the client never
issued has no form behind it, so no response can be attached to it and there is nothing to
lose. A **real** form that 404s is never discarded, because it may be in Drive's bin with
every response still on it, and restoring it is the recovery — throwing the row away would
strand them.

The demo guard exists because the alternative is a destructive default. `make demo` is the
first thing anybody runs and the thing they run again to show someone; it should not be
able to delete a term's roster because it was pointed at the wrong database. Refusing is
recoverable; resetting is not.

**Related, found the same day.** Provisioning promised that a copy made before a failure
"leaves a row that a later run resumes". It did not: every caller wraps provisioning in a
transaction and rolls it back, taking the bookkeeping row with it, so each failed attempt
left another untracked form in Drive. The row is now written on a separate autocommit
connection. A promise about what survives a failure has to be tested under the failure.

## ADR-030 — Slack participation is captured by a bot, as it happens

**Decision.** A Slack app subscribed to message, reaction and membership events
writes each one to `slack_event` on arrival. `conversations.history` is used only
to backfill what the bot did not see. Every row is keyed by the act (channel +
message ts; or channel + message + user + reaction), never by Slack's delivery
id, so a retry, a restart and a backfill all collide with the live row instead of
duplicating it.

**Rejected.** Periodic workspace exports; a scheduled `conversations.history`
pull with no live component; the Slack analytics CSV as the source of record;
Zapier.

**Why.** Slack's free plan hides messages after 90 days and deletes them after a
year, and the workspace CU is waiting on may start on the free plan. Any approach
that *asks Slack later* is bounded by that window; a bot that records on arrival
is not. The analytics CSV counts messages but not reactions, and the Director's
definition names reacting explicitly. Zapier cannot confirm attendance at all.

The cost is that a bot must be running, and the contract ends. That risk is not
solved here; it is made visible (`load_run` left in `running`, `last_received`
in `cufa slack stats`) and made recoverable (backfill, and the Slack for
Nonprofits upgrade that removes the window). docs/setup/slack-bot.md ends with a
`TODO(owner)` for the person who restarts it, because a bot with no named owner
is a bot that is off by November.

The idempotency key is the same design as the forms pipeline: what identifies the
act, not how it was delivered. The test that matters is the one where a message
is recorded live, then read back through history, and the table still has one
row.

## ADR-031 — Message text is not stored

**Decision.** `slack_event.text` is NULL unless `CUFA_SLACK_STORE_TEXT=1`. Length,
word count, whether there was a link, whether it was a thread reply, and the
reaction name are stored; the words are not. The status page and the logs never
show text at any level, even when it is stored.

**Rejected.** Storing text by default and redacting on display; storing a hash of
the text; storing text for public channels only.

**Why.** The participation definition is "sending messages, reacting to messages,
etc" — it counts acts, it does not read them. Nothing downstream needs the words.
The people talking are young; the channel is their conversation with each other;
and a table of everything they said is a very different thing to hold than a
table of when they said something, both for them and for whoever inherits this
database without a data manager. Minimum necessary is the rule, and here the
minimum is the count.

Redact-on-display would still leave the text in Postgres. A hash still answers
"did anyone write exactly this", which is a question this system should not be
able to answer. Public-only would make the exposure depend on a channel setting a
fellow cannot see.

The switch exists because the data owner may decide otherwise for a specific
workspace — for the muddiest-point-style clustering that Part B does on form
answers, say. That is her decision to take, and taking it should be one line in
`.env`, not a code change.

## ADR-032 — Q&A channels keep their text, by name, in their own tables; a repeat is pointed at an *answered* earlier question; the model sees strings

**Context.** Two things the Director's team asked for need the words of a
message: a summary of a session's Q&A for the teacher, and a reply that points
someone who asks a question that was already answered at the earlier answer.
ADR-031 stores no text.

**Decision.** Channels named in `CUFA_SLACK_QA_CHANNELS` — and only those — have
their question and reply text stored, in `slack_qa_question` / `slack_qa_answer`,
never on `slack_event`. The rows carry a Slack user id and no email, and nothing
joins them to the roster. The bot points a new question at an earlier one only when
the earlier one was **answered** (a ✅, or a reply from someone other than the
asker), links the ✅'d reply when there is one and the thread otherwise, names the
session it came from, is worded as a guess, and posts once per question. Matching is
two-tier: word overlap decides what it can; a model is asked only about earlier
questions that share a word, only as anonymous strings, and only with a key. The
summary is generated from numbered question and reply texts and nothing else,
degrades to a plain digest without a model, and is superseded rather than
overwritten on regeneration.

**Rejected.** Turning `CUFA_SLACK_STORE_TEXT` on for the whole workspace to get
the Q&A features; storing Q&A text on `slack_event.text`; pointing at any earlier
question that looks similar, answered or not; a pointer to the *newest* similar
question rather than the answered one; sending every earlier question to the
model on every new one; embedding vectors; a summary that names who asked or
answered; letting the mention handler answer a retried delivery.

**Why.** A Q&A channel is different in kind from #general. A question is posted
*so that* it can be found and answered; the value of an answer is that the next
person can be pointed at it. Neither is possible without the words — and both are
the channel's own purpose, not a repurposing of what fellows said to each other.
Naming the channel is the data owner's explicit act, the same shape as ADR-031's
switch, and narrower. Keeping the text off `slack_event` keeps ADR-031's
invariant simple to state and simple to test: that column is NULL, everywhere.

Answered-only, because a pointer to an unanswered thread helps nobody and teaches
fellows to ignore the bot. Hedged wording and one-per-question, because word
overlap is not understanding: "when does the session start" and "when does the
session end" share every content word. The model is asked only about the
candidates overlap could not settle, as the passphrase tier 2 is asked only about
what edit distance cannot read (ADR-027's boundary, applied to Slack: it sees
strings, never people). No key means the deterministic tier still works; a
feature that stops when the key runs out has made the key mandatory.

Superseding on regeneration is the muddiest-point rule: what a teacher read last
week should still be there to read.
