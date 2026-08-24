-- The help request path — the most sensitive thing this system touches.
--
-- One checkbox on the end-of-session form: "I'd like someone to check in with
-- me." It is last on the form, after rapport is built, because sensitive items
-- placed early measurably raise abandonment.
--
-- Four properties hold, and three of them are enforced somewhere other than a
-- comment:
--
--   1. It lives in its OWN table, not as a column on `checkin_b`. A column would
--      travel with every SELECT * a staffer ever wrote, into every export and
--      every aggregate, and nothing would announce it.
--   2. Asking for help NEVER lowers any participation signal. It is excluded
--      from every count, score, rate and aggregate — forever. If a fellow can
--      suspect the box costs them something, the field stops working and the
--      programme loses its only self-reported distress channel.
--   3. Stricter RLS than anything else here. Explicitly NOT covered by the
--      general "visible to every full-time team member" default — that question
--      is unresolved for ordinary attendance data and is nowhere near resolved
--      for this.
--   4. The AI tier never sees it. No classification, no summarisation, no
--      clustering, no scoring.
--
-- Nothing about a request is ever logged, at any level, DEBUG included.

create table if not exists help_request (
    help_request_id  uuid        primary key default gen_random_uuid(),

    -- Nullable on purpose. An address we cannot place on the roster still
    -- produces a request: identity failure must never swallow a person asking to
    -- be contacted.
    fellow_id        text        references fellow (fellow_id) on delete set null,
    submitted_email  text        not null,
    session_id       uuid        references "session" (session_id) on delete set null,
    submitted_at_utc timestamptz not null,

    status           text        not null default 'open'
                     check (status in ('open', 'acknowledged', 'closed')),
    acknowledged_by  text,
    acknowledged_at  timestamptz,
    note             text,

    -- Same idempotency key as the check-in it arrived with, so a re-pull of the
    -- same response does not raise the same hand twice.
    source_event_id  text        not null unique,
    created_at       timestamptz not null default now(),

    constraint help_request_ack_has_provenance check (
        (status = 'open' and acknowledged_by is null and acknowledged_at is null) or
        (status in ('acknowledged', 'closed') and acknowledged_by is not null
                                              and acknowledged_at is not null)
    )
);

create index if not exists help_request_open_idx
    on help_request (submitted_at_utc desc) where status = 'open';

create index if not exists help_request_session_idx on help_request (session_id);

comment on table help_request is
    'A fellow asked to be contacted. Routed by email to the configured recipient '
    'the moment it lands — not on a batch schedule, because someone asking for '
    'contact should not wait for a weekly pipeline run.';

comment on column help_request.fellow_id is
    'Nullable: an unresolved address is still recorded. Losing a request because '
    'the roster is out of date is the one failure mode this table cannot have.';

comment on column help_request.note is
    'Written by the responding staff member, not by the fellow. Nothing the '
    'fellow typed on the form is copied here.';

-- TODO(retention): CU has not defined a retention period for ANY table in this
-- system, and a record that a young person asked for help is the most sensitive
-- thing in it. Do not invent a number — whatever is written here becomes the
-- policy nobody revisits. This marker is on this table specifically, and
-- deliberately separately from the general retention TODO in
-- src/cufa/form_content.py, because the answer for this table is very unlikely
-- to be the same as the answer for a timestamp.
--
-- What has to be decided:
--   * How long is an open request kept? A closed one?
--   * Is a closed request deleted, or anonymised down to a count?
--   * Does a fellow leaving the programme delete their requests, or not?
comment on column help_request.created_at is
    'TODO(retention): no retention period is defined for this table. See the '
    'comment block above it in the migration, and docs/safeguarding.md.';

-- ---------------------------------------------------------------------------
-- Row Level Security
--
-- Part A's tables get a `TODO(access)` stub whose predicate is `false`. This
-- table gets the same denial, and additionally has its grants revoked outright,
-- so that a future migration which loosens the shared stub — the likely way this
-- leaks — does not reach here by accident.
-- ---------------------------------------------------------------------------

alter table help_request enable row level security;
revoke all on help_request from anon, authenticated;

comment on table help_request is
    'A fellow asked to be contacted. Routed by email to the configured recipient '
    'the moment it lands. ACCESS: stricter than every other table here. RLS is on '
    'with no permissive policy and grants are revoked from anon and authenticated, '
    'so only the service role reads it. This is NOT covered by the general '
    '"visible to every full-time team member" default — that question is '
    'unresolved for attendance data and is not resolved for this.';

-- The three Part B tables that carry a fellow's words follow Part A's stub.
alter table checkin_b     enable row level security;
alter table peer_shoutout enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'checkin_b'
          and policyname = 'checkin_b_read_todo'
    ) then
        create policy checkin_b_read_todo on checkin_b
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'peer_shoutout'
          and policyname = 'peer_shoutout_read_todo'
    ) then
        -- TODO(access): a shoutout is data about a third party who did not
        -- submit it. Whatever rule CU writes for checkin_b, this one additionally
        -- has to answer: may the person NAMED read it? The default is no, and
        -- surfacing it to them is a decision for the data owner, not a default.
        create policy peer_shoutout_read_todo on peer_shoutout
            for select to authenticated
            using (false);
    end if;
end
$$;
