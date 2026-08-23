# CIF Fellowship Analytics

An evergreen participation report for the Civic Innovators Fellowship.
Reads Zoom, Slack and Google Sheets exports, normalises them into one event
model, and renders a single self-contained HTML report.

## Quickstart

```bash
export PYTHONPATH=src

python3 -m cufa status                       # which terms are defined, and what that gates
python3 -m cufa ingest --roster fixtures/roster.csv --fixtures fixtures
python3 -m cufa report --out out/report.html
open out/report.html
```

The `fixtures/` directory is synthetic data shaped like the real exports, so
the whole pipeline runs today, before any real credentials exist. Regenerate
it with `python3 tools/make_fixtures.py`.

## The one idea

Every term the report depends on lives in [`config/definitions.toml`](config/definitions.toml)
with a status and an owner. Nothing is hardcoded in `src/`.

- A term marked `confirmed` records who decided it and when. Metrics that
  depend on it compute.
- A term marked `undefined` **withholds** every metric that depends on it. The
  report prints the open question and the role that has to answer it.

This is deliberate. A threshold for "falling behind" that ships as a
placeholder becomes the program's real standard the moment somebody reads a
dashboard and acts on it. Withholding keeps that decision with the person who
owns it, and makes the gap visible instead of invisible.

Turning a measure on is a config edit, not a code change: fill in the
definition, set `status = "confirmed"`, name the owner and the date, re-run
the report.

## Design constraints

**Standard library only.** No pandas, no framework, no build step. Python 3.11+
is the only requirement. The contract ends 2 October; the tool has to keep
running on whatever machine the CU team has.

**SQLite as the warehouse.** One file, no server, openable by any free tool
after handover.

**Idempotent ingest.** Event ids are content hashes, so re-running last week's
export is a no-op rather than a doubling of everyone's score.

**Unresolved events are kept, never dropped.** An account that matches no
Fellow goes to a quarantine table and is counted in the report. A rising
count there is the earliest warning that the roster has drifted.

**No alerting UI.** Nothing in the report is styled as a warning, because a red
badge is a definition of "falling behind" written in CSS.

## Layout

```
config/definitions.toml   the terms, their owners, the gate
src/cufa/
  model.py                Fellow, Identity, RawEvent, Event, week maths
  config.py               loads definitions.toml, enforces the gate
  roster.py               who is in the cohort and their per-system ids
  store.py                SQLite warehouse, idempotent writes
  sources/                one adapter per export format
  metrics.py              participation scoring; declares what each metric needs
  report.py               self-contained HTML
  cli.py                  ingest / report / status
docs/                     open decisions, data model, runbook
tools/make_fixtures.py    regenerates the synthetic fixtures
tests/                    31 tests, `make test`
```

## Status

Working end to end on synthetic data. Not yet connected to live sources —
that needs the Slack workspace to exist and read credentials for Zoom and
Sheets. See [`docs/01-open-decisions.md`](docs/01-open-decisions.md) for what
is blocking what.
