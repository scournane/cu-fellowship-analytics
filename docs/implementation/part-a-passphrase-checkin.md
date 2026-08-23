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

**Nobody at CU should have to build a form by hand.** A staff member fills in
session details in a small web console; the system provisions the Google Form,
publishes it, and later pulls the responses. CU has no data manager — every manual
step is a step that stops happening in November.

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
   rows.
6. **Identity never blocks ingest.** An unrecognized email still produces a
   record; the address goes to a review queue.
7. **Verify, don't assume.** Any external state this system depends on — that a
   form is published, that email collection is Verified — must be read back and
   checked, never trusted because a call returned 200. See the three traps below.
8. **Everything is cohort-keyed** for later year-over-year comparison.
9. **Timestamps are UTC** past the parser boundary.

---

## ⚠️ Three verified Google API traps

These are researched and current as of August 2026. Each one fails **silently** —
the code appears to work and no responses arrive, or arrive unattributable. Build
against them from the start; do not discover them later.

### Trap 1 — API-created forms are unpublished by default

Google changed this in 2026. Forms created via the API were auto-published through
**June 30, 2026**; after that date they are created in an **unpublished state and
accept no responses**. The form exists, the link resolves, and every submission is
refused.

**You must explicitly call `forms.setPublishSettings()`** with
`publishState: { isPublished: true, isAcceptingResponses: true }` after creating
each form, then **read the state back and assert it** before telling anyone the
form is ready. Required scope: `https://www.googleapis.com/auth/forms.body`.

### Trap 2 — `emailCollectionType: VERIFIED` is unreliable via the API

Setting email collection through `forms.batchUpdate` → `updateSettings` has been
reported returning `400 INVALID_ARGUMENT` on
`requests[0].update_settings.settings.email_collection_type`, with no working enum
value found and no documented resolution. Verified email collection is the entire
premise of this design, so it cannot depend on a call that may reject.

**Use a template-and-copy architecture instead:**

1. The app creates one **template form** via the API, once.
2. The app then shows a human a link and asks them to open it and set
   **Settings → Responses → Collect email addresses → Verified** by hand. This is
   a one-time, ~30-second step — the single thing the API cannot reliably do.
3. The app **reads `form.settings` back and refuses to proceed** until it confirms
   Verified. Do not accept the human's word for it; check.
4. Every per-session form is then a **Drive `files.copy()` of that template**.
   Copying a Google Form preserves its settings, including email collection.
5. Use `forms.batchUpdate` only for what works reliably — title, description, and
   the passphrase question text.

Re-verify the template's settings before each provisioning run. If someone edits
the template and breaks it, fail loudly rather than quietly producing forms that
collect nothing.

### Trap 3 — The REST API cannot link a response spreadsheet

`Form.setDestination()` exists in Apps Script but has no REST equivalent. An
API-provisioned form has **no linked Sheet**, so there is no CSV to export.

**Read responses via `forms.responses.list` instead.** This is strictly better:
it returns `respondentEmail` and RFC3339 UTC timestamps directly, which
**eliminates the Google Sheets timezone trap entirely** — Sheets writes form
timestamps in the spreadsheet's locale with no offset marker, and parsing those as
UTC silently shifts every check-in by hours.

Supports incremental polling via `filter=timestamp > N`. Store a per-form
watermark and poll forward.

Keep a CSV import path as a documented fallback for manually created forms — and
there, `--sheet-timezone` remains mandatory with no default.

### Trap 4 — Service accounts cannot own Workspace assets

A service account cannot own a Google Form. Options are domain-wide delegation
(requires Workspace admin console access) or ordinary user OAuth.

**Use user OAuth.** A CU staff member signs in once and grants:

- `https://www.googleapis.com/auth/forms.body` — create, update, publish
- `https://www.googleapis.com/auth/drive.file` — copy the template

`drive.file` is sufficient because the app creates the template itself, so the
template is app-created and stays in scope. Do not request broader Drive scope.

Store the refresh token **encrypted at rest** in Postgres, never in a file or in
the repo. Forms are then owned by that staff member's account, which is what CU
wants — the work product stays in CU's Drive, not in a contractor's.

---

## Deliverable 1 — The session console (web app)

A small internal web app. Audience: two or three CU staff, non-technical.

### Stack

**FastAPI + Jinja2 server-rendered HTML + HTMX.** Rationale: same language as the
pipeline, one deploy, one dependency set, no build step. CU inherits this without
a data manager, and a Python-only codebase is the shortest handoff. (Next.js on
Supabase is the reasonable alternative if the team already runs JS; do not build
both.)

