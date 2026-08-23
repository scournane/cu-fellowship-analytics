# Data model

## Shape

Everything normalises to one event table. A source adapter's only job is to
turn its export format into `RawEvent`s; it knows nothing about Fellows,
scoring or thresholds.

```
export file ──> RawEvent ──> resolve() ──> Event  ──> events table
   (adapter)   (source id)   (identity)  (fellow_id)
                                  │
                                  └────> Unresolved ──> quarantine table
```

### RawEvent
What an adapter emits. Carries a `source_user_id` in that system's own
namespace — a Zoom email, a Slack member id, a spreadsheet email.

### Identity
Maps `(source, source_user_id) -> fellow_id`. This is the seam where three
namespaces are reconciled, and the only place that happens. It comes from the
roster CSV, because it cannot be derived: nothing in a Slack export says which
Zoom account belongs to the same person.

### Event
A `RawEvent` successfully attached to a Fellow. `event_id` is a content hash of
`(fellow_id, source, kind, timestamp, meta)`, which makes ingest idempotent —
re-running last week's export inserts nothing.

### Unresolved
A `RawEvent` whose `source_user_id` matched nobody. Kept rather than dropped,
and counted in the report's data-health section. A rising count means the
roster has drifted from reality — usually a new joiner or a changed handle.

## Event kinds

| Kind | Source | Notes |
|---|---|---|
| `live_lesson_attended` | Zoom | ≥10 minutes; re-joins summed, earliest join kept |
| `live_lesson_absent` | Zoom | Emitted for expected attendees who did not join. Absence is a *fact*, not the absence of one |
| `slack_message` | Slack | Excludes joins, leaves, topic changes, bot posts |
| `slack_reaction` | Slack | Credited to the reactor, stamped at the reacted-to message (the export does not record when the reaction was added) |
| `assignment_submitted` | Sheets | `meta.late` set if past the due date |
| `assignment_missed` | Sheets | Blank submission past due date, as of the run time |

## Time

Stored in UTC, always. `RawEvent` rejects naive datetimes at construction.

Weeks are ISO weeks starting Monday, labelled `2026-W37`. Zoom exports local
wall-clock times without an offset, so the adapter takes an explicit `tz`.

## Enrolment window

A Fellow is *enrolled* in a week if that week's Monday is on or after the
Monday of the week they joined. Cohort aggregates count only enrolled Fellows,
so a late joiner does not drag down the week-one average. The Zoom adapter uses
the same dates to avoid recording an absence from a lesson somebody was not yet
invited to.

## Scoring

Weighted, with per-week caps on the Slack components so one noisy channel
cannot dominate. Weights and caps live in `definitions.toml`:

| Component | Weight | Weekly cap |
|---|---|---|
| Live lesson attended | 3.0 | — |
| Assignment submitted | 3.0 | — |
| Slack message | 1.0 | 10 |
| Slack reaction | 0.25 | 4 |

Raw counts are stored and displayed uncapped; caps apply only to the points
column.

**There is deliberately no 0–100 score.** A single normalised number would be
an implicit definition of *performance*, which is undefined. See
[01-open-decisions.md](01-open-decisions.md).

## Year-over-year

The schema supports it — `cohort` is on every Fellow and all queries filter by
it, so a 2027–28 roster ingests alongside 2026–27 without migration. What is
not settled is whether the underlying per-Fellow records may be kept that long.
That is the `retention` decision, and it has to be made before the comparison
feature is built, not after.
