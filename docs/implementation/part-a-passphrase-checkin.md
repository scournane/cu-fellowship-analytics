# Implementation Prompt: Part A — Mid-Session Passphrase Check-in

**Assume the repository is empty.** No schema, no database, no code, no config.
Build everything from scratch, and **set it all up yourself** — the person running
this should need one command to get a working local stack, not a setup checklist.

**Scope: three fields.** Verified email, timestamp, session passphrase. Read
"Out of scope" before starting and honor it.

---

## Context

Civics Unplugged (CU) runs the Civic Innovators Fellowship — roughly 30 young
people attending live lessons over Zoom. Attendance at live lessons is one
component of the participation CU wants to track.

Zoom is a poor source for this. Since a March 2023 API change, guest participants
have `id` and `participant_user_id` hidden as PII, and email is returned only for
participants signed into Zoom. For an unauthenticated joiner the entire record is
a self-typed display name and a duration — so renaming yourself, or joining and
walking away, produces a record identical to real attendance.

The replacement is a **Google Form check-in released mid-session**, using Google's
*Verified* email collection so the address is confirmed by Google rather than
typed. The teacher says a **passphrase** aloud during the lesson and displays it on
screen; the form asks for it. Someone not in the room does not have it.

**Why mid-session:** released at the start, the form proves only that someone
joined. Released 15–25 minutes in, it proves presence at a moment the fellow could
not have predicted. The passphrase is what makes that provable — a timestamp alone
is satisfied by an idle open tab.

---

## Design invariants

1. **Never drop a submission.** Wrong passphrase, unknown email, timestamp outside
   every window — all recorded, with the reason. A dropped row is an unrecoverable
   observation, and it hides exactly the cases worth looking at.
2. **Separate the observation from the decision.** `checkin` stores what was
   observed and is immutable. `attendance_decision` stores the judgment — whether
   they attended — and is versioned. Never mutate a decision in place; supersede
   it. This is what makes the human override auditable and lets a changed
   definition be re-run over history.
3. **Every decision carries its provenance** — which rule, which model, which
   human, and when. "Why is this fellow marked absent?" must be answerable months
   later without guessing.
4. **A human override always wins** over a rule or an AI decision, and is never
   silently overwritten by a later automated pass.
5. **Ingest must be idempotent.** Re-running over the same input writes zero new
   rows. This tool re-reads the same sheet every run.
6. **Identity never blocks ingest.** An unrecognized email still produces a
   record; the address goes to a review queue.
7. **Everything is cohort-keyed** for later year-over-year comparison.
8. **Timestamps are UTC** past the parser boundary.

---

## Deliverable 1 — The Google Form

Write `docs/setup/part-a-form.md` — an exact click path a non-technical CU staffer
can follow unaided.

**Settings:**

- Settings → Responses → **Collect email addresses: Verified**
  Google offers *Verified* and *Responder input*. Responder input lets anyone type
  any address, destroying the only reason this form exists. Verified requires
  Google sign-in and address confirmation.
- **Do NOT enable "Limit to 1 response."** It is per-form, not per-session, so it
  would block every check-in after the first. Duplicates are handled in the loader.
- Link responses to a Google Sheet.

**Questions — exactly one:**

| Question | Type | Required |
|---|---|---|
| "Today's passphrase" | Short answer | Yes |

No session dropdown. Session is derived from the timestamp; a dropdown is user
input that can contradict reality.

**Form header notice.** Plain language, no jargon: what is collected, who sees it,
what it is used for. Research on adolescent survey participation is consistent
that transparency drives honest responding.

Leave a literal `TODO(retention)` marker where the retention period belongs. CU
has not defined one. **Do not invent a number** — an assumed retention period
silently becomes policy.

**Passphrase guidance for the teacher** (include in the doc):

- One word, ~5–10 letters
- Avoid homophones (`their`/`there`, `flour`/`flower`)
- Avoid words in the slides or readings — guessable from materials
- Never reuse across sessions
- **Say it aloud AND display it on screen.** Audio-only excludes deaf and
  hard-of-hearing fellows and anyone with an audio failure. This widens the leak
  surface, which is exactly why the passphrase is one signal among several and
  never proof on its own.

---

## Deliverable 2 — Session configuration

A plain CSV at `config/sessions.csv`, loaded by a CLI command. No external
provisioning required.