Auth for the console itself: Google sign-in, restricted to an allowlist of CU
email addresses in config. Do not build a password system.

### Screens

**1. Connect Google** — one-time OAuth flow. Shows connection status, the
connected account, granted scopes, and a disconnect button.

**2. Template setup** — one-time. Creates the template form, shows the human the
link and the one manual step (set email collection to Verified), then a
**"Verify template"** button that reads `form.settings` back. Green only when the
API confirms `VERIFIED`. Blocks everything downstream until it does.

**3. Sessions** — list, create, edit. Fields:

| Field | Required | Notes |
|---|---|---|
| `title` | yes | |
| `scheduled_at` | yes | local date+time picker |
| `timezone` | yes | IANA, defaults to the browser's |
| `duration_minutes` | yes | |
| `grace_minutes` | no, default 15 | widens the window both sides |
| `passphrase` | no | absent is legal |
| `announced_at` | no | filled by the "Announce now" button |
| `cohort_id` | yes | |

**Passphrase field guidance shown inline** (not buried in a doc):
one word, ~5–10 letters; avoid homophones (`their`/`there`, `flour`/`flower`);
avoid words appearing in the slides or readings, which are guessable from
materials; never reuse across sessions. Offer a **"suggest a passphrase"** button
drawing from a curated wordlist that excludes homophones and near-homophones.

**Warn on reuse** — if a passphrase matches any previous session in the cohort,
show a warning before saving.

**4. Session detail** — the live view a teacher uses mid-lesson:
- **"Provision form"** button → copies the template, sets title and question,
  publishes, verifies publish state, stores the form ID and URL
- The form URL, with a big copy button and a QR code
- **"Announce now"** → stamps `announced_at_utc`, which is what latency is
  measured from
- Live response count, polled
- **"Pull responses"** → runs ingest for this session on demand

**5. Review** — the `needs_review` queue with one-click attended / not-attended,
which writes a human decision (Deliverable 7). Also a tab listing AI decisions
with their reasoning so a human can spot-check the model rather than trust it.

### Accessibility and the passphrase

The console must remind the teacher to **say the passphrase aloud AND display it
on screen**. Audio-only excludes deaf and hard-of-hearing fellows and anyone with
an audio failure. This widens the leak surface, which is exactly why the
passphrase is one signal among several and never proof on its own. Put that
sentence in the UI, not only in the docs.

### Provisioning must be safe to retry

Provisioning is idempotent per session: if a form already exists for that session,
show it rather than creating a second one. Every provisioning attempt is logged
with its outcome. A `--dry-run` mode logs the calls it would make without touching
Google.

### The form itself

- **Exactly one question:** "Today's passphrase", short answer, required
- No session dropdown — session is derived from the timestamp, and a dropdown is
  user input that can contradict reality
- **Header notice**, plain language, no jargon: what is collected, who sees it,
  what it is used for. Research on adolescent survey participation is consistent
  that transparency drives honest responding.
- Leave a literal `TODO(retention)` marker where the retention period belongs. CU
  has not defined one. **Do not invent a number** — an assumed retention period
  silently becomes policy.

---

## Deliverable 2 — Supabase (Postgres)

Use the **Supabase CLI** for a fully local, offline stack. No cloud account
needed — `login` and `link` are only required for deployment.

**Set this up yourself.** Run `supabase init`, author the migrations, write the
seed, and verify `supabase start` + `supabase db reset` produce a working
database. Leave no manual setup steps.

- Migrations in `supabase/migrations/` (`supabase migration new <name>`)
- Seed in `supabase/seed.sql`, applied by `supabase db reset`
- Local Postgres: `postgresql://postgres:postgres@localhost:54322/postgres`
- Studio (visual table browser): `http://localhost:54323`
- Docker must be running before `supabase start` — say so, and fail with a clear
  message if the stack is unreachable

Access Postgres with **psycopg 3** and plain SQL. Not `supabase-py` — this is
batch ingest plus a small server, and direct SQL is easier to test and hand off.

### Schema

Real Postgres types — `uuid`, `timestamptz`, `jsonb`, `text` with CHECK
constraints.

**`cohort`** — `cohort_id` PK, label, start/end dates

**`fellow`** — `fellow_id` PK (CU-issued, stable), `cohort_id` FK, `full_name`,
`primary_email`, `status`. The roster.

