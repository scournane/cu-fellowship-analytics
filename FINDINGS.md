# Findings — cu-fellowship-analytics verification run, 2026-09-14

Ranked. **Blocks** = someone deploying this for a real cohort hits it and is
stuck or misled. **Annoying** = costs an hour, no lasting damage. **Cosmetic** =
worth fixing, harms nobody.

---

## F-01 · Blocks · The repo's default branch has no Slack bot in it

`git clone https://github.com/scournane/cu-fellowship-analytics` checks out
`claude/civics-unplugged-analytics-tool-6nfb9b`, because that is where the
remote `HEAD` symref points, not `main`.

That branch contains **no file matching `slack`** — no `src/cufa/slack/`, no
`docs/setup/slack-bot.md`, no `demo-slack` / `demo-slack-batch` / `slack-bot`
targets in `tasks.py`. The README on it still advertises the bot and still links
to `docs/setup/slack-bot.md`, which 404s.

**Repro**

```
git clone https://github.com/scournane/cu-fellowship-analytics
cd cu-fellowship-analytics
ls docs/setup/slack-bot.md     # No such file
grep -c slack tasks.py         # 0
```

**Why it matters more than it looks.** Nothing errors. You get a working
repository, `tasks.py setup` succeeds, `tasks.py demo` passes all 36 acceptance
checks. You only discover the bot is absent when you go looking for it. Anyone
following the brief's own instruction ("branch `main`") has no reason to check
`git rev-parse --abbrev-ref HEAD` first.

**Fix.** Set the default branch to `main` in the GitHub repo settings. One
click. Optionally delete or archive the four stale `claude/*` branches.

---

## F-02 · Annoying · `README` / `local-dev.md` give one Supabase CLI install path and it is the fragile one

The docs point at the GitHub releases download. In a sandboxed or proxied
environment the GitHub API answers `403` and the documented command produces an
empty URL and a confusing `curl: (3) URL rejected` rather than a useful error.

`npm install -g supabase` worked first try and gave 2.117.0.

**Fix.** Add the npm line to `docs/setup/local-dev.md` as the fallback, and to
what `tasks.py doctor` prints when it reports `MISS Supabase CLI`. `doctor`
already prints per-OS fixes for everything else, so this is a one-line addition
in the place people will actually be looking.

---

## F-03 · Blocks · Two definitions of "sessions held", four surfaces, and an attendance rate of 235%

This is the big one. The codebase computes *sessions held* two different ways,
and the four staff-facing surfaces do not agree with each other.

On one database, at one moment, with 47 attended check-ins across 20 fellows:

| Surface | Says | Source |
|---|---|---|
| `out/report.html` | **21%** attendance, 11 sessions held | `report_html.py:75` |
| `/report` in Slack | **"Sessions held: 1 · overall attendance: 235%"** | `engagement.py:294` |
| `/dashboard` | **"235% overall attendance · 47 of 20 × 1 sessions"** | same |
| `/fellow` and `cufa fellow card` | **"Attendance: 2/1"**, `4/1`, `3/1` | same |
| `/leaderboard checkins`, `cufa slack badges` | **0 for everyone** | `badges.py:112` |

An attendance rate of **235%**, and per-fellow attendance rendered as **4 of 1
sessions**, on a screen whose whole purpose is to be trusted with a number.

**The two rules**

| Rule | Where | Counts a session as held when |
|---|---|---|
| A | `report_html.py:75` | `scheduled_at_utc < now` **OR** a check-in exists against it |
| B | `engagement.py:294` (`cohort_attendance`), `badges.py:112` | `scheduled_at_utc <= now`, nothing else |

Rule B's numerator is not scoped the same way as its denominator. `attended`
counts every attended decision in the cohort regardless of date, while `held`
counts only sessions already in the past. Nothing clamps the result, so as soon
as check-ins exist against a session that rule B does not consider held, the
fraction goes above 1 and gets printed as a percentage.

**Repro**

```
python tasks.py demo-slack-batch          # Slack rows
python /tmp/demo_no_reset.py              # Part A/B rows, same database
cufa slack cmd --as <any admin> /report
#  → Sessions held: 1 · active fellows: 20 · overall attendance: 235%
cufa report --cohort demo --html out/report2.html
#  → 21%
```

