# Running the console and the bot on Vercel

The problem this solves: the bot used to be a process on somebody's laptop.
When the laptop slept, reminders stopped, and nobody found out until a fellow
did not get one. Tying it to a host that is always up means giving up the
long-lived process, and that costs something specific. This is what it costs
and what replaces it.

## The shape

One deployment serves both halves.

| Path | What answers |
|---|---|
| `/` | the staff console and the fellow `/me/<token>` pages |
| `/bot/slack/events` | Slack's request URL: slash commands, events, buttons |
| `/bot/cron/tick` | the scheduler's minute hand |
| `/bot/health`, `/bot/stats` | the probes |

They share a deployment because they share two secrets and a database. The
`/me/<token>` link the bot mints is verified by the console with the same
`CUFA_CONSOLE_SECRET`, and both read and write the same Postgres. Split them
and the day somebody rotates one of those, half the links stop opening and the
error says nothing useful.

## What serverless takes away

`AutomationLoop` is a thread inside a process that never exits. It is what
sends the 24h, 1h and 10m reminders, the badge DMs, the welcome DMs, the
Monday digest, and it is what pulls Slack every minute. Here the process *is*
the request, so that thread cannot exist.

So `CUFA_SLACK_AUTOMATIONS=0` on this deployment, and something outside calls
`POST /bot/cron/tick` once a minute instead. The route runs exactly one
`tick()` — the same function `cufa slack tick` runs from cron — and answers
with what it did.

**Vercel's own cron cannot do this on a Hobby plan**: minimum interval is once
per day, and it fires anywhere inside a one-hour window. A reminder that says
"starts in 10 minutes" cannot come out of that. Pro gets per-minute cron; until
then the scheduler lives elsewhere.

## The scheduler, on Supabase

The database is already Supabase, `pg_cron` runs on time, and it costs nothing
extra.

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

-- The token goes in Vault, not inline: cron.job.command is readable by anyone
-- who can see the cron schema, and this token fires DMs to a cohort.
select vault.create_secret('<CUFA_CRON_SECRET>', 'cufa_cron_secret',
       'Bearer token for POST /bot/cron/tick');

select cron.schedule('cufa-tick', '* * * * *', $job$
  select net.http_post(
    url := 'https://<your-deployment>/bot/cron/tick',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (
        select decrypted_secret from vault.decrypted_secrets
         where name = 'cufa_cron_secret')),
    body := '{}'::jsonb,
    timeout_milliseconds := 25000);
$job$);
```

Is it running?

```sql
select status, return_message, start_time
  from cron.job_run_details where jobid = (
    select jobid from cron.job where jobname = 'cufa-tick')
 order by start_time desc limit 10;

select status_code, content from net._http_response order by id desc limit 5;
```

To stop it: `select cron.unschedule('cufa-tick');`

Two ticks never run at once. The route takes a Postgres advisory lock and a
second caller leaves quietly, so a slow tick does not get overlapped by the
next minute's.

## Deploying

```bash
python tasks.py frontend      # the console's bundle, or the build refuses
deploy/vercel/build.sh        # vendors src/cufa next to the entrypoint
cd deploy/vercel && vercel deploy --prod
```

From a browser instead: **Actions → deploy → Run workflow** runs the same three
steps on a runner (`.github/workflows/deploy.yml`). It needs the repository
secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID`, and it never
touches the database — apply any new migration in the Supabase SQL editor
first.

`api/cufa/` is generated and gitignored. Vercel's Python builder installs
`requirements.txt` and uploads the folder; it does not `pip install` the
repository around it, which is why the package is copied in.

## Settings that must be set here

Everything from `.env` that the bot reads, plus:

| Variable | Value | Why |
|---|---|---|
| `CUFA_SLACK_AUTOMATIONS` | `0` | there is no process for the loop to live in |
| `CUFA_CRON_SECRET` | a long random string | the tick route is shut without it |
| `CUFA_DATABASE_URL` | the hosted database | not the local Supabase stack |
| `CUFA_CONSOLE_SECRET` | same everywhere | signs `/me/<token>` and the session cookie |
| `CUFA_PUBLIC_BASE_URL` | this deployment | the links the bot hands out |

`SLACK_APP_TOKEN` is not needed: that is the Socket Mode token, and Socket Mode
is what this replaces.

## Things worth knowing before they surprise you

**Slack allows 3 seconds to acknowledge a slash command.** Measured here, a
warm invocation answers in about 0.7 to 1.6 seconds. A cold start adds the
Python import of FastAPI, Bolt and psycopg on top of that. If commands start
showing `operation_timeout` in Slack, set `CUFA_SLACK_ACK_FIRST=1`: Bolt then
acks immediately and replies through `response_url`.

**Stale `running` rows in `load_run`.** Each cold start opens one for
provenance and closes it at exit. An instance the platform reclaims without
warning leaves it open. RUNBOOK section 7 used to read a stale `running` row as
"the bot crashed"; under serverless it usually means an idle instance was
collected, which is ordinary. Judge liveness by `cron.job_run_details` instead.