**`session`** — every field from Deliverable 1, plus `scheduled_at_utc` and
`announced_at_utc` alongside the local values and timezone.

**`google_credential`** — connected account email, **encrypted** refresh token,
granted scopes, connected_at, last_refreshed_at. Encryption key from env, never
committed.

**`form_template`** — template form ID, `verified_email_confirmed_at`, a snapshot
of the settings as last read back, `last_verified_at`.

**`session_form`** — `session_id` FK, `form_id`, `form_url`, `provisioned_at`,
`published_at`, `publish_verified_at`, `response_watermark` (RFC3339, for
incremental polling), `last_polled_at`.

**`checkin`** — one row per response. **Immutable.**

| Column | Notes |
|---|---|
| `checkin_id` | uuid PK |
| `source_event_id` | text UNIQUE — idempotency key |
| `source` | `forms_api` \| `csv` |
| `submitted_email` | normalized |
| `submitted_at_utc` | timestamptz |
| `submitted_at_raw` | text, verbatim |
| `source_timezone` | text, NULL on the API path |
| `session_id` | FK, nullable |
| `session_match` | `matched` \| `none` \| `ambiguous` |
| `passphrase_raw` | text, exactly as typed |
| `passphrase_match` | see Deliverable 4 |
| `edit_distance` | int, nullable |
| `latency_seconds` | int, nullable |
| `extra_fields` | jsonb |
| `load_id` | FK |
| `ingested_at` | timestamptz |

`checkin` stores the **email**, not a `fellow_id`. Identity resolves at read time
by joining the roster, so fixing a roster entry re-attributes all history with no
backfill.

**`attendance_decision`** — the judgment. Append-only, versioned.

| Column | Notes |
|---|---|
| `decision_id` | uuid PK |
| `checkin_id` | FK |
| `attended` | boolean, **nullable** |
| `status` | `attended` \| `not_attended` \| `needs_review` |
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

**Partial unique index** on `checkin_id WHERE superseded_at IS NULL` — exactly one
current decision per check-in. Superseding is an UPDATE of `superseded_at` plus an
INSERT, never an in-place edit.

**`ai_adjudication_cache`** — key `(expected_normalized, submitted_normalized,
prompt_version, model)`; value verdict, confidence, reasoning, created_at.

**`identity_unresolved`** — email, first/last seen, occurrence count, optional
best guess and score.

**`load_run`** — source, origin, SHA-256 of input bytes (CSV path), start/finish,
rows read/written/skipped, status, error.

**`provisioning_log`** — session, action, request summary, outcome, error, at.

### Row Level Security

Enable RLS on `fellow`, `checkin`, `attendance_decision`, and
`google_credential`. The pipeline connects as the service role. Leave a documented
`TODO(access)` policy stub — CU has said the data should be visible to every
full-time team member but has not defined granular permissions, and a derived
attendance judgment should not automatically be as open as a raw timestamp. **Do
not invent the policy.**

---

## Deliverable 3 — Response ingestion

**Primary path — Forms API:**

```
cufa pull --session <id>          # one session
cufa pull --cohort <id>           # every provisioned session, incremental
```

Call `forms.responses.list` with `filter=timestamp > <watermark>`, paginate via
`nextPageToken`, advance the watermark only after a successful full page loop.
Timestamps are already RFC3339 UTC — no conversion, no timezone flag.

**Fallback path — CSV**, for manually created forms:

```
cufa ingest part-a --csv <path> --cohort <id> --sheet-timezone <IANA>
```

Here `--sheet-timezone` is **mandatory with no default** — not UTC, not the
machine's local zone. Missing → fail with an error naming the flag. Convert at the
parser boundary using `zoneinfo`; handle DST correctly; store the raw string and
the zone used so conversion is auditable. Match headers case-insensitively,
tolerate reordering, preserve unrecognized columns into `extra_fields`.

**Idempotency.** `source_event_id` = SHA-256 of `(form_id_or_file, normalized_email,
submitted_at_utc_iso)`. **Not the row number** and **not the Forms `responseId`
alone** — the hash must be stable across both ingestion paths so a CSV re-import
of API-ingested data does not duplicate. With the UNIQUE constraint and
`ON CONFLICT DO NOTHING`, a second run writes zero rows.

**Session assignment.** Match `submitted_at_utc` against session windows
(`[scheduled_at_utc - grace, scheduled_at_utc + duration + grace]`):
- exactly one → `matched`
- none → `none`, `session_id` NULL, **row still written**
- multiple → `ambiguous`, `session_id` NULL, warn naming the overlapping sessions

