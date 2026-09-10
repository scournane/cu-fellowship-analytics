-- The Slack bot, and everything the bot and the staff dashboard need.
--
-- The same rules as the rest of the schema apply:
--
--   * Observations (Slack messages, joins, transcript turns) are written once
--     and never edited. Judgments (badges, interventions, scores) are separate
--     rows with provenance.
--   * Identity resolves at READ time. A Slack message stores a Slack user id;
--     the join to the roster happens in a view, so linking an alias or fixing a
--     roster typo re-attributes history with no backfill.
--   * `help_request` is not read by anything here. Asking for help is never a
--     participation signal, and the engagement metrics this migration enables
--     must never touch that table. The safeguarding tests enforce it for every
--     export path; the new ones are registered in `cufa.report.EXPORT_PATHS`.

-- ---------------------------------------------------------------------------
-- Aliases: a second (or third) address on one roster record.
--
-- Fellows join Slack from a school address and fill in forms from a personal
-- one, or the other way round. CU does not reliably hold both, so aliases are
-- added by staff (a manual merge) or recorded by the bot when a staffer links
-- an unrostered Slack account to a fellow. Either way the roster row is the
-- one identity; an alias is an extra key to it, not a second person.
-- ---------------------------------------------------------------------------

create table if not exists fellow_alias (
    alias_id    uuid        primary key default gen_random_uuid(),
    fellow_id   text        not null references fellow (fellow_id) on delete cascade,
    email       text        not null,
    kind        text        not null default 'other'
                check (kind in ('school', 'personal', 'other')),
    added_by    text,
    note        text,
    created_at  timestamptz not null default now()
);

-- One address maps to at most one fellow. Cohort-agnostic on purpose: an
-- alias is added by a human who has already decided who the address belongs to.
create unique index if not exists fellow_alias_email_uniq on fellow_alias (lower(email));

comment on table fellow_alias is
    'Additional email addresses for a roster record. Identity resolution joins '
    'primary_email UNION aliases, so linking one re-attributes every historical '
    'check-in and Slack message at once.';

-- An alias may not be another fellow''s primary address, and a primary address
-- may not be recorded as somebody else''s alias. Either would make attribution
-- ambiguous rather than merely wrong.
create or replace function fellow_alias_guard() returns trigger
language plpgsql as $$
declare
    clash text;
begin
    select f.fellow_id into clash
      from fellow f
     where lower(f.primary_email) = lower(new.email)
       and f.fellow_id <> new.fellow_id
     limit 1;
    if clash is not null then
        raise exception 'alias % is the primary address of fellow %', new.email, clash
            using errcode = 'unique_violation';
    end if;
    return new;
end
$$;

drop trigger if exists fellow_alias_guard_trg on fellow_alias;
create trigger fellow_alias_guard_trg
    before insert or update on fellow_alias
    for each row execute function fellow_alias_guard();

create or replace function fellow_primary_guard() returns trigger
language plpgsql as $$
declare
    clash text;
begin
    select a.fellow_id into clash
      from fellow_alias a
     where lower(a.email) = lower(new.primary_email)
       and a.fellow_id <> new.fellow_id
     limit 1;
    if clash is not null then
        raise exception 'primary address % is already an alias of fellow %',
            new.primary_email, clash
            using errcode = 'unique_violation';
    end if;
    return new;
end
$$;

drop trigger if exists fellow_primary_guard_trg on fellow;
create trigger fellow_primary_guard_trg
    before insert or update of primary_email on fellow
    for each row execute function fellow_primary_guard();

-- Every address that resolves to a fellow, primary and aliases alike.
create or replace view v_fellow_email
with (security_invoker = true) as
select f.fellow_id, f.cohort_id, lower(f.primary_email) as email, 'primary'::text as kind
  from fellow f
union all
select a.fellow_id, f.cohort_id, lower(a.email) as email, a.kind
  from fellow_alias a
  join fellow f on f.fellow_id = a.fellow_id;

comment on view v_fellow_email is
    'One row per address that belongs to a fellow. Join on this, not on '
    'fellow.primary_email, so aliases count.';

