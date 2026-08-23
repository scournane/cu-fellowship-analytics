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

**The teacher never touches Google Forms.** They fill in a session in a small web
app; the app creates the form programmatically, configures it correctly, and hands
back a link to paste into Zoom chat. Manual form-building is the failure mode this
design exists to remove — a form built by hand with the wrong email setting
silently destroys every identity guarantee downstream.

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
7. **A form is never usable until its settings are verified.** See Deliverable 1c —
   this is the single most dangerous failure mode in the system.
8. **Everything is cohort-keyed** for later year-over-year comparison.
9. **Timestamps are UTC** past the API boundary.

---

## Deliverable 1 — Admin web app and programmatic form creation

### 1a. The web app

A small server-rendered app. **FastAPI + Jinja2 templates.** No npm, no build
step, no SPA framework — CU has no data manager, and a build toolchain is
inherited maintenance for someone who did not write this.

**Screens:**

1. **Sessions list** — every session with its date, passphrase, form status
   (`draft` / `ready` / `failed`), responder link with a copy button, and a live
   response count.
2. **New session form** — the only thing a teacher fills in:

   | Field | Notes |
   |---|---|
   | Title | e.g. "Week 3 — Local Government" |
   | Cohort | dropdown |
   | Date and start time | local |
   | Timezone | IANA, defaulted from the browser, editable |
   | Duration (minutes) | |
   | Grace (minutes) | default 15 |
   | Passphrase | with the guidance below shown inline |
   | Announce time | optional |

   On submit: persist the session, then create the Google Form (1c), then show
   the responder URL.

3. **Session detail** — responder URL, edit URL, response count, a "re-verify
   settings" button, and a "recreate form" action for the failure case.

**Inline passphrase guidance** (show it in the UI, not buried in docs):

- One word, ~5–10 letters
- Avoid homophones (`their`/`there`, `flour`/`flower`)
- Avoid words in the slides or readings — guessable from materials
- Never reuse across sessions — **the app must reject a passphrase already used by
  another session in the same cohort**
- **Say it aloud AND display it on screen.** Audio-only excludes deaf and
  hard-of-hearing fellows and anyone with an audio failure. This widens the leak
  surface, which is exactly why the passphrase is one signal among several and
  never proof on its own.

### 1b. Google OAuth

The Forms API needs two scopes:

- `https://www.googleapis.com/auth/forms.body` — create and update forms
- `https://www.googleapis.com/auth/forms.responses.readonly` — read responses

**Forms must be owned by a real CU user account, never by a bare service
account.** A service-account-owned form has no human owner in the Drive UI: after
this contract ends, nobody at CU could open, edit, or recover it. That is an
offboarding failure, not a technical detail.

Two acceptable paths — implement the first, document the second:

1. **OAuth authorization-code flow.** A CU staffer signs in once and grants the
   scopes; store the refresh token. Forms are owned by that person's account.
2. **Domain-wide delegation** with a service account impersonating a CU user, if
   CU's Workspace admin prefers it. Same ownership outcome, more admin setup.

Store the refresh token in Postgres, **encrypted at rest** with a key from the
environment. Never log it. Never commit it. Provide a `cufa auth google` CLI
command that runs the flow and stores the token, so the app can be re-authorized
without touching the database by hand.

**Be honest in the docs about what cannot be automated:** creating the Google
Cloud project, enabling the Forms API, and configuring the OAuth consent screen
are one-time console steps a human must do. Write them out precisely, in order,
with the exact scope strings. Everything after that is automated.

### 1c. Creating the form — the two-step trap

⚠️ **`emailCollectionType` is ignored during `forms.create`.** It can only be set
through an `UpdateSettingsRequest` inside `forms.batchUpdate`. This means form
creation is unavoidably two calls, and there is a window between them where the
form exists and is **not collecting verified emails**.

If the second call fails and nobody notices, the form silently collects
submissions with no identifiable respondent. Every check-in from that session
becomes unattributable, and you will not find out until you try to join the data.

**Required sequence:**

