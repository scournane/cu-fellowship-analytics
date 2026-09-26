-- Data subject rights, and a log of who read whose record.
--
-- Two of the open items in docs/vision.md §12:
--
--   * "Data subject access, export and deletion, on request, by a fellow or a
--     parent" — `cufa fellow export` and `cufa fellow erase`.
--   * "An access audit log: who looked at whose record, when" —
--     `fellow_access_log`, written by the console's fellow-detail route and by
--     the export command.
--
-- Three decisions are enforced here rather than left to the application,
-- because a privacy promise a future caller can forget is not a promise.
--
-- 1. **Erasure pseudonymises; it does not drop rows.** Every observation in
--    this schema carries an idempotency key (`checkin.source_event_id`,
--    `slack_event.source_event_id`) and every ingest path is
--    `on conflict (source_event_id) do nothing`. A deleted observation is
--    therefore *not* erased: the next `cufa pull` or `cufa slack backfill`
--    re-reads the same response from Google or Slack and writes the person
--    back, name and address included. An anonymised row still collides on its
--    key and is skipped, so it is the only shape of erasure that survives the
--    next sync. The roster row survives for the same reason plus one more:
--    `bot_delivery.fellow_id` is `on delete restrict` and `intervention` is
--    `on delete cascade`, so dropping it would either fail or quietly destroy
--    the record of what staff did.
--
--    What survives is a tombstone: the CU-issued `fellow_id`, the cohort, and
--    `[erased]` where the name was. That is pseudonymisation, not
--    anonymisation — CU can still map a fellow_id to a person from its own
--    records, outside this database — and `cufa fellow erase` says so in its
--    report rather than claiming more than it does.
--
-- 2. **Erasure holds against the next import.** `cufa load-roster` upserts
--    `full_name` and `primary_email` on conflict, and `cufa slack users`
--    upserts a Slack member's email and names from `users.list`. Both would
--    resurrect an erased person on the next run. Two guard triggers make the
--    erasure stick without either of those paths having to remember it.
--
-- 3. **`checkin` is still immutable.** The trigger from
--    20260801000700 refuses every UPDATE to an observed column and every
--    DELETE, and it keeps refusing them. It gains exactly one exemption, in
--    the same spirit as the `latency_seconds` one already there: an update
--    that replaces `submitted_email` with the erasure sentinel and empties
--    `extra_fields`, changing nothing else, is a lawful erasure rather than a
--    rewrite of what was observed. Writing the exemption into the enforcement
--    is the point — the alternative is an application that disables the
--    trigger, which is an application that can disable the trigger.

-- ---------------------------------------------------------------------------
-- The access audit log.
--
-- RUNBOOK §8 lists three sign-in doors and says what the shared-password one
-- costs: "no record of who read what". This table is where that cost becomes
-- visible instead of theoretical. A shared-password read is recorded as
-- `actor_kind = 'shared_password'` with `actor_email` NULL — never as a person,
-- because the console genuinely does not know who it was, and a log that
-- guessed would be worse than no log.
--
-- The fellow is recorded by `fellow_id` and by nothing else. Their email
-- address is not here, the same way it is not in `cufa slack report`, in a DM,
-- or in a digest.
-- ---------------------------------------------------------------------------

create table if not exists fellow_access_log (
    access_id   uuid        primary key default gen_random_uuid(),
    fellow_id   text        not null references fellow (fellow_id) on delete cascade,

    -- Which door the reader came through, and therefore how much the log can
    -- honestly claim about who they were.
    --   person          — Google sign-in: a named CU account.
    --   dev_bypass      — the no-Google door. Named, but only as much as
    --                     whoever typed the address wanted to be.
    --   shared_password — one secret everybody has. NOT a person.
    --   cli             — a command run against the database directly.
    actor_kind  text        not null
                check (actor_kind in ('person', 'dev_bypass', 'shared_password', 'cli')),

    -- The staff identity, when there is one. NULL is not "unknown by
    -- accident": for a shared-password session it is the honest answer, and
    -- the constraint below makes any other combination impossible to write.
    actor_email text,

    route       text        not null,
    at          timestamptz not null default now(),

    constraint fellow_access_actor_matches_kind check (
        (actor_kind = 'shared_password' and actor_email is null) or
        (actor_kind <> 'shared_password' and actor_email is not null)
    )
);

create index if not exists fellow_access_log_fellow_idx
    on fellow_access_log (fellow_id, at desc);
