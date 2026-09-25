# Civic Innovators check-in

Two forms per live lesson for the Civics Unplugged Civic Innovators Fellowship,
and a Slack bot plus staff dashboard built on what those forms record.

**Part A is the exit ticket.** Its questions are CU's own week-1 exit ticket
by default — name, a 1–5 rating of the session, the biggest takeaway, what to
see more and less of, open questions, other feedback — and staff can edit the
default, or customise one session, until that session's form is published.
Submitting it is also the attendance record: a **Google-verified email** and a
**submit time inside the session window**. The answers are collected and
counted, and never used to decide attendance.

**Part B** goes out at the **end** and measures what landed: a 1–7 confidence
rating, a one-sentence takeaway, one question that rotates weekly, an optional
peer shoutout, and an optional "I'd like someone to check in with me" checkbox.

They are two forms because they do different jobs. Part A's questions belong to
staff and change when staff want them to; Part B's are fixed, in a
research-backed order, so its numbers compare week to week. A fellow may answer
one and not the other — both are valid data, and neither is ever used to fill in
the other.

**The Slack bot** sits on the same database. Fellows get reminders they control
(24 h / 1 h / 10 min, with the Zoom link, in their own time zone), private badge
DMs they can switch off, a *check in with me* button, and a link to their own
dashboard. Staff get slash commands (`/attendance`, `/fellow`, `/report`,
`/leaderboard`, `/assignment`, `/score`, `/zoom`, `/outreach`, `/alias`,
`/link`, `/alerts`), a session summary in the staff channel after every lesson,
a Monday digest, an alert when someone joins who is not on the roster, a
per-fellow funnel, an attention index for who might be falling behind, hand-entered
Solvathon and case-brief scores, and speaking share from a Zoom transcript.
See [`docs/setup/slack-bot.md`](docs/setup/slack-bot.md).

---

## Why not Zoom

Since a March 2023 API change, Zoom hides `id` and `participant_user_id` for
guest participants as PII, and returns an email address only for participants
signed into Zoom. For an unauthenticated joiner the entire record is a
self-typed display name and a duration. Renaming yourself, or joining and
walking away, produces a record identical to real attendance.

A form with Google-verified email collection proves something Zoom cannot:
which Google account submitted it, and when. Attendance is that address plus a
submit time inside the session window — scheduled start minus the grace minutes
to scheduled end plus the grace minutes, inclusive.

That is weaker evidence than it sounds, and weaker than what came before. It
shows the fellow's account submitted the form while the lesson was on; it does
not show the fellow was watching. The link left open in an idle tab satisfies
it, and so does the link forwarded to someone who was not there. Part A used to
close part of that gap with a passphrase the teacher said aloud — never all of
it, since a spoken and displayed word reaches anyone who can see a fellow's
screen — at the cost of a word to choose every week, fuzzy matching, a model to
read the answers matching could not, and a review queue for the rest. CU chose
the exit ticket over the passphrase knowing this. Attendance is decided at
confidence 0.7 rather than 1.0 to say so, and it is one of three participation
signals, never a verdict on its own (ADR-037).

The full reasoning, and every alternative rejected, is in
[`docs/decisions.md`](docs/decisions.md).

---

## Clone to a working demo

**Windows (PowerShell), macOS, Linux — the same two commands everywhere:**

```
git clone <this repo>
cd cu-fellowship-analytics
python tasks.py setup     # creates .venv, installs deps, checks Docker and Supabase
python tasks.py demo      # the whole pipeline on synthetic data
```

On macOS and Linux, `make setup` and `make demo` do exactly the same thing — the
Makefile just forwards to `tasks.py`, so the two cannot drift. There is no
`make` on a stock Windows install, which is why `tasks.py` is the canonical
entry point rather than a Windows afterthought.

**If something is missing, ask before guessing:**

```
python tasks.py doctor
```

It reports Python, dependencies, Docker and the Supabase CLI, and prints the
install command for whichever of them is absent, for your OS.