1. `forms.create` with the title
2. `forms.batchUpdate` with:
   - `UpdateSettingsRequest` setting `emailCollectionType: "VERIFIED"`
     (enum values: `EMAIL_COLLECTION_TYPE_UNSPECIFIED`, `DO_NOT_COLLECT`,
     `VERIFIED`, `RESPONDER_INPUT`)
   - `CreateItemRequest` adding one required short-answer question,
     `"Today's passphrase"`
   - The transparency notice as the form description
3. **`forms.get` and assert `emailCollectionType == "VERIFIED"`** before marking
   the session `ready`. If it is anything else, mark the session `failed`, surface
   it loudly in the UI, and **do not hand out the responder link.**

Add only that one question. No session dropdown — the session is derived from the
timestamp, and a dropdown is user input that can contradict reality.

**Form description** must carry a plain-language notice: what is collected, who
sees it, what it is used for. Research on adolescent survey participation is
consistent that transparency drives honest responding. Include a literal
`TODO(retention)` marker where the retention period belongs — CU has not defined
one, and **inventing a number would silently make it policy.**

**Idempotency:** store `google_form_id` on the session. If it is already set,
never create a second form — reconcile the existing one instead. A duplicate form
for one session splits that session's responses across two sources.

Capture both URLs: `responderUri` (what fellows open) and the edit URL (what staff
open). They are different, and handing out the wrong one is a live incident.

### 1d. Manual fallback

Keep a short `docs/setup/manual-form.md` describing the click path for building
the form by hand, for use if the API path is unavailable. It must state clearly
that **Settings → Responses → Collect email addresses must be set to *Verified*,
not *Responder input*** — responder input lets anyone type any address and
destroys the only reason this form exists — and that **"Limit to 1 response" must
stay off**, since it is per-form and would block every session after the first.

---

## Deliverable 2 — Sessions

Sessions are created in the web app (1a) and stored in Postgres. Also provide
`cufa load-sessions --csv <path>` for bulk import and for tests, with the same
fields as the UI.

Attendance window: `[scheduled_at_utc - grace, scheduled_at_utc + duration + grace]`

The app must **warn on overlapping windows within a cohort** at creation time.
Overlaps make session assignment ambiguous later, and it is far cheaper to catch
here than in the data.

---

## Deliverable 3 — Supabase (Postgres)

Use the **Supabase CLI** for a fully local, offline Postgres stack. No Supabase
Cloud account needed — `login` and `link` are only required for deployment.

**Set this up yourself.** Run `supabase init`, author the migrations, write the
seed file, and verify `supabase start` + `supabase db reset` produce a working
database. Do not leave manual setup steps for the user.

- Migrations in `supabase/migrations/` (`supabase migration new <name>`)
- Seed data in `supabase/seed.sql`, applied by `supabase db reset`
- Local Postgres: `postgresql://postgres:postgres@localhost:54322/postgres`
- Studio (visual table browser): `http://localhost:54323`
- Docker must be running before `supabase start` — say so, and fail with a clear
  message if the stack is unreachable

Access Postgres with **psycopg 3** and plain SQL. Not `supabase-py` — this is
batch ingest plus a small server-rendered app, and direct SQL is easier to test
and hand off.

### Schema

Real Postgres types — `uuid`, `timestamptz`, `jsonb`, `text` with CHECK
constraints.

**`cohort`** — `cohort_id` PK, label, start/end dates

**`fellow`** — `fellow_id` PK (CU-issued, stable), `cohort_id` FK, `full_name`,
`primary_email`, `status`. The roster.

**`session`** — fields from Deliverable 2, plus `scheduled_at_utc`,
`announced_at_utc`, `google_form_id`, `responder_uri`, `edit_uri`,
`form_status` (`draft` \| `ready` \| `failed`), `settings_verified_at`.

**`google_credential`** — encrypted refresh token, granted scopes, the account
email that owns the forms, created/updated timestamps.

**`checkin`** — one row per form response. **Immutable.**

