# Run it tomorrow

The shortest correct sequence to bring the whole thing up on a machine that has
none of it. Written after doing exactly this once, so the order is the order
that worked and the notes are the things that actually went wrong.

---

## 0 · Before anything

```
git clone https://github.com/scournane/cu-fellowship-analytics
cd cu-fellowship-analytics
git checkout main          # ← do not skip this
```

**The `git checkout main` is not optional.** The repo's remote `HEAD` points at
`claude/civics-unplugged-analytics-tool-6nfb9b`, so a plain clone lands you on a
branch with no Slack bot in it — no `src/cufa/slack/`, no
`docs/setup/slack-bot.md`, no `demo-slack` targets. Nothing errors; the demo even
passes. You just quietly do not have the thing you came for. (F-01)

Check you are where you think you are:

```
git rev-parse --abbrev-ref HEAD     # main
ls docs/setup/slack-bot.md          # must exist
```

## 1 · Prerequisites

| Need | Check | If missing |
|---|---|---|
| Python 3.11+ | `python3 --version` | your platform's installer |
| Docker, **running** | `docker info` | Docker Desktop, and actually start it |
| Supabase CLI | `supabase --version` | `npm install -g supabase` |
| Node (optional frontend) | `node -v` | any 20+ |

On the Supabase CLI: the docs point at the GitHub releases download. If you are
behind a proxy or in a sandbox that answers `403` on the GitHub API, that path
gives you an empty URL and a baffling `curl: (3) URL rejected`. `npm install -g
supabase` just works. (F-02)

If you are running in a container where the Docker daemon is not already up,
start it detached or it dies between shell invocations:

```
sudo setsid bash -c 'nohup dockerd >/var/log/dockerd.log 2>&1 &'
```

## 2 · Setup

```
python tasks.py doctor      # tells you what is missing, per-OS
python tasks.py setup       # .venv, pip install -e ".[dev]", console bundle
cp .env.example .env
.venv/bin/python -m cufa.crypto keygen     # paste into CUFA_ENCRYPTION_KEY
python tasks.py doctor      # all six rows ok
```

`doctor` failures you should expect on a clean machine, in this order:
`MISS Dependencies` (fixed by `setup`), `MISS Console bundle` (also fixed by
`setup`), and `Docker not running` if you forgot step 1. Nothing else fired.

There is no `requirements.txt`. Deps live in `pyproject.toml`; the manual
equivalent is `pip install -e ".[dev]"`, and in PowerShell the quotes are
mandatory.

## 3 · Prove it works, no accounts needed

```
python tasks.py demo            # ~2 min, 36 acceptance checks
python tasks.py demo-again      # same numbers, proves idempotency
python tasks.py test            # 597 tests, ~46 s, no network
python tasks.py report          # out/report.html
```

Two lines in `demo` look like errors and are not. Confirm you saw both:

- `blocked, as designed: emailCollectionType is not VERIFIED yet` — twice, once
  per form part
- `blocked, as designed: no generic question was substituted` — the
  teacher-question week refusing rather than inventing a question

Ports in play: Postgres **64322**, Studio **64323**, API **64321**, bot **3000**,
fake Slack **3001**, console **8000**. If 3000 or 3001 is taken the demo refuses
to share rather than proving nothing; move with
`SLACK_BOT_PORT=3100 FAKE_SLACK_PORT=3101`.

Migrations apply on `supabase db reset`, **not** on `supabase start`. Schema
looks stale → `python tasks.py db-reset`.

## 4 · The `.env` values that actually mattered

Of the 96 lines in `.env.example`, these are the ones that changed an outcome.
Everything else I left at its default and never thought about again.

