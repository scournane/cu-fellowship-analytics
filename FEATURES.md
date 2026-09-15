# Feature-by-feature results

Every feature, what happened, and the evidence. Run 2026-09-14 against the
**real** Slack workspace `Civicsunplugged` (`T0BSTD4J14Z`), cohort
`cu-2026-test`, bot `cif-participation` (`U0BUNEG4V25`), app `A0BUJJ73UP8`.
Socket Mode. Tokens redacted everywhere.

Legend: ✅ worked · ❌ didn't work · ⚠️ worked with a caveat · ⛔ blocked, not the product's fault

---

## Setting up

| Feature | Result | Proof |
|---|---|---|
| Clone and build | ✅ | `python tasks.py setup` → `cufa 0.1.0 installed in .venv`, console bundle built (vite, 2190 modules). But `git clone` lands on `claude/civics-unplugged-analytics-tool-6nfb9b`, which has **no Slack bot in it at all** — `git checkout main` first |
| `tasks.py doctor` | ✅ | Named exactly what was missing (`MISS Dependencies`, `MISS Console bundle`) and printed the fix. All six rows `ok` after setup |
| `cufa slack doctor` preflight | ✅ | Exits 0 with "Ready." It caught the real problems before the bot ran: missing Q&A channel, empty roster, `CUFA_PUBLIC_BASE_URL` unreachable by fellows. Every one of those is silent once the bot is running, which is the point of the check |
| App created from the repo's manifest | ❌ | Pasting `docs/setup/slack-bot.md`'s manifest into **Create from a manifest** gives *"We can't translate a manifest with errors"* with the error marker on `slash_commands`. The app that exists was built from a cut-down manifest and ended up with **9 of 12 scopes and 1 of 20 slash commands** |
| Scopes | ⚠️ then ✅ | Installed app was missing `im:write`, `files:read`, `app_mentions:read`. Without `im:write` the bot cannot open a DM at all — no welcomes, no reminders, no badges. Added them, reinstalled, verified against the API rather than the settings page: `12 granted` |
| **DM-reading boundary** | ✅ | `im:history` and `mpim:history` absent from the granted list. Proved by consequence below |

---

## Capture — what the bot records

I posted, reacted, edited and deleted as a real user in `#general`. Eight rows,
every one with `text` **NULL**:

| Feature | Result | Proof (row from `slack_event`) |
|---|---|---|
| Message in a channel | ✅ | `message · C0C1V4Z7N68 · scournane@… · text=(empty) · text_length=35 · word_count=7`. "test message one from the setup run" is exactly 35 characters |
| **Message text is not stored** | ✅ | `text` NULL on all 14 message rows. `raw` stripped to `{type, event_ts, channel_type}`. Only length, word count, link/attachment flags survive |
| Thread reply | ✅ | `message · is_thread_reply=t · 44 chars` |
| Reaction added | ✅ | `reaction_added · reaction=white_check_mark` |
| Reaction removed | ✅ | `reaction_removed · reaction=white_check_mark` |
| Edit a message | ✅ | `message_changed · 43 chars, 8 words` written as a **new row**; the original 36-char row is still there |
| Delete a message | ✅ | `message_deleted` written as a **new row**; the original 39-char row is still there |
| Channel join | ✅ | `member_joined_channel` recorded when the bot and I joined each new channel |
| **@-mention extracted before the text was dropped** | ✅ | `message · 57 chars · mentions={U0BUNEG4V25}` — the mention survived, the sentence did not |
| Attribution by email | ✅ | Every row carries `scournane@civicsunplugged.org`, joined from the roster through `v_fellow_email` |
| Bot's own messages skipped (live socket) | ✅ | Bot posted 3 DMs + a digest during the socket run; **0** became events |
| **Bot's own messages skipped (sync/backfill)** | ❌ | **Bug.** Two bot posts were ingested as participation: the Q&A pointer (331 chars) and the session summary (178 chars). See F-09 |
| Huddle join / leave | ⛔ | `user_huddle_changed` is not in the installed app's event subscriptions, so nothing fires. `cufa slack insights` reports `huddles: 0` |
| Canvas created / edited / commented | ⛔ | `file_created`/`file_change`/`file_shared`/`file_comment_added` not subscribed. `insights` reports `canvases: 0 touched` |
| Someone not on the roster posting | ⚠️ | Not testable — the workspace has exactly one human. The mechanism is proven though: `cufa slack report` shows a `— (not on roster)` row, and `identity_unresolved_open` counts it |