| Column | Notes |
|---|---|
| `checkin_id` | uuid PK |
| `source_event_id` | text UNIQUE — idempotency key |
| `google_response_id` | text, from the API |
| `submitted_email` | normalized |
| `submitted_at_utc` | timestamptz |
| `session_id` | FK, nullable |
| `session_match` | `matched` \| `none` \| `ambiguous` |
| `passphrase_raw` | text, exactly as typed |
| `passphrase_match` | see Deliverable 5 |
| `edit_distance` | int, nullable |
| `latency_seconds` | int, nullable |
| `raw_response` | jsonb — the full API payload |
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
| `attended` | boolean, nullable |
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

Add a **partial unique index** on `checkin_id WHERE superseded_at IS NULL` so
exactly one decision is current per check-in. Superseding sets `superseded_at` on
the old row and inserts a new one — never an in-place edit.

**`ai_adjudication_cache`** — key `(expected_normalized, submitted_normalized,
prompt_version, model)`; value: verdict, confidence, reasoning, created_at.

**`identity_unresolved`** — email, first/last seen, occurrence count, optional
best guess + score.

**`load_run`** — source, origin, start/finish, rows read/written/skipped, status,
error. For API pulls, record the response-filter window used.

### Row Level Security

Enable RLS on `fellow`, `checkin`, `attendance_decision`, and
`google_credential`. The pipeline connects as the service role and bypasses it.
Leave a documented `TODO(access)` policy stub — CU has said data should be visible
to every full-time team member but has not defined granular permissions, and a
derived attendance judgment should not automatically be as open as a raw
timestamp. Do not invent the policy.

---

## Deliverable 4 — Ingest from the Forms API

```
cufa ingest part-a --cohort <id> [--session <id>] [--since <RFC3339>]
```

Read responses with `forms.responses.list` for each session's `google_form_id`.

**This path removes the timezone problem entirely.** `lastSubmittedTime` is
returned as **Z-normalized RFC 3339 UTC**, not a locale-formatted string. There is
no conversion step and no timezone flag. Parse it directly.

`respondentEmail` is populated because the form is set to `VERIFIED` — which is
exactly why Deliverable 1c must verify that setting before the form is used.

**Incremental pulls:** `forms.responses.list` supports a timestamp filter
(`timestamp > "2026-09-15T00:00:00Z"`). Store the high-water mark per form on
`load_run` and pull only what is new. Handle pagination via `nextPageToken`.

**Quotas** are generous — 450 requests/min/project for `forms.responses.list`
(classed as an expensive read), 375/min for writes, with no daily cap. Polling
every few minutes is trivially within budget. On `429`, back off exponentially.

**Push notifications exist but are out of scope.** `forms.watches` can deliver
`RESPONSES` events to a Cloud Pub/Sub topic, but watches expire after a week and
must be renewed, notifications carry no payload (you still call the API to fetch
data), and it requires Pub/Sub infrastructure CU does not have. Document it as the
future path; poll for now.

**CSV fallback:** keep `cufa ingest part-a --csv <path> --sheet-timezone <IANA>`
for a hand-built form whose responses land in a Sheet, and for offline tests.
That path **does** carry the timezone trap — Google Sheets writes form timestamps
in the spreadsheet's locale with no offset marker, and parsing them as UTC shifts
every check-in by hours and misassigns sessions across window boundaries. On the
CSV path, require `--sheet-timezone` explicitly and **never default it to UTC or
to the machine's local timezone.**

### Idempotency

`source_event_id` = SHA-256 of `(google_form_id, google_response_id)` on the API
path, or `(source_file, normalized_email, submitted_at_utc_iso)` on the CSV path.
With the UNIQUE constraint and `ON CONFLICT DO NOTHING`, a second run over
identical input writes zero rows.

Note that Google may update an existing response (`lastSubmittedTime` changes
while `responseId` stays the same). Treat that as the same check-in — do not
create a second row — but record the observed `lastSubmittedTime` in
`raw_response` so the change is visible.

### Session assignment