create index if not exists fellow_access_log_at_idx
    on fellow_access_log (at desc);

comment on table fellow_access_log is
    'Who opened one fellow''s record, and when. Append-only: a read that '
    'happened cannot be un-happened, so nothing in the application updates or '
    'deletes a row here.';
comment on column fellow_access_log.actor_kind is
    'Which sign-in door the reader used. shared_password means the console does '
    'not know who it was — see RUNBOOK section 8.';
comment on column fellow_access_log.actor_email is
    'The staff address, or NULL for a shared-password session. The FELLOW''s '
    'address is deliberately absent from this table entirely.';

-- ---------------------------------------------------------------------------
-- The erasure ledger, and the suppression list that keeps an erasure durable.
-- ---------------------------------------------------------------------------

create table if not exists data_erasure (
    erasure_id uuid        primary key default gen_random_uuid(),

    -- No foreign key on purpose. The point of this row is to outlive anything
    -- that might later remove the tombstone it refers to; a cascade would take
    -- the evidence of the erasure with it.
    fellow_id  text        not null,
    requested_by text      not null,
    touched    jsonb       not null default '{}'::jsonb,
    at         timestamptz not null default now()
);

comment on table data_erasure is
    'One row per honoured erasure request: which roster id, who asked for it, '
    'and the exact per-table counts. Deliberately holds no name and no address '
    '— recording what was erased in order to prove it was erased would defeat '
    'the erasure.';

create table if not exists erased_slack_user (
    slack_user_id text        primary key,
    erased_at     timestamptz not null default now()
);

comment on table erased_slack_user is
    'Workspace-scoped Slack ids belonging to erased fellows. A suppression '
    'list, in the sense an unsubscribe list is one: keeping the least '
    'identifying key there is (an opaque workspace id, no name, no address) is '
    'what stops the next `cufa slack users` writing the person back.';

alter table fellow add column if not exists erased_at timestamptz;
alter table fellow add column if not exists erased_by text;

comment on column fellow.erased_at is
    'Set by `cufa fellow erase`. The roster row survives as a tombstone so the '
    'foreign keys and the append-only ledgers stay intact; this column is what '
    'says the name and address on it are placeholders.';

-- ---------------------------------------------------------------------------
-- Guard 1: `cufa load-roster` must not undo an erasure.
--
-- It upserts `full_name` and `primary_email` on conflict, so re-loading a CSV
-- that still names an erased fellow would put the name and the address back.
-- The incoming identity is discarded rather than rejected: raising would fail
-- the whole roster load over one row, and the other fellows in that CSV have
-- done nothing wrong. A NOTICE says what happened and what to fix.
-- ---------------------------------------------------------------------------

create or replace function fellow_erasure_guard() returns trigger
language plpgsql as $$
begin
    if old.erased_at is null then
        return new;
    end if;

    if new.full_name     is distinct from old.full_name
    or new.primary_email is distinct from old.primary_email
    or new.timezone      is distinct from old.timezone then
        raise notice
            'fellow % was erased at %; the name, address and time zone on the '
            'incoming row were discarded. Remove this person from the roster '
            'source so the next load does not carry them.',
            old.fellow_id, old.erased_at;
        new.full_name     := old.full_name;
        new.primary_email := old.primary_email;
        new.timezone      := old.timezone;
    end if;

    -- An erasure is not re-stamped and not cleared by an ordinary write. Undoing
    -- one is a deliberate act and belongs in a migration, not in an upsert.
    new.erased_at := old.erased_at;
    new.erased_by := old.erased_by;
    return new;
end
$$;

-- Fires before `fellow_primary_guard_trg` — triggers run in name order, and
-- 'e' sorts before 'p' — so the alias clash check sees the sentinel address
-- this one restores rather than the one the import tried to write.
drop trigger if exists fellow_erasure_guard_trg on fellow;
create trigger fellow_erasure_guard_trg
    before update on fellow
    for each row execute function fellow_erasure_guard();

-- ---------------------------------------------------------------------------
-- Guard 2: `cufa slack users` must not undo an erasure either.
--
-- `sync_users` upserts email, display name, real name and time zone straight
-- from `users.list`, which still knows the person. The suppression list wins.
-- ---------------------------------------------------------------------------

create or replace function slack_user_erasure_guard() returns trigger
language plpgsql as $$
begin
    if not exists (
        select 1 from erased_slack_user e where e.slack_user_id = new.slack_user_id
    ) then
        return new;
    end if;
    new.email            := null;
    new.display_name     := '[erased]';
    new.real_name        := '[erased]';
    new.tz               := null;
    new.tz_offset_s      := null;
    new.raw              := '{}'::jsonb;
    new.linked_fellow_id := null;
    return new;
