# The Slack participation bot

The Director of Programs defined Slack participation as *"sending messages,
reacting to messages, etc"*. This bot records those acts — as they happen — into
the same database the check-in forms write to, keyed to the same roster by
email.

## Why a bot, in one paragraph

Slack's free plan **hides messages after 90 days and deletes them after a
year**. A workspace that starts on the free plan loses September's participation
record by December, while the fellowship is still running. A bot that writes
each event to Postgres on arrival owns a permanent copy from the first day, so
nothing evaporates. The cost is real and it is the last section of this
document: **a bot has to be running.**

## What it records — and what it deliberately does not

| Recorded | Not recorded |
|---|---|
| that a message was sent, by whom, where, when | **the message text** |
| its length, word count, whether it had a link or a file | inbound direct messages (the bot never subscribes to or stores them) |
| whether it was a thread reply | anything from bots, including itself |
| reactions added and removed, by whom, to whose message | reactions *received* as a ranking of anyone |
| channel joins and leaves | |
| edits and deletions, as new events — the original row stays | |
| **in Q&A channels only:** the question and reply text, so a repeat can be pointed at the earlier answer and a session's Q&A summarised (ADR-032) | |

Text is off by default because the participation definition counts acts; it
does not read them. The text of young people's casual conversation with each
other is a much larger exposure than the fact of the conversation, and nothing
in the definition needs it. `CUFA_SLACK_STORE_TEXT=1` turns it on, for a
workspace whose data owner has decided that. Even then the bot's status page and
logs never show it.

