-- Alerting state: the two rows of bookkeeping that turn "something is wrong"
-- into "somebody was told once".
--
-- Why this needs the database at all. On the long-lived process a module-level
-- dict would have done: one process, one memory, one "have I already said
-- this". Serverless took that away. Every tick may run in a different instance
-- that has never seen the previous one, so any suppression held in memory
-- suppresses nothing — the dead-man switch would post every minute for the
-- whole outage, and a failing step would post every minute forever. The
-- suppression has to live where both instances can see it, and the only such
-- place is Postgres.
--
-- Shaped after `digest_log` and `bot_delivery`: the row is CLAIMED before the
-- message is sent, so two ticks racing produce one post rather than two, and a
-- crash mid-post loses a message rather than repeating one. The difference from
-- `digest_log` is that these keys RECUR — an outage in October and another in
-- November are two alerts, not one — so this is a state machine per key rather
-- than a permanent "already posted" mark, and `digest_log`'s unique (kind,
-- target_key) would have wedged the second outage shut forever.

-- ---------------------------------------------------------------------------
-- ops_alert — one row per thing that can be wrong.
--
-- alert_key is the identity of the CONDITION, not of the occurrence:
--   'liveness'              the dead-man switch
--   'tick_error:<hash>'     one fingerprinted tick step failure
--   'tick_gap:<timestamp>'  a specific gap in the scheduler's minute hand,
--                           keyed by when it started so each gap alerts once
--                           and the primary key does the deduplication.
-- ---------------------------------------------------------------------------

create table if not exists ops_alert (
    alert_key      text        primary key,
    -- 'firing' means the condition is currently true AND the channel has been
    -- told. 'ok' means it is not true; a key that was firing and is now ok has
    -- had its recovery notice posted.
    state          text        not null default 'ok' check (state in ('ok', 'firing')),
    -- Redacted, always: cufa.slack.alerting scrubs email addresses before
    -- anything reaches this column or Slack. Alerts are operational, and the
    -- rest of this codebase keeps addresses out of every post.
    detail         text,
    first_seen_at  timestamptz not null default now(),
    last_seen_at   timestamptz not null default now(),
    -- How many times the condition has been observed since it started firing.
    -- This is what lets a repeating error say it is repeating instead of
    -- repeating itself.
    occurrences    integer     not null default 1,
    last_posted_at timestamptz,
    posted_count   integer     not null default 0
);

create index if not exists ops_alert_state_idx on ops_alert (state, last_seen_at);

comment on table ops_alert is
    'Alert suppression state, one row per condition. Written before the Slack '
    'post, like digest_log, so a racing tick posts once. Holds no personal '
    'data: detail is scrubbed of email addresses before it is stored.';

comment on column ops_alert.occurrences is
    'Observations since the condition started firing. A repeating error is '
    'reported as "still failing, seen N times" rather than posted N times.';

-- ---------------------------------------------------------------------------
-- ops_heartbeat — a named pulse, last beat wins.
--
-- 'tick'          the last tick that completed. The gap between two beats is
--                 the only in-band evidence that the external scheduler
--                 stopped calling, and it is why a laptop that wakes up (or a
--                 pg_cron job somebody unscheduled and rescheduled) can say
--                 out loud how long it was gone.
-- 'auto_backfill' the last automatic backfill, so its cadence survives the
--                 instance that set it. An interval held in process memory
--                 would mean every cold start backfills again.
--
-- Deliberately NOT load_run. A `running` load_run row is not evidence of
-- anything here: every cold start opens one and a reclaimed instance leaves it
-- open, so the table accumulates rows that look exactly like crashes and are
-- not (FINDINGS F-14, RUNBOOK section 9). A heartbeat row that is only ever
-- written on success cannot lie that way.
-- ---------------------------------------------------------------------------

create table if not exists ops_heartbeat (
    name    text        primary key,
    beat_at timestamptz not null default now(),
    detail  text
);

comment on table ops_heartbeat is
    'Named "this completed at" markers for the scheduled work. Replaces '
    'load_run as the liveness signal on serverless, where a stale running '
    'load_run row means an idle instance was collected, not a crash.';

-- ---------------------------------------------------------------------------
-- Row Level Security: the same shape as every other table here. Neither table
-- holds fellow data, but the default in this schema is deny and an exception
-- would have to be argued for rather than assumed.
-- ---------------------------------------------------------------------------

alter table ops_alert     enable row level security;
alter table ops_heartbeat enable row level security;

do $$
declare
    t text;
begin
    foreach t in array array['ops_alert', 'ops_heartbeat'] loop
        if not exists (
            select 1 from pg_policies
             where schemaname = 'public' and tablename = t
               and policyname = t || '_read_todo'
        ) then
            execute format(
                'create policy %I on %I for select to authenticated using (false)',
                t || '_read_todo', t
            );  -- TODO(access): replace `false` with CU's rule.
        end if;
    end loop;
end
$$;