On the API path the form already implies a session, so a timestamp that lands
outside that session's own window is a **config error worth flagging**, not a
reason to reassign. Record both what the form implies and what the timestamp says.

**Identity.** Normalize emails: strip, lowercase. **Do not strip Gmail dots or
`+suffix`** — a lossy guess, and collapsing one fellow's address into another's is
worse than leaving it unmatched. Match `fellow.primary_email` within the cohort;
on a miss, upsert `identity_unresolved`. Never auto-link on a fuzzy name guess.

---

## Deliverable 4 — Deterministic adjudication (tier 1)

Normalize both strings: trim, collapse whitespace, lowercase, strip punctuation.

| Outcome | Condition |
|---|---|
| `exact` | normalized strings equal |
| `fuzzy` | Levenshtein ≤ `max_edit_distance` (config, default 1) |
| `mismatch` | no match |
| `not_set` | session has no passphrase configured |
| `no_session` | matched no session window |

**Rules produce decisions:**

| Condition | `status` | `rule_name` | Confidence |
|---|---|---|---|
| `exact` + in window | attended | `exact_match` | 1.0 |
| `fuzzy` + in window | attended | `fuzzy_match` | 0.9 |
| `not_set` + in window | attended | `no_passphrase_required` | 0.7 |
| `no_session` | not_attended | `outside_all_windows` | 0.6 |
| `mismatch` + in window | **escalate to tier 2** | — | — |

Fuzzy is on by default: the passphrase is heard aloud and typed on a phone.
Rejecting `justise` for `justice` penalizes someone who *was there and heard it* —
backwards from the intent.

Implement Levenshtein directly; ~20 lines, not worth a dependency.

---

## Deliverable 5 — AI adjudication (tier 2, Gemini)

Only `mismatch`-in-window cases reach this tier. Levenshtein cannot handle
`"the word was justice"`, `"justice i think?"`, `"jushtis"`, or `"sorry I missed
it"` — all of which a human reads instantly and edit distance scores wrongly.

**SDK:** `google-genai` (`from google import genai`; `client = genai.Client()`).
The older `google-generativeai` was deprecated in August 2025 — do not use it.

**Model:** `gemini-2.5-flash` by default, configurable. Free tier at time of
writing: 10 RPM / 250 requests per day for Flash; 15 RPM / 1,000 RPD for
Flash-Lite. Gemini 2.5 Pro left the free tier in April 2026. Key from
`GEMINI_API_KEY`; `.env` gitignored, `.env.example` committed.

**Send only two strings — the expected passphrase and the submitted answer.** No
names, no emails, no attendance history, no cohort data. Narrower context is both
better privacy and better accuracy: the model's only job is judging whether the
answer indicates the person heard the word.

Structured JSON output with a response schema:

```json
{ "heard_the_passphrase": true, "confidence": 0.0, "reasoning": "one sentence" }
```

Requirements:

- `temperature=0` for reproducibility
- Version the prompt (`PROMPT_VERSION = "v1"`) and store it on every decision
- **Check `ai_adjudication_cache` before every call**; write through after
- Exponential backoff on 429; cap calls per run via config
- **Degrade, never crash.** No key, no network, or quota exhausted → write
  `status='needs_review'`, `decided_by='rule'`, `rule_name='ai_unavailable'`. The
  pipeline must complete fully without Gemini.
- `--no-ai` skips tier 2 entirely

**`needs_review` is not `not_attended`.** Absent evidence is not evidence of
absence. Never collapse unknown into false.

---

## Deliverable 6 — Human override (tier 3)

```
cufa decide --checkin <id> --status attended|not_attended --by <email> --note "<text>"
```

Also exposed as one-click buttons in the console's review screen.

Supersedes the current decision: sets `superseded_at` on the old row, inserts a
new one with `decided_by='human'`, `confidence=1.0`, the human's email, and the
note.

**A human decision is never superseded by a later rule or AI pass.** Re-running
adjudication skips any check-in whose current decision is `decided_by='human'`.
Add `--force` to override, printing a loud warning naming exactly what it will
overwrite.

```
cufa review --status needs_review    # the queue, oldest first
cufa review --status ai              # AI decisions with reasoning, for spot-checks
```

The second matters: the AI tier must be auditable by a human sampling its
judgments, not a black box.

---

## Deliverable 7 — Latency

`latency_seconds` = seconds between announcement and submission.