```sql
select (select count(*) from "session" s where s.cohort_id='demo'
          and (s.scheduled_at_utc < now()
               or exists (select 1 from checkin c where c.session_id = s.session_id))) as rule_a_held,
       (select count(*) from "session" where cohort_id='demo' and scheduled_at_utc <= now()) as rule_b_held,
       (select count(distinct (fellow_id, session_id)) from v_checkin_resolved
         where cohort_id='demo' and fellow_id is not null and status='attended') as attended;
--  rule_a_held = 11 | rule_b_held = 1 | attended = 47
```

**Does this happen with real data?** Partly, and the part that does is the part
that matters. Once sessions are in the past both rules agree and the rate is
sane. The divergence survives for any session that has check-ins but whose
`scheduled_at_utc` is not yet passed:

- a makeup or rescheduled session (the demo fixtures ship one on purpose:
  *"Session 5 — makeup (overlaps deliberately)"*)
- a session run earlier than scheduled
- a timestamp corrected after the fact
- the window between a session starting and its scheduled time, if the form is
  released early

In every one of those, check-ins against that session are counted in the
report's numerator, excluded from the dashboard's denominator, and **silently
dropped from badges and streaks entirely** — `first_checkin`, `regular` and
`streak` are computed from rule B's session list, so a fellow who attended that
session gets no credit for it and never earns the badge.

**Fix, in order of value**

1. Make `cohort_attendance` and `badges.build_evidence` use rule A. One shared
   helper, used by all four surfaces, so they cannot drift again. This is the
   real fix and it also corrects badges.
2. Clamp and guard the display regardless: a rate above 1 is a bug, not a
   number, and should render as "—" with the components beside it rather than
   `235%`. Same for `47 of 20 × 1 sessions` and `Attendance: 4/1`.
3. Date the demo fixtures in the past. `tasks.py` already knows they are in the
   future (`_slack_prereqs`: *"The fixture sessions are all in the future"*);
   that choice is what makes the bug visible on day one to anyone evaluating
   the tool, which is both the good news and the bad news here.

**Why 597 tests did not catch it.**
`tests/test_engagement.py:139` is the only test of `cohort_attendance`. Its
fixture puts every session in the past and every attended check-in against a
session that counts as held, so `rate` comes out at exactly 0.5 and the
assertion passes. Nothing anywhere asserts the invariant that would have caught
this: **`0 <= rate <= 1`**, always, on any data. One property test on that one
line is worth more than the four call-site fixes, because it fails loudly the
next time the two rules drift apart.

---

## F-07 · Annoying · Every `--cohort` command silently defaults to a cohort that does not exist, and blames your query

`.env.example` ships `CUFA_SLACK_COHORT=cu-2026`. Any CLI command that takes
`--cohort` falls back to it when the flag is omitted. On a fresh demo database
the only cohort is `demo`, so those commands quietly operate on an empty cohort
and report emptiness as a fact about your input:

```
$ cufa fellow card "Ardith Aldergrove"
error: No fellow matches 'Ardith Aldergrove'.

$ cufa fellow card "Ardith Aldergrove" --cohort demo
*Ardith Aldergrove* · CU-2600 · active …
```

```
$ cufa slack badges
computed=0 new=0
*Most check-ins* … No data yet.

$ cufa slack badges --cohort demo
computed=20 new=73
```

The first message is actively wrong — that fellow exists, under that exact
name. The second is worse in a way, because `computed=0 new=0` looks like a
successful run.

**Fix.** When the resolved cohort has no fellows, say that: *"cohort 'cu-2026'
has no fellows. Cohorts in this database: demo."* The information is one query
away and it turns a ten-minute confusion into a five-second one.

---

## F-08 · Cosmetic · The retention rubric and the demo fixtures share no vocabulary, so `cufa fellow retention` always prints zeros

`config/retention_rubric.json` looks for *accuracy, fact-check, verify,
evidence, transparency, consistency, stakeholder*. Across all 82 Part B
responses in the demo fixtures, the count of each of those terms is **zero** —
the fixture answers are about budgets and line items.

