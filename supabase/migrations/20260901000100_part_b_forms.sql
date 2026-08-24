-- Part B — the end-of-session check-in. Form-side schema.
--
-- A session now has TWO forms, not one. Part A is released mid-lesson and
-- proves presence; Part B is released at the end and measures what landed. They
-- are released at different moments, so one form cannot be both.
--
-- Everything here EXTENDS Part A's tables rather than forking a parallel set.
-- Two `session_form` tables would mean two provisioning paths, two publish
-- verifications and two places for trap 1 to be forgotten.

-- ---------------------------------------------------------------------------
-- form_template.part — each part has its own template and its own one-time
-- human Verified-email confirmation.
--
-- The manual step is per-template because the setting is per-form: copying
-- carries it, creating does not. A Part B template created after Part A was
-- verified starts unverified, exactly like Part A did.
-- ---------------------------------------------------------------------------

alter table form_template
    add column if not exists part text not null default 'a';

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid = 'form_template'::regclass and conname = 'form_template_part_valid'
    ) then
        alter table form_template
            add constraint form_template_part_valid check (part in ('a', 'b'));
    end if;
end
$$;

comment on column form_template.part is
    'a = mid-session passphrase check-in, b = end-of-session check-in. Each part '
    'has its own template form and its own Verified-email confirmation, because '
    'the setting lives on the form and is only carried by a Drive copy.';

-- At most one active template per part. Two would mean two sets of settings to
-- keep correct and only one of them ever checked.
create unique index if not exists form_template_one_active_per_part
    on form_template (part)
    where is_active;

-- ---------------------------------------------------------------------------
-- session_form.part — a session has at most one form per part.
--
-- Part A shipped with UNIQUE (session_id), which now has to become
-- UNIQUE (session_id, part). The old constraint is found by definition rather
-- than by name so this applies to a database created by any Part A revision.
-- ---------------------------------------------------------------------------

alter table session_form
    add column if not exists part text not null default 'a';

do $$
declare
    old_constraint text;
begin
    select conname into old_constraint
      from pg_constraint
     where conrelid = 'session_form'::regclass
       and contype = 'u'
       and pg_get_constraintdef(oid) = 'UNIQUE (session_id)';
    if old_constraint is not null then
        execute format('alter table session_form drop constraint %I', old_constraint);
    end if;

    if not exists (
        select 1 from pg_constraint
         where conrelid = 'session_form'::regclass and conname = 'session_form_part_valid'
    ) then
        alter table session_form
            add constraint session_form_part_valid check (part in ('a', 'b'));
    end if;
end
$$;

create unique index if not exists session_form_session_part_uniq
    on session_form (session_id, part);

comment on column session_form.part is
    'Which check-in this form is. A session has at most one form per part; '
    'provisioning is idempotent against (session_id, part).';

-- ---------------------------------------------------------------------------
-- session — the week that drives the rotating question, and the teacher's own
-- question for the weeks that call for one.
-- ---------------------------------------------------------------------------

alter table "session"
    add column if not exists week_index integer;

alter table "session"
    add column if not exists teacher_question text;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid = '"session"'::regclass and conname = 'session_week_index_positive'
    ) then
        alter table "session"
            add constraint session_week_index_positive
            check (week_index is null or week_index >= 1);
    end if;
end
$$;

comment on column "session".week_index is
    'Which week of the rotation this session is, typed in by staff. Deliberately '
    'NOT derived from scheduled_at: sessions get rescheduled, skipped and doubled '
    'up, and a calendar-derived week desynchronises the whole rotation silently.';

comment on column "session".teacher_question is
    'The teacher''s own question for the rotating slot. Required only on the weeks '
    'the schedule assigns to teacher_question; provisioning refuses rather than '
    'substituting a generic question, because the teacher''s question is the only '
    'unfakeable item on the form.';

-- ---------------------------------------------------------------------------
-- form_question_map — the new trap.
--
-- Part A had one question, so "the answer" was unambiguous. Part B has five,
-- and forms.responses.list keys answers by questionId, not by title or
-- position. Whether Drive files.copy preserves question ids across copies is
-- NOT verified either way, so ids are never assumed and never hardcoded: after
-- provisioning, the form is read back with forms.get and the mapping recorded
-- here.
--
-- Slots are matched by ITEM INDEX, which this app controls at creation time —
-- not by title text. The rotating slot's title changes every week and a teacher
-- may edit any of the others.
-- ---------------------------------------------------------------------------

create table if not exists form_question_map (
    id            uuid        primary key default gen_random_uuid(),
    form_id       text        not null,
    question_id   text        not null,
    slot          text        not null
                  check (slot in ('confidence', 'takeaway', 'rotating', 'shoutout', 'help')),
    rotating_kind text
                  check (rotating_kind is null or
                         rotating_kind in ('teacher_question', 'muddiest_point', 'application')),
    question_text text        not null,
    item_index    integer     not null,
    recorded_at   timestamptz not null default now()
);

create unique index if not exists form_question_map_form_question_uniq
    on form_question_map (form_id, question_id);

create unique index if not exists form_question_map_form_slot_uniq
    on form_question_map (form_id, slot);

create index if not exists form_question_map_form_idx on form_question_map (form_id);

comment on table form_question_map is
    'questionId -> semantic slot, per form, recorded by reading the form back '
    'after provisioning. Ingest resolves every answer through this table and '
    'REFUSES a form whose map is missing or incomplete — silently mis-keyed '
    'answers would file a confidence score as a takeaway and corrupt every '
    'downstream number in a way that looks entirely plausible.';

comment on column form_question_map.question_text is
    'The exact text shown to fellows, snapshot at provisioning time. Never '
    'reconstructed later from config: the config may have changed since, and '
    '"what was actually asked in week 3" has to be answerable from the database '
    'alone.';

comment on column form_question_map.rotating_kind is
    'Set only on the rotating slot, so the question TYPE travels with the answer '
    'rather than having to be re-derived from a week number and a config file.';