- Use `session.announced_at_utc` if set — the console's "Announce now" button
- Otherwise derive `T0` = earliest submission matched to that session
- **Store it; do not interpret it.** No thresholds, no flags, no "suspicious"
  marker. It is an input to analysis that does not exist yet.
- Under derived `T0` the first submitter always has latency 0 — document as
  expected, not a bug
- NULL when no session matched

---

## Deliverable 8 — CLI

```
cufa db up | down
cufa serve                          # the console
cufa google connect | status
cufa template create | verify
cufa load-roster    --csv <path> --cohort <id>
cufa load-sessions  --csv <path>            # bulk import; console is the primary UI
cufa provision      --session <id> [--dry-run]
cufa pull           --session <id> | --cohort <id>
cufa ingest part-a  --csv <path> --cohort <id> --sheet-timezone <IANA>
cufa adjudicate     --cohort <id> [--no-ai] [--force]
cufa decide         --checkin <id> --status <s> --by <email> --note "<text>"
cufa review         [--status needs_review|ai|unresolved-identity]
cufa report         --cohort <id> [--json]
```

Everything the console does must also be doable from the CLI — the console is a
convenience layer, not the only entry point. That keeps the system scriptable and
testable, and keeps it usable if the web app ever breaks.

---

## Deliverable 9 — How to test it (build this)

Must work end to end with **no Google account and no Gemini key**.

**`make setup`** — installs deps, `supabase init`, checks Docker, clear message if
it is not running.

**`make demo`** — one command:
1. `supabase start` + `db reset`
2. generate synthetic fixtures
3. load roster and sessions
4. ingest check-ins **via the fake Google client**
5. adjudicate with `--no-ai`
6. print the report

Must succeed on a clean machine with no `GEMINI_API_KEY` and no OAuth connection.

**`FakeGoogleClient`** — implements the same interface as the real Forms/Drive
client: `create_template`, `read_settings`, `copy_form`, `batch_update`,
`set_publish_settings`, `list_responses`. It must be able to simulate **each trap**
so the handling is actually tested:
- a form returned unpublished
- `emailCollectionType` rejected with 400
- a template whose settings read back as `RESPONDER_INPUT` instead of `VERIFIED`
- a paginated response list
- a 429 followed by success

**`scripts/generate_fixtures.py`** — deterministic (fixed seed):
- 20 synthetic fellows, obviously fake names, `@example.invalid` addresses
- 6 sessions over 6 weeks, with passphrases, one with none set
- responses covering **every** edge case: exact, case and whitespace variants,
  punctuation, edit-distance-1 typos, conversational answers
  (`"the word was justice"`), plain wrong answers, blank answers, submissions
  outside every window, an overlapping-window pair, an unknown email, an exact
  duplicate, a DST-boundary submission, an unexpected extra column

**`make demo-ai`** — same, with tier 2 live. Skips with a clear message if
`GEMINI_API_KEY` is unset.

**`make demo-console`** — starts the web app against demo data with the fake
Google client, so every screen including provisioning and review can be clicked
through without touching Google.

**Inspecting results:** document Supabase Studio at `http://localhost:54323`, plus
copy-pasteable SQL for: current decisions per fellow, everything needing review,
AI decisions with reasoning, cache hit rate, provisioning log.

**`make test`** — pytest, **no network**.

**`make clean`** — `supabase stop`, remove generated fixtures.

---

## Deliverable 10 — Tests

Real assertions. Never commit real fellow data.

**Ingest and identity**
1. Idempotency — same input twice, identical row count, all skipped on the second
2. Cross-path idempotency — the same response ingested by API then CSV does not
   duplicate
3. Row reordering yields identical `source_event_id`s
4. CSV timezone — `2026-09-15 13:05:00` in `America/New_York` → `2026-09-15T17:05:00Z`
5. DST boundary — one row each side, both correct
6. Missing `--sheet-timezone` errors clearly rather than defaulting
7. Session assignment — matched / none / ambiguous, all three still write a row
8. Unknown email → `identity_unresolved`, check-in still written
9. Gmail dots preserved — `a.b@gmail.com` does not match `ab@gmail.com`
10. Extra column preserved in `extra_fields`, parser does not crash

**Google traps**
11. **Unpublished form is detected** — publish state read back as false raises,
    and provisioning is not reported as successful
12. **Publish is actually called** after every form creation
13. **Template verification fails closed** — settings reading `RESPONDER_INPUT`
    blocks provisioning entirely