The one exception is a channel you name in `CUFA_SLACK_QA_CHANNELS`. A Q&A
channel is different in kind: a question is posted so it can be found and
answered, and the value of an answer is that the next person can be pointed at
it. Neither works without the words. See [Q&A channels](#qa-channels-this-was-asked-before-and-a-summary-for-the-teacher)
below — and note that even there, `slack_event.text` stays NULL; the text lives
in its own tables.

Every row stores the **email**, not a `fellow_id`. Identity resolves at read
time by joining the roster, so correcting a roster entry re-attributes history
with no backfill — the same rule as everywhere else in this project. An address
that matches nobody still produces a row and lands in the review queue
(`cufa review --status unresolved-identity`).

## Try it with no Slack account

```
make demo-slack
```

That starts the real bot and a **fake Slack** on `http://127.0.0.1:3001/`. The
fake serves the Web API the bot calls (`auth.test`, `users.info`, …) and has
buttons that build real Events API payloads, **sign them with the bot's signing
secret exactly as Slack would**, and POST them to the bot. So:

* *Post message / React / Join / Edit / Delete* — watch the bot's status page
  count them.
* *Replay last delivery* — re-sends the identical event with Slack's retry
  headers. The bot answers 200 and writes **nothing**: "duplicates dropped"
  goes up, "events" does not.
* *Send with bad signature* — the bot refuses it. This is the only thing
  standing between the events table and anyone who can reach the URL.
* *Bot message* — skipped, never recorded.
* *40 events* — a plausible day across the public channels.
* *Q&A in #q-and-a* — ask a question, answer it in the thread, mark the answer
  ✅, then **ask the first question again**: the bot replies in the new thread
  with a link to the earlier answer. **@bot summary** posts the session's Q&A
  digest. Both show under "What the bot posted".

`make demo-slack-batch` drives the same stack without a browser and runs the
acceptance checks (`scripts/verify_slack_demo.py`). It is what CI runs.

## Creating the real Slack app

The fastest way is a manifest. Go to <https://api.slack.com/apps> → **Create
New App** → **From a manifest** → pick the workspace → paste this:

```yaml
display_information:
  name: CIF participation bot
  description: Records that fellows post and react, for the participation report. Stores no message text.
features:
  bot_user:
    display_name: cif-participation
    always_online: true
  slash_commands:             # one entry per command; the URL is ignored in Socket Mode
    - command: /cufa-reminders
      description: Your reminder cadence, timezone and quiet hours
      usage_hint: all | fewer | later | none | timezone <zone> | quiet <from> <to> | status
      should_escape: false
    - command: /reminders
      description: See or change which reminder intervals you get
    - command: /badges
      description: Your badges and streak; /badges off stops the messages
    - command: /checkin
      description: Ask a staff member to check in with you
    - command: /me
      description: Your own attendance, exit tickets and Slack activity
    - command: /dashboard
      description: A private link to your dashboard
    - command: /help
      description: What the bot can do
    - command: /attendance
      description: "Staff: who checked in to a session"
    - command: /fellow
      description: "Staff: a fellow's profile card"
    - command: /report
      description: "Staff: the cohort so far"
    - command: /leaderboard
      description: "Staff: rankings"
    - command: /assignment
      description: "Staff: create and list assignments"
    - command: /score
      description: "Staff: record a Solvathon or case-brief score"
    - command: /zoom
      description: "Staff: put the Zoom link on a session"
    - command: /outreach
      description: "Staff: mark that someone reached out to a fellow"
    - command: /alias
      description: "Staff: a second address on one roster record"
    - command: /link
      description: "Staff: attach a Slack account to a fellow"
    - command: /alerts
      description: "Staff: accounts not on the roster"
    - command: /digest
      description: "Staff: post the weekly digest now"
    - command: /sync
      description: "Staff: pull Slack now"
oauth_config:
  scopes:
    bot:
      - channels:history      # read messages in public channels the bot is in
      - channels:read         # list channels, resolve names
      - groups:history        # same, private channels — only if invited
      - groups:read
      - users:read            # slack_user_id → profile
      - users:read.email      # → email, which is what joins to the roster
      - reactions:read        # reactions on backfill
      - chat:write            # reminders, digests, agendas, badge DMs, staff posts, Q&A pointers and summaries
      - app_mentions:read     # Q&A: "@bot summary" in a channel
      - im:write              # open a DM to send a reminder or a welcome
      - commands              # the slash commands listed under features above
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - reaction_added
      - reaction_removed
      - member_joined_channel
      - member_left_channel
      # Q&A: "@bot summary" in a channel.
      - app_mention
      # Roster alert when someone new joins the workspace.
      - team_join
      # Huddle joins and leaves. Slack sends the user, not the channel.
      - user_huddle_changed
      # Canvases arrive as file events; the bot asks files.info whether the
      # file is a canvas and skips everything that is not. Needs files:read.
      - file_created
      - file_change
      - file_shared
      - file_comment_added
  interactivity:
    # Votes on polls the bot posts, and the "check in with me" button. Same
    # URL as events in HTTP mode; nothing extra in Socket Mode.
    is_enabled: true
  socket_mode_enabled: true   # flip to false for HTTP mode; then set request_url
  org_deploy_enabled: false
  token_rotation_enabled: false
```

Do **not** add `im:history` or `mpim:history`. The bot can send reminders to a
fellow, but cannot read or store replies or any other direct-message history.
The manifest is where that boundary is enforced.

Add `files:read` to the bot scopes for canvases. Without it every file event
is looked up, found unreadable, and skipped — nothing breaks, canvases just
do not appear.

## The signals, and their limits

`cufa slack insights` (and `/insights` on the bot) reads eight things off the
same event table. Each has a rule, written down in ADR-033:

| Signal | What it says | What it will not say |
|---|---|---|
| Who replies to whom | thread-reply edges, ordered by the person replying; and an alphabetical list of active fellows nobody has replied to *or* mentioned in the window, with whether they posted at all | who is replied to most — received recognition is recorded, never ranked |
| Mentions | `@`-mentions **given** per fellow, and how many different people | mentions received |
| Huddles | joins and leaves per fellow, huddle count for the cohort | which channel — Slack does not send it |
| Canvases | created / edited / shared / commented, cohort-wide | who edited — Slack's `file_change` names no editor, so an edit is recorded with the actor empty rather than guessed. Slack also has no public event for canvas *comments*; `file_comment_added` is recorded if it arrives |
| Emoji | which reactions the cohort uses and how that moves by week | anything per person. The query has no user column and a test keeps it that way |
| Channel liveness | alive / quiet / silent per channel, recent message and poster *counts* | which people |
| Rhythm | hour-of-day and day-of-week histograms, each act read in its own fellow's zone | — . It is also used, per fellow, to stand in for the *default* quiet hours when a fellow has not set their own, so a night owl gets the evening reminder and not the morning one. A stated preference always wins |
| Polls | option totals and how many voted, latest vote per person | who voted for what |

Post a poll with `cufa slack poll --channel general --question "…" --option A --option B`;
the bot never edits the tally into the message, so a channel cannot watch it move.

Then:

1. **Install to Workspace** (OAuth & Permissions). Copy the **Bot User OAuth
   Token** (`xoxb-…`) → `SLACK_BOT_TOKEN` in `.env`.
2. **Basic Information → App-Level Tokens → Generate** with scope
   `connections:write`. Copy it (`xapp-…`) → `SLACK_APP_TOKEN`. Socket Mode
   only.
3. **Basic Information → Signing Secret** → `SLACK_SIGNING_SECRET`. HTTP mode
   only, but harmless to set always.
4. `CUFA_SLACK_COHORT=cu-2026` (or whichever cohort this workspace is).
5. **Invite the bot to every channel you want counted.** In each channel:
   `/invite @cif-participation`. This is the step people forget. A channel the
   bot is not in produces nothing, silently. `cufa slack channels` shows what
   it can see.
6. **Optionally name the Q&A channel(s):** `CUFA_SLACK_QA_CHANNELS=q-and-a`.
   Their text is stored, repeats get a pointer, and summaries work. Leave it
   blank and none of that runs.
7. **For the reminder / staff-command half** (the second part of this
   document): `CUFA_SLACK_STAFF_CHANNEL` = the id of a private staff-only
   channel the bot is invited to; `CUFA_SLACK_ADMINS` = staff addresses that
   may run staff commands (workspace admins always can);
   `CUFA_PUBLIC_BASE_URL` = where the console is reachable, for `/dashboard`
   links.

## First real run — the checklist

Do these in order. Each one is checked by `cufa slack doctor`, which names the
fix beside anything that fails.

1. **Create the app from the manifest** above, in the workspace you are testing
   in. A personal test workspace is fine — the bot does not care which
   workspace it is in, and the cohort it writes to is whatever
   `CUFA_SLACK_COHORT` says.
2. **Install to Workspace** and copy the three values into `.env`:
   `SLACK_BOT_TOKEN` (xoxb-…), `SLACK_APP_TOKEN` (xapp-…, for Socket Mode),
   `SLACK_SIGNING_SECRET`. Set `CUFA_SLACK_COHORT` to something that is not
   `demo` — `cu-2026-test` is a good name for a test run.
3. **Make sure `SLACK_API_BASE_URL` is blank** in `.env`. The demo sets it to
   point at the fake server; against real Slack it must be unset, or the bot
   will talk to a server that is not there.
4. **Invite the bot to a channel**: in Slack, `/invite @cif-participation`
   (or whatever you named it). This is the step everyone forgets.
5. **Start the database** (`make db-up`), then:

```
cufa slack doctor
```

It checks, in order: the three values are set; the database answers; the
token works and which workspace it belongs to; the scopes the token actually
carries against the ones the manifest asks for; which channels the bot is a
member of; and whether `users.info` returns an email for real members — the
thing that joins Slack activity to the roster. It exits 0 only when the bot
would actually record something.

6. **Run it**:

```
cufa slack socket
```

Post a message in the channel. In another terminal:

```
cufa slack stats          # events: 1, messages: 1
cufa slack report --cohort cu-2026-test
```

If the address on your Slack profile is not on the roster, the row is still
there — it shows as not-on-roster and the address is queued for review. Load a
roster with your email on it (`cufa load-roster`) and the same row is
attributed on the next report, with no re-ingest.

7. **Backfill** what was in the channel before the bot arrived:

```
cufa slack backfill
```

When it is time to move to the real CIF workspace, repeat steps 1–4 there
with the real cohort id. Nothing in the code changes.

## Running it

### Socket Mode — no public URL

```
cufa slack socket
```

Slack opens a WebSocket *to* the bot; nothing needs to be reachable from the
internet. Slack's own docs describe Socket Mode as a development transport
rather than the production one, but for a bot a nonprofit runs on one machine
it is the transport that does not require hosting, and that outweighs it.

### HTTP mode — a public URL

```
cufa slack serve --host 0.0.0.0 --port 3000
```

Set the app's **Event Subscriptions → Request URL** to
`https://<your-host>/slack/events`. Slack verifies the URL with a challenge the
bot answers automatically. Use this when the bot lives on a server anyway.
Set the `/cufa-reminders` command's **Request URL** to that same
`https://<your-host>/slack/events` URL. Socket Mode does not need either public
URL.

Both modes serve nothing else. HTTP mode also exposes `/` (a status page),
`/stats` (JSON) and `/health`. None of them shows an address.

### Outbound reminders and agendas

The long-lived HTTP or Socket Mode process runs one idempotent automation tick
per minute. For a one-shot check or an external scheduler, run:

```
cufa slack reminders --json
```

Set the following data before the bot reaches a reminder window:

* Add each fellow's IANA `timezone` (for example `America/Chicago`) to the
  roster CSV. Rows without one use `CUFA_DEFAULT_FELLOW_TIMEZONE`.
* Add `zoom_url`, `agenda`, and optionally `slack_channel_id` to the sessions
  CSV, or edit those fields on the session form in the console. The configured
  announcement channel is the agenda fallback.
* Record work with a due date using `cufa assignment create`, for example:

```
cufa assignment create --cohort cu-2026 --title "Community interview notes" --due-at 2026-09-18T17:00 --timezone America/New_York --url https://classroom.example.org/interview
cufa assignment list --cohort cu-2026
```

With the default `all` preference, session DMs go at 24 hours, 1 hour, and 10
minutes and always include the Zoom link. Assignment DMs go at 24 hours and 1
hour. The weekly digest goes Monday at 09:00 local time and includes this
week's sessions, due work, and recently changed sessions or assignments.

Each fellow controls all personal delivery from Slack:

```
/cufa-reminders all
/cufa-reminders fewer
/cufa-reminders later
/cufa-reminders none
/cufa-reminders timezone America/Chicago
/cufa-reminders quiet 21:00 08:00
/cufa-reminders status
```

`fewer` sends a 1-hour session reminder, a 24-hour assignment reminder, and at
most one Part B nudge. `later` sends session and assignment reminders at 10
minutes and 1 hour respectively, and delays its first Part B nudge. `none`
stops reminders, nudges, and weekly digests. Channel agendas are operational
session posts, not personal DMs, so an individual's preference does not remove
them.

No personal message is sent during that fellow's quiet hours, evaluated in
their timezone. The deployment default is 21:00-08:00 and can be changed with
`CUFA_SLACK_QUIET_START` and `CUFA_SLACK_QUIET_END`.

At session start the bot posts the staff-authored agenda once. After Part B
closes (session end plus grace), it refreshes responses before deciding who is
missing. A non-submitter can receive no more than two personalized DMs: the
first after 30 minutes and the second after 24 hours. The scheduler rechecks
submission immediately before each send, and the database rejects any third
nudge even if application code regresses.

### First connect: backfill

The bot starts recording the moment it is invited. Everything before that is
only in Slack — and on a free workspace, only for 90 days. Read it while it is
there:

```
cufa slack backfill                 # every channel the bot can see
cufa slack backfill --days 60       # or only recent history
```

Backfill is safe to re-run. Every row is keyed by the *act* (channel + message
ts, or channel + message + user + reaction), not by Slack's delivery id, so a
backfilled message collides with the one the bot recorded live instead of
sitting beside it. A watermark per channel means the next run starts where the
last one finished. Reactions come back on history messages as an aggregate
block; they are expanded into one reaction event per person so the
Director's definition is honoured on this path too.

Use backfill for gaps as well: the bot was down for a weekend, run it, done.

## Q&A channels: "this was asked before", and a summary for the teacher

Name the channel(s) where fellows ask questions:

```
CUFA_SLACK_QA_CHANNELS=q-and-a          # names or ids, comma-separated
CUFA_SLACK_QA_SUMMARY_CHANNEL=          # where `--post` goes; blank = the first Q&A channel
```

Invite the bot to each of them. `cufa slack doctor` checks that it is a
member and that the token carries `chat:write`. Then two things happen in
those channels and nowhere else.

### "This was asked before"

A top-level message is a question; the replies in its thread are the answers;
a ✅ (`:white_check_mark:`) on a reply marks *the* answer — Slack's own
convention, and the only one anyone needs to learn. When a new question
resembles an earlier one that was **answered**, the bot replies in the new
thread:

> 👋 This looks like a question that came up before, during *Sep 2 · Voting
> systems*. The reply that was marked ✅ is here: see the answer.
> *If it's a different question, carry on — a person will answer here.*

The rules, because a wrong pointer is worse than none:

* Only **answered** questions are pointed at: one with a ✅, or with a reply
  from someone other than the asker. The asker's own "anyone?" is not an
  answer. A deleted question is never pointed at.
* The link goes to the ✅'d reply when there is one, else to the earlier
  thread. The session named is the one whose window the earlier question fell
  in (from an hour before a lesson until an hour before the next).
* Matching is in two tiers, like passphrase adjudication. Tier 1 is word
  overlap after stop words — *"does anyone have the slides from tuesday"* and
  *"can someone share tuesday's slides"* match; *"what does quorum mean"* and
  *"what does filibuster mean"* do not. Tier 2 only runs with a
  `GEMINI_API_KEY`: the model is shown the new question and at most eight
  earlier answered questions that share at least a word, as anonymous strings,
  and picks one or none. Without a key, tier 1 is all there is.
* One pointer per question, ever. A retried delivery cannot post a second.
  The wording is hedged on purpose: overlap is not understanding.

### The summary

```
cufa slack qa list    --latest                       # what was asked, what is open
cufa slack qa summary --latest                       # the session in effect now
cufa slack qa summary --date 2026-09-02 --post       # a session by date, posted to Slack
cufa slack qa summary --session <id> --regenerate    # redo it; the old one is superseded
```

or, in Slack, `@cif-participation summary` (or `… summary sept 2`) in any
channel the bot is in — it replies in the thread. A summary is:

* the session's questions (every top-level message in the Q&A channels inside
  that session's window) with their replies;
* a count — *7 questions · 5 answered · 2 still open*;
* with a `GEMINI_API_KEY`, a short paragraph on what was asked and what the
  replies settled, grouped into topics; without one, the plain digest;
* **always** the list of what is still open, and every question as a link, so
  the teacher can go straight to a thread.

The model sees numbered question and reply texts and nothing else: no names,
no ids, no counts per person. Mentions inside a question reach it as
`@someone`. Regenerating supersedes rather than overwrites — the row the
teacher read last week keeps its `generated_at`.

### Where the text lives, and what follows it

`slack_qa_question` and `slack_qa_answer` hold the text; `slack_qa_pointer`
records each pointer (and the error, when Slack refused the post);
`slack_qa_summary` holds each summary. These are working tables, not
observations: an edit updates the text, a deletion stamps `deleted_at_utc`,
a ✅ flips `accepted`. `slack_event` is still the immutable record that the
message happened, with `text` NULL, exactly as for every other channel. The
rows store the Slack user id, never an email, and nothing joins them to the
roster: nothing about a question is ever attributed to a fellow.

`cufa slack backfill` reads a Q&A channel's threads too (`conversations.replies`,
one call per thread — only there, where the replies are the point), so a bot
that was down still ends up with the answers. No pointers are posted for
history; the moment has passed.

