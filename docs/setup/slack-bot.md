# The Slack bot

One bot, two audiences. **Fellows** get reminders they control, a private
badge DM they can switch off, a *check in with me* button, and a link to
their own dashboard. **Staff** get slash commands for attendance, profile
cards, reports and rankings; a session summary in the staff channel after
every lesson; a digest every Monday; and an alert whenever somebody joins the
workspace who is not on the roster.

The bot and the console read the same database. Nothing is computed twice:
`/fellow` in Slack, `cufa fellow card` in a terminal, and the staff dashboard
in a browser all call the same function.

---

## 1. Create the Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → *From scratch*.
   Name it (e.g. *CU Fellowship*) and pick the fellowship workspace.
2. **Socket Mode** → enable it. Create an app-level token with the
   `connections:write` scope. That is `SLACK_APP_TOKEN` (`xapp-…`). Socket
   Mode means the bot dials out to Slack, so nothing needs a public URL.
3. **OAuth & Permissions** → *Bot Token Scopes*. Add:

   | Scope | Why |
   |---|---|
   | `users:read`, `users:read.email` | resolve members to the roster by address; read their time zone |
   | `channels:read`, `groups:read` | list the channels the bot is in |
   | `channels:history`, `groups:history` | count messages in fellow-facing channels |
   | `chat:write` | reminders, summaries, digests, badge DMs |
   | `im:write` | open a DM to send a reminder |
   | `commands` | slash commands |

   Then **Install to Workspace**. The *Bot User OAuth Token* is
   `SLACK_BOT_TOKEN` (`xoxb-…`). `SLACK_SIGNING_SECRET` is under *Basic
   Information*.
4. **Event Subscriptions** → enable, and subscribe the bot to
   `team_join`, `message.channels`, `message.groups`. (With Socket Mode there
   is no request URL to fill in.)
5. **Slash Commands** → create one entry per command. Each is just a name and
   a one-line description; the request URL is ignored in Socket Mode.

   Fellows: `/reminders`, `/badges`, `/checkin`, `/me`, `/dashboard`, `/help`
   Staff: `/attendance`, `/fellow`, `/report`, `/leaderboard`, `/assignment`,
   `/score`, `/zoom`, `/outreach`, `/alias`, `/link`, `/alerts`, `/digest`, `/sync`

   Permission is enforced by the bot, not by Slack: a fellow who types
   `/report` is told it is a staff command. Staff are workspace admins plus
   any address in `CUFA_SLACK_ADMINS`.
6. **Interactivity** → enable, so the *check in with me* button works.

## 2. Configure

In `.env`:

```
SLACK_BOT_TOKEN=xoxb-…
SLACK_APP_TOKEN=xapp-…
SLACK_SIGNING_SECRET=…
CUFA_SLACK_STAFF_CHANNEL=C0123456789     # a PRIVATE staff-only channel; invite the bot
CUFA_SLACK_ADMINS=you@civicsunplugged.org,colleague@civicsunplugged.org
CUFA_SLACK_COHORT=fall-2026
CUFA_PUBLIC_BASE_URL=https://console.example.org   # where the console is reachable, for /dashboard links
```

Install the Slack extra once:

```
pip install -e ".[slack]"
```

Invite the bot to the staff channel and to every fellow-facing channel you
want counted. It cannot read a channel it is not in.

## 3. Run

```
cufa slack serve
```

That runs Socket Mode and, in a background thread, a **tick** every five
minutes. A tick does everything that is due, in order, and is safe to repeat:

1. **sync** — members, channels, new messages (incremental, by watermark)
2. **welcome** — one DM to each newly resolved fellow: what the bot does,
   how to switch each part off, and the *check in with me* button
3. **reminders** — 24 h / 1 h / 10 min before each session and assignment
4. **badges** — compute, store, DM the new ones
5. **roster alerts** — one post per unrostered account
6. **session summaries** — once per session, after its end
7. **weekly digest** — Mondays, once

If you would rather use cron, run `cufa slack tick` on a schedule instead and
skip `serve` — everything except live slash commands and the button works
from the tick alone. `cufa slack tick --fake` runs against the in-memory
client for a dry run.

Everything can be tried without a workspace: `CUFA_FAKE_SLACK=1` swaps in
the fake, and `cufa slack cmd /report --as U123` runs a command as any user id.

---

## What fellows see

**Reminders** arrive by DM in the fellow's own time zone (Slack reports it),
with the Zoom link for sessions and the submission link for assignments.
Nothing is sent between 22:00 and 07:00 local — a reminder that would land
then is skipped, not delayed. Each person controls the intervals:

```
/reminders                       show
/reminders session 10m off       keep 24h and 1h, drop the 10-minute one
/reminders assignment off        no assignment reminders at all
/reminders all on                back to the defaults
```

**Badges** are computed from what is already recorded — check-ins, exit
tickets, messages, thread replies, shoutouts given and received, streaks —
and DM'd privately when earned. `/badges` shows them; `/badges off` stops the
messages. Nothing is ever posted publicly, and there is no fellow-facing
leaderboard: ADR-028 records why. Staff can see rankings with
`/leaderboard`, ranked by *giving* shoutouts, not receiving them.

**`/checkin [note]`** pings the staff channel and records an open request on
the fellow's profile. It is closed when a staffer marks outreach. This is a
public, operational button and is deliberately *not* the Part B "I'd like
someone to check in with me" checkbox, which stays on its own path with its
own recipient and access list (see `docs/safeguarding.md`).

**`/me`** is the fellow's own attendance, exit tickets and Slack activity;
**`/dashboard`** is a signed link (valid 7 days) to the same on the web, with
an export button and click-to-toggle reminder and badge preferences. Only
their own data, ever.

## What staff see