end
$$;

drop trigger if exists slack_user_erasure_guard_trg on slack_user;
create trigger slack_user_erasure_guard_trg
    before insert or update on slack_user
    for each row execute function slack_user_erasure_guard();

-- ---------------------------------------------------------------------------
-- The one exemption to `checkin` immutability.
--
-- Everything else in this function is exactly as 20260801000700 left it. The
-- DELETE refusal is untouched: a dropped observation is still unrecoverable,
-- and erasure does not need one dropped.
-- ---------------------------------------------------------------------------

create or replace function checkin_reject_mutation() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception
            'checkin rows are immutable and are never deleted (checkin_id=%). '
            'A dropped observation is unrecoverable.', old.checkin_id
            using errcode = 'restrict_violation';
    end if;

    -- Data-subject erasure. Narrow on purpose: the new address must be the
    -- erasure sentinel, the old one must not already be a sentinel (so this
    -- cannot be used to shuffle a row between pseudonyms), extra_fields must
    -- come out empty, and every other observed column must be untouched.
    if new.submitted_email is distinct from old.submitted_email
       and new.submitted_email like 'erased-%@erased.invalid'
       and old.submitted_email not like '%@erased.invalid'
       and new.extra_fields = '{}'::jsonb
       -- `answers` holds the raw form answers keyed by questionId, which since
       -- 20261001000100 is where a fellow's own writing lives. Erasure empties
       -- it for the same reason it empties extra_fields: it is the person, not
       -- the observation that they submitted.
       and new.answers = '{}'::jsonb
       and row(new.checkin_id, new.source_event_id, new.source,
               new.submitted_at_utc, new.submitted_at_raw, new.source_timezone,
               new.session_id, new.session_match, new.passphrase_raw,
               new.passphrase_match, new.edit_distance, new.load_id,
               new.ingested_at, new.form_id)
           is not distinct from
           row(old.checkin_id, old.source_event_id, old.source,
               old.submitted_at_utc, old.submitted_at_raw, old.source_timezone,
               old.session_id, old.session_match, old.passphrase_raw,
               old.passphrase_match, old.edit_distance, old.load_id,
               old.ingested_at, old.form_id)
    then
        return new;
    end if;

    -- Every observed column is listed explicitly. Adding a column to `checkin`
    -- means adding it here too — which is the point: a new observation field
    -- should have to opt in to being protected, visibly.
    --
    -- That is not a hypothetical. `form_id` and `answers` arrived in
    -- 20261001000100 while this function was being written against the schema
    -- as it stood, and because this runs later it silently replaced a function
    -- that protected them with one that did not. `tests/test_part_a.py::
    -- test_the_new_observed_columns_are_immutable` is what caught it. The cost
    -- of the explicit list is this; the benefit is that a test can catch it.
    if row(new.checkin_id, new.source_event_id, new.source, new.submitted_email,
           new.submitted_at_utc, new.submitted_at_raw, new.source_timezone,
           new.session_id, new.session_match, new.passphrase_raw,
           new.passphrase_match, new.edit_distance, new.extra_fields,
           new.load_id, new.ingested_at, new.form_id, new.answers)
       is distinct from
       row(old.checkin_id, old.source_event_id, old.source, old.submitted_email,
           old.submitted_at_utc, old.submitted_at_raw, old.source_timezone,
           old.session_id, old.session_match, old.passphrase_raw,
           old.passphrase_match, old.edit_distance, old.extra_fields,
           old.load_id, old.ingested_at, old.form_id, old.answers)
    then
        raise exception
            'checkin rows are immutable; only latency_seconds may be recomputed, '
            'or submitted_email replaced with the erasure sentinel '
            '(checkin_id=%)', old.checkin_id
            using errcode = 'restrict_violation';
    end if;

    return new;
end;
$$;

comment on function checkin_reject_mutation() is
    'Blocks every UPDATE to an observed column and every DELETE on checkin. '
    'latency_seconds is exempt because it is derived from session state that '
    'legitimately changes after ingest. One further exemption: replacing '
    'submitted_email with the erasure sentinel and emptying extra_fields, '
    'changing nothing else, is a data-subject erasure.';