---

## The privacy boundaries

These are the ones worth trusting, because each was checked against the running
system rather than the docs.

| Feature | Result | Proof |
|---|---|---|
| **The bot cannot read DMs** | ✅ | I tried to read the bot's own DM conversation **with the bot's own token**. Slack: `{"ok": false, "error": "missing_scope", "needed": "im:history", "provided": "channels:history,…,im:write,app_mentions:read,files:read"}` |
| The bot cannot receive DMs | ✅ | The DM composer in Slack reads **"Sending messages to this app has been turned off."** The inbound path is closed at Slack, not just unsubscribed |
| Nothing the bot posts names an address | ✅ | Weekly digest (171 chars) and session summary (178 chars) in the private staff channel: **0** addresses. Every DM: **0** addresses |
| No read-side command leaks an address | ✅ | `slack report`, `slack stats`, `slack channels`, `slack polls`, `slack engagement`, `slack qa list` → **0 addresses each** |
| Addresses redacted in logs at INFO | ✅ | `alias added fellow=CU-2604 email=g***@example.invalid` |
| Q&A text lives in its own tables | ✅ | The question is stored in `slack_qa_question` in full; the same event in `slack_event` has `text` NULL, `text_length=58` |
| Staff channel is private | ✅ | `cufa slack doctor`: `ok staff channel is private`. Every staff-facing post went there |
| Nothing reads `help_request` | ✅ | `information_schema.views` where the definition mentions `help_request` → **0 rows**. Only `cufa help-requests` and the console's separately-gated screen touch it |
| Console help screen has its own access list | ✅ | `/help-requests` → **403** signed in as `staff@civicsunplugged.org`, because `CUFA_HELP_ALLOWLIST` falls back to `adiah@civicsunplugged.org` from `config/help_routing.json` |

---

## Q&A channel

| Feature | Result | Proof |
|---|---|---|
| Question captured | ✅ | Posted "how do I find the budget line items for a city department?" in `#q-and-a` → row in `slack_qa_question` |
| Answer in thread captured | ✅ | Row in `slack_qa_answer` |
| ✅ marks an answer accepted | ✅ | `slack_qa_answer.accepted = t` |
| **Repeat question gets a pointer to the earlier answer** | ✅ | Asked "where can I find a city department's budget line items?" The bot replied in the **new** thread: *":wave: This looks like a question that came up before, on Sep 14. The reply that was marked :white_check_mark: is here: <…\|see the answer>. If it's a different question, carry on — a person will answer here."* `slack_qa_pointer`: `method=lexical, similarity=1.000, reasoning="6 shared words, overlap 1.00"`, `post_error` empty |
| The pointer names nobody | ✅ | Text above — no person named, no address |
| `@bot summary` in the channel | ⛔ | `app_mention` is not in the installed app's event subscriptions. The scope is granted, the event is not subscribed |

---

## Reminders, welcomes and the scheduler

Driven with `cufa slack tick --now <RFC3339>` rather than waiting a day.