So `cufa fellow retention --cohort demo` prints `0/4  none` for all 20 fellows.
That is the command working correctly, but an evaluator cannot tell it apart
from the command being broken, and the feature is the one thing in the system
that most needs to visibly *not* be a language model.

**Fix.** Put a handful of rubric terms into the fixture generator's Part B text
so the demo shows a non-zero column.

---

## F-04 · Annoying · `cufa slack cmd` cannot run `/cufa-reminders`, and says the command does not exist

```
$ CUFA_FAKE_SLACK=1 cufa slack cmd --as U0DEMO0007 /cufa-reminders status
I don't know `/cufa-reminders`. Try `/help`.
```

The command is fine in real Slack. There are two dispatch paths and only one
knows about it:

- `slack/bot.py:584` registers `@app.command("/cufa-reminders")` directly on the
  Bolt app, handled by `reminders.preference_command`
- `slack/commands.py:500` `HANDLERS` — the map `dispatch()` uses, and therefore
  the map `cufa slack cmd` uses — has `reminders` but no `cufa-reminders`

So the offline driver, which is the tool the docs offer for exercising commands
without a workspace, reports a working command as non-existent. Verifying a
fellow's saved cadence (`cufa slack cmd /cufa-reminders status --as <id>`,
exactly as the test brief specifies) is not possible.

**Fix.** Add `"cufa-reminders"` to `HANDLERS` pointing at a thin wrapper over
`preference_command`, or have `dispatch()` normalise a `cufa-` prefix. Either
way both paths then answer the same way, which is the property worth having.

---

## F-05 · Cosmetic · Two different things share the phrase "asked to be checked in with"

`docs/safeguarding.md` is emphatic that the Part B help checkbox
(`help_request`) and the `/checkin` slash command (`intervention` with
`kind='check_in_request'`) are separate paths with separate recipients. The code
honours that strictly — I checked every reference, and no view and no dashboard
query reads `help_request`.

But `report_html.py:866` labels the `intervention` count **"Fellows who asked to
be checked in with"**, which is the same sentence the Part B checkbox uses. On
the demo data the report shows `0` for that row while `help_request` holds 2
rows. Both numbers are correct; the label makes them look like the same number
disagreeing with itself.

**Fix.** Label the report row "Open `/checkin` requests" and leave the checkbox
wording to the help path.

---

## F-06 · Cosmetic · `docs/setup/local-dev.md` snippet "provisioning failures" finds nothing when provisioning was refused

Snippet [23] selects `provisioning_log where outcome = 'failure'`. The demo's
deliberate refusal (the teacher-question week) is recorded as
`outcome = 'skipped'`, so that query returns 0 rows immediately after a run that
printed "1 failed" on the console. Snippet [22], which lists every outcome, does
show it.

**Fix.** Either widen snippet [23] to `outcome <> 'success'`, or add a sentence
saying a deliberate refusal is `skipped`, not `failure`.

---

## Things that turned out better than expected

Worth writing down, because a findings list read alone gives the wrong
impression of this codebase.

- **The privacy boundaries hold under inspection, not just assertion.** All 64
  `slack_event` rows have `text` NULL; `raw` is stripped to
  `{type, event_ts, channel_type}`; `text_length` and `word_count` survive, the
  content does not. No view reads `help_request`. The bot's status page, its
  `/stats` JSON and its entire INFO-level log contain zero email addresses, and
  `/link` logs an address as `g***@example.invalid`.
- **The signature boundary is real.** A body signed with the wrong secret gets
  `401`, and so does a correctly signed body with a ten-minute-old timestamp.
  Only `/`, `/stats` and `/health` are served; everything else 404s.
- **Deduplication is keyed on the act, not on Slack's delivery id.** Posting the
  same event twice with a valid signature returns `200` both times and writes
  one row. That is the property that makes backfill idempotent too.
- **Read-time identity actually works as advertised.** An event ingested at
  16:36:40 resolved to `CU-2604` after an alias created at 16:41:32, with no
  re-ingest, through `v_fellow_email`.
- **One address belongs to one fellow, enforced below the application.**
  `/alias` on another fellow's primary address is refused by name.
- **The documentation is unusually careful.** Where a query returns a
  surprising number, the doc has already explained why (the `retry_num` snippet
  returning 0 is explicitly anticipated). F-02 and F-06 are the only two places
  the docs were wrong, and both are small.