> There is no `requirements.txt`. Dependencies live in `pyproject.toml`, which is
> what pip reads — `pip install -e ".[dev]"` is the manual equivalent of
> `python tasks.py setup`. (In PowerShell the quotes are required: without them
> `[dev]` is parsed as an array.)

`make demo` needs **no Google account and no `GEMINI_API_KEY`**. It runs against
`FakeGoogleClient`, which reproduces each documented Google failure mode, so the
demo exercises the trap handling rather than routing around it. It prints an
attendance report and then asserts the acceptance criteria.

Re-run `make demo` and the numbers are identical — ingest is idempotent.

Requirements: Python 3.11+, Docker (the local Supabase stack runs in it), and
the [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started).
`python tasks.py setup` checks all three and tells you exactly what is missing.

### What the demo actually does

1. Resets the database and generates deterministic fixtures — 20 invented
   fellows on `@example.invalid`, 11 sessions, 98 Part A responses and 6 rows of
   a hand-made form's CSV export covering 23 cases, and 83 Part B responses
   covering 57 field combinations.
2. Seeds the cohort's default exit ticket from
   `config/part_a_default_questions.json`, seeds it again to show that writes no
   new version, and **customises Session 3** — one question added (checkboxes,
   with *Other*), one removed, one moved.
3. Creates **both** template forms, and for each one **proves `template verify`
   blocks** before a human sets email collection to Verified, performs that step,
   and verifies. The manual step is per part, because email collection lives on
   a form and is carried only by a Drive copy.
4. Prints the rotation schedule, then **proves provisioning refuses** a
   teacher-question week with no question set — no generic substitute.
5. Provisions two forms per session — copy, write the questions, publish, **read
   the publish state and Verified email back and assert them**, and **read the
   question ids back and record the map** — then **proves Session 3's questions
   are locked** now its form is published.
6. Pulls Part A through the Forms API, then imports a manually created form's
   CSV export through the fallback path.
7. Seeds the end-of-session responses, then **proves ingest refuses** a Part B
   form whose question map is incomplete, repairs it by re-provisioning, and
   pulls.
8. Adjudicates Part A by address and timing, clusters muddiest-point themes
   (degrading cleanly with no `GEMINI_API_KEY`), prints the reports, and runs
   the acceptance checks.

### Other entry points

```
python tasks.py demo-console   # demo data plus the web console, zero Google calls
python tasks.py demo-again     # re-run over the same database, to show idempotency
python tasks.py demo-ai        # the demo, then muddiest-point themes live; skips without GEMINI_API_KEY
python tasks.py test           # the whole suite, no network
python tasks.py clean          # stop Supabase, remove generated fixtures
```

Each has a `make` equivalent on macOS and Linux (`make demo-console`, and so on).

To run `cufa` directly, activate the virtualenv first:

```
.venv\Scripts\Activate.ps1     # Windows PowerShell
source .venv/bin/activate      # macOS / Linux
```

Inspect the data visually in **Supabase Studio** at http://localhost:64323, or
use the copy-pasteable SQL in
[`docs/setup/local-dev.md`](docs/setup/local-dev.md).

---

```
make demo-slack          # the Slack bot + a fake Slack workspace you drive from a browser
make demo-slack-batch    # the same, driven automatically and checked — what CI runs
make report              # regenerate out/report.html — the self-contained HTML report
make slack-bot           # preflight (cufa slack doctor), then run the bot against real Slack
```

`make report` writes one file that opens from disk and attaches to an email:
every fellow against every session, attendance by session, who might need a word
and why, assignments and their hand-entered scores, the accepted-to-finished
funnel, the confidence trend, Slack activity by week, the review queues, and
where each number came from. No addresses, nothing from the help table, and no
combined participation score. `make
demo` writes it as its last step, so there is always a fresh one to look at.

`demo-slack` starts the real bot and a fake Slack on `http://127.0.0.1:3001/`
with buttons that post, react, join, edit and delete as any fellow — and two
that matter more: **Replay last delivery** re-sends an event with Slack's retry
headers (the bot acks it and writes nothing), and **Send with bad signature**
(the bot refuses it). No Slack account is involved. See
[docs/setup/slack-bot.md](docs/setup/slack-bot.md).