**HTTP mode:** a summary can take longer than the three seconds Slack allows
before it retries the delivery. The bot ignores a retried mention, but set
`CUFA_SLACK_ACK_FIRST=1` in HTTP mode so it acknowledges first and works in
the background. Socket Mode does not have the constraint.

## Reading the data

```
cufa slack stats                       # totals, including Q&A counts
cufa slack report --cohort cu-2026     # messages, replies, reactions, active days — per fellow
cufa slack report --cohort cu-2026 --days 14
cufa slack qa list --latest            # the current session's questions and what is still open
cufa review --status unresolved-identity   # addresses that matched nobody
```

The view `slack_activity_daily` is the join everything else should build on:
acts per person per UTC day, roster-attached where the email matches, kept
where it does not. **Reactions received are not counted anywhere.** The
definition is about what a fellow does, and a ranking on received recognition
is exactly the leaderboard the research warns against (see ADR-028).

## Who runs it after October 2

This is the risk, and it belongs in the setup document rather than a footnote.

A collector that stops fails **silently**: Slack keeps delivering nothing, the
database keeps looking fine, and the gap is found in March. So:

* The bot opens a `load_run` row on start and closes it on a clean stop. A
  run left in `running` is the record that the process died. `cufa slack
  stats` shows `last_received`; if it is older than the last lesson, the bot
  is not running.