---

Added from the real-workspace run.

### F-09 · Blocks · The bot's own posts are counted as participation

`events.py:216` skips a live message when `event["bot_id"]` is set. `sync.py:251`
— the path `cufa slack sync`, `tick` and backfill use — skips only when
`message.subtype in ("bot_message", "channel_join", "channel_leave")`. A message
posted by a modern Slack app carries `bot_id` and **no subtype**, so it sails
through.

Result on live data: the Q&A pointer (331 chars) and the session summary (178
chars) are both `slack_event` rows attributed to `U0BUNEG4V25`. They show up as a
`— (not on roster)` participant with 2 messages in `cufa slack report`, and in
the reply graph as `U0BUNEG4V25 → Samson Cournane 1`.

The more the bot talks, the more it inflates the numbers it is reporting.

**Fix:** make `sync.py` skip on `bot_id` the way `events.py` does.

### F-10 · Annoying · A reminder fired late still says "starts in 24 hours"

The session was created ~90 minutes before it began. The bot sent the 24-hour
reminder immediately (correct — the 24h mark had passed unserved) but worded it
*"starts in 24 hours (Mon, Sep 14 at 2:43 PM EDT)"*. A fellow reading that at
1:15 PM would think they had a day.

The absolute time is right, so the bug is only the phrasing: the copy uses the
offset bucket's name instead of the real remaining time.

**Fix:** render from the actual delta, or drop the relative clause and keep the
timestamp.

### F-11 · Blocks · The repo's manifest will not import, so 19 of 20 slash commands don't exist

Pasting `docs/setup/slack-bot.md`'s manifest verbatim into **Create from a
manifest** produces *"We can't translate a manifest with errors"* with the error
marker on `slash_commands`. The workspace's app therefore has **one** command,
`/cufa-reminders`, and is missing `/me`, `/help`, `/report`, `/fellow` and the
rest, plus the event subscriptions for `app_mention`, `team_join`,
`user_huddle_changed` and the four `file_*` events.

Two candidate causes I could not separate without a scratch workspace: Slack
reserves `/me` and `/help` as built-ins (typing `/me` in Slack autocompletes to
Slack's own *"/me your message — Displays action text"*, so even a registered
`/me` would collide), and the manifest's `usage_hint` for `/cufa-reminders`
contains `|` and `<>` characters.

This is the single most expensive thing in the setup, because everything
downstream of it looks like a broken bot rather than a config gap.

**Fix:** rename `/me` → `/mystats` and `/help` → `/cufa-help` in the manifest and
in `HANDLERS`, then re-test the import in a scratch workspace and note in
`slack-bot.md` which names Slack reserves.

### F-12 · Blocks · Polls cannot be posted to real Slack at all

`polls.py:20` builds one `actions` block with one button per option and gives
**every button the same `action_id`**:

```python
"action_id": POLL_ACTION_ID,   # same string for every option
```

Slack requires `action_id` to be unique within a message, so any poll with two or
more options is rejected outright:

```
invalid_blocks: `action_id` "cufa_poll_vote" already exists
                [json-pointer:/blocks/1/elements/1/action_id]
```

The fake Slack server does not validate Block Kit, so the whole poll feature
passes its acceptance checks and has never worked against Slack.

**Fix:** `f"{POLL_ACTION_ID}:{i}"` per button. `parse_interaction` already
resolves the poll from `block_id` and the option from `value`, so it only needs
to match the action_id by prefix.

---

---

# Fixes applied — 2026-09-14

All five were verified against the live `Civicsunplugged` workspace, not the fake
server. `598 tests pass` (597 + one new regression test), exit 0.