- exactly one window → `matched`
- no window → `none`, `session_id` NULL, **row still written**
- multiple windows → `ambiguous`, `session_id` NULL, log a warning naming the
  overlapping sessions

When ingesting per-form, the form's own session is the obvious candidate — still
run the window check and record a mismatch rather than trusting the form linkage
blindly. A response arriving days after a session is a real signal.

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

| Condition | Status | `rule_name` | Confidence |
|---|---|---|---|
| `exact` + in window | attended | `exact_match` | 1.0 |
| `fuzzy` + in window | attended | `fuzzy_match` | 0.9 |
| `not_set` + in window | attended | `no_passphrase_required` | 0.7 |
| `no_session` | not_attended | `outside_all_windows` | 0.6 |
| `mismatch` + in window | **escalate to tier 2** | — | — |

Fuzzy is on by default because the passphrase is heard aloud and typed on a phone.
Rejecting `justise` for `justice` penalizes someone who *was there and heard it* —
backwards from the intent.

Implement Levenshtein directly; ~20 lines, not worth a dependency.

---

## Deliverable 6 — AI adjudication (tier 2, Gemini)

Only `mismatch`-in-window cases reach this tier. Levenshtein cannot handle
`"the word was justice"`, `"justice i think?"`, `"jushtis"`, or `"sorry I missed
it"` — a human reads those instantly and edit distance scores them wrong.

**SDK:** `google-genai` (`from google import genai`; `client = genai.Client()`).
The older `google-generativeai` was deprecated in August 2025 — do not use it.

**Model:** `gemini-2.5-flash` by default, configurable. Free tier: 10 RPM / 250
requests per day for Flash; 15 RPM / 1,000 RPD for Flash-Lite. Gemini 2.5 Pro left
the free tier in April 2026. Key from `GEMINI_API_KEY`; `.env` gitignored, with a
committed `.env.example`.

**Send only two strings — the expected passphrase and the submitted answer.** No
names, no emails, no attendance history. Narrower context is both better privacy
and better accuracy: the model's only job is judging whether the answer indicates
the person heard the word.

Structured JSON output with a response schema:

```json
{ "heard_the_passphrase": true, "confidence": 0.0, "reasoning": "one sentence" }
```

**Requirements:**

- `temperature=0` for reproducibility
- Version the prompt string (`PROMPT_VERSION = "v1"`) and store it on every
  decision
- **Check `ai_adjudication_cache` before every call**; write through after
- Exponential backoff on 429; cap total calls per run via config
- **Degrade, never crash.** No key, no network, or quota exhausted → write the
  decision as `status='needs_review'`, `decided_by='rule'`,
  `rule_name='ai_unavailable'`. The pipeline must complete fully without Gemini.
- `--no-ai` skips tier 2 and routes everything to `needs_review`

**`needs_review` is not `not_attended`.** Absent evidence is not evidence of
absence. Never collapse unknown into false.

---

## Deliverable 7 — Human override (tier 3)

```
cufa decide --checkin <id> --attended true|false --by <email> --note "<text>"
```

Also expose this in the web app on the session detail screen — a reviewer should
not need the CLI to correct one row.

Supersedes the current decision: sets `superseded_at` on the old row, inserts a
new one with `decided_by='human'`, `confidence=1.0`, and the human's email.

**A human decision is never superseded by a later rule or AI pass.** Re-running
adjudication skips any check-in whose current decision has `decided_by='human'`.
A `--force` flag overrides this and must print a loud warning naming what it is
about to overwrite.

```
cufa review --status needs_review     # the queue, oldest first
cufa review --status ai               # everything the model decided, for spot-checking
```