* **Backfill is the safety net**, but it is time-limited on a free workspace.
  If the bot is found dead, run `cufa slack backfill` *before* investigating
  anything else — the history is worth more than the diagnosis.
* The **Slack for Nonprofits** upgrade (free Pro) removes the 90-day limit
  entirely. With it, a dead bot costs nothing that a backfill cannot recover.
  Without it, a dead bot for 91 days is permanent loss. That upgrade is
  therefore not a nice-to-have; it is what makes the bot's failure mode
  survivable.
* Name the person who restarts it. Put their name here: **TODO(owner)**.

---

## Part two: reminders, badges, staff commands, dashboards

Everything below runs inside the same bot process — `cufa slack socket` or
`cufa slack serve` — on a scheduler thread that runs a **tick** every five
minutes, and answers slash commands as they arrive. `cufa slack tick` from cron
does the scheduled part without the bot running (and then also pulls messages,
which the live bot otherwise captures itself). Everything can be tried with no
workspace: `CUFA_FAKE_SLACK=1` swaps in an in-memory client, and
`cufa slack cmd /report --as U123` runs any command as any user id.

A tick does, in order, and safely on repeat: sync members and channels →
welcome newly resolved fellows → reminders → badges → roster alerts →
session summaries → the Monday digest. Reminders are sent by the one engine
described under *Outbound reminders and agendas* above; inside the bot
process that engine's own loop sends them every minute and the tick leaves
them alone, and from cron the tick sends them itself. Either way a reminder
is recorded in `bot_delivery` before it goes out, so nothing is sent twice.

