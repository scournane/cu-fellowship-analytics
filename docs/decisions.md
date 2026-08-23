# Architecture decision records

One record per decision that would be expensive or dangerous to reverse by accident.
Each says what the situation was, what was chosen, what was rejected, and why. If you
are about to change one of these, the "why" is the thing to argue with.

Status of every ADR below: **accepted**, August 2026, Part A.

Two things are deliberately **not** decided and must not be invented — see ADR-020.

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
attendance.

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