The second matters: the AI tier must be auditable by a human sampling its
judgments, not a black box.

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
cufa auth google                # OAuth flow, stores encrypted refresh token
cufa serve                      # the admin web app
cufa load-roster    --csv <path> --cohort <id>
cufa load-sessions  --csv <path>
cufa create-form    --session <id>          # same path the web app uses
cufa verify-form    --session <id>          # re-assert emailCollectionType
cufa ingest part-a  --cohort <id> [--session <id>] [--since <RFC3339>]
cufa ingest part-a  --csv <path> --sheet-timezone <IANA>
cufa adjudicate     --cohort <id> [--no-ai] [--force]
cufa decide         --checkin <id> --attended <bool> --by <email> --note "<text>"
cufa review         [--status needs_review|ai|unresolved-identity]
cufa report         --cohort <id> [--json]
```

`ingest` prints rows read / written / skipped-duplicate, session match breakdown,
passphrase outcome breakdown, unresolved identity count.

`adjudicate` prints decisions by tier, AI calls vs. cache hits, `needs_review`
count.

---

## Deliverable 10 — How to test it (build this)

The person running this must see it work end to end **with no Google credentials
and no Gemini key.**

**`make setup`** — installs deps, `supabase init`, checks Docker, clear message if
not running.

**`make demo`** — the one-command path:
1. `supabase start` + `supabase db reset`
2. generate synthetic fixtures
3. load roster and sessions
4. ingest check-ins **from a fake Forms API**
5. adjudicate with `--no-ai`
6. print the report

Must succeed on a clean machine with no `GEMINI_API_KEY` and no Google OAuth.

**Fake Google Forms API.** Build a `FormsClient` protocol with two
implementations: the real `google-api-python-client` one, and a fake that serves
canned responses from JSON fixtures. The fake must reproduce real API behavior,
including the two-step settings trap — a mode where `forms.create` succeeds and
`batchUpdate` fails, so the `failed` path is actually exercised. Select via
`--fake-google` or an env var.

**`scripts/generate_fixtures.py`** — deterministic (fixed seed), producing:
- 20 synthetic fellows, obviously fake names, `@example.invalid` addresses
- 6 sessions across 6 weeks, one with no passphrase set, one overlapping pair
- responses covering **every** case: exact, case/whitespace/punctuation variants,
  edit-distance-1 typos, conversational answers (`"the word was justice"`), plain
  wrong answers, blank answers, submissions outside every window, an unknown
  email, a duplicate `responseId`, an updated response (same `responseId`, later
  `lastSubmittedTime`), and an unexpected extra field

**`make demo-ai`** — same, with tier 2 live. Skips with a clear message if
`GEMINI_API_KEY` is unset.

**`make demo-web`** — starts the admin app against the fake Google client, seeded
so the sessions list is populated. This is how the UI gets exercised without a
Cloud project.

**Inspecting results:** document that Supabase Studio at `http://localhost:54323`
browses every table. Include copy-pasteable SQL: current decisions per fellow,
everything needing review, AI decisions with reasoning, cache hit rate, sessions
whose form settings are unverified.

**`make test`** — pytest, **no network**.

**`make clean`** — `supabase stop`, remove generated fixtures.

---

## Deliverable 11 — Tests

Real assertions. Never commit real fellow data.

**Form creation**
1. Happy path — form created, settings set, `forms.get` confirms `VERIFIED`,
   session marked `ready`
2. **`batchUpdate` fails after `forms.create` succeeds** → session marked
   `failed`, responder link withheld, error surfaced
3. **Settings verification returns `RESPONDER_INPUT`** → treated as failure, not
   success
4. Idempotency — creating a form for a session that already has `google_form_id`
   does not create a second form
5. Duplicate passphrase within a cohort is rejected
6. Overlapping session windows produce a warning at creation

**Ingest**
7. Idempotency — same responses twice; identical row count, all skipped
8. Updated response (same `responseId`, later `lastSubmittedTime`) does not create
   a second row
9. API timestamps parse as UTC with no conversion flag
10. CSV path: missing `--sheet-timezone` errors clearly rather than defaulting
11. CSV path: `2026-09-15 13:05:00` in `America/New_York` → `2026-09-15T17:05:00Z`
12. CSV path: DST boundary, one row each side, both correct
13. Session assignment — matched / none / ambiguous, all three still write a row
14. Unknown email → `identity_unresolved`, check-in still written
15. Gmail dots preserved — `a.b@gmail.com` does not match `ab@gmail.com`
16. Pagination — a multi-page response list is fully consumed