| Field | Required | Notes |
|---|---|---|
| `session_id` | yes | stable slug, e.g. `2026-27-w03` |
| `cohort_id` | yes | e.g. `2026-27` |
| `title` | yes | |
| `scheduled_at` | yes | local date+time |
| `timezone` | yes | IANA, e.g. `America/New_York` |
| `duration_minutes` | yes | |
| `grace_minutes` | no, default 15 | widens window both sides |
| `passphrase` | no | absent is legal |
| `announced_at` | no | local time the passphrase was given |

Window: `[scheduled_at_utc - grace, scheduled_at_utc + duration + grace]`

---

## Deliverable 3 — Supabase (Postgres)

Use the **Supabase CLI** for a fully local, offline Postgres stack. No Supabase
Cloud account is needed — `login` and `link` are only required for deployment.

**Set this up yourself.** Run `supabase init`, author the migrations, write the
seed file, and verify `supabase start` + `supabase db reset` produce a working
database. Do not leave manual setup steps for the user.

- `supabase init` → creates `supabase/`
- Migrations live in `supabase/migrations/` (`supabase migration new <name>`)
- Seed data in `supabase/seed.sql`, applied automatically by `supabase db reset`
- Local Postgres: `postgresql://postgres:postgres@localhost:54322/postgres`
- Studio (visual table browser): `http://localhost:54323`
- Docker Desktop must be running before `supabase start` — say so in the docs, and
  fail with a clear message if the stack is unreachable

Access Postgres from Python with **psycopg 3** and plain SQL. Do not use
`supabase-py` — this is batch ingest, not a web app, and direct SQL is easier to
test and to hand off.

### Schema

Author as migrations. Use real Postgres types — `uuid`, `timestamptz`, `jsonb`,
`text` with CHECK constraints.

**`cohort`** — `cohort_id` PK, label, start/end dates

**`fellow`** — `fellow_id` PK (CU-issued, stable), `cohort_id` FK, `full_name`,
`primary_email`, `status`. The roster.

**`session`** — every field from Deliverable 2, plus `scheduled_at_utc` and
`announced_at_utc` stored alongside the local values and timezone.

**`checkin`** — one row per form submission. **Immutable.**

| Column | Notes |
|---|---|
| `checkin_id` | uuid PK |
| `source_event_id` | text UNIQUE — idempotency key |
| `submitted_email` | normalized |
| `submitted_at_utc` | timestamptz |
| `submitted_at_raw` | text, verbatim |
| `source_timezone` | text |
| `session_id` | FK, nullable |
| `session_match` | `matched` \| `none` \| `ambiguous` |
| `passphrase_raw` | text, exactly as typed |
| `passphrase_match` | see Deliverable 5 |
| `edit_distance` | int, nullable |
| `latency_seconds` | int, nullable |
| `extra_fields` | jsonb — unrecognized columns |
| `load_id` | FK |
| `ingested_at` | timestamptz |

Note `checkin` stores the **email**, not a `fellow_id`. Identity resolves at read
time by joining the roster, so fixing a roster entry re-attributes all history
with no backfill.

**`attendance_decision`** — the judgment. Append-only, versioned.

| Column | Notes |
|---|---|
| `decision_id` | uuid PK |
| `checkin_id` | FK |
| `attended` | boolean |
| `confidence` | numeric 0–1 |
| `decided_by` | `rule` \| `ai` \| `human` |
| `rule_name` | text, nullable |
| `ai_model` | text, nullable |
| `ai_prompt_version` | text, nullable |
| `ai_reasoning` | text, nullable |
| `human_email` | text, nullable |
| `note` | text, nullable |
| `superseded_at` | timestamptz, NULL = current |
| `created_at` | timestamptz |

Add a **partial unique index** on `checkin_id WHERE superseded_at IS NULL` so
exactly one decision is current per check-in. Superseding is an UPDATE of
`superseded_at` on the old row plus an INSERT of the new one — never an in-place
edit of the decision itself.

**`ai_adjudication_cache`** — key: `(expected_normalized, submitted_normalized,
prompt_version, model)`. Value: verdict, confidence, reasoning, created_at. The
free Gemini tier is rate-limited; identical string pairs must never re-call.

**`identity_unresolved`** — email, first/last seen, occurrence count, optional
best guess + score.

**`load_run`** — source, origin, **SHA-256 of input bytes**, start/finish, rows
read/written/skipped, status, error.

### Row Level Security

