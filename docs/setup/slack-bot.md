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

Text is off by default because the participation definition counts acts; it
does not read them. The text of young people's casual conversation with each
other is a much larger exposure than the fact of the conversation, and nothing
in the definition needs it. `CUFA_SLACK_STORE_TEXT=1` turns it on, for a
workspace whose data owner has decided that. Even then the bot's status page and
logs never show it.

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
      - chat:write            # posting check-in links later; unused by the collector
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - reaction_added
      - reaction_removed
      - member_joined_channel
      - member_left_channel
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

## Reading the data

```
cufa slack stats                       # totals
cufa slack report --cohort cu-2026     # messages, replies, reactions, active days — per fellow
cufa slack report --cohort cu-2026 --days 14
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