| # | What was broken | Fix | Proof it works now |
|---|---|---|---|
| F-12 | Polls could never post to Slack | unique `action_id` per option button + Bolt matches the family by regex | Poll posted to `#general`; voted Tuesday, changed to Thursday; tally reads **Tuesday 0 / Thursday 1, "1 voted"** |
| F-09 | Bot's own posts counted as participation | `sync.record_message` now skips on `bot_id`, the same rule the live path uses | Bot posted a poll, a Q&A pointer and a Q&A summary after the fix → **0 new bot rows**, and a full backfill re-read every channel and wrote **0** |
| F-11 | Manifest wouldn't import; 19 of 20 commands missing | `/me` → `/mystats`, `file_comment_added` removed | `apps.manifest.validate` → **`"ok": true`**. Live app now has **20 slash commands and 12 bot events** |
| F-10 | Late reminder said "starts in 24 hours" | countdown rendered from the real delta | 88 minutes out now reads **"starts in about 1.5 hours"**; a reminder that really is on the 24h mark still reads "24 hours" |
| F-04 | `cufa slack cmd /cufa-reminders` said the command didn't exist | `dispatch()` maps `cufa-reminders` → `reminders` | Returns the cadence instead of "I don't know" |

## What Slack actually said about the manifest

The UI's *"We can't translate a manifest with errors"* hides the reason. Asking
`apps.manifest.validate` directly gives it:

```json
{"ok": false, "error": "invalid_manifest", "errors": [
  {"code": "invalid_user_event_types",
   "message": "Invalid bot event types `file_comment_added`"},
  {"code": "invalid_name",
   "message": "The slash command has an invalid name",
   "pointer": "/features/slash_commands"}]}
```

Validating each command on its own isolates the second one to exactly one name:

```
ok    /cufa-reminders   ok    /reminders   ok    /badges    ok  /checkin
FAIL  /me               The slash command has an invalid name
ok    /dashboard        ok    /help        ok    /attendance …
```

So `/me` is the only reserved name in the set — `/help` is fine, which is the
opposite of what I guessed before checking. Both causes are now fixed in
`docs/setup/slack-bot.md`, with a comment at each so the next person does not
re-introduce them.

## Now working that was blocked before

- **`@cif-participation summary`** — `app_mention` is subscribed, so the mention
  produces a digest. It names nobody: the raw `<@U…>` renders as `@someone`, and
  the post contains 0 addresses and 0 raw mentions.
- **`team_join`** — subscribed, so a new workspace member will raise a roster
  alert. Not fired yet; this workspace has one human.
- **Huddles and canvases** — `user_huddle_changed`, `file_created`,
  `file_change`, `file_shared` are subscribed.
- **All 20 slash commands** — registered. `/mystats` is the name to type;
  `/me` still resolves internally for the offline driver and older installs.

## Two things the fixes do not undo

**The corrupt rows already written are permanent, by design.** The two bot posts
ingested before the fix cannot be removed:

```
ERROR: slack_event rows are immutable and are never deleted
       (slack_event_id=3ad15787-…). A dropped observation is unrecoverable.
```

That trigger is right, and it means any workspace that ran the buggy version
still carries the inflated rows. `cufa slack report` here still shows a
`— (not on roster)` participant with 2 messages. Cleaning that needs a migration
that marks them, or a read-side exclusion of the workspace's own bot user id —
a decision worth making deliberately rather than with a DELETE.

**F-03 is untouched.** The 235% attendance figure was not on the list, so I left
it. It is still the finding I would fix next, and the one-line property test
(`0 <= rate <= 1`) is still the cheapest way to stop it recurring.

## One new finding

### F-13 · Annoying · The test suite reads the developer's `.env`, so configuring the bot breaks it

After pointing `.env` at the real workspace, two tests started failing:

```
FAILED tests/test_reminders.py::test_quiet_session_reminder_waits_until_morning
FAILED tests/test_slack_qa.py::test_doctor_checks_the_qa_channels
```

Neither is a code fault. `test_quiet_session_reminder_waits_until_morning`
assumes the default 21:00–08:00 quiet window; my `.env` had narrowed it to
03:00–04:00 so the reminder was correctly sent and the assertion failed. The
Q&A doctor test picks up `CUFA_SLACK_QA_CHANNELS` the same way. Running the
same two tests with those variables reset passes both.

So the suite is not hermetic: it is green on a fresh clone and can go red the
moment someone configures the bot for a real workspace — which is exactly when
they most want to trust it.

**Fix:** have the test fixtures build `Settings` from explicit values rather
than inheriting the process environment, or have `conftest.py` clear the
`CUFA_SLACK_*` variables for the session.