| Key | Set it to | What happens if you do not |
|---|---|---|
| `CUFA_ENCRYPTION_KEY` | output of `cufa.crypto keygen` | `cufa google connect` refuses to store a token |
| `CUFA_SLACK_COHORT` | `cu-2026-test`, **never** `demo` | the test run writes into the demo cohort |
| `SLACK_API_BASE_URL` | **blank** | the demo sets it at the fake server; leaked into a real run the bot talks to nothing. `tasks.py slack-bot` strips it deliberately |
| `SLACK_BOT_TOKEN` | `xoxb-…` | nothing works |
| `SLACK_APP_TOKEN` | `xapp-…` | falls back to HTTP mode, which needs a public URL |
| `SLACK_SIGNING_SECRET` | from Basic Information | HTTP mode refuses every delivery |
| `CUFA_SLACK_STAFF_CHANNEL` | id of a **private** channel the bot is in | `/digest`, session summaries, roster alerts and `/checkin` pings all silently go nowhere. `/checkin` tells you; the scheduled ones do not |
| `CUFA_SLACK_ADMINS` | your email | every staff command answers `⛔ … is a staff command` |
| `CUFA_SLACK_QA_CHANNELS` | `q-and-a` | no "asked before" pointers, no Q&A summaries, nothing errors |
| `CUFA_SLACK_QUIET_START` / `_END` | a window **outside** your test session | the 21:00–08:00 default silently swallows DMs and you conclude reminders are broken |
| `CUFA_HELP_ALLOWLIST` | your email, if you want `/help-requests` | the console's help screen 403s and falls back to whoever is in `config/help_routing.json` (by default `adiah@civicsunplugged.org`) |
| `CUFA_LOG_LEVEL` | leave at `INFO` | `DEBUG` is the only level that prints raw addresses |

## 5 · The real Slack app

Manifest is in `docs/setup/slack-bot.md` → *Creating the real Slack app*. Paste
it verbatim. Add `files:read` for canvases. **Never add `im:history` or
`mpim:history`** — the boundary that the bot can send DMs but cannot read them
is enforced by that omission and nothing else.

Then, in order:

1. Create from manifest → Install to Workspace → copy `xoxb-…`
2. Basic Information → App-Level Tokens → scope `connections:write` → `xapp-…`
3. Basic Information → Signing Secret
4. Create `#general`, `#announcements`, `#q-and-a` and a private staff channel,
   and run `/invite @<botname>` in **every one**. A channel the bot is not in
   produces nothing, silently. This is the step that costs people an afternoon.
5. Load a roster containing your own Slack email, with a `timezone` column:
   `fellow_id, full_name, primary_email, status, timezone`, then
   `cufa load-roster --csv <path> --cohort cu-2026-test`
6. `make db-up`, then `cufa slack doctor` — **must exit 0** before you start the
   bot. Every failure it names is silent once the bot is running, which is the
   entire reason the check exists.
7. `python tasks.py slack-bot` (doctor, then Socket Mode)

Socket Mode needs no public URL and is the right default. HTTP mode needs a
public Request URL at `/slack/events` and the same URL on `/cufa-reminders`.

## 6 · Gotchas worth knowing in advance

- **`--as` goes first or last, never in the middle.** `cufa slack cmd /fellow
  --as U123 Ardith` fails with an argparse error. Use `cufa slack cmd --as U123
  /fellow Ardith`. The CLI's own help says so; it is easy to miss.
- **Quote multi-word arguments** exactly as you would in Slack. The command
  parser uses `shlex`, so `/score "Solvathon deck" "Bexley Brambleton" 87`
  works and the unquoted version silently matches the wrong fellow.
- **Pass `--cohort` to everything.** Omitted, it falls back to
  `CUFA_SLACK_COHORT`, and on a demo database that cohort is empty. You get
  `No fellow matches 'Ardith Aldergrove'` for a fellow who plainly exists, and
  `computed=0 new=0` from `cufa slack badges` that looks like a clean run. (F-07)
- **`cufa slack cmd /cufa-reminders` does not work** and says the command does
  not exist. It works fine in Slack; the offline driver has not been taught
  about it. (F-04)
- **`demo-slack-batch` calls `db-reset`.** It will destroy Part A/B data you
  just built. Capture evidence before you run it, or layer the Part A/B pipeline
  on afterwards with the reset stubbed out.
- **The demo guard is correct.** `make demo` refuses over a database that looks
  like a real install. Point `CUFA_DATABASE_URL` at a scratch database rather
  than reaching for `CUFA_DEMO_FORCE=1`.
- **`latency_seconds` is recorded and never interpreted.** A session's first
  submitter reading `0` is expected.
- **A fellow whose Slack profile has no email is recorded with a NULL address,
  not dropped.** That is correct behaviour.

## 7 · The one-line health probe

