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
| Postgres | `postgresql://postgres:postgres@localhost:54322/postgres` |
| Studio (visual table browser) | http://localhost:54323 |
| API gateway | http://localhost:54321 |

The DSN goes in `.env` as `CUFA_DATABASE_URL`. The Makefile exports it for every recipe,
so a clean checkout works before you have written a `.env` at all.

**`make db-reset` destroys all local data.** It drops and recreates the database, applies
`supabase/migrations/*.sql` in filename order, then applies `supabase/seed.sql` (which
creates the `cu-2026` and `demo` cohorts and nothing else — fellows and sessions arrive
through `cufa load-roster` / `cufa load-sessions`, because that is the path CU staff
actually use).

### Supabase Studio

http://localhost:54323 — the table editor and SQL editor for the local stack. Use it to
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
psql "postgresql://postgres:postgres@localhost:54322/postgres"
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
| `make demo` | The whole pipeline on synthetic data, **no Google account and no `GEMINI_API_KEY`**: `db-reset` → generate fixtures → load roster and sessions → create the template → *prove provisioning is blocked until the template verifies* → verify → provision every session → seed responses into the fake → pull via the Forms API path → import a CSV via the fallback path → adjudicate `--no-ai` → report → acceptance checks. |
| `make demo-again` | Re-runs pull, ingest, adjudicate and report over the **same** database, without a reset. This is the idempotency demonstration: identical numbers, zero new rows. |
| `make demo-ai` | `make demo`, then adjudicates with tier 2 live, then adjudicates again to show the second pass makes zero API calls, then prints `cufa review --status ai`. Exits with a clear message (not an error) if `GEMINI_API_KEY` is unset. |
| `make demo-console` | `make demo`, then `cufa serve` on `PORT` against the demo data and the fake client — every screen, including provisioning and review, clickable with zero Google calls. |
| `make test` | `pytest`. No network. |
| `make clean` | `supabase stop --no-backup`, removes `fixtures/`, `__pycache__/`, `.pytest_cache`. |
| `make db-up` / `db-reset` / `db-down` / `studio` | The database, directly. |
| `make fixtures` | Regenerate synthetic fixtures (`scripts/generate_fixtures.py`, fixed seed — deterministic). |

Every recipe exports `CUFA_FAKE_GOOGLE=1`, so a stray command in a demo recipe cannot
reach Google by forgetting a flag.

---

## Inspecting results

Open Studio (http://localhost:54323) or paste these into `psql`. They are written
against the real schema and the two views — `v_current_decision` (the one live decision
per check-in) and `v_checkin_resolved` (every check-in with its roster identity and
current decision attached). Replace `'demo'` with your cohort id.

Identity resolves at read time in `v_checkin_resolved`, so `fellow_id IS NULL` means
"the address is not on the roster", not "the row is missing".

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
and is slow. If a port is already taken, something else is on 54321–54329; stop it or
edit `supabase/config.toml`.

**`connection refused` on 54322.** The stack is down. `make db-up`.

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