### What fellows see

**Reminders** arrive by DM in the fellow's own time zone — a zone they set
with `/cufa-reminders`, else the roster's, else the one Slack reports — with
the Zoom link for sessions and the submission link for assignments. Nothing
is sent overnight (`CUFA_SLACK_QUIET_START`/`END`, 21:00–08:00 by default;
each fellow can set their own with `/cufa-reminders quiet`) — a reminder that
would land then is skipped, not delayed. `/cufa-reminders` sets the cadence;
`/reminders` switches single intervals off, and both are honoured:

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

### What staff see

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

### The staff dashboard

`/dashboard` in the console (behind the staff allowlist). Overall attendance
rate; every fellow sorted by attention index with the parts shown; a
*mark reached out* button per row; this week's most active; open check-in
requests and roster alerts; badges and ranks; assignments with a score-entry
form per fellow; the funnel; when data last arrived. **Export CSV** gives the
engagement table. Each fellow's name opens `/dashboard/fellow/<id>`: the
same page the fellow sees, plus attention index and reasons, aliases,
interventions, airtime on recordings, and the outreach toggle.

#### The attention index

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

### Identity: aliases and merging

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

### Zoom: who spoke, and how much

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

### Retention

`cufa fellow retention` reads `config/retention_rubric.json` — the early
concepts and the terms that signal them — and counts, per fellow, how many
of those concepts appear in exit-ticket answers given *after* the week each
was taught, against the cohort mean. Deterministic term matching only: no
model reads a fellow's words (invariant 12), and a mention is counted, never
graded (invariant 13). Edit the rubric to match the syllabus.

The midpoint and end-of-fellowship reflection the Director suggested needs
no new form: set the rotating question on those weeks as a teacher question
in `config/rotation.json`.

### The funnel

`cufa fellow funnel` (cohort) or `cufa fellow funnel --fellow CU-0001`:
accepted → joined Slack → first message → first check-in → completed, with
how many reached each stage and the median days between stages. Every stage
but the first and last is derived from observations. Stamp completion with
`cufa fellow completed CU-0001`.

---

### What is deliberately not here

* **No public leaderboard, no public badges.** DMs only, opt-out in one
  command. ADR-028.
* **No AI reads a fellow's words to decide anything about them.** Retention
  is term-matching against a rubric staff wrote.
* **No score is a participation signal.** Solvathon and case-brief scores are
  stored and shown, and enter no metric.
* **The help checkbox stays on its own path.** Nothing in the bot, the
  dashboards or the engagement queries reads that table, and the
  safeguarding tests run every one of those queries to prove it.
