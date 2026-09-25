-- Slack participation events, captured by the bot as they happen.
--
-- Why a bot and not an export: Slack's free plan hides messages after 90 days
-- and deletes them after a year. A bot that records each event on arrival
-- owns a permanent copy from day one, so the participation record cannot
-- evaporate mid-fellowship. (It does nothing for history that predates the
-- bot — that is what `cufa slack backfill` is for, and it is only as good as
-- what Slack still returns.)
--
-- Same shape as `checkin`: an observation is what arrived, exactly as it
-- arrived, and it is never rewritten. The Director of Programs' definition of
-- Slack participation is "sending messages, reacting to messages, etc" — the
-- counts, not the content. So by default **message text is not stored**. What
-- is stored is enough to count and to characterise: length, word count,
-- whether there was a link, whether it was a thread reply. Storing the text of
-- teenagers' casual conversation with each other is a much larger privacy
-- exposure than storing that a conversation happened, and nothing in the
-- definition needs it. CUFA_SLACK_STORE_TEXT=1 turns it on for a workspace
-- whose data owner has decided that.

-- ---------------------------------------------------------------------------
-- load_run.source — the bot and the backfill are two more sources.
-- ---------------------------------------------------------------------------

alter table load_run drop constraint if exists load_run_source_check;
alter table load_run
    add constraint load_run_source_check
    check (source in ('forms_api', 'csv', 'slack_bot', 'slack_backfill'));

-- ---------------------------------------------------------------------------
-- slack_workspace — which cohort a workspace belongs to.
--
-- Slack user ids are workspace-scoped, so everything below is keyed by
-- team_id, and the cohort is attached here rather than to each event.
-- ---------------------------------------------------------------------------

create table if not exists slack_workspace (
    team_id      text        primary key,
    team_name    text,
    cohort_id    text        references cohort (cohort_id) on delete restrict,
    bot_user_id  text,
    connected_at timestamptz not null default now(),
    last_seen_at timestamptz
);

comment on table slack_workspace is
    'One row per Slack workspace the bot has been connected to, and the cohort '
    'its members belong to. Adiah''s cohort definition is "accepted to the CIF '
    'and in the CIF Slack workspace", so this is the boundary, not just a source.';

-- ---------------------------------------------------------------------------
-- slack_user — the identity cache.
--
-- The event stream carries only a workspace-scoped user id. `users.info` turns
-- that into an email (with users:read.email), which is what joins to the
-- roster. Cached so a busy channel does not make one API call per message.
-- ---------------------------------------------------------------------------

create table if not exists slack_user (
    team_id       text        not null references slack_workspace (team_id) on delete cascade,
    slack_user_id text        not null,
    email         text,
    display_name  text,
    real_name     text,
    is_bot        boolean     not null default false,
    is_deleted    boolean     not null default false,
    fetched_at    timestamptz not null default now(),
    primary key (team_id, slack_user_id)
);

create index if not exists slack_user_email_idx on slack_user (lower(email));

comment on column slack_user.email is
    'From users.info profile.email, normalized. NULL when the profile has none '
    '(bots, some guests) or the token lacks users:read.email. Events from such a '
    'user are still recorded — identity never blocks ingest — and they show up '
    'as unattributed in the report rather than vanishing.';

-- ---------------------------------------------------------------------------
-- slack_channel — names for ids, and the backfill watermark.
-- ---------------------------------------------------------------------------

create table if not exists slack_channel (
    team_id               text        not null references slack_workspace (team_id) on delete cascade,
    channel_id            text        not null,
    name                  text,
    is_private            boolean     not null default false,
    is_member             boolean,
    backfilled_through_ts text,
    fetched_at            timestamptz not null default now(),
    primary key (team_id, channel_id)
);

comment on column slack_channel.backfilled_through_ts is
    'The newest message ts a backfill has read for this channel. The next '
    'backfill starts here, so the 90-day window is walked forward rather than '
    're-read from scratch.';

-- ---------------------------------------------------------------------------
-- slack_event — the observations. Immutable.
-- ---------------------------------------------------------------------------

