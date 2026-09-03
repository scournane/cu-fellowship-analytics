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
| its length, word count, whether it had a link or a file | direct messages (the bot is never given that scope) |
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
      - chat:write            # Q&A: posting "asked before" pointers and summaries
      - app_mentions:read     # Q&A: "@bot summary" in a channel
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - reaction_added
      - reaction_removed
      - member_joined_channel
      - member_left_channel
      - app_mention           # Q&A: "@bot summary"
  socket_mode_enabled: true   # flip to false for HTTP mode; then set request_url
  org_deploy_enabled: false
  token_rotation_enabled: false
```

Do **not** add `im:history` or `mpim:history`. The bot has no business in
direct messages, and the manifest is the place that decision is enforced.

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

Both modes serve nothing else. HTTP mode also exposes `/` (a status page),
`/stats` (JSON) and `/health`. None of them shows an address.

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