| Feature | Result | Proof |
|---|---|---|
| Welcome DM, once | ✅ | First tick: `welcomed=1`. The DM explains `/reminders`, `/badges`, `/me`, `/dashboard`, `/checkin` |
| Welcome carries the check-in button | ✅ | **"Check in with me"** button visible in the DM |
| Session reminder at 24h | ✅ | `bot_delivery kind=session_reminder scheduled_for=2026-09-13 18:43 status=sent` |
| Session reminder at 1h | ✅ | tick at `17:43:30Z` → `reminders=1`; row `scheduled_for=2026-09-14 17:43` |
| Session reminder at 10min | ✅ | tick at `18:33:30Z` → `reminders=1`; row `scheduled_for=2026-09-14 18:33` |
| Assignment reminder at 24h | ✅ | tick at `22:00:30Z` → `reminders=1`; `kind=assignment_reminder` |
| Reminder carries the Zoom link | ✅ | DM: *"reminder: **Test session 1 - deliberation** starts in 24 hours (Mon, Sep 14 at 2:43 PM EDT). **Join Zoom**"* |
| Rendered in the fellow's timezone | ✅ | "2:43 PM EDT" — roster timezone `America/New_York` |
| **Reminder wording when the mark has already passed** | ❌ | That DM arrived ~88 minutes before the session and still said **"starts in 24 hours"**. See F-10 |
| Nothing sent twice | ✅ | Re-ran the 1h tick: `reminders=0`. `bot_delivery` is written before the send and is what dedupes |
| Session summary after end + grace | ✅ | tick at `19:58Z` → `summaries=1`; `digest_log kind=session_summary channel=C0C1V5BAYE8` (178 chars) |
| Weekly digest | ✅ | `digest_log kind=weekly target_key=cu-2026-test:2026-W38` (171 chars), posted to the private staff channel |
| Agenda posted at session start | ⚠️ | tick at session start produced nothing. No announcement channel is configured (`CUFA_SLACK_ANNOUNCE_CHANNEL` unset); the bot is in `#announcements` but was never pointed at it |
| Roster alert on `team_join` | ⛔ | `team_join` not subscribed in the installed app |
| Badges DM'd privately, never posted publicly | ✅ | `badges=0/0` (nobody has earned one yet), and no public badge post exists anywhere. `/leaderboard` output is labelled *"(staff view — not shown to fellows)"* |
| **Quiet hours skip rather than delay** | ✅ | `is_quiet_time` → `_should_send` returns False and increments `suppressed_quiet_hours`, surfaced as `skipped_quiet`. There is no defer path: `defer`, `postpone`, `reschedul`, `retry_after`, `queue_for_later` appear nowhere in `reminders.py`. Overnight windows wrap midnight correctly (22:30 ✓, 03:00 ✓, 07:59 ✓, 08:00 ✗) |
| **Two-nudge cap enforced by the database** | ✅ | Inserted nudge 1 and 2 directly. Nudge **3** → `violates check constraint "bot_delivery_nudge_number_valid"`. Re-sending nudge **2** under a fresh dedupe key → `violates unique constraint "bot_delivery_two_nudges_only"`. Exactly 2 rows land, whatever the application code does |

---

## Slash commands

Only **`/cufa-reminders`** is registered in this workspace. The other 19 are in
the repo's manifest but never made it into the app, and the manifest will not
re-import (see F-11). So they are proven against the real database through
`cufa slack cmd`, which runs the same `dispatch()` the bot calls, rather than
end-to-end through Slack.

| Feature | Result | Proof |
|---|---|---|
| `/cufa-reminders` registered in Slack | ✅ | Listed on the app's Slash Commands page |
| `/help` | ✅ | Lists exactly the 5 fellow commands, no staff ones |
| `/me` | ✅ | Own attendance, exit tickets, message counts. No other fellow named |
| `/dashboard` | ✅ | `http://127.0.0.1:8000/me/<signed token>`, described as "private link, valid for 7 days" |
| `/badges`, `/badges off` | ✅ | Own badges, streak, counts, `/badges off` hint |
| `/checkin <note>` | ✅ | `intervention recorded fellow=CU-2600 kind=check_in_request`, and told the truth when no staff channel was set: *"No staff channel is configured yet, so nobody was pinged automatically"* |
| `/reminders` | ✅ | Session 24h/1h/10m, assignment 24h/1h/10m, badges on |
| **`/cufa-reminders` via `cufa slack cmd`** | ❌ | *"I don't know `/cufa-reminders`. Try `/help`."* Works in Slack, invisible to the offline driver. See F-04 |
| `/report` | ✅ | Sessions, fellows, attendance, check-ins, most active, attention index, per-source freshness |
| `/fellow <name>` | ✅ | Aliases, attendance, activity vs cohort mean, attention index and why, outreach flag, assignments, interventions, funnel |
| `/attendance next` | ✅ | Session card with checked-in count and the names of non-attenders |
| `/leaderboard` + variants | ✅ | Labelled staff-only |
| `/assignment create` / `list` | ✅ | *"Created **Test deck** (Solvathon), due 2026-09-15 18:00 America/New_York. Reminders go out 24h, 1h and 10m before."* |
| `/score` | ✅ | `score recorded assignment=… fellow=CU-2601` |
| `/zoom next <url>` | ✅ | *"Zoom link set on **Test session 1 - deliberation**. It will be in every reminder."* — and it was |
| `/outreach` and `/outreach clear` | ✅ | Both, each an `intervention` row with provenance |
| `/alias` | ✅ | *"**Delphine Dunmore** now also resolves from other@example.invalid … past ones included"* |
| `/link @user <fellow>` | ✅ | Alias added, Slack user linked, address redacted in the log |
| `/alerts`, `/alerts resolve` | ✅ | Lists non-roster accounts including one with no email on profile |
| `/digest` | ✅ | Refuses clearly when no staff channel: *"⚠️ No staff channel the bot can find. Set CUFA_SLACK_STAFF_CHANNEL … and invite the bot to it."* |
| `/sync` | ✅ | Reports users/channels/messages read and written |
| **Non-admin refused a staff command** | ✅ | `⛔ /fellow is a staff command. Ask a workspace admin, or have your address added to CUFA_SLACK_ADMINS.` |
| `/admin-dashboard` (added this run) | ✅ | Registered in the real app via `apps.manifest.update`; replies with the console URL and deliberately **not** the site password |