create table if not exists slack_event (
    slack_event_id   uuid        primary key default gen_random_uuid(),

    -- Idempotency key. Slack retries deliveries (X-Slack-Retry-Num), and the
    -- backfill re-reads what the live bot already saw, so the key is built
    -- from what identifies the act, NOT from Slack's event_id (which a
    -- backfilled message does not have):
    --   message:            (team, channel, 'message', ts)
    --   message_changed:    (team, channel, 'message_changed', ts, edited ts)
    --   reaction_added:     (team, channel, 'reaction_added', item ts, user, reaction)
    --   member_joined:      (team, channel, 'member_joined_channel', user, event ts)
    source_event_id  text        not null unique,

    team_id          text        not null references slack_workspace (team_id) on delete restrict,
    event_type       text        not null check (event_type in (
                                     'message', 'message_changed', 'message_deleted',
                                     'reaction_added', 'reaction_removed',
                                     'member_joined_channel', 'member_left_channel')),
    channel_id       text        not null,
    channel_type     text,
    slack_user_id    text        not null,

    -- Resolved at ingest from the slack_user cache. Stored on the row, like
    -- checkin.submitted_email, so the roster join happens at read time and a
    -- corrected roster re-attributes history with no backfill.
    user_email       text,

    message_ts       text,
    thread_ts        text,
    is_thread_reply  boolean     not null default false,

    reaction         text,
    item_user_id     text,

    text_length      integer,
    word_count       integer,
    has_link         boolean,
    has_attachment   boolean,
    -- NULL unless CUFA_SLACK_STORE_TEXT=1. See the header comment.
    text             text,

    event_time_utc   timestamptz not null,
    raw              jsonb       not null default '{}'::jsonb,
    load_id          uuid        references load_run (load_id) on delete set null,
    received_at      timestamptz not null default now()
);

create index if not exists slack_event_team_time_idx    on slack_event (team_id, event_time_utc);
create index if not exists slack_event_channel_time_idx on slack_event (channel_id, event_time_utc);
create index if not exists slack_event_email_idx        on slack_event (lower(user_email));
create index if not exists slack_event_type_idx         on slack_event (event_type);
create index if not exists slack_event_load_idx         on slack_event (load_id);

comment on table slack_event is
    'Immutable observation of one act in Slack: a message, an edit, a deletion, '
    'a reaction, a join. Stores the EMAIL, not a fellow_id. Text is NULL by '
    'default — the participation definition counts acts, it does not read them.';

comment on column slack_event.raw is
    'The event minus its text: type, subtype, channel_type, edited/thread '
    'markers, and the retry header if there was one. Enough to reconstruct why '
    'a row exists, never enough to reconstruct what somebody said.';

-- Invariant 2, enforced the same way as on checkin. Nothing in the application
-- rewrites an observation. A message that was later edited or deleted produces
-- a NEW row of a different type; the original stays.
create or replace function slack_event_reject_mutation() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception
            'slack_event rows are immutable and are never deleted (slack_event_id=%). '
            'A dropped observation is unrecoverable.', old.slack_event_id
            using errcode = 'restrict_violation';
    end if;
    raise exception
        'slack_event rows are immutable (slack_event_id=%)', old.slack_event_id
        using errcode = 'restrict_violation';
end;
$$;

drop trigger if exists slack_event_no_mutation on slack_event;
create trigger slack_event_no_mutation
    before update or delete on slack_event
    for each row execute function slack_event_reject_mutation();

-- ---------------------------------------------------------------------------
-- Read-time views. Counts per fellow per day, joined to the roster by email —
-- the same join every other report makes.
-- ---------------------------------------------------------------------------

create or replace view slack_activity_daily as
select
    w.cohort_id,
    e.team_id,
    f.fellow_id,
    e.user_email,
    (e.event_time_utc at time zone 'UTC')::date          as day_utc,
    count(*) filter (where e.event_type = 'message')     as messages,
    count(*) filter (where e.event_type = 'message'
                       and e.is_thread_reply)            as thread_replies,
    count(*) filter (where e.event_type = 'reaction_added') as reactions_given,
    count(distinct e.channel_id)
        filter (where e.event_type = 'message')          as channels_posted_in
from slack_event e
join slack_workspace w on w.team_id = e.team_id
left join fellow f
       on f.cohort_id = w.cohort_id
      and lower(f.primary_email) = lower(e.user_email)
where e.event_type in ('message', 'reaction_added')
group by w.cohort_id, e.team_id, f.fellow_id, e.user_email, day_utc;

comment on view slack_activity_daily is
    'Acts per person per UTC day. fellow_id is NULL for an address not on the '
    'roster — those rows are kept, not dropped, so the unattributed total is '
    'visible. Reactions RECEIVED are not counted anywhere: the definition is '
    'about what a fellow does, and ranking on received recognition is exactly '
    'the leaderboard the research warns against.';

-- ---------------------------------------------------------------------------
-- Row Level Security. Same stub as every other table holding addresses.
-- ---------------------------------------------------------------------------

alter table slack_event enable row level security;
alter table slack_user  enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'slack_event'
          and policyname = 'slack_event_read_todo'
    ) then
        create policy slack_event_read_todo on slack_event
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'slack_user'
          and policyname = 'slack_user_read_todo'
    ) then
        create policy slack_user_read_todo on slack_user
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;
end
$$;