14. **`emailCollectionType` 400 is handled** — the failure does not leave a
    half-provisioned form recorded as ready
15. Pagination — a multi-page response list is fully consumed
16. Watermark only advances after a complete successful pull; a mid-pull failure
    leaves it unmoved
17. Provisioning is idempotent — a second `provision` for the same session creates
    no second form

**Adjudication**
18. Passphrase outcomes — all five
19. Normalization — `"  Justice "`, `"JUSTICE"`, `"justice."` all → `exact`
20. Latency — derived `T0`, explicit `announced_at`, NULL on no-session
21. Decision versioning — overriding supersedes; exactly one current decision per
    check-in; the partial unique index actually enforces it
22. Human wins — re-running `adjudicate` does not overwrite a human decision;
    `--force` does, and warns
23. AI cache — a repeated string pair makes exactly one API call
24. AI unavailable — no key → pipeline completes, cases land in `needs_review`
25. **`needs_review` never becomes `not_attended`**

**Security**
26. Refresh tokens are encrypted at rest — a raw DB read does not expose one
27. No email, token, or API key appears in logs at INFO or above

Tests must not hit the network. Inject fakes for both the Google client and the
Gemini adjudicator.

---

## Deliverable 11 — Documentation

- `docs/setup/console.md` — running the console, connecting Google, the one-time
  template step and why it exists
- `docs/setup/google-cloud.md` — enabling the Forms and Drive APIs, creating the
  OAuth client, the exact scopes and why each is needed
- `docs/setup/local-dev.md` — Docker, Supabase CLI, Studio, make targets, SQL
  snippets
- `docs/google-api-traps.md` — the four traps, what each breaks, how this codebase
  handles each, with links. **Write this for the person who inherits the repo**;
  they will hit these otherwise.
- `docs/decisions.md` — ADRs for: form-over-Zoom-API, Verified-over-responder-
  input, template-copy-over-API-settings, responses-API-over-linked-Sheet,
  user-OAuth-over-service-account, timestamp-over-dropdown, never-drop-a-
  submission, observation-separate-from-decision, three-tier adjudication.
  Each records the decision, alternatives rejected, and why.
- `README.md` — clone to working demo in under ten minutes

---

## Constraints

- **Python 3.11+.** Dependencies: `fastapi`, `uvicorn`, `jinja2`, `psycopg[binary]`,
  `google-genai`, `google-auth`, `google-auth-oauthlib`, `google-api-python-client`,
  `cryptography`, `python-dotenv`, `pytest`. Everything else stdlib. CU has no data
  manager; each dependency is inherited maintenance.
- **Supabase CLI** for the database. Docker required locally.
- **No secrets committed.** `.env` gitignored, `.env.example` committed. Refresh
  tokens encrypted at rest.
- **Never log an email at INFO or above.** Counts and `fellow_id`s at INFO; raw
  addresses only at DEBUG. Never log an API key, OAuth token, or encryption key at
  any level.
- Type-hint public functions. Docstrings explain *why*, not *what*.

## Acceptance criteria

1. `make setup && make demo` succeeds on a clean machine with **no Google account
   and no `GEMINI_API_KEY`**, and prints an attendance report
2. Re-running `make demo` produces identical output — no duplicates
3. Every fixture response appears in `checkin` regardless of passphrase or session
   outcome, asserted as `count(checkin) == count(fixture responses)`
4. Exactly one current `attendance_decision` per check-in, enforced by the index
5. A human override survives a subsequent `adjudicate` run
6. `make demo-console` lets a person click through every screen, including
   provisioning and review, with zero Google calls
7. Provisioning against `FakeGoogleClient` **fails loudly** when the fake returns
   an unpublished form or a non-Verified template — proving traps 1 and 2 are
   handled rather than assumed
8. `make demo-ai` with a key set routes only `mismatch` cases to Gemini; a second
   run makes zero API calls
9. `make test` passes with no network access
10. `docs/google-api-traps.md` exists and is accurate

## Out of scope

- Part B (takeaway, confidence scale, muddiest point, application prompt, peer
  shoutout, help checkbox)
- Slack or Zoom integration
- Auto-posting the form link to Zoom chat or Slack, scheduled triggers, reminder
  nudges
- Gamification, points, streaks, leaderboards
- Participation scoring across components, or any at-risk flag
- Dashboards beyond the review screen and the terminal report
- Deploying to Supabase Cloud or hosting the console publicly — local only
- Domain-wide delegation — user OAuth only

Land these three fields end to end first.