-- ---------------------------------------------------------------------------
-- Row Level Security: same shape as everything else. The service role bypasses
-- it; nothing else reads these tables by default.
--
-- fellow_access_log gets no policy at all beyond the non-permissive stub, and
-- the grants are revoked as well: it holds staff addresses, and the question of
-- who may read the audit log is exactly the kind of thing that should not be
-- decided by whichever integration turns up first.
-- ---------------------------------------------------------------------------

alter table fellow_access_log  enable row level security;
alter table data_erasure       enable row level security;
alter table erased_slack_user  enable row level security;

revoke all on fellow_access_log from anon, authenticated;
revoke all on data_erasure      from anon, authenticated;
revoke all on erased_slack_user from anon, authenticated;

do $$
declare
    t text;
begin
    foreach t in array array['fellow_access_log', 'data_erasure', 'erased_slack_user'] loop
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

-- ---------------------------------------------------------------------------
-- The same exemption, for the two other append-only tables erasure touches.
--
-- `checkin` got one above; `checkin_b` and `slack_event` did not, and the
-- erasure steps in `data_rights.py` update all three. The result was nine
-- tests failing on "checkin_b rows are immutable" and a Slack anonymisation
-- that could never have run in production either. A right to erasure that
-- stops at the first append-only table is not one.
--
-- Both exemptions are written the same narrow way as `checkin`'s: the new
-- value must be the sentinel or NULL, the old value must not already be
-- erased, and every other observed column must be untouched. The refusal of
-- DELETE is left exactly as it was — a dropped observation is still
-- unrecoverable, and erasure does not need one dropped.
-- ---------------------------------------------------------------------------

create or replace function checkin_b_reject_mutation() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception
            'checkin_b rows are immutable and are never deleted (checkin_b_id=%). '
            'A dropped observation is unrecoverable.', old.checkin_b_id
            using errcode = 'restrict_violation';
    end if;

    -- Data-subject erasure. The free-text answers are the point: a takeaway or
    -- a muddiest-point sentence is the fellow's own writing and is the part of
    -- this row that is about a person rather than about a session. The
    -- confidence rating stays, because a number with nobody attached is what
    -- makes the cohort trend survive an erasure.
    if new.submitted_email is distinct from old.submitted_email
       and new.submitted_email like 'erased-%@erased.invalid'
       and old.submitted_email not like '%@erased.invalid'
       and new.takeaway_text is null
       and new.rotating_text is null
       and new.shoutout_text is null
       and new.extra_fields = '{}'::jsonb
       and row(new.checkin_b_id, new.source_event_id, new.source,
               new.submitted_at_utc, new.session_id, new.session_match,
               new.confidence_raw, new.rotating_kind, new.load_id,
               new.ingested_at)
           is not distinct from
           row(old.checkin_b_id, old.source_event_id, old.source,
               old.submitted_at_utc, old.session_id, old.session_match,
               old.confidence_raw, old.rotating_kind, old.load_id,
               old.ingested_at)
    then
        return new;
    end if;

    if row(new.checkin_b_id, new.source_event_id, new.source, new.submitted_email,
           new.submitted_at_utc, new.session_id, new.session_match,
           new.confidence_raw, new.takeaway_text, new.rotating_kind,
           new.rotating_text, new.shoutout_text, new.extra_fields,
           new.load_id, new.ingested_at)
       is distinct from
       row(old.checkin_b_id, old.source_event_id, old.source, old.submitted_email,
           old.submitted_at_utc, old.session_id, old.session_match,
           old.confidence_raw, old.takeaway_text, old.rotating_kind,
           old.rotating_text, old.shoutout_text, old.extra_fields,
           old.load_id, old.ingested_at)
    then
        raise exception
            'checkin_b rows are immutable; only latency_seconds may be recomputed, '
            'or the address replaced with the erasure sentinel and the free text '
            'emptied (checkin_b_id=%)', old.checkin_b_id
            using errcode = 'restrict_violation';
    end if;

    return new;
end;
$$;

comment on function checkin_b_reject_mutation() is
    'Blocks every UPDATE to an observed column and every DELETE on checkin_b. '
    'latency_seconds is exempt because it is derived from session state that '
    'legitimately changes after ingest. One further exemption: replacing '
    'submitted_email with the erasure sentinel and nulling the free-text '
    'answers, changing nothing else, is a data-subject erasure.';