-- Funnel timestamps on the roster row. `accepted_on` is the date CU accepted
-- the fellow (defaults to when the row was loaded); `completed_at` is stamped
-- by staff when the fellow finishes the programme. Slack join, first message
-- and first check-in are derived from observations, never stored here.
alter table fellow add column if not exists accepted_on  date;
alter table fellow add column if not exists completed_at timestamptz;

-- ---------------------------------------------------------------------------
-- The Slack workspace as observed.
-- ---------------------------------------------------------------------------

create table if not exists slack_user (
    slack_user_id  text        primary key,
    team_id        text,
    email          text,
    display_name   text        not null default '',
    real_name      text        not null default '',
    tz             text,
    tz_offset_s    integer,
    is_admin       boolean     not null default false,
    is_bot         boolean     not null default false,
    deleted        boolean     not null default false,
    -- A staff link made by hand. Read-time resolution tries email and aliases
    -- first; this column wins when set, and says who set it.
    linked_fellow_id text      references fellow (fellow_id) on delete set null,
    linked_by      text,
    linked_at      timestamptz,
    joined_at_utc  timestamptz,
    -- When the bot sent its one welcome DM (how it works, the check-in button).
    welcomed_at    timestamptz,
    first_seen_at  timestamptz not null default now(),
    last_seen_at   timestamptz not null default now(),
    raw            jsonb       not null default '{}'::jsonb
);

create index if not exists slack_user_email_idx on slack_user (lower(email));

comment on column slack_user.tz is
    'IANA zone as Slack reports it. Reminders are rendered in it, and never '
    'sent between 22:00 and 07:00 local — nobody wants a nudge at 2am.';

create table if not exists slack_channel (
    channel_id   text        primary key,
    name         text        not null default '',
    is_private   boolean     not null default false,
    is_member    boolean     not null default false,
    -- Staff-only channels are where summaries, alerts and digests go, and
    -- their messages are excluded from every fellow-facing count.
    is_staff     boolean     not null default false,
    tracked      boolean     not null default true,
    last_ts      text,
    synced_at    timestamptz,
    created_at   timestamptz not null default now()
);

-- One row per message the bot can see. Immutable. The text is kept because
-- staff asked for the qualitative side too; it is RLS-locked below.
create table if not exists slack_message (
    message_key     text        primary key,           -- channel_id || ':' || ts
    channel_id      text        not null references slack_channel (channel_id) on delete restrict,
    slack_user_id   text        not null,
    ts              text        not null,
    posted_at_utc   timestamptz not null,
    thread_ts       text,
    is_thread_reply boolean     not null default false,
    subtype         text,
    text            text        not null default '',
    word_count      integer     not null default 0,
    reaction_count  integer     not null default 0,
    ingested_at     timestamptz not null default now()
);

create index if not exists slack_message_user_time_idx on slack_message (slack_user_id, posted_at_utc);
create index if not exists slack_message_time_idx on slack_message (posted_at_utc);

-- Idempotency for anything that arrives as an event rather than a pull.
create table if not exists slack_event_log (
    event_id    text        primary key,
    event_type  text        not null,
    received_at timestamptz not null default now()
);

-- Somebody joined who is not on the roster. Written once per Slack account,
-- resolved by staff: linked to a fellow, marked as staff, or ignored.
create table if not exists roster_alert (
    alert_id      uuid        primary key default gen_random_uuid(),
    slack_user_id text        not null references slack_user (slack_user_id) on delete cascade,
    email         text,
    kind          text        not null default 'unrostered_join'
                  check (kind in ('unrostered_join')),
    posted_at     timestamptz,
    resolved_at   timestamptz,
    resolved_by   text,
    resolution    text        check (resolution is null or resolution in ('linked', 'staff', 'ignored')),
    created_at    timestamptz not null default now(),
    constraint roster_alert_one_per_user unique (slack_user_id, kind),
    constraint roster_alert_resolution_has_provenance check (
        (resolved_at is null and resolved_by is null and resolution is null) or
        (resolved_at is not null and resolved_by is not null and resolution is not null)
    )
);

-- ---------------------------------------------------------------------------
-- Preferences a fellow controls from the bot. Absent row = defaults.
-- ---------------------------------------------------------------------------