| Command | What it gives you |
|---|---|
| `/attendance <session\|next\|last>` | who checked in, who filled in the exit ticket, who attended but was not heard on the recording |
| `/fellow <name\|id\|email>` | profile card: emails and aliases, Slack account, attendance, activity vs. cohort mean, attention index and why, reached-out flag, assignment scores, interventions, badges, funnel |
| `/report` | the cohort so far, plus when each data source last produced anything |
| `/leaderboard [checkins\|streak\|messages\|shoutouts_given\|exit_tickets]` | staff-only ranking |
| `/assignment create "Title" 2026-10-01 18:00 [solvathon\|case_brief] [link]` | an assignment with reminders; `list`, `link` |
| `/score <assignment> <fellow> <score> [note]` | record a Solvathon or case-brief score you gave by hand |
| `/zoom <session\|next> <link>` | put the Zoom link on a session; it goes out in every reminder |
| `/outreach <fellow> [note]` · `/outreach clear <fellow>` | the "has anyone reached out?" boolean, with who and when |
| `/alias <fellow> <email> [school\|personal]` | a second address on one roster record |
| `/link <@user> <fellow>` | attach a Slack account to a roster record (and record its address as an alias) |
| `/alerts` · `/alerts resolve <@user> staff\|ignored` | accounts that joined but are not on the roster |
| `/digest` · `/sync` | post the weekly digest now; pull Slack now |

**The session summary** is posted to the staff channel once the session's
scheduled end (plus grace) has passed: check-ins over roster, exit tickets,
who is missing, anything waiting for a human. If a Zoom transcript has been
ingested for the session (below) it also names who had the most airtime and
who attended but was never heard.

**The Monday digest** lists what is on this week and whether it has a Zoom
link yet, what is due, who has been quiet for 7+ days, the most active
fellows, fellows with a high attention index nobody has reached out to,
open check-in requests, and unrostered accounts.

## The staff dashboard

`/dashboard` in the console (behind the staff allowlist). Overall attendance
rate; every fellow sorted by attention index with the parts shown; a
*mark reached out* button per row; this week's most active; open check-in
requests and roster alerts; badges and ranks; assignments with a score-entry
form per fellow; the funnel; when data last arrived. **Export CSV** gives the
engagement table. Each fellow's name opens `/dashboard/fellow/<id>`: the
same page the fellow sees, plus attention index and reasons, aliases,
interventions, airtime on recordings, and the outreach toggle.

### The attention index

Three signals, each relative to the cohort rather than an absolute:

* **Slack** — fellow-facing messages as a share of the cohort mean
* **Attendance** — attended over sessions held; a session under review leaves
  the denominator (absent evidence is not evidence of absence)
* **Form completeness** — of the exit tickets submitted, how many fields
  were answered. *Counted, never graded.* Length and quality of writing are
  never scored (design invariant 13).

Weights are in `cufa.engagement.WEIGHTS` and are a starting point for the
Director to change. The index is 0–100, higher meaning "more reason for a
human to look", and its components are always shown beside it. Two things
never enter it, and tests enforce both: the help checkbox, and assignment
scores.

## Identity: aliases and merging

Fellows join Slack from one address and fill in forms from another. Identity
resolves at read time through `v_fellow_email` — the primary address plus
any aliases — so linking an alias re-attributes every historical check-in
and message at once, with no backfill.

* A fellow whose Slack address matches the roster is resolved automatically.
* Anyone else who joins raises a **roster alert** in the staff channel. Link
  them with `/link @them <fellow>`; the bot records the address as an alias
  and resolves the alert. Staff or guests: `/alerts resolve @them staff`.
* When the forms side used a different address, `/alias <fellow> <email>` or
  `cufa fellow merge --keep CU-0001 --other-email …` attaches it.

One address can belong to one fellow. A trigger refuses an alias that is
another fellow's primary address, and vice versa.

## Zoom: who spoke, and how much

No Zoom bot is needed. Zoom's cloud recordings come with a `.vtt` transcript
tagged by display name. Download it and:

```
cufa zoom ingest --session <id> --vtt path/to/transcript.vtt
```

That stores speaking turns and prints speaking share per fellow, unmatched
names, and fellows who attended but were never heard. Names are matched to
the roster and to Slack profile names, which is why the session reminder
asks everyone to set their real name and why the teacher should say so again
at the top of each call — a fellow who joined as "iPhone" is invisible here.

## Retention

`cufa fellow retention` reads `config/retention_rubric.json` — the early
concepts and the terms that signal them — and counts, per fellow, how many
of those concepts appear in exit-ticket answers given *after* the week each
was taught, against the cohort mean. Deterministic term matching only: no
model reads a fellow's words (invariant 12), and a mention is counted, never
graded (invariant 13). Edit the rubric to match the syllabus.

The midpoint and end-of-fellowship reflection the Director suggested needs
no new form: set the rotating question on those weeks as a teacher question
in `config/rotation.json`.

## The funnel

`cufa fellow funnel` (cohort) or `cufa fellow funnel --fellow CU-0001`:
accepted → joined Slack → first message → first check-in → completed, with
how many reached each stage and the median days between stages. Every stage
but the first and last is derived from observations. Stamp completion with
`cufa fellow completed CU-0001`.

---

## What is deliberately not here

* **No public leaderboard, no public badges.** DMs only, opt-out in one
  command. ADR-028.
* **No AI reads a fellow's words to decide anything about them.** Retention
  is term-matching against a rubric staff wrote.
* **No score is a participation signal.** Solvathon and case-brief scores are
  stored and shown, and enter no metric.
* **The help checkbox stays on its own path.** Nothing in the bot, the
  dashboards or the engagement queries reads that table, and the
  safeguarding tests run every one of those queries to prove it.