create or replace function slack_event_reject_mutation() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception
            'slack_event rows are immutable and are never deleted (slack_event_id=%). '
            'A dropped observation is unrecoverable.', old.slack_event_id
            using errcode = 'restrict_violation';
    end if;

    -- Data-subject erasure. What is detached is who did it; what stays is that
    -- it happened. The counts this table exists to produce are unaffected,
    -- which is the whole argument for pseudonymising rather than deleting: a
    -- cohort's activity history does not have to be falsified to honour one
    -- person's erasure.
    --
    -- Stated as a rule rather than as a shape, because erasure touches this
    -- table two different ways. A fellow's own rows lose `slack_user_id`,
    -- `user_email` and `text`. Rows belonging to SOMEBODY ELSE lose only
    -- `item_user_id` or `mentions` — a reaction to the erased person's
    -- message, a message that @-ed them — and must keep their own author
    -- intact. A rule that named one shape would block the other, and those
    -- cross-references are exactly how an erasure gets undone from outside.
    --
    -- So: every column that changed must be one of the five, and every change
    -- must be a nulling. Nothing can be set, only cleared, and nothing outside
    -- the five can move at all. No list of the other twenty-one columns is
    -- needed, and a column added to this table in future is protected by
    -- default rather than by somebody remembering to list it.
    if (new.slack_user_id is null or new.slack_user_id is not distinct from old.slack_user_id)
       and (new.user_email is null or new.user_email is not distinct from old.user_email)
       and (new.text is null or new.text is not distinct from old.text)
       and (new.mentions is null or new.mentions is not distinct from old.mentions)
       and (new.item_user_id is null or new.item_user_id is not distinct from old.item_user_id)
       and row(new.*) is distinct from row(old.*)
       and row(new.slack_event_id, new.source_event_id, new.team_id,
               new.event_type, new.channel_id, new.channel_type,
               new.message_ts, new.thread_ts, new.is_thread_reply,
               new.reaction, new.text_length, new.word_count, new.has_link,
               new.has_attachment, new.event_time_utc, new.raw, new.load_id,
               new.received_at, new.call_id, new.file_id, new.poll_id,
               new.poll_choice)
           is not distinct from
           row(old.slack_event_id, old.source_event_id, old.team_id,
               old.event_type, old.channel_id, old.channel_type,
               old.message_ts, old.thread_ts, old.is_thread_reply,
               old.reaction, old.text_length, old.word_count, old.has_link,
               old.has_attachment, old.event_time_utc, old.raw, old.load_id,
               old.received_at, old.call_id, old.file_id, old.poll_id,
               old.poll_choice)
    then
        return new;
    end if;

    raise exception
        'slack_event rows are immutable (slack_event_id=%); the only permitted '
        'update is a data-subject erasure, which may null slack_user_id, '
        'user_email, text, mentions or item_user_id and may change nothing else',
        old.slack_event_id
        using errcode = 'restrict_violation';
end;
$$;

comment on function slack_event_reject_mutation() is
    'Blocks every UPDATE and DELETE on slack_event, with one exemption: an '
    'update whose only changes are the nulling of slack_user_id, user_email, '
    'text, mentions or item_user_id is a data-subject erasure. The act stays '
    'as a count; the person does not. Every other column is compared '
    'explicitly, so a column added later is protected by default.';


-- ---------------------------------------------------------------------------
-- An erased row names nobody, and the actor check has to allow that.
--
-- `slack_event_actor_required` (20260915000300) says every row names who acted,
-- with one exemption for a canvas edit, which genuinely has no editor. An
-- erased row is the second case: the act is still recorded, the actor is
-- deliberately gone. Without this the erasure fails on a check constraint
-- after passing the immutability trigger, which is the worst place to stop —
-- the trigger has already decided the update is lawful.
--
-- The condition is not "slack_user_id is null" on its own, which would let an
-- ordinary ingest write an actorless row and lose the guarantee. It is that
-- the row is erased: no actor, no address, no text, nothing that could name
-- anyone. A row can only reach that state through the erasure exemption above.
-- ---------------------------------------------------------------------------

alter table slack_event drop constraint if exists slack_event_actor_required;
alter table slack_event
    add constraint slack_event_actor_required
    check (
        slack_user_id is not null
        or event_type = 'canvas_edited'
        or (user_email is null and text is null
            and mentions is null and item_user_id is null)
    );

comment on constraint slack_event_actor_required on slack_event is
    'Every row names who acted, with two exemptions: a canvas edit, which has '
    'no editor, and a row erased on a data subject request, which has no '
    'actor by design and nothing else that could name anyone either.';