create table if not exists slack_preference (
    slack_user_id        text        primary key references slack_user (slack_user_id) on delete cascade,
    session_reminders    integer[]   not null default '{1440,60,10}',
    assignment_reminders integer[]   not null default '{1440,60,10}',
    gamification         boolean     not null default true,
    updated_at           timestamptz not null default now()
);

comment on column slack_preference.session_reminders is
    'Minutes before a session at which this person wants a DM. Empty array = none.';

-- ---------------------------------------------------------------------------
-- Sessions get a Zoom link (put in by an admin through the bot or the console)
-- and a record of when the post-session summary went out.
-- ---------------------------------------------------------------------------

alter table "session" add column if not exists zoom_link         text;
alter table "session" add column if not exists summary_posted_at timestamptz;

-- ---------------------------------------------------------------------------
-- Assignments: the Solvathon, the case brief, and anything else with a due
-- date. Scores are entered by staff by hand; the bot never grades anything.
-- ---------------------------------------------------------------------------

create table if not exists assignment (
    assignment_id uuid        primary key default gen_random_uuid(),
    cohort_id     text        not null references cohort (cohort_id) on delete restrict,
    title         text        not null,
    kind          text        not null default 'other'
                  check (kind in ('solvathon', 'case_brief', 'other')),
    due_at_utc    timestamptz not null,
    -- The zone the due time was typed in, so it can be shown back the same way.
    timezone      text        not null default 'America/New_York',
    link          text,
    max_score     numeric(6, 2),
    created_by    text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists assignment_cohort_due_idx on assignment (cohort_id, due_at_utc);

create table if not exists assignment_submission (
    submission_id    uuid        primary key default gen_random_uuid(),
    assignment_id    uuid        not null references assignment (assignment_id) on delete cascade,
    fellow_id        text        not null references fellow (fellow_id) on delete cascade,
    submitted_at_utc timestamptz,
    score            numeric(6, 2),
    graded_by        text,
    graded_at        timestamptz,
    note             text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    constraint assignment_submission_uniq unique (assignment_id, fellow_id),
    constraint assignment_score_has_provenance check (
        score is null or (graded_by is not null and graded_at is not null)
    )
);

-- ---------------------------------------------------------------------------
-- Reminders actually sent, so a tick that runs twice sends once.
-- ---------------------------------------------------------------------------

create table if not exists reminder_sent (
    target_kind    text        not null check (target_kind in ('session', 'assignment')),
    target_id      uuid        not null,
    slack_user_id  text        not null,
    offset_minutes integer     not null,
    sent_at        timestamptz not null default now(),
    primary key (target_kind, target_id, slack_user_id, offset_minutes)
);

-- ---------------------------------------------------------------------------
-- Interventions: a staff member did something about a fellow. The "has anyone
-- reached out?" boolean the dashboard shows is `exists (kind = 'outreach')`.
-- A check-in request from the bot's button is kind 'check_in_request' and is
-- NOT the Part B help checkbox — that stays in help_request with its own rules.
-- ---------------------------------------------------------------------------

create table if not exists intervention (
    intervention_id uuid        primary key default gen_random_uuid(),
    fellow_id       text        not null references fellow (fellow_id) on delete cascade,
    kind            text        not null
                    check (kind in ('outreach', 'check_in_request', 'note')),
    by_email        text,
    by_slack_user   text,
    note            text,
    source          text        not null default 'console'
                    check (source in ('console', 'slack', 'cli')),
    resolved_at     timestamptz,
    resolved_by     text,
    created_at      timestamptz not null default now()
);

create index if not exists intervention_fellow_idx on intervention (fellow_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Gamification. Awards are append-only facts with the evidence they were
-- computed from; the rules live in code so they can change without a rewrite.
-- ---------------------------------------------------------------------------

create table if not exists badge_award (
    award_id    uuid        primary key default gen_random_uuid(),
    fellow_id   text        not null references fellow (fellow_id) on delete cascade,
    badge_key   text        not null,
    level       integer     not null default 1,
    evidence    jsonb       not null default '{}'::jsonb,
    awarded_at  timestamptz not null default now(),
    notified_at timestamptz,
    constraint badge_award_uniq unique (fellow_id, badge_key, level)
);

-- ---------------------------------------------------------------------------
-- Everything the bot posted on a schedule, so nothing posts twice.
-- ---------------------------------------------------------------------------

create table if not exists digest_log (
    digest_id  uuid        primary key default gen_random_uuid(),
    kind       text        not null check (kind in ('session_summary', 'weekly', 'roster_alert', 'check_in_ping')),
    target_key text        not null,
    channel_id text,
    body       text        not null,
    posted_at  timestamptz not null default now(),
    constraint digest_log_once unique (kind, target_key)
);

-- ---------------------------------------------------------------------------
-- Zoom transcript turns: who spoke, how much, in which session. Comes from a
-- cloud-recording VTT file, which names speakers by their Zoom display name —
-- hence the reminder at the top of every session to use your real name.
-- ---------------------------------------------------------------------------

create table if not exists zoom_transcript_turn (
    turn_id      uuid        primary key default gen_random_uuid(),
    session_id   uuid        not null references "session" (session_id) on delete restrict,
    speaker_name text        not null,
    started_at_s numeric(10, 3) not null,
    ended_at_s   numeric(10, 3) not null,
    word_count   integer     not null default 0,
    source_sha256 text       not null,
    ingested_at  timestamptz not null default now(),
    constraint zoom_turn_uniq unique (session_id, source_sha256, started_at_s, speaker_name)
);

create index if not exists zoom_turn_session_idx on zoom_transcript_turn (session_id);

-- ---------------------------------------------------------------------------
-- Read-time views.
-- ---------------------------------------------------------------------------

-- A Slack account with its roster identity attached. Three ways to resolve, in
-- order: a manual link wins, then the primary address, then an alias.
create or replace view v_slack_user_resolved
with (security_invoker = true) as
select
    u.slack_user_id, u.team_id, u.email, u.display_name, u.real_name, u.tz,
    u.tz_offset_s, u.is_admin, u.is_bot, u.deleted, u.joined_at_utc,
    u.first_seen_at, u.last_seen_at,
    coalesce(u.linked_fellow_id, e.fellow_id)     as fellow_id,
    case when u.linked_fellow_id is not null then 'manual'
         when e.kind = 'primary' then 'primary_email'
         when e.kind is not null then 'alias'
         else null end                             as match_method,
    f.full_name, f.cohort_id, f.status as fellow_status
from slack_user u
left join v_fellow_email e on e.email = lower(u.email)
left join fellow f on f.fellow_id = coalesce(u.linked_fellow_id, e.fellow_id);

-- Every message with the fellow who wrote it, staff channels flagged so they
-- can be excluded from fellow-facing counts.
create or replace view v_slack_message_resolved
with (security_invoker = true) as
select
    m.message_key, m.channel_id, c.name as channel_name, c.is_staff as staff_channel,
    m.slack_user_id, m.ts, m.posted_at_utc, m.is_thread_reply, m.subtype,
    m.text, m.word_count, m.reaction_count,
    r.fellow_id, r.full_name, r.cohort_id
from slack_message m
join slack_channel c on c.channel_id = m.channel_id
left join v_slack_user_resolved r on r.slack_user_id = m.slack_user_id;

-- Part A and Part B now resolve through aliases too. Same column list as
-- before, so nothing downstream changes; only the join does.
create or replace view v_checkin_resolved
with (security_invoker = true) as
select
    c.checkin_id,
    c.source,
    c.source_event_id,
    c.submitted_email,
    c.submitted_at_utc,
    c.submitted_at_raw,
    c.source_timezone,
    c.session_id,
    c.session_match,
    c.passphrase_raw,
    c.passphrase_match,
    c.edit_distance,
    c.latency_seconds,
    c.extra_fields,
    c.ingested_at,
    s.title            as session_title,
    s.cohort_id        as session_cohort_id,
    s.scheduled_at_utc,
    s.announced_at_utc,
    f.fellow_id,
    f.full_name,
    f.cohort_id        as fellow_cohort_id,
    lr.cohort_id       as load_cohort_id,
    coalesce(f.cohort_id, s.cohort_id, lr.cohort_id) as cohort_id,
    d.decision_id,
    d.status,
    d.attended,
    d.confidence,
    d.decided_by,
    d.rule_name,
    d.ai_model,
    d.ai_prompt_version,
    d.ai_reasoning,
    d.human_email,
    d.note,
    d.created_at       as decided_at
from checkin c
left join "session" s on s.session_id = c.session_id
left join load_run lr on lr.load_id = c.load_id
left join v_fellow_email e
       on e.email = lower(c.submitted_email)
      and (s.cohort_id is null or e.cohort_id = s.cohort_id)
left join fellow f on f.fellow_id = e.fellow_id
left join v_current_decision d on d.checkin_id = c.checkin_id;

create or replace view v_checkin_b_resolved
with (security_invoker = true) as
select
    b.checkin_b_id,
    b.source,
    b.source_event_id,
    b.submitted_email,
    b.submitted_at_utc,
    b.session_id,
    b.session_match,
    b.confidence_raw,
    b.takeaway_text,
    b.rotating_kind,
    b.rotating_text,
    b.shoutout_text,
    b.latency_seconds,
    b.extra_fields,
    b.ingested_at,
    s.title            as session_title,
    s.week_index,
    s.scheduled_at_utc,
    s.announced_at_utc,
    f.fellow_id,
    f.full_name,
    lr.cohort_id       as load_cohort_id,
    coalesce(f.cohort_id, s.cohort_id, lr.cohort_id) as cohort_id,
    (btrim(coalesce(b.takeaway_text, '')) <> '') as has_takeaway,
    (btrim(coalesce(b.rotating_text, '')) <> '') as has_rotating_answer,
    (btrim(coalesce(b.shoutout_text, '')) <> '') as has_shoutout
from checkin_b b
left join "session" s on s.session_id = b.session_id
left join load_run lr on lr.load_id = b.load_id
left join v_fellow_email e
       on e.email = lower(b.submitted_email)
      and (s.cohort_id is null or e.cohort_id = s.cohort_id)
left join fellow f on f.fellow_id = e.fellow_id;

-- The per-fellow funnel: accepted → joined Slack → first message → first
-- check-in → completed. Every stage is derived at read time from what was
-- observed; only `accepted_on` and `completed_at` are typed in.
create or replace view v_fellow_funnel
with (security_invoker = true) as
select
    f.fellow_id, f.cohort_id, f.full_name, f.status,
    coalesce(f.accepted_on::timestamptz, f.created_at) as accepted_at,
    (select min(coalesce(u.joined_at_utc, u.first_seen_at))
       from v_slack_user_resolved u where u.fellow_id = f.fellow_id and not u.is_bot)
                                                        as slack_joined_at,
    (select min(m.posted_at_utc)
       from v_slack_message_resolved m
      where m.fellow_id = f.fellow_id and not m.staff_channel) as first_message_at,
    (select min(x.submitted_at_utc) from (
        select submitted_at_utc from v_checkin_resolved   where fellow_id = f.fellow_id
        union all
        select submitted_at_utc from v_checkin_b_resolved where fellow_id = f.fellow_id
     ) x)                                                    as first_checkin_at,
    f.completed_at
from fellow f;

-- ---------------------------------------------------------------------------
-- Row Level Security: same shape as everything else. The service role bypasses
-- it; nothing else reads a fellow's Slack messages by default.
-- ---------------------------------------------------------------------------

alter table fellow_alias           enable row level security;
alter table slack_user             enable row level security;
alter table slack_message          enable row level security;
alter table roster_alert           enable row level security;
alter table slack_preference       enable row level security;
alter table assignment_submission  enable row level security;
alter table intervention           enable row level security;
alter table badge_award            enable row level security;
alter table zoom_transcript_turn   enable row level security;

do $$
declare
    t text;
begin
    foreach t in array array[
        'fellow_alias', 'slack_user', 'slack_message', 'roster_alert',
        'slack_preference', 'assignment_submission', 'intervention',
        'badge_award', 'zoom_transcript_turn'
    ] loop
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