**Adjudication**
17. All five passphrase outcomes
18. Normalization — `"  Justice "`, `"JUSTICE"`, `"justice."` all → `exact`
19. Decision versioning — override supersedes; exactly one current decision per
    check-in; the partial unique index actually enforces it
20. Human wins — re-running `adjudicate` does not overwrite a human decision;
    `--force` does, and warns
21. AI cache — a repeated string pair makes exactly one API call (fake
    adjudicator, call counter)
22. AI unavailable — no key → pipeline completes, cases land in `needs_review`
23. **`needs_review` never becomes `not_attended`**

**Latency**
24. Derived `T0`, explicit `announced_at`, NULL on no-session

Tests must not hit the network. Inject fakes for both the Forms client and the
Gemini client.

---

## Deliverable 12 — Documentation

- `docs/setup/google-cloud.md` — the one-time console steps a human must do:
  create the project, enable the Forms API, configure the OAuth consent screen,
  create credentials. Exact scope strings, in order. Be explicit that this part
  cannot be automated.
- `docs/setup/manual-form.md` — the hand-built fallback (1d)
- `docs/setup/local-dev.md` — Docker, Supabase CLI, Studio, make targets, SQL
  snippets
- `docs/decisions.md` — ADRs for: form-over-Zoom-API, programmatic-over-manual
  form creation, verify-settings-before-use, Forms-API-over-Sheets for responses,
  user-owned-not-service-account forms, observation-separate-from-decision,
  three-tier adjudication, Gemini-for-ambiguous-only. Each records the decision,
  alternatives rejected, and why.
- `README.md` — clone to working demo in under ten minutes

---

## Constraints

- **Python 3.11+.** Dependencies: `fastapi`, `uvicorn`, `jinja2`,
  `psycopg[binary]`, `google-api-python-client`, `google-auth-oauthlib`,
  `google-genai`, `cryptography`, `python-dotenv`, `pytest`. Nothing else — CU has
  no data manager, and each dependency is inherited maintenance.
- **Supabase CLI** for the database. Docker required locally.
- **No secrets committed.** `.env` gitignored, `.env.example` committed. Refresh
  tokens encrypted at rest.
- **Never log an email at INFO or above.** Counts and `fellow_id`s at INFO; raw
  addresses only at DEBUG. Never log a token or API key at any level.
- Type-hint public functions. Docstrings explain *why*, not *what*.

## Acceptance criteria

1. `make setup && make demo` succeeds on a clean machine with **no Google
   credentials and no `GEMINI_API_KEY`**, and prints an attendance report
2. Re-running `make demo` produces identical output — no duplicate check-ins
3. Every fixture response appears in `checkin` regardless of passphrase or session
   outcome, asserted as `count(checkin) == count(fixture responses)`
4. **A form whose `batchUpdate` fails is marked `failed` and its responder link is
   never shown** — verified by test
5. Exactly one current `attendance_decision` per check-in, enforced by the index
6. A human override survives a subsequent `adjudicate` run
7. `make demo-web` serves the admin app against the fake Google client
8. `make demo-ai` with a key set routes only `mismatch` cases to Gemini, and a
   second run makes zero API calls
9. `make test` passes with no network access
10. `docs/setup/google-cloud.md` is complete enough for a non-technical staffer to
    do the console setup unaided

## Out of scope

- Part B (takeaway, confidence scale, muddiest point, application prompt, peer
  shoutout, help checkbox)
- Slack or Zoom integration
- Auto-posting the link to Zoom chat or Slack; reminder nudges
- `forms.watches` / Pub/Sub push notifications — document, do not build
- Gamification, points, streaks, leaderboards
- Participation scoring across components, or any at-risk flag
- Charts or dashboards beyond the plain sessions list
- Deploying to Supabase Cloud or hosting the web app publicly — local only

Land these three fields end to end first.