Enable RLS on `fellow`, `checkin`, and `attendance_decision`. The pipeline
connects as the service role and bypasses it. Leave a documented `TODO(access)`
policy stub — CU has said the data should be visible to every full-time team
member but has not defined granular permissions, and a derived attendance
judgment should not automatically be as open as a raw timestamp. Do not invent
the policy.

---

## Deliverable 4 — The loader

```
cufa ingest part-a --csv <path> --cohort <id> --sheet-timezone <IANA>
```

Read the CSV exported from the responses Sheet. Match headers
case-insensitively, tolerate reordering, and preserve unrecognized columns into
`extra_fields` rather than dropping them — Google appends columns when a form
changes.

### ⚠️ The timezone trap

Google Sheets writes form timestamps in the **spreadsheet's** locale timezone with
no offset marker. Parsing as UTC shifts every check-in by hours and misassigns
sessions across window boundaries — a silent corruption producing plausible wrong
answers.

- Require `--sheet-timezone` explicitly. **Never default to UTC or to the
  machine's local timezone.** Missing → fail with an error naming the flag.
- Convert at the parser boundary; everything downstream is UTC.
- Store the raw string and the timezone used, so conversion is auditable.
- Use `zoneinfo`. Handle DST correctly.

### Idempotency

`source_event_id` = SHA-256 of `(source_file_or_sheet_id, normalized_email,
submitted_at_utc_iso)`.

**Not the row number** — row numbers shift when anyone sorts or deletes a row,
which would re-ingest everything forever. With the UNIQUE constraint and
`ON CONFLICT DO NOTHING`, a second run over identical input writes zero rows.

### Session assignment

- exactly one window → `matched`
- no window → `none`, `session_id` NULL, **row still written**
- multiple windows → `ambiguous`, `session_id` NULL, log a warning naming the
  overlapping sessions (a config bug worth surfacing loudly)

### Identity

Normalize emails: strip whitespace, lowercase. **Do not strip Gmail dots or
`+suffix`** — that is a lossy guess, and collapsing one fellow's address into
another's is worse than leaving it unmatched. Match against
`fellow.primary_email` within the cohort; on a miss, upsert
`identity_unresolved`. Never auto-link on a fuzzy name guess.

---

## Deliverable 5 — Deterministic adjudication (tier 1)

Normalize both strings: trim, collapse internal whitespace, lowercase, strip
punctuation.

**Match outcomes** — all recorded, none fatal:

| Outcome | Condition |
|---|---|
| `exact` | normalized strings equal |
| `fuzzy` | Levenshtein ≤ `max_edit_distance` (config, default 1) |
| `mismatch` | no match |
| `not_set` | session has no passphrase configured |
| `no_session` | matched no session window |

**Rules produce a decision directly:**

| Condition | `attended` | `rule_name` | Confidence |
|---|---|---|---|
| `exact` + in window | true | `exact_match` | 1.0 |
| `fuzzy` + in window | true | `fuzzy_match` | 0.9 |
| `not_set` + in window | true | `no_passphrase_required` | 0.7 |
| `no_session` | false | `outside_all_windows` | 0.6 |
| `mismatch` + in window | **escalate to tier 2** | — | — |

Fuzzy is on by default because the passphrase is heard aloud and typed on a phone.
Rejecting `justise` for `justice` penalizes someone who *was there and heard it* —
backwards from the intent.

Implement Levenshtein directly; ~20 lines, not worth a dependency.

---

## Deliverable 6 — AI adjudication (tier 2, Gemini)

Only `mismatch`-in-window cases reach this tier. Levenshtein cannot handle
`"the word was justice"`, `"justice i think?"`, `"jushtis"`, or `"sorry I missed
it"` — all of which a human reads instantly and distance scores wrongly.

**SDK:** `google-genai` (`from google import genai`; `client = genai.Client()`).
The older `google-generativeai` package was deprecated in August 2025 — do not use
it.

**Model:** `gemini-2.5-flash` by default, configurable. Free tier as of this
writing: 10 RPM / 250 requests per day for Flash, 15 RPM / 1,000 RPD for
Flash-Lite. Gemini 2.5 Pro left the free tier in April 2026. Read the key from
`GEMINI_API_KEY`; `.env` must be gitignored, with a committed `.env.example`.

**Send only two strings — the expected passphrase and the submitted answer.** No
names, no emails, no attendance history, no cohort data. Narrower context is both
better privacy and better accuracy: the model's only job is judging whether the
answer indicates the person heard the word.

Use structured JSON output with a response schema:

```json
{ "heard_the_passphrase": true, "confidence": 0.0, "reasoning": "one sentence" }
```

**Requirements:**

- `temperature=0` for reproducibility
- Version the prompt string (`PROMPT_VERSION = "v1"`) and store it on every
  decision — a changed prompt must be distinguishable in the record
- **Check `ai_adjudication_cache` before every call**; write through after
- Respect rate limits: retry with exponential backoff on 429, cap total calls per
  run via config
- **Degrade, never crash.** No API key, no network, or quota exhausted → write
  the decision as `attended = NULL`-equivalent status `needs_review` with
  `decided_by='rule'`, `rule_name='ai_unavailable'`. The pipeline must complete
  fully without Gemini.
- `--no-ai` flag skips tier 2 entirely and routes everything to `needs_review`

Represent `needs_review` explicitly — either a nullable `attended` with a
`status` column, or a three-valued enum. Do not encode "unknown" as `false`;
absent evidence is not evidence of absence.

---

## Deliverable 7 — Human override (tier 3)

```
cufa decide --checkin <id> --attended true|false --by <email> --note "<text>"
```

Supersedes the current decision: sets `superseded_at` on the old row, inserts a
new one with `decided_by='human'`, `confidence=1.0`, and the human's email.

**A human decision is never superseded by a later rule or AI pass.** Re-running
adjudication must skip any check-in whose current decision has
`decided_by='human'`. Add a `--force` flag that overrides this, and make it print
a loud warning naming what it is about to overwrite.

```
cufa review --status needs_review     # the queue, oldest first
cufa review --status ai               # everything the model decided, for spot-checking
```

The second command matters: the AI tier should be auditable by a human who wants
to sample its judgments, not a black box.

---

## Deliverable 8 — Latency

`latency_seconds` = seconds between the passphrase being announced and submission.

- Use `session.announced_at_utc` if set
- Otherwise derive `T0` = earliest submission matched to that session
- **Store it; do not interpret it.** No thresholds, no flags, no "suspicious"
  marker. It is an input to analysis that does not exist yet.
- Under derived `T0` the first submitter always has latency 0 — document that as
  expected, not a bug
- NULL when no session matched

---

## Deliverable 9 — CLI

```
cufa db up                      # supabase start + db reset, idempotent
cufa db down
cufa load-roster    --csv <path> --cohort <id>
cufa load-sessions  --csv <path>
cufa ingest part-a  --csv <path> --cohort <id> --sheet-timezone <IANA>
cufa adjudicate     --cohort <id> [--no-ai] [--force]
cufa decide         --checkin <id> --attended <bool> --by <email> --note "<text>"
cufa review         [--status needs_review|ai|unresolved-identity]
cufa report         --cohort <id>
```

`ingest` prints rows read / written / skipped-duplicate, session match breakdown,
passphrase outcome breakdown, unresolved identity count.

`adjudicate` prints decisions by tier, AI calls made vs. cache hits, and the
`needs_review` count.

`report` prints a per-fellow, per-session attendance grid to the terminal, with a
`--json` flag.

---

## Deliverable 10 — How to test it (build this)

The person running this needs to see it work end to end without real data or a
Gemini key. Provide all of:

**`make setup`** — installs Python deps, runs `supabase init`, checks Docker is
running, prints a clear message if not.

**`make demo`** — the one-command path:
1. `supabase start` + `supabase db reset`
2. generate synthetic fixtures
3. load roster and sessions
4. ingest check-ins
5. adjudicate with `--no-ai`
6. print the report

It must succeed on a clean machine with no `GEMINI_API_KEY` set.

**`scripts/generate_fixtures.py`** — deterministic (fixed seed), producing:
- 20 synthetic fellows, obviously fake names, `@example.invalid` addresses
- 6 sessions across 6 weeks, with passphrases, one session with none set
- check-ins covering **every** edge case: exact, case/whitespace variants,
  punctuation, edit-distance-1 typos, conversational answers
  (`"the word was justice"`), plain wrong answers, blank answers, submissions
  outside every window, an overlapping-window pair, an unknown email, an exact
  duplicate row, a DST-boundary submission, and an unexpected extra column

**`make demo-ai`** — same as `make demo` but with tier 2 live. Skips with a clear
message if `GEMINI_API_KEY` is unset. This is how the AI path gets exercised
against real ambiguous strings.

