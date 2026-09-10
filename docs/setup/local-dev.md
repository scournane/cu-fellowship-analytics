# Local development

Everything in this project runs locally: Postgres in Docker via the Supabase CLI, the
pipeline and console in a Python virtualenv, and a fake Google client so the whole
system can be exercised with **no Google account and no Gemini key**. No cloud account
is needed — `supabase login` and `supabase link` matter only for deployment, which is
out of scope for Part A.

```bash
make setup && make demo
```

If that works, you have a database, synthetic data, provisioned forms (fake), ingested
check-ins, decisions, and an attendance report.

---

## Prerequisites

| | Why |
|---|---|
| **Docker**, running | The local Supabase stack is containers. `supabase start` cannot do anything without it. |
| **Python 3.11+** | `zoneinfo`, `X \| None` syntax, `datetime.fromisoformat` handling offsets. |
| **Supabase CLI** | Owns the migrations, the seed, and the local stack. |

`make setup` checks all three and fails with an actionable message rather than a
traceback. The Docker check is `docker info`; if Docker is installed but not running you
get:

```text
error: Docker is installed but not running.
  1. Start Docker Desktop, or: sudo systemctl start docker
  2. Confirm with: docker ps
  3. Re-run 'make setup'
```

### Installing the Supabase CLI

```bash
brew install supabase/tap/supabase       # macOS / Linuxbrew
npm install -g supabase                  # anywhere with node
# or a binary from https://github.com/supabase/cli/releases
```

Confirm with `supabase --version`. Do **not** install it as a project dependency —
Supabase does not support that, and `npx supabase` inside the repo will warn you.

---

## Windows

Everything here works on Windows; only the entry point differs.

`make` is not present on a stock Windows install, and the Makefile's recipes were
bash. Rather than ask you to install a POSIX toolchain to run a Python project,
every target is implemented in `tasks.py` using only the standard library:

```
python tasks.py doctor        what is installed, what is missing, how to fix it
python tasks.py setup
python tasks.py demo
python tasks.py test
```

On macOS and Linux, `make <target>` forwards to exactly these, so the two cannot
drift.

Windows-specific notes:

- **Activating the virtualenv** is `.venv\Scripts\Activate.ps1` in PowerShell,
  not `source .venv/bin/activate`. If PowerShell refuses to run it, that is
  execution policy:
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
- **There is no `requirements.txt`.** Dependencies are declared in
  `pyproject.toml`. The manual install is `pip install -e ".[dev]"` — **with the
  quotes**, because PowerShell parses a bare `[dev]` as an array.
- **Docker Desktop must be running**, not merely installed. `python tasks.py
  doctor` distinguishes the two.