---

## Polls

| Feature | Result | Proof |
|---|---|---|
| **Post a poll to a real channel** | ❌ | **Hard failure.** `cufa slack poll --channel general --question "…" --option Tuesday --option Thursday` → Slack rejects the message: `invalid_blocks: action_id "cufa_poll_vote" already exists [json-pointer:/blocks/1/elements/1/action_id]`. See F-12 |
| Vote, change a vote, latest-vote-only tally | ⚠️ | Cannot be tested against real Slack because the poll never posts. Proven against the fake server only: 4 votes from 3 people, tally Thursday 2 / Tuesday 1 |
| Tally never edited into the message | ✅ | By construction — `bot.py:576`: *"results are never edited into it, so a channel cannot watch a tally move"* |
| `insights --section polls` names no voter | ✅ | Totals only |

---

## Backfill and resilience

The socket dropped on its own mid-run, which turned into the best test in the set.

| Feature | Result | Proof |
|---|---|---|
| **Socket drop is survivable** | ⚠️ | Bolt logged `SSLEOFError … EOF occurred in violation of protocol` five times, closed, and reconnected on its own. But the message I posted during the gap was **never recorded** — silently missed |
| **Backfill recovers what the socket missed** | ✅ | `cufa slack backfill` → `written=5 duplicate=3`. The lost `#q-and-a` message appeared with the right timestamp, and its Q&A question row was created |
| Backfill is idempotent | ✅ | Second run: `messages_read=1 written=0 duplicate=1` |
| Per-channel watermarks advance | ✅ | `cufa slack channels`: `#general backfilled through 1789406580.507869`, `#q-and-a … 1789406537.115679`, and `—` for the two channels the bot is not in |
| Channels the bot is not in are reported, not hidden | ✅ | `error: C0BSA4D1FKR: not_in_channel`, `error: C0BSV5U0CKW: not_in_channel` |
| "Is it alive" probe | ✅ | `select max(received_at) from slack_event` |
| A dead bot is visible | ✅ | `load_run` row `source=slack_bot status=running` with no newer run |

---

## Analytics on the real data

| Feature | Result | Proof |
|---|---|---|
| `cufa slack report` | ✅ | Per-fellow msgs/replies/reacts/channels/days, plus a `— (not on roster)` row |
| `cufa slack insights` | ✅ | Reply graph, "nobody has replied to these fellows", mentions given (*"received is recorded, never ranked"*), huddles, canvases, cohort-wide emoji (*"never per person"*) |
| Attention index 0–100, components beside it | ✅ | Every row shows attendance, exit tickets, messages, outreach flag and named flags |
| Help checkbox and scores excluded from the index | ✅ | No query in `engagement.py` references `help_request` or a score column |
| No model reads a fellow's words | ✅ | Retention is keyword matching against `config/retention_rubric.json`, whose own comment says so. The only AI path is passphrase adjudication, which sees a passphrase |
| **Identity resolves at read time** | ✅ | An event ingested at 16:36:40 resolved to `CU-2604` after an alias created at 16:41:32, **no re-ingest**. `v_fellow_email` is a plain view |
| One address belongs to one fellow | ✅ | `⚠️ alias faro.fallowmere@example.invalid is the primary address of fellow CU-2605` |
| Zoom transcript → speaking share | ✅ | 6 turns from a synthetic `.vtt`: Ardith 67.2% (2 turns, 51 words), Bexley 15.3%, Corvin 6.6%. **"Not A Real Fellow" listed as `[unmatched]`** and called out separately, not guessed at |
| Funnel | ✅ | accepted 20 → joined Slack 20 → first message 16 → first check-in 20 → completed 1, with median gaps |
| HTML report | ✅ | 76 697 bytes, self-contained, **0 addresses**, **0 `help_request` references**, renders with no console errors |
| Staff dashboard | ✅ | Attention index with per-row *mark reached out*, most active, badges and ranks, assignments with score entry, funnel, per-source freshness, Export CSV (0 addresses) |
| **Overall attendance figure** | ❌ | `/report` and `/dashboard` printed **235%**, `out/report.html` printed 21%, same database. See F-03 |
| Test suite after everything | ✅ | `597 passed` |