The long-lived Slack bot also sends timezone-aware session and assignment
reminders, a weekly fellow digest, and session-start agendas. Fellows control
their own cadence and quiet hours with `/cufa-reminders`; Part B non-submitter
nudges require a fresh successful form pull and are capped at two in both code
and the database. Configuration and operating commands are in the
[Slack bot setup guide](docs/setup/slack-bot.md#outbound-reminders-and-agendas).

## How it works

```
  staff fill in a session  ─────►  console  ─────►  Google Form (copied, questions
  (title, time, week)                                written, published, verified)
  default or custom questions                               │
  (editable until published)                                │
                                                            │ fellows submit
  teacher shares the link — QR on screen AND in             ▼
  the Zoom chat — then presses "Announce now"      forms.responses.list
                                                            │
                                                            ▼
                                          checkin  ── immutable observation:
                                                            │  verified email, time,
                                                            │  raw answers by questionId
                                       ┌────────────────────┴────────────────────┐
                                       ▼                                         ▼
                                 rules: verified address,                 a human
                                 submit time inside the                   (always wins)
                                 session window?
                                       └────────────────────┬────────────────────┘
                                                            ▼
                                              attendance_decision
                                          append-only, versioned, provenanced
```

The rules never read the answers. They are kept exactly as Google returned
them, keyed by question id, and resolved to the questions they answered only
when something reads them (`v_checkin_answer`) — counted, never graded.

The load-bearing separation is between the two tables. **`checkin` is what was
observed** and is immutable — a database trigger refuses every update to an
observed column and every delete. **`attendance_decision` is the judgment** and
is append-only: superseding sets `superseded_at` and inserts a new row, and a
partial unique index guarantees exactly one current decision per check-in.

That is what makes a human override auditable months later, and what lets a
changed definition of "attended" be re-run over history without destroying the
evidence it was applied to.

### Design invariants

1. **Never drop a submission.** A blank answer, an unknown address, a
   timestamp outside every window, an answer to a question nobody mapped — all
   recorded, with the reason. A dropped row is an unrecoverable observation, and
   it hides exactly the cases worth looking at.
2. **The observation is separate from the decision.** See above.
3. **Every decision carries its provenance** — which rule, judged against
   which window, which human, and when.
4. **A human override always wins**, and is never silently overwritten.
   `--force` overrides that and names exactly what it is about to destroy.
5. **Ingest is idempotent**, including across both ingestion paths.
6. **Identity never blocks ingest.** An unrecognized address still produces a
   record; the address goes to a review queue.
7. **Verify, don't assume.** Anything living in Google's systems is read back
   and asserted, never inferred from a 200.
8. **Everything is cohort-keyed**, for year-over-year comparison.
9. **Timestamps are UTC** past the parser boundary.

Part B adds five more, and these are ethical constraints rather than
engineering preferences:

10. **Asking for help never lowers any participation signal.** The help checkbox
    is excluded from every count, score, rate and aggregate, permanently, and
    two tests enforce it — one over every export path, one over the SQL those
    paths actually execute. If a fellow can suspect the box costs them
    something, the field stops working and the programme loses its only
    self-reported distress channel.
11. **The help checkbox cannot be provisioned without a named recipient.** With
    nobody configured, the field is left off the form and provisioning says why.
    A system that invites someone to ask for help and routes it nowhere is worse
    than one that never asks.
12. **No AI ever judges an individual fellow's free text.** The model's only job
    is clustering *muddiest-point* answers into themes — aggregate, about
    content, never about a person.
13. **Free text is counted, never graded.** Recording that a substantive
    response exists is fair; rating how well written it is penalises ESL and
    neurodivergent fellows for reasons unrelated to engagement.
14. **A peer shoutout is data about a third party** who did not submit it. It
    gets the same protection as the submitter's own data, and is never surfaced
    to the person named without an explicit decision by the data owner.

---

## The six Google traps

Each one fails **silently** — the code appears to work and no responses arrive,
arrive unattributable, or arrive attributed to the wrong field. Read
[`docs/google-api-traps.md`](docs/google-api-traps.md) before changing anything
in `src/cufa/google/`, `template.py`, `provisioning.py` or `question_map.py`.

| # | Trap | How this repo handles it |
|---|---|---|
| 1 | API-created forms are **unpublished** since 2026-07-01 and accept nothing, while the link still resolves | `setPublishSettings` is called, then the state is **read back and asserted**. "Ready" means `publish_verified_at IS NOT NULL`, nothing else. |
| 2 | `emailCollectionType: VERIFIED` is rejected by `batchUpdate` with a 400 | One template form; a human sets Verified by hand once; the API confirms it before anything proceeds; each session form is a Drive `files.copy` of that template. Re-verified on **every** provisioning run. |
| 3 | No REST equivalent of `Form.setDestination()`, so there is no linked Sheet to export | Read `forms.responses.list` directly — `respondentEmail` plus RFC3339 UTC, which removes the Sheets timezone trap entirely. Incremental via a watermark that advances only after a complete pass. |
| 4 | A service account cannot own a Google Form | User OAuth, exactly two scopes, refresh token encrypted at rest. The forms end up in a CU staff member's Drive, which is where CU wants them. |
| 5 | Responses are keyed by `questionId`, and `files.copy` **preserves** them — so every form copied from one template answers under the *same* ids, and the rotating slot's id is identical in week 2 and week 5 | Nothing is assumed. Each Part B form is read back with `forms.get` after it is built and the `questionId` → slot map recorded **per form**, with the exact question text snapshotted; slots are matched by **item index**, never by title. A form whose map is missing or incomplete **refuses to ingest**. Part A records the same kind of map per form, but keeps each answer raw under its `questionId` and resolves it at read time, so there a missing map is a warning, never a dropped check-in (ADR-039). Measured against a live account in August 2026; the fake reproduces both possible behaviours and the suite still runs the mapping tests under each. |
| 6 | `updateItem` with only the field named in `updateMask` is rejected — the body must describe the whole item, or Google reads it as turning a question into a text block | Both requests are built from one shared item body (`ItemSpec`). Part B retitles the rotating slot on every provision, so this broke Part B entirely while Part A — which always sent a full body — kept working. The fake now raises the same 400, so every Part B test exercises the correct shape. |

---

## The CLI

Everything the console does is also a command. That keeps the system
scriptable, keeps it testable without a browser, and keeps it usable on the day
the web app breaks.

```
cufa db up | down | reset
cufa serve                              # the console
cufa google connect | status | disconnect
cufa template create | verify | status | replace   [--part a|b]
cufa load-roster    --csv <path> --cohort <id>
cufa load-sessions  --csv <path>
cufa session        list | create | edit | announce
cufa questions      show | seed-default | import-form | set | revert | export
cufa rotation       [--cohort <id>] [--from-week N] [--weeks N]
cufa provision      --session <id> | --cohort <id> [--part a|b] [--dry-run]
cufa pull           --session <id> | --cohort <id> [--part a|b]
cufa ingest part-a  --csv <path> --cohort <id> --sheet-timezone <IANA>
cufa adjudicate     --cohort <id> [--force] [--redecide-legacy]
cufa decide         --checkin <id> --status <s> --by <email> --note "<text>"
cufa review         [--status needs_review | outside-window | ai | unresolved-identity]
cufa themes         --session <id> [--regenerate]
cufa shoutouts      review | link --shoutout <id> --fellow <id> --by <email>
cufa help-requests  list | ack --id <id> --by <email> --note "<text>" | close
cufa report         --cohort <id> [--confidence] [--json]

cufa slack          serve | sync | tick | reminders | digest [--post] | summary --session <id>
cufa slack          cmd </command> --as <slack user id> [text…] | alerts | link | badges | engagement | outreach
cufa assignment     create | list | link | submitted | score | show
cufa fellow         alias | merge | funnel | completed | retention | card <query>
cufa zoom           ingest --session <id> --vtt <file> | share --session <id>
```

`--sheet-timezone` is **mandatory and has no default** — not UTC, not the
machine's zone. A Sheets export writes wall-clock times with no offset marker,
so guessing shifts every check-in by hours without failing.

`cufa questions` edits Part A's questions: `--cohort` for the default, `--session`
for one session's override. `set` takes a JSON file in the shape of
`config/part_a_default_questions.json`; `export` writes one out to edit;
`import-form` reads the questions off an existing Google Form; `revert` sends a
session back to the default. Every save is a new version, the old one kept, and
a session's set cannot change once its Part A form is published.

---

## Adjudication

Rules decide what they can, and a human decides the rest. No model takes part
(ADR-040): every decision is reproducible from the row and the schedule alone.

| Observation | Decision | Rule | Confidence |
|---|---|---|---|
| Forms API, inside the form's session window | attended | `verified_email_in_window` | 0.7 |
| Forms API, outside that window | not attended | `outside_session_window` | 0.6 |
| CSV, inside exactly one window | needs review | `unverified_email_in_window` | — |
| CSV, inside no window | not attended | `outside_all_windows` | 0.6 |
| CSV, inside two overlapping windows | needs review | `ambiguous_session` | — |

The window is `[scheduled start − grace, scheduled end + grace]`, inclusive at
both ends, read from the session as it is scheduled **now** — so fixing a
wrongly entered time and re-running `cufa adjudicate` re-judges, and each
decision's note names the window it was judged against. The answers are never
read: a blank, one-word or off-topic reflection from someone in the window is
someone in the window.

A CSV row is never attended on its own, because a hand-made form's export
cannot show that Google verified the address. Outside-the-window decisions have
their own review tab, since the commonest cause is a session time entered
wrongly rather than a fellow who was absent. **`needs_review` is never turned
into `not_attended`** — absent evidence is not evidence of absence.

Check-ins from the passphrase era keep the decisions they were given;
`--redecide-legacy` re-judges them by timing.

---

## Part B: what the six fields are for

| # | Field | Why it is where it is |
|---|---|---|
| 1 | Confidence, 1–7 | Opens with a **click, not a text box** — 89% completion versus 83% for open-ended. Seven points rather than five because 5-point scales induce interpolation and this field gets graphed. |
| 2 | One-sentence takeaway | The core processing artefact. **Counted, never graded.** |
| 3 | The rotating question | Teacher's own question on weeks 1/4/7/10, muddiest point on 2/5/8, application on 3/6/9. |
| 4 | Peer shoutout | Optional. Collected and resolved; **no leaderboard, by design**. |
| 5 | ☐ "I'd like someone to check in with me" | **Last**, after rapport is built — sensitive items placed early raise abandonment of the whole form. |

The order is load-bearing. Six fields is the design, not a starting point: three
questions to four drops completion by 18%, and the rotating slot exists so a
seventh is never needed. The console shows those numbers wherever somebody might
be tempted to add one.

**The confidence field is stored raw, 1–7, and read as median and IQR — never a
mean.** A Likert scale is ordinal, so the mean of it is a number with no defined
meaning. And the signal is the **trend and the dip**, not the level: a fellow
moving 6 → 3 across two sessions is informative; a fellow sitting flat at 4
mostly is not. That sentence is printed next to every chart.

The **rotating question's week is typed in**, never derived from the calendar.
Sessions get rescheduled, skipped and doubled up, and a date-derived week
desynchronises the whole rotation without announcing it. A teacher-question week
with no question **blocks provisioning** rather than substituting something
generic — the teacher's question is the only genuinely unfakeable item on the
form.

The **help checkbox** has its own table, its own access list, and its own
document: [`docs/safeguarding.md`](docs/safeguarding.md), written for CU staff
rather than for engineers.

---

## Slack: the third participation signal

The Director's definition of participation has three parts: attendance at live
lessons (Parts A and B above), **Slack activity**, and assignment submission.
`cufa slack` covers the second.

It is a bot rather than an export because Slack's free plan **hides messages
after 90 days and deletes them after a year** — a workspace that starts on the
free plan would lose September's record by December. The bot writes each
message, reaction, join, edit and deletion to `slack_event` as it happens, keyed
by the act rather than by Slack's delivery id, so a retry, a restart and a
backfill all collide with the live row instead of duplicating it.

**Message text is not stored.** The definition counts acts; it does not read
them. Length, word count, link/file presence and thread position are kept; the
words are not (ADR-031). Every row stores the email, and the roster join happens
at read time, exactly as for the forms.

```
cufa slack socket                 # run it — Socket Mode, no public URL
cufa slack backfill               # read what it missed, while Slack still has it
cufa slack stats                  # totals, no addresses
cufa slack report --cohort cu-2026
cufa slack qa summary --latest    # a session's Q&A, summarised for the teacher

cufa slack tick                   # reminders, welcomes, badges, summaries, digest — from cron
cufa slack cmd /fellow --as U123 ada   # any slash command, from a terminal
cufa slack engagement | badges | digest | alerts | link | outreach
cufa assignment create | list | link | submitted | score | show
cufa fellow  alias | merge | funnel | completed | retention | card <query>
cufa zoom    ingest --session <id> --vtt <file> | share --session <id>
```

The same Bolt app also carries the **fellow- and staff-facing half**: reminders
24 h / 1 h / 10 min before sessions and assignments (Zoom link included, in the
fellow's own time zone, each interval switchable), a one-time welcome DM with a
*check in with me* button, private badges and streaks with opt-out, staff slash
commands (`/attendance`, `/fellow`, `/report`, `/leaderboard`, `/assignment`,
`/score`, `/zoom`, `/outreach`, `/alias`, `/link`, `/alerts`), a session summary
in the staff channel after every lesson, a Monday digest, roster alerts for
unrostered joins, aliases for fellows on two addresses, an attention index for
who might be falling behind, a per-fellow funnel, hand-entered Solvathon and
case-brief scores, a staff dashboard at `/dashboard` and a fellow-only page
behind a signed link. It reads the same `slack_event` rows the capture writes —
one event store, one identity path, one Bolt app, one HTTP client (ADR-034) — and
its every query is held to the same rule about the help table. `cufa slack doctor`
checks both halves; `make demo-slack-batch` drives both against a fake Slack over
real HTTP and asserts what each sent. Details in the second half of
[`docs/setup/slack-bot.md`](docs/setup/slack-bot.md).

**Q&A channels** are the one exception to no-text. Name them in
`CUFA_SLACK_QA_CHANNELS` and the bot keeps their questions and replies in their
own tables (ADR-032), so it can do two things there: when a question resembles
an earlier one that was *answered*, it replies in the new thread with a link
to that answer and the session it came from — *"came up before, during Sep 2 ·
Voting systems"* — and `cufa slack qa summary` (or `@bot summary` in Slack)
writes the session's Q&A up for the teacher: what was asked, what got settled,
what is still open, each with a link. With a `GEMINI_API_KEY` the model matches
paraphrases and writes the paragraph, from anonymous strings only; without one,
word overlap and a plain digest. Nobody is named in either.

The honest cost is that a bot has to be running, and the contract ends.
docs/setup/slack-bot.md ends with what that means and a `TODO(owner)` for the
person who restarts it.

## Accessibility

Share the exit ticket's link **both ways: a QR code on screen AND the link in
the Zoom chat**. A QR code alone excludes anyone on a phone that is also their
Zoom screen, and anyone using a screen reader; a chat link alone misses anyone
who joined after it was posted. The console says this on the session screen,
not only here.

The window's grace minutes are also an accessibility setting. A fellow who
types slowly, uses assistive technology, or finishes after the lesson ends must
still land inside `end + grace`; the week-1 form stayed open ten minutes after
the lesson, which the default of 15 covers.

---

## Three things deliberately left undecided

Each is marked in the code with a `TODO` and none has a placeholder value,
because a plausible-looking guess in any of them quietly becomes the policy.

- **`TODO(retention)`** in `src/cufa/form_content.py` and
  `src/cufa/form_content_b.py` — CU has not set a retention period, and both
  forms tell fellows what happens to their data.
- **`TODO(retention)`** in `supabase/migrations/20260901000300_help_request.sql`
  — deliberately separate, because a record that a young person asked for help is
  the most sensitive thing here and the right answer for it is very unlikely to
  be the right answer for a timestamp.
- **`TODO(access)`** in `supabase/migrations/20260801000500_rls.sql` — CU has
  said the data should be visible to every full-time team member but has not
  defined granular permissions, and a derived attendance judgment should not
  automatically be as open as a raw timestamp. `help_request` is explicitly
  **not** covered by that default: RLS is on with no permissive policy and
  grants revoked.

---

## Documentation

| Document | What it is for |
|---|---|
| [`docs/setup/local-dev.md`](docs/setup/local-dev.md) | Docker, Supabase, Studio, make targets, SQL snippets |
| [`docs/setup/console.md`](docs/setup/console.md) | Running the console, connecting Google, the one manual step, editing the exit ticket |
| [`docs/setup/google-cloud.md`](docs/setup/google-cloud.md) | Enabling the APIs, the OAuth client, the exact scopes |
| [`docs/setup/part-b-form.md`](docs/setup/part-b-form.md) | The end-of-session form, its own Verified step, the rotation, what a teacher prepares |
| [`docs/setup/slack-bot.md`](docs/setup/slack-bot.md) | The Slack bot: app setup, scopes, every command, the tick, the dashboards, aliases, Zoom transcripts, retention, the funnel |
| [`docs/safeguarding.md`](docs/safeguarding.md) | The help-request path — **written for CU staff, not engineers** |
| [`docs/google-api-traps.md`](docs/google-api-traps.md) | The six traps — **read this before touching the Google code** |
| [`docs/decisions.md`](docs/decisions.md) | 40 ADRs: what was decided, what was rejected, and why |
| [`docs/handoff/credentials.md`](docs/handoff/credentials.md) | Every account, key and secret — the handover checklist |

---

## Scope

**Part A** — the exit ticket: verified email and submit time for attendance,
plus the questions staff set — and **Part B** — confidence, takeaway, a rotating
question, a peer shoutout, and the help checkbox.

**The Slack bot and dashboards** — reminders, badges, the check-in button,
staff commands, session summaries, the weekly digest, roster alerts, aliases,
assignments and scores, the attention index, the funnel, retention tracking,
and Zoom speaking share. Every one of these is a function over the same
database and the same `SlackClient` protocol, and every one runs against the
in-memory fake with no workspace.

Where a feature sits next to an earlier design decision, the decision held:

- **Gamification is private.** Badges and streaks are DM'd to the person they
  are about and can be switched off with one command. There is no public
  leaderboard and no public shoutout display; the staff-only ranking
  (`/leaderboard`) ranks shoutouts by *giving*, per ADR-028.
- **The attention index is a sorted list for a human, not a label.** Its
  three components (Slack against the cohort mean, attendance with
  `needs_review` removed from the denominator, exit-ticket completeness) are
  always shown beside it, its weights are one dictionary the Director can
  change, and two things never enter it by test: the help checkbox and
  assignment scores.
- **Free text is still counted, never graded.** Form completeness counts
  fields answered; retention counts rubric terms in later answers. No model
  reads a fellow's words to decide anything about them.
- **The help checkbox stays on its own path.** The bot's *check in with me*
  button is a public, operational request that pings the staff channel; the
  Part B checkbox is unchanged, and the safeguarding tests run every new
  query to prove none of them reads its table.
- **Fellow-facing views show only the fellow's own data**, behind a signed,
  expiring link the bot hands out. Scores are entered by staff by hand.

Still out of scope: a live Zoom bot (speaking share comes from the cloud
recording's transcript instead), AI rubric grading of Solvathon or case-brief
work, and cloud deployment. Local only.

Never commit real fellow data. Every fixture name is invented and every fixture
address is `@example.invalid`, a reserved TLD that cannot be registered.