```sql
select max(received_at) from slack_event;
```

Plus: a `slack_bot` row in `load_run` still `running` with no newer run means
the bot died without stopping cleanly — **on a long-lived process**. On the
serverless deployment it usually means an idle instance was collected, which is
ordinary; see section 9 for what to read instead.

## 8 · Getting into the staff console

The console has three doors, in descending order of how much you should like
them.

1. **Google.** The only one that records *who* opened a fellow's page. Needs
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and the origin registered as a
   redirect URI. Address must be on `CUFA_CONSOLE_ALLOWLIST`.
2. **One shared site password.** `CUFA_CONSOLE_PASSWORD=<passphrase>`. Set it
   and the sign-in page grows a password field; leave it blank and the door does
   not exist. Everyone who uses it signs in as the same nobody, so it never
   opens `/help-requests` — that stays on the email allowlist.
3. **Dev bypass.** Only when `CUFA_FAKE_GOOGLE=1` or no allowlist is set. Not
   for anything reachable from a network.

The deployed instance
(`https://vercel-deploy-scournane-7328.vercel.app`) is on door 2, because no
Google client is registered for that origin yet.

**To rotate or revoke the password:** change or delete
`CUFA_CONSOLE_PASSWORD` and redeploy. It is re-read on every request, so
everyone it let in is signed out on their next click. On Vercel:

```bash
curl -X POST "https://api.vercel.com/v10/projects/<project_id>/env?upsert=true" \
  -H "Authorization: Bearer $VERCEL_TOKEN" -H "Content-Type: application/json" \
  -d '{"key":"CUFA_CONSOLE_PASSWORD","value":"<new>","type":"encrypted",
       "target":["production","preview","development"]}'
vercel deploy --prod
```

**`/admin-dashboard`** in Slack replies with the console address, to staff only.
It never sends the password — a Slack message is searchable, exportable and
forwardable, so the password travels some other way.

**What this costs you:** no record of who read what, and no rate limit on
guessing beyond whatever is in front of the console. Register a Google OAuth
client for the deployed origin when there is time, and unset the password.


## 9 · Where the bot actually runs

Two deployments are possible and they fail differently.

**A long-lived process** (`cufa slack socket`, or `cufa slack serve`). One
process holds the Slack connection and runs `AutomationLoop`, which sends
reminders, badges, welcomes and the digest every minute. Simple, and its uptime
is the uptime of whatever machine it is on. A laptop that sleeps is a bot that
stops, silently, and the first symptom is a fellow not getting a reminder.

**Serverless** (`deploy/vercel/`) — what this install runs as of Sep 2026.
Slack posts to `/bot/slack/events`, and
because there is no process to hold a loop, an outside scheduler calls
`/bot/cron/tick` once a minute. Uptime stops depending on anybody's laptop. The
full setup, including the `pg_cron` job, is in `deploy/vercel/README.md`.

### Is it alive?

Not from `load_run` — see section 7. Ask the scheduler:

```sql
select status, return_message, start_time
  from cron.job_run_details
 where jobid = (select jobid from cron.job where jobname = 'cufa-tick')
 order by start_time desc limit 10;
```

Ten rows, all `succeeded`, one a minute: healthy. Gaps, or `failed` rows, mean
the tick is not landing and reminders are not going out. The HTTP side of it:

```sql
select status_code, left(content, 200) from net._http_response
 order by id desc limit 5;
```

A `200` body carries the tick's own counts. Non-200 with `not authorised` means
`CUFA_CRON_SECRET` in Vault and on the host have drifted apart.

### Switching back to a long-lived process

1. Settings → Socket Mode → on. Slack clears the request URLs itself. (Going
   the other way, note that turning Socket Mode *off* silently switches
   Interactivity off too, because it had no URL of its own — that is the one
   that takes the check-in button and poll votes with it.)
2. `select cron.unschedule('cufa-tick');`
3. `CUFA_SLACK_AUTOMATIONS=1` wherever the process runs, and start it.

Do 1 and 2 together. Leaving the cron running while a process also holds the
loop means two things deciding the same reminder is due; `bot_delivery` dedupes
so nobody gets it twice, but you will be reading a log where half the work is
claimed by a machine you forgot about.