- **Installing the Supabase CLI**: `scoop install supabase` (after
  `scoop bucket add supabase https://github.com/supabase/scoop-bucket.git`), or
  `npm install -g supabase`, or the `supabase_windows_amd64.zip` from the
  [releases page](https://github.com/supabase/cli/releases).
- **Console output is forced to UTF-8** by both `tasks.py` and the `cufa` CLI.
  The report contains em-dashes, and a legacy Windows code page would otherwise
  turn printing them into a `UnicodeEncodeError` that reads like a crash.


## The database

```bash
make db-up       # supabase start  (first run pulls images; give it a few minutes)
make db-reset    # supabase db reset — re-applies every migration, then the seed
make db-down     # supabase stop
```

`cufa db up | down | reset` does the same thing from the CLI, with the same Docker check.

| Service | URL |
|---|---|
| Postgres | `postgresql://postgres:postgres@localhost:64322/postgres` |
| Studio (visual table browser) | http://localhost:64323 |
| API gateway | http://localhost:64321 |

The DSN goes in `.env` as `CUFA_DATABASE_URL`. The Makefile exports it for every recipe,
so a clean checkout works before you have written a `.env` at all.

**`make db-reset` destroys all local data.** It drops and recreates the database, applies
`supabase/migrations/*.sql` in filename order, then applies `supabase/seed.sql` (which
creates the `cu-2026` and `demo` cohorts and nothing else — fellows and sessions arrive
through `cufa load-roster` / `cufa load-sessions`, because that is the path CU staff
actually use).

### Bringing your own Postgres

Every `make` target that needs the database first checks whether
`CUFA_DATABASE_URL` already answers. If it does, the Supabase stack is **not**
started — so a machine without Docker, a CI runner, or a hosted Postgres all
work by pointing that variable at them:

```
export CUFA_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/cufa_demo
make demo-slack-batch
```

`db-reset` then (re)creates that database and applies `supabase/migrations/*`
to it directly. The migrations reference the `authenticated` role for Row Level
Security, which Supabase provides and plain Postgres does not; create it once:

```sql
create role authenticated nologin;
create role anon nologin;
create role service_role nologin bypassrls;
```

### Supabase Studio

http://localhost:64323 — the table editor and SQL editor for the local stack. Use it to
browse `checkin`, `attendance_decision`, `provisioning_log` and the two views without
writing a connection string.

Two things to know:

- RLS is enabled on `fellow`, `checkin`, `attendance_decision` and `google_credential`.
  Studio connects with a privileged role, so it is not what those policies are protecting
  against — you will see every row. (The `TODO(access)` policy stubs deny everything to
  `authenticated`; see `supabase/migrations/…_rls.sql` and ADR-020.)
- `google_credential.refresh_token_enc` renders as bytes. That is correct — it is Fernet
  ciphertext. If it ever renders as something resembling a Google refresh token, the
  encryption path has been bypassed.

`psql` works too:

```bash
psql "postgresql://postgres:postgres@localhost:64322/postgres"
```

---

## Environment

```bash
cp .env.example .env
.venv/bin/python -m cufa.crypto keygen    # → paste into CUFA_ENCRYPTION_KEY
```

`.env` is gitignored; `.env.example` is committed and contains no real secrets. Nothing
in `.env` is needed for `make demo` — the Makefile exports what the demo requires. You
need it when you connect a real Google account (see `docs/setup/google-cloud.md`) or run
tier 2 against Gemini.

Variables worth knowing:

| Variable | Effect |
|---|---|
| `CUFA_DATABASE_URL` | Postgres DSN. Defaults to the local stack. |
| `CUFA_FAKE_GOOGLE=1` | Use `FakeGoogleClient` everywhere. Zero network calls to Google. |
| `CUFA_FAKE_GOOGLE_STATE` | Where the fake persists its forms and responses between processes. Default `fixtures/fake_google_state.json`. |
| `CUFA_ENCRYPTION_KEY` | Fernet key for the stored refresh token. Without it, `cufa google connect` refuses to store anything. |
| `GEMINI_API_KEY` | Enables tier 2. Absent is fine — mismatch cases become `needs_review` with `rule_name='ai_unavailable'`. |
| `CUFA_MAX_EDIT_DISTANCE` | Fuzzy passphrase tolerance. Default 1. |
| `CUFA_LOG_LEVEL` | `DEBUG` is the only level at which raw email addresses are emitted. |

---

## Make targets

Run `make` (or `make help`) for the list. Overridable variables: `COHORT` (default
`demo`), `FIXTURES` (default `fixtures`), `SHEET_TZ` (default `America/New_York`),
`PORT` (default `8000`).

| Target | What it does |
|---|---|
| `make setup` | Creates `.venv`, installs `-e '.[dev]'`, checks Python 3.11+, checks Docker is running, checks the Supabase CLI, runs `supabase init` if needed. |
| `make demo` | **Refuses to run if this database looks like a real install** — a connected Google account, a real template form, or recorded check-ins. `make demo` begins with a database reset, and running it over a working install deletes the roster, the sessions and the credential while leaving the real forms stranded in Drive. Point it at a scratch database (`CUFA_DATABASE_URL=…/cufa_demo make demo`, which resets *that* database rather than the linked project's) or override with `CUFA_DEMO_FORCE=1`. Otherwise: **both parts** end to end on synthetic data, **no Google account and no `GEMINI_API_KEY`**: `db-reset` → generate fixtures → load roster and sessions → create *each part's* template and *prove provisioning is blocked until each verifies* → print the rotation and *prove a teacher-question week with no question is refused* → provision both forms for every session → seed responses → pull Part A → import a CSV via the fallback path → seed Part B → *prove a form with an incomplete question map refuses to ingest*, then repair it by re-provisioning → pull Part B → adjudicate `--no-ai` → cluster themes → shoutout queue → help requests → reports → acceptance checks. |
| `make demo-again` | Re-runs both pulls, ingest, adjudicate, report and the acceptance checks over the **same** database, without a reset. This is the idempotency demonstration: identical numbers, zero new rows. |
| `make demo-ai` | `make demo`, then adjudicates with tier 2 live, then adjudicates again to show the second pass makes zero API calls, then prints `cufa review --status ai`, then clusters the muddiest-point themes for real. Exits with a clear message (not an error) if `GEMINI_API_KEY` is unset. |
| `make demo-console` | `make demo`, then `cufa serve` on `PORT` against the demo data and the fake client — every screen, including provisioning and review, clickable with zero Google calls. |
| `make test` | `pytest`. No network. |
| `make clean` | `supabase stop --no-backup`, removes `fixtures/`, `__pycache__/`, `.pytest_cache`. |
| `make db-up` / `db-reset` / `db-down` / `studio` | The database, directly. |
| `make fixtures` | Regenerate synthetic fixtures (`scripts/generate_fixtures.py`, fixed seed — deterministic). |

Every recipe exports `CUFA_FAKE_GOOGLE=1`, so a stray command in a demo recipe cannot
reach Google by forgetting a flag.

---

## Inspecting results

Open Studio (http://localhost:64323) or paste these into `psql`. They are written
against the real schema and its views — `v_current_decision` (the one live decision per
check-in), `v_checkin_resolved` (every Part A check-in with its roster identity and
current decision attached), and `v_checkin_b_resolved` plus the confidence views for
Part B. Replace `'demo'` with your cohort id.

Identity resolves at read time in both resolved views, so `fellow_id IS NULL` means "the
address is not on the roster", not "the row is missing".

**One table is deliberately absent from every view and every snippet below:
`help_request`.** It is read by one module and one console screen, and by nothing else —
see [`../safeguarding.md`](../safeguarding.md). Please do not add a join to it here.

### Current decisions per fellow

Roll-up, one row per fellow on the roster:

```sql
select f.fellow_id,
       f.full_name,
       count(v.checkin_id)                                as checkins,
       count(*) filter (where v.status = 'attended')      as attended,
       count(*) filter (where v.status = 'needs_review')  as needs_review,
       count(*) filter (where v.status = 'not_attended')  as not_attended
  from fellow f
  left join v_checkin_resolved v on v.fellow_id = f.fellow_id
 where f.cohort_id = 'demo'
 group by f.fellow_id, f.full_name
 order by f.full_name;
```

Every check-in with the decision that is currently live for it, including submissions
from addresses that are not on the roster:

```sql
select coalesce(v.full_name, '(not on roster)')            as fellow,
       v.submitted_email,
       coalesce(v.session_title, '(no session matched)')    as session,
       v.submitted_at_utc,
       v.passphrase_match,
       v.status,
       v.decided_by,
       coalesce(v.rule_name, v.ai_model, v.human_email)     as decided_how,
       v.confidence,
       v.latency_seconds
  from v_checkin_resolved v
 where v.cohort_id = 'demo'
 order by fellow, v.submitted_at_utc;
```

`latency_seconds` is recorded and never interpreted — no threshold is applied to it
anywhere. Where a session has no `announced_at_utc`, T0 is the earliest matched
submission, so that session's first submitter reads `0`. Expected, not a bug.

### Everything needing review

```sql
select v.checkin_id,
       v.submitted_at_utc,
       coalesce(v.full_name, '(not on roster)')          as fellow,
       v.submitted_email,
       coalesce(v.session_title, '(no session matched)') as session,
       v.passphrase_match,
       v.passphrase_raw                                  as typed,
       coalesce(v.rule_name, v.ai_reasoning, '(no reason recorded)') as why
  from v_checkin_resolved v
 where v.status = 'needs_review'
 order by v.submitted_at_utc;
```

Oldest first: the longest-waiting judgment is the most overdue one. `needs_review` means
*undecided*, never *absent* — `attended` is NULL on those rows, and nothing converts
that to false.

Check-ins with no decision at all (ingested but never adjudicated):

```sql
select v.checkin_id, v.submitted_at_utc, v.submitted_email,
       v.session_match, v.passphrase_match
  from v_checkin_resolved v
 where v.decision_id is null
 order by v.submitted_at_utc;
```

Addresses that matched nobody on the roster — the check-ins themselves were still
written:

```sql
select iu.cohort_id, iu.email, iu.occurrence_count, iu.first_seen_at, iu.last_seen_at
  from identity_unresolved iu
 where iu.resolved_at is null
 order by iu.last_seen_at desc;
```

### AI decisions with their reasoning

```sql
select v.checkin_id,
       coalesce(v.session_title, '(no session matched)') as session,
       v.passphrase_raw   as typed,
       v.status,
       v.confidence,
       v.ai_model,
       v.ai_prompt_version,
       v.ai_reasoning,
       v.decided_at
  from v_checkin_resolved v
 where v.decided_by = 'ai'
 order by v.decided_at desc;
```

Sample these rather than trusting them — that is what the tier is for. Same thing from
the CLI: `cufa review --status ai --cohort demo`.

### AI cache

What has been sent to the model, by model and prompt version:

```sql
select model, prompt_version, count(*) as cached_pairs,
       count(*) filter (where verdict) as verdict_true,
       min(created_at) as first_cached, max(created_at) as last_cached
  from ai_adjudication_cache
 group by model, prompt_version
 order by model, prompt_version;
```

An approximate hit rate. The cache holds one row per **distinct** string pair actually
sent, so anything beyond that count was served from cache:

```sql
with sent   as (select count(*)::numeric as pairs
                  from ai_adjudication_cache),
     judged as (select count(*)::numeric as decisions
                  from attendance_decision
                 where decided_by = 'ai')
select judged.decisions as ai_decision_rows,
       sent.pairs       as distinct_pairs_sent,
       round(100 * (1 - sent.pairs / nullif(judged.decisions, 0)), 1)
                        as approx_cache_hit_pct
  from sent, judged;
```

It is an approximation on purpose: a re-run that produces an identical verdict writes no
new decision row (by design — otherwise the decision history would fill with rows saying
what the previous row said) but still performs a cache lookup. **The exact per-run
numbers are printed by `cufa adjudicate`**, which reports `ai_calls=` and `cache_hits=`
on its summary line.

### Part B — both parts side by side, per session

Joined on the session, never merged. A blank in one column and a number in the other is
legal data: a fellow may answer one form and not the other.

```sql
select s.week_index,
       s.title,
       (select count(*) from checkin   c where c.session_id = s.session_id) as part_a,
       (select count(*) from checkin_b b where b.session_id = s.session_id) as part_b
  from "session" s
 where s.cohort_id = 'demo'
 order by s.scheduled_at_utc;
```

### Confidence by week — median and IQR, never a mean

A 7-point Likert scale is ordinal, so the mean of it is a number with no defined
meaning. `percentile_disc` returns an actual point on the scale rather than
interpolating a 4.5 nobody could have selected.

```sql
select week_index, session_title, responses, median, q1, q3, iqr
  from v_confidence_trend
 where cohort_id = 'demo'
 order by week_index nulls last;
```

Read the **trend and the dip**, not the level. A fellow moving 6 → 3 across two sessions
is informative; a fellow sitting flat at 4 mostly is not.

### Confidence values the scale cannot express

Never clamped. An out-of-range answer is stored as NULL with the raw value kept, because
a clamped 8 is a plausible number invented from a broken form.

```sql
select submitted_email,
       extra_fields ->> '_confidence_rejected_raw' as raw_value
  from checkin_b
 where extra_fields ? '_confidence_rejected_raw';
```

### What each form actually asked

Snapshotted at provisioning time, never reconstructed from `config/rotation.json` — the
config may well have changed since.

```sql
select s.week_index, m.slot, m.rotating_kind, m.question_text
  from form_question_map m
  join session_form sf on sf.form_id = m.form_id and sf.part = 'b'
  join "session" s     on s.session_id = sf.session_id
 where s.cohort_id = 'demo' and m.slot = 'rotating'
 order by s.week_index;
```

### Shoutouts waiting for a human

Two kinds land here and only one is a problem: an ambiguous name (never auto-linked,
because a wrong link is invisible) and a name matching nobody (legal — guest speakers and
staff get thanked too).

```sql
select raw_text, session_title, submitted_at_utc
  from v_shoutout_review
 where cohort_id = 'demo'
 order by created_at;
```

### Straight-lining

A data-quality flag on the responses, not a finding about the person, and an input to
nothing.

```sql
select full_name, confidence_raw, run_length, session_titles
  from v_confidence_straightline
 where cohort_id = 'demo'
 order by run_length desc;
```

### Muddiest-point themes, with the answers behind them

Regenerating supersedes rather than overwrites, so this filters on `superseded_at`.

```sql
select t.label, t.summary, b.rotating_text
  from muddiest_theme t
  join muddiest_theme_member m on m.theme_id = t.theme_id
  join checkin_b b             on b.checkin_b_id = m.checkin_b_id
  join "session" s             on s.session_id = t.session_id
 where s.cohort_id = 'demo' and t.superseded_at is null
 order by t.label, b.submitted_at_utc;
```

### Slack — acts per fellow per day

The view every Slack report should build on. Roster-attached where the email
matches, kept where it does not.

```sql
select day_utc, fellow_id, user_email, messages, thread_replies, reactions_given, channels_posted_in
  from slack_activity_daily
 where cohort_id = 'cu-2026'
 order by day_utc desc, messages desc;
```

### Slack — who is not on the roster

```sql
select e.user_email, count(*) as events, min(e.event_time_utc) as first_seen
  from slack_event e
  join slack_workspace w on w.team_id = e.team_id
  left join fellow f on f.cohort_id = w.cohort_id and lower(f.primary_email) = lower(e.user_email)
 where f.fellow_id is null
 group by e.user_email
 order by events desc;
```

`user_email` is NULL for a profile with no email (a bot, some guests) — those
rows are still here.

### Slack — is the bot alive?

```sql
select source, status, started_at, finished_at, rows_read, rows_written
  from load_run
 where source in ('slack_bot', 'slack_backfill')
 order by started_at desc
 limit 5;
```

A `slack_bot` run still `running` with an old `started_at` and no newer run is a
bot that died without stopping cleanly. `select max(received_at) from
slack_event` is the last thing it heard.

### Slack — Q&A questions still open

```sql
select q.asked_at_utc, left(q.text, 80) as question,
       (select count(*) from slack_qa_answer a where a.question_id = q.question_id and a.deleted_at_utc is null) as replies
  from slack_qa_question q
 where q.deleted_at_utc is null
   and not q.resolved
   and not exists (select 1 from slack_qa_answer a
                    where a.question_id = q.question_id and a.deleted_at_utc is null
                      and (a.accepted or a.slack_user_id <> q.slack_user_id))
 order by q.asked_at_utc desc;
```

Only the channels named in `CUFA_SLACK_QA_CHANNELS` are here. The rows carry a
Slack user id and no address; nothing joins them to the roster.

### Slack — retries Slack sent, and what they cost

```sql
select count(*) filter (where raw ? 'retry_num') as redeliveries_recorded
  from slack_event;
```

That number is always small: a redelivery of something already recorded is
dropped by the unique key before it reaches this table, so only a retry that
was the *first* successful delivery lands here.

### The provisioning log

Every attempt, successful or not:

```sql
select p.at,
       coalesce(s.title, '(session deleted)') as session,
       p.action,
       p.outcome,
       p.error,
       p.request_summary
  from provisioning_log p
  left join "session" s on s.session_id = p.session_id
 order by p.at desc
 limit 50;
```

Failures only — start here when a form is not collecting:

```sql
select p.at, p.action, p.outcome, p.error
  from provisioning_log p
 where p.outcome = 'failure'
 order by p.at desc;
```

Form readiness per session. **`publish_verified_at` is the column that matters**: a form
with `published_at` set and `publish_verified_at` NULL is one whose publish call returned
200 and whose state did not actually change — it accepts nothing while its link still
resolves (`docs/google-api-traps.md`, trap 1). `response_watermark` is how far
incremental polling has got.

```sql
select s.title,
       s.scheduled_at_utc,
       sf.form_id,
       sf.published_at,
       sf.publish_verified_at,
       sf.response_watermark,
       sf.last_polled_at
  from "session" s
  left join session_form sf on sf.session_id = s.session_id
 where s.cohort_id = 'demo'
 order by s.scheduled_at_utc;
```

---

## Troubleshooting

**`supabase start` hangs or fails.** Check `docker ps`. First run pulls several images
and is slow. If a port is already taken, something else is on 64321–64329; stop it or
edit `supabase/config.toml`. On Windows, Hyper-V often reserves 54321–54329, which is
why this project uses 6432x instead of the Supabase defaults.

**`connection refused` on 64322.** The stack is down. `python tasks.py db-up`.

**Migrations changed and the schema did not.** `supabase db reset` — migrations are only
applied on reset locally, not on `start`.

**`make demo` prints "blocked, as designed" in step 2.** Expected output, not an error.
The demo deliberately runs `cufa template verify` *before* the (simulated) human sets
Verified email collection and asserts it is refused — that is the proof trap 2 is handled
rather than assumed. The demo aborts if that check unexpectedly passes.

**Tests hitting the network.** They must not. Inject the fakes:
`google.factory.set_fake_client()` for Google, the `adjudicator=` argument for Gemini.

**Emails appearing in logs.** They should not above DEBUG. `logging_setup.RedactionFilter`
rewrites the formatted record, so a stray `log.info("... %s", email)` is redacted rather
than trusted to have been written correctly. Credentials are redacted at every level,
DEBUG included.

---

## See also

- `docs/setup/google-cloud.md` — enabling the APIs, the OAuth client, the two scopes.
- `docs/setup/console.md` — running the console and the one-time template step.
- `docs/google-api-traps.md` — why provisioning reads state back, and what breaks if it
  stops.
- `docs/decisions.md` — the ADRs, including why this is plain SQL and psycopg 3.
