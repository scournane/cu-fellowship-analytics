-- Outbound Slack automations: session and assignment reminders, Part B nudges,
-- weekly digests, and session-start agendas.
--
-- Delivery history is first-class data. The scheduler is intentionally safe to
-- run more than once (or in two processes): a stable dedupe_key is claimed
-- before Slack is called, and a partial unique index makes a third Part B nudge
-- impossible even if application code regresses.

alter table fellow
    add column if not exists timezone text;

comment on column fellow.timezone is
    'IANA timezone used for DM quiet hours and local deadline rendering. NULL '
    'falls back to CUFA_DEFAULT_FELLOW_TIMEZONE until the fellow sets one.';

alter table "session"
    add column if not exists zoom_url text;

alter table "session"
    add column if not exists agenda text;

alter table "session"
    add column if not exists slack_channel_id text;

alter table session_form
    add column if not exists last_successful_poll_at timestamptz;

comment on column "session".zoom_url is
    'Join URL included in every automated session reminder and weekly digest.';

comment on column "session".agenda is
    'Staff-authored agenda posted by the bot when the session begins.';

comment on column "session".slack_channel_id is
    'Optional per-session destination for the agenda. Falls back to '
    'CUFA_SLACK_ANNOUNCEMENT_CHANNEL.';

comment on column session_form.last_successful_poll_at is
    'Advanced only after every response page is consumed successfully. Part B '
    'nudges use this rather than last_polled_at, which also records failures.';

create table if not exists assignment (
    assignment_id       uuid        primary key default gen_random_uuid(),
    cohort_id           text        not null references cohort (cohort_id) on delete restrict,
    title               text        not null,
    description         text,
    url                 text,
    due_at_local        timestamp   not null,
    timezone            text        not null,
    due_at_utc          timestamptz not null,
    status              text        not null default 'active'
                        check (status in ('active', 'cancelled')),
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists assignment_cohort_due_idx
    on assignment (cohort_id, due_at_utc);

comment on table assignment is
    'A cohort-wide deliverable. Local and UTC due times are both retained for '
    'the same auditability guarantee as session scheduling.';

create table if not exists fellow_reminder_preference (
    fellow_id          text        primary key references fellow (fellow_id) on delete cascade,
    mode               text        not null default 'all'
                       check (mode in ('all', 'fewer', 'later', 'none')),
    timezone           text,
    quiet_start_local  time        not null default time '21:00',
    quiet_end_local    time        not null default time '08:00',
    updated_at         timestamptz not null default now()
);

comment on table fellow_reminder_preference is
    'Fellow-controlled DM cadence, timezone override, and overnight quiet '
    'hours. Channel posts such as agendas are intentionally unaffected.';

create table if not exists bot_delivery (
    delivery_id       uuid        primary key default gen_random_uuid(),
    dedupe_key        text        not null unique,
    kind              text        not null check (kind in (
                                      'session_reminder', 'assignment_reminder',
                                      'part_b_nudge', 'weekly_digest', 'session_agenda')),
    team_id           text        not null references slack_workspace (team_id) on delete restrict,
    fellow_id         text        references fellow (fellow_id) on delete restrict,
    session_id        uuid        references "session" (session_id) on delete restrict,
    assignment_id     uuid        references assignment (assignment_id) on delete restrict,
    nudge_number      smallint,
    target_channel    text,
    scheduled_for_utc timestamptz not null,
    status            text        not null default 'pending'
                                  check (status in ('pending', 'sent', 'failed')),
    attempt_count     integer     not null default 1 check (attempt_count between 1 and 3),
    attempted_at      timestamptz not null default now(),
    delivered_at      timestamptz,
    slack_ts          text,
    error             text,
    constraint bot_delivery_nudge_number_valid check (
        (kind = 'part_b_nudge' and nudge_number between 1 and 2
         and fellow_id is not null and session_id is not null)
        or (kind <> 'part_b_nudge' and nudge_number is null)
    )
);

-- This is independent of dedupe_key. Even a future implementation that builds
-- the wrong key cannot turn a third message into an allowed row.
create unique index if not exists bot_delivery_two_nudges_only
    on bot_delivery (fellow_id, session_id, nudge_number)
    where kind = 'part_b_nudge';

create index if not exists bot_delivery_status_attempt_idx
    on bot_delivery (status, attempted_at);

create index if not exists bot_delivery_fellow_idx
    on bot_delivery (fellow_id, delivered_at);

comment on table bot_delivery is
    'Idempotency and audit trail for outbound Slack messages. Bodies are not '
    'stored; only routing, timing, outcome, and Slack timestamp are retained.';

-- These tables are server-side operational data. Enabling RLS with no browser
-- policies keeps them closed through PostgREST; the direct bot connection is
-- the trusted path and the database owner used locally bypasses RLS.
alter table assignment enable row level security;
alter table fellow_reminder_preference enable row level security;
alter table bot_delivery enable row level security;