---

## New findings from this run

These are additions to `FINDINGS.md`.

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

## Getting into the staff console (added this run)

The hosted console had no working door: no Google client is registered for the
Vercel origin, and the dev bypass is off whenever an allowlist exists. So one
shared password was added, on purpose kept weaker and narrower than Google.

| Feature | Result | Proof |
|---|---|---|
| Password door absent unless configured | ✅ | `passwordSignin: false`; `POST /signin/password` 403s, empty guess included |
| Right password signs in (production) | ✅ | `303 → /dashboard`, then `200` with real cohort data |
| Wrong password does not | ✅ | `403`, no cookie, screens still closed |
| **Shared password never opens `/help-requests`** | ✅ | `403` on production with a valid password session, while `/dashboard` stays `200`. The session's identity is on no allowlist by construction |
| Clearing the password signs everyone out | ✅ | Re-checked on every request, not at cookie expiry |
| Constant-time comparison | ✅ | `hmac.compare_digest` |
| `/admin-dashboard` hands staff the link | ✅ | Registered in Slack; staff-only; carries the URL and not the password |

Known weakness, stated rather than buried: everyone who uses this password signs
in as the same nobody, so nothing records *who* looked at a fellow's page, and
nothing rate-limits guessing beyond the host. Registering a Google OAuth client
for the deployed origin is the fix; this is the stopgap that makes the dashboard
reachable meanwhile.

---

## Where it runs (added this run)

| Feature | Result | Proof |
|---|---|---|
| `POST /bot/cron/tick`, bearer-authenticated | ✅ | 503 with no secret, 403 on a wrong one, runs on the right one |
| Two ticks never overlap | ✅ | advisory lock; the second returns `skipped` and does no work |
| Console and bot from one deployment | ✅ | console at `/`, bot mounted at `/bot`, one secret, one database |
| A real tick on Vercel against Supabase | ✅ | `synced=true, alerts=1, errors=[]` in 0.59s |
| Slack URL verification, live and signed | ✅ | challenge echoed; a forged signature gets 401 |
| `pg_cron` fires every minute | ✅ | the dashboard's `slack_sync` stamp advanced once a minute, checked against a wall clock |
| Cron token kept out of `cron.job.command` | ✅ | stored in Vault, read via `decrypted_secrets` |
| Slack app moved off Socket Mode | ✅ | socket off, events + interactivity URLs **Verified ✓** by Slack, 21 of 21 commands carrying the URL, app reinstalled, bot token unchanged |
| A real slash command answered from Vercel | ✅ | `/help` in #cif-staff, answered by the deployment over HTTP — minutes after Slackbot had been saying "the app did not respond" |

Known limits: Slack allows 3 seconds to ack a slash command and a warm
invocation uses 0.7–1.6s of that, so a cold start could exceed it
(`CUFA_SLACK_ACK_FIRST=1` is the lever). And `load_run` no longer distinguishes
a reclaimed instance from a crash — see F-14.

---

## Verdict

Would this survive a real cohort? The recording half, yes. The privacy claims are
the strongest part of the system and they hold up when you push on them rather
than read them: no message text anywhere, DM history unreadable with the bot's own
token, no address in any log, page or post, and a two-nudge cap that the database
enforces even if the code regresses. Read-time identity genuinely re-attributes
history without a re-ingest. Backfill genuinely recovered what a dropped socket
lost.

The sending half is where I would not go live yet. Polls have never worked against
Slack. The bot counts its own messages as participation, so the numbers drift
upward the more it talks. The headline attendance figure printed 235%. And the
manifest that the docs tell you to paste does not import, which is why this
workspace has one slash command instead of twenty.

**What I would fix first:** F-12 and F-09, in that order — they are each a few
lines, and one of them is silently corrupting the data the whole product exists to
produce. Then F-03, then a property test asserting `0 <= attendance rate <= 1`,
which would have caught F-03 before any of this.