**Inspecting results:** document that Supabase Studio at `http://localhost:54323`
browses every table visually — the easiest way to see what the pipeline produced.
Include a few copy-pasteable SQL queries in the docs: current decisions per
fellow, everything needing review, AI decisions with reasoning, cache hit rate.

**`make test`** — pytest, **no network**.

**`make clean`** — `supabase stop`, remove generated fixtures.

---

## Deliverable 11 — Tests

Real assertions. Never commit real fellow data.

1. **Idempotency** — same file twice; identical row count, second run reports all
   skipped
2. **Row reordering** — shuffling the fixture yields identical `source_event_id`s
3. **Timezone** — `2026-09-15 13:05:00` in `America/New_York` → `2026-09-15T17:05:00Z`
4. **DST boundary** — one row each side, both correct
5. **Missing `--sheet-timezone`** errors clearly rather than defaulting
6. **Session assignment** — matched / none / ambiguous each behave as specified
   and all three still write a row
7. **Passphrase outcomes** — all five
8. **Normalization** — `"  Justice "`, `"JUSTICE"`, `"justice."` all → `exact`
9. **Unknown email** → `identity_unresolved`, check-in still written
10. **Gmail dots preserved** — `a.b@gmail.com` does not match `ab@gmail.com`
11. **Latency** — derived `T0`, explicit `announced_at`, NULL on no-session
12. **Extra column** preserved in `extra_fields`, parser does not crash
13. **Decision versioning** — overriding supersedes; exactly one current decision
    per check-in; the partial unique index actually enforces it
14. **Human wins** — re-running `adjudicate` does not overwrite a human decision;
    `--force` does, and warns
15. **AI cache** — a repeated string pair makes exactly one API call (fake
    adjudicator, call counter)
16. **AI unavailable** — no key set → pipeline completes, cases land in
    `needs_review`, nothing crashes
17. **`needs_review` ≠ absent** — an unknown case never silently becomes
    `attended = false`

Tests must not hit the network. Inject a fake adjudicator implementing the same
interface as the Gemini client.

---

## Deliverable 12 — Documentation

- `docs/setup/part-a-form.md` — the click path from Deliverable 1
- `docs/setup/local-dev.md` — Docker, Supabase CLI, Studio, the make targets, the
  SQL snippets
- `docs/decisions.md` — ADRs for: form-over-Zoom-API, Verified-over-responder-
  input, timestamp-over-dropdown, never-drop-a-submission, observation-separate-
  from-decision, three-tier adjudication, Gemini-for-ambiguous-only. Each records
  the decision, alternatives rejected, and why.
- `README.md` — clone to working demo in under ten minutes

---

## Constraints

- **Python 3.11+.** Dependencies limited to `psycopg[binary]`, `google-genai`,
  `pytest`, and `python-dotenv`. Everything else stdlib. CU has no data manager;
  each dependency is inherited maintenance.
- **Supabase CLI** for the database. Docker required locally.
- **No secrets committed.** `.env` gitignored, `.env.example` committed.
- **Never log an email at INFO or above.** Counts and `fellow_id`s at INFO; raw
  addresses only at DEBUG. Never log a Gemini API key at any level.
- Type-hint public functions. Docstrings explain *why*, not *what*.

## Acceptance criteria

1. `make setup && make demo` succeeds on a clean machine with **no
   `GEMINI_API_KEY`**, and prints an attendance report
2. Re-running `make demo` produces identical output — no duplicate check-ins
3. Every fixture row appears in `checkin` regardless of passphrase or session
   outcome, asserted as `count(checkin) == count(fixture rows)`
4. Exactly one current `attendance_decision` per check-in, enforced by the index
5. A human override survives a subsequent `adjudicate` run
6. `make demo-ai` with a key set routes only `mismatch` cases to Gemini, and the
   second run makes zero API calls (all cached)
7. `make test` passes with no network access
8. Supabase Studio shows populated tables after `make demo`
9. `docs/setup/part-a-form.md` is complete enough for a non-technical staffer to
   build the form and export responses unaided

## Out of scope

- Part B (takeaway, confidence scale, muddiest point, application prompt, peer
  shoutout, help checkbox)
- Slack or Zoom integration
- The link-posting bot, scheduled triggers, reminder nudges
- Gamification, points, streaks, leaderboards
- Participation scoring across components, or any at-risk flag
- HTML reports or dashboards
- Google Sheets API access — CSV export only
- Deploying to Supabase Cloud — local stack only

Land these three fields end to end first.
