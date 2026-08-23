# Runbook

Written for whoever holds this after the contract ends on 2 October 2026.

## Requirements

Python 3.11 or later. Nothing else — no pip install, no build step, no
network. `tomllib` and `sqlite3` are standard library.

Verify: `python3 --version && make test` (31 tests, ~0.1s).

## Weekly run

```bash
export PYTHONPATH=src
python3 -m cufa ingest --roster <roster.csv> --fixtures <export-dir>
python3 -m cufa report --out out/report.html
```

`<export-dir>` holds:

```
zoom/*.csv          one Zoom participant export per live lesson
slack/<channel>/<YYYY-MM-DD>.json    Slack workspace export
assignments.csv     email,assignment,due_on,submitted_at
```

Ingest is idempotent. Re-running with the same files changes nothing, so
"ingest everything again" is always a safe recovery move.

## Getting the exports

**Zoom** — Reports → Usage → select the meeting → Participants → Export.
Needs an account with usage-report permission.

**Slack** — Workspace admin → Settings → Import/Export Data → Export.
Unzip into `slack/`. On the free plan this covers public channels only.

**Google Sheets** — File → Download → CSV. Column names must be `email`,
`assignment`, `due_on`, `submitted_at`; the adapter reads by name, not
position, so column order can change safely but names cannot.

**HubSpot** — named as a source but not yet needed. No adapter written.

## Maintaining the roster

`roster.csv` is the only file a human maintains:

```csv
fellow_id,display_name,cohort,joined_on,status,zoom_email,slack_user_id,sheets_email
cif26-001,Amara Osei,2026-27,2026-09-07,active,amara.osei@example.org,U2600,amara.osei@example.org
```

`fellow_id` must be stable forever — it is what history joins on. If somebody
changes their Slack handle, update `slack_user_id` and keep `fellow_id` as it
was; past events stay attached.

Find a Slack member id: click the profile → More → Copy member ID.

## Reading data health

The report's section 4 is the check that matters. If **unresolved accounts**
is above zero, somebody is active in a source system and not on the roster —
usually a new Fellow, a changed handle, or a staff member. Their activity is
being counted for nobody. Fix by adding or correcting a roster row and
re-ingesting.

## Changing a definition

Never edit `src/`. Edit `config/definitions.toml`:

- To turn a withheld measure on: set `status = "confirmed"`, fill in
  `definition`, `owner`, `decided_on`, `source`. Re-run the report.
- To change scoring: edit `[participation.weights]` / `[participation.caps]`.
  This changes the ranking, so it is a decision, not a tweak.
- To pause a measure: set `status = "undefined"` and write the `question`.
  The report will start showing the gap again.

Bump `config_version` in `[meta]` when a change alters how numbers are
computed, so old reports remain interpretable.

## Automating it

The report is a static file, so any scheduler works. A GitHub Actions cron on
this repo, or a weekly cron on any machine:

```
0 7 * * MON  cd /path/to/repo && make all
```

Publishing is deliberately not wired up. Where the report goes determines who
can read it, and `downstream_use` currently says it is not shared with Fellows.
Decide the destination before scheduling the job.

## If something breaks

| Symptom | Cause | Fix |
|---|---|---|
| `unrecognised Zoom timestamp` | Zoom changed its export format | Add the format string to `ZOOM_TIME_FORMATS` in `sources/zoom.py` |
| Unresolved count jumps | Roster drift | Add or correct the roster row, re-ingest |
| A fellow's points look too low | Slack cap, or a missing identity mapping | Check raw counts in the table; check `roster.csv` |
| Report shows 0 weeks | Nothing ingested, or cohort label mismatch | Check `[meta] cohort_label` matches the roster's `cohort` column |
| Everyone's score doubled | Should be impossible — event ids are content hashes | Check whether `meta` changed between runs, which changes the hash |

## Handover checklist

- [ ] Repo access transferred
- [ ] Roster CSV location agreed and documented here
- [ ] Zoom, Slack, Sheets export permissions held by a named CU staff member
- [ ] Report destination decided (see `downstream_use`)
- [ ] Open decisions in `docs/01-open-decisions.md` assigned to owners
- [ ] One weekly run done end to end by a CU staff member, unaided
