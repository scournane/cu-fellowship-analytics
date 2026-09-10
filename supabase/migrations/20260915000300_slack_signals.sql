-- More kinds of Slack participation, same observation stream.
--
-- Huddle joins and leaves, canvas edits and comments, bot-run poll votes, and
-- who was @-mentioned in a message all land in `slack_event`, alongside
-- messages and reactions, rather than in tables of their own. One immutable
-- stream means one idempotency key, one identity resolution, one load_run
-- provenance and one immutability trigger — splitting the new kinds out would
-- have meant re-implementing all four.
--
-- Two invariants of the original table are RELAXED here, deliberately and
-- narrowly. A huddle event carries no channel (Slack reports the huddle, not
-- where it started), and a canvas edit carries no editor (Slack's file_change
-- says only that the file changed). Recording "a canvas was edited" with the
-- editor honestly NULL is better than attributing it to the canvas owner,
-- which would be a guess presented as a fact. The check constraints below say
-- exactly which event types may leave which column empty; nothing else may.
--
-- What is deliberately NOT here, because the safeguarding rules forbid it:
--   * no per-person reaction table: which emoji a cohort uses is a mood signal
--     for the cohort; which emoji one fellow uses is surveillance.
--   * no "mentions received" or "replies received" count column or view:
--     received recognition is recorded on the rows (the mentions array, the
--     thread parent) and is used only to find people NOBODY talks to. It is
--     never ranked. See ADR-028 and ADR-033.

-- ---------------------------------------------------------------------------
-- slack_event — new kinds of act
-- ---------------------------------------------------------------------------

alter table slack_event drop constraint if exists slack_event_event_type_check;
alter table slack_event
    add constraint slack_event_event_type_check
    check (event_type in (
        'message', 'message_changed', 'message_deleted',
        'reaction_added', 'reaction_removed',
        'member_joined_channel', 'member_left_channel',
        'huddle_joined', 'huddle_left',
        'canvas_created', 'canvas_edited', 'canvas_shared', 'canvas_comment',
        'poll_vote'
    ));

-- A huddle is reported per user with no channel, and a canvas is a file, not
-- a message: creating, editing or commenting on one happens nowhere in
-- particular. Only a canvas SHARE names a channel. Everything else must.
alter table slack_event alter column channel_id drop not null;
alter table slack_event drop constraint if exists slack_event_channel_required;
alter table slack_event
    add constraint slack_event_channel_required
    check (channel_id is not null or event_type in (
        'huddle_joined', 'huddle_left', 'canvas_created', 'canvas_edited', 'canvas_comment'
    ));

-- A canvas edit names no editor. Everything else must name who acted.
alter table slack_event alter column slack_user_id drop not null;
alter table slack_event drop constraint if exists slack_event_actor_required;
alter table slack_event
    add constraint slack_event_actor_required
    check (slack_user_id is not null or event_type = 'canvas_edited');

alter table slack_event add column if not exists call_id     text;
alter table slack_event add column if not exists file_id     text;
alter table slack_event add column if not exists poll_id     uuid;
alter table slack_event add column if not exists poll_choice text;
-- Slack user ids that appeared as <@U…> in a message, in order, duplicates
-- kept. Extracted at parse time from text that is then discarded (ADR-031),
-- so the who-was-mentioned fact survives the text not being stored.
alter table slack_event add column if not exists mentions    text[];

comment on column slack_event.call_id is
    'The huddle (Slack "call") a huddle_joined/huddle_left row refers to. Slack '
    'does not say which channel the huddle belongs to, so channel_id is NULL.';
comment on column slack_event.mentions is
    'User ids @-mentioned in the message, extracted before the text is dropped. '
    'Reported as "mentions GIVEN" per author. Never summed per mentioned person: '
    'that is recognition received, and it is recorded so that absence can be '
    'noticed, not so that presence can be ranked.';

create index if not exists slack_event_thread_idx on slack_event (team_id, channel_id, thread_ts)
    where thread_ts is not null;
create index if not exists slack_event_poll_idx on slack_event (poll_id) where poll_id is not null;
create index if not exists slack_event_mentions_idx on slack_event using gin (mentions)
    where mentions is not null;

-- ---------------------------------------------------------------------------
-- slack_file — is this file a canvas? Cached so one edit costs one files.info.
-- ---------------------------------------------------------------------------

create table if not exists slack_file (
    team_id    text        not null references slack_workspace (team_id) on delete cascade,
    file_id    text        not null,
    filetype   text,
    is_canvas  boolean     not null default false,
    owner_id   text,
    fetched_at timestamptz not null default now(),
    primary key (team_id, file_id)
);

comment on table slack_file is
    'The one fact needed about a file: whether it is a canvas. No title, no '
    'content, no link — the title of a canvas is content.';

-- ---------------------------------------------------------------------------
-- slack_poll — polls the bot itself posted. Question and options are
-- staff-authored, so storing them is not storing fellows'' words.
-- ---------------------------------------------------------------------------

create table if not exists slack_poll (
    poll_id     uuid        primary key default gen_random_uuid(),
    team_id     text        not null references slack_workspace (team_id) on delete restrict,
    channel_id  text        not null,
    message_ts  text,
    question    text        not null,
    options     jsonb       not null,
    created_by  text,
    created_at  timestamptz not null default now(),
    closed_at   timestamptz
);

comment on table slack_poll is
    'A poll the bot posted, with its options. Votes are slack_event rows of '
    'type poll_vote; the latest vote per person per poll is the one that '
    'counts. Results are reported as option totals and a participation count. '
    'Who voted for what is recorded — it is participation — and is never '
    'listed.';

-- ---------------------------------------------------------------------------
-- The reply graph, at read time. A reply row joined to its parent row.
-- ---------------------------------------------------------------------------

create or replace view slack_reply_edge as
select
    w.cohort_id,
    r.team_id,
    r.channel_id,
    r.slack_user_id                                  as replier_user_id,
    r.user_email                                     as replier_email,
    p.slack_user_id                                  as parent_user_id,
    p.user_email                                     as parent_email,
    r.event_time_utc
from slack_event r
join slack_event p
  on p.team_id = r.team_id
 and p.channel_id = r.channel_id
 and p.event_type = 'message'
 and p.message_ts = r.thread_ts
join slack_workspace w on w.team_id = r.team_id
where r.event_type = 'message'
  and r.is_thread_reply
  and r.slack_user_id is distinct from p.slack_user_id;

comment on view slack_reply_edge is
    'One row per thread reply that answered somebody else. The graph this '
    'builds is read for who nobody replies to, not for who gets replied to '
    'most: an unranked absence, never a ranked presence.';

alter table slack_file enable row level security;
alter table slack_poll enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'slack_file'
          and policyname = 'slack_file_read_todo'
    ) then
        create policy slack_file_read_todo on slack_file
            for select to authenticated using (false);  -- TODO(access)
    end if;
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'slack_poll'
          and policyname = 'slack_poll_read_todo'
    ) then
        create policy slack_poll_read_todo on slack_poll
            for select to authenticated using (false);  -- TODO(access)
    end if;
end $$;
