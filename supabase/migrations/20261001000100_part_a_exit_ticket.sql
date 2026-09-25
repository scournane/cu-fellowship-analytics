-- Part A becomes the exit ticket.
--
-- Part A used to be a single mid-session question: a passphrase the teacher
-- said aloud, typed back by the fellow, string-compared (tier 1) and, when that
-- was unsure, judged by a model (tier 2). It is now several questions CU staff
-- write themselves — the "exit ticket" — and attendance is decided by the two
-- things that were always the real evidence: a Google-VERIFIED address and a
-- submit time inside the session's window.
--
-- What changes here, and what deliberately does not:
--
--   * Questions become versioned DATA (`part_a_question_set`): a cohort default
--     plus an optional full-snapshot override per session. Append-only, like
--     attendance_decision — an edit is a new version, the old one is stamped
--     superseded and kept, so "what did week 3's form actually ask?" never
--     depends on what the config says today.
--   * Every provisioned form records questionId -> question
--     (`part_a_form_question`), read back from the form, never assumed.
--   * `checkin` gains the raw answers, keyed by questionId, exactly as the API
--     returned them. Resolution to question keys happens at READ time through
--     the map (`v_checkin_answer`), so a missing map costs nothing at ingest and
--     is repaired by re-recording it — the observation itself never changes.
--   * The passphrase columns are kept, nullable, as legacy. Rows decided in the
--     passphrase era keep what they were decided on; new rows leave them NULL.
--
-- Invariants restated because this migration touches every one of them:
-- never drop a submission; observation separate from decision; append-only
-- with provenance; idempotent ingest; cohort-keyed; no AI reads answers; free
-- text is counted, never graded.

-- ---------------------------------------------------------------------------
-- part_a_question_set — the questions, versioned
-- ---------------------------------------------------------------------------

create table if not exists part_a_question_set (
    question_set_id uuid        primary key default gen_random_uuid(),
    -- Always set, overrides included: every question set is cohort-keyed, so a
    -- cohort's history is one WHERE clause and never a join through sessions.
    cohort_id       text        not null references cohort (cohort_id) on delete restrict,
    -- NULL = the cohort default. Set = a full-snapshot override for one session.
    session_id      uuid        references "session" (session_id) on delete restrict,
    -- 1, 2, 3 … within one scope (one cohort's default chain, or one session's
    -- override chain). For humans: "default v4", "custom v2".
    version         integer     not null check (version >= 1),
    schema_version  integer     not null default 1 check (schema_version >= 1),
    content         jsonb       not null,
    -- SHA-256 of the canonical cleaned content. Saving identical content is a
    -- no-op rather than a new version, which is what makes seeding idempotent.
    content_sha256  text        not null,
    source          text        not null
                    check (source in ('seed_file', 'import_form', 'console', 'cli')),
    -- File path, form id, or whatever else says where `source` got it from.
    source_ref      text,
    -- For an override: the cohort-default version it was customised from, so
    -- "custom, based on default v3" survives the default moving on to v5.
    based_on_id     uuid        references part_a_question_set (question_set_id),
    created_by      text,
    created_at      timestamptz not null default now(),
    -- NULL means current. Stamped once, never cleared.
    superseded_at   timestamptz,
    -- The version that replaced this one. NULL on a superseded override means
    -- it was reverted: the session went back to the cohort default.
    superseded_by   uuid        references part_a_question_set (question_set_id),
    -- Who stamped superseded_at — the author of the replacement, or whoever
    -- reverted. Set once, alongside it.
    retired_by      text,
    check (jsonb_typeof(content) = 'object'),
    check (jsonb_typeof(content -> 'questions') = 'array')
);

-- One current default per cohort, one current override per session. Partial
-- unique indexes rather than application checks, so two browser tabs saving at
-- once collide in the database instead of both "winning".
create unique index if not exists part_a_question_set_one_current_default
    on part_a_question_set (cohort_id)
    where session_id is null and superseded_at is null;

create unique index if not exists part_a_question_set_one_current_override
    on part_a_question_set (session_id)
    where session_id is not null and superseded_at is null;

create unique index if not exists part_a_question_set_default_version_uniq
    on part_a_question_set (cohort_id, version)
    where session_id is null;

create unique index if not exists part_a_question_set_override_version_uniq
    on part_a_question_set (session_id, version)
    where session_id is not null;

create index if not exists part_a_question_set_cohort_idx
    on part_a_question_set (cohort_id, created_at desc);

comment on table part_a_question_set is
    'Part A (exit ticket) questions as versioned data. session_id NULL = cohort '
    'default; set = a full-snapshot override for one session, editable until that '
    'session''s Part A form is published. Append-only: an edit inserts a new '
    'version and stamps the previous one superseded; nothing is updated in place '
    'or deleted.';

comment on column part_a_question_set.content is
    '{"schema_version":1,"title","description","questions":[{"key":"q_…","type",'
    '"title","description","required","options","allow_other","shuffle","scale"}]}. '
    'Placeholders {lesson} and {session_title} are filled per session at '
    'provisioning; the stored set keeps them.';

comment on column part_a_question_set.based_on_id is
    'For an override: the cohort-default version it was customised from.';

-- Append-only, enforced rather than asserted. The only permitted change is
-- stamping superseded_at / superseded_by / retired_by from NULL to a value,
-- once each.
create or replace function part_a_question_set_append_only() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception
            'part_a_question_set is append-only; versions are superseded, never '
            'deleted (question_set_id=%)', old.question_set_id
            using errcode = 'restrict_violation';
    end if;

    if row(new.question_set_id, new.cohort_id, new.session_id, new.version,
           new.schema_version, new.content, new.content_sha256, new.source,
           new.source_ref, new.based_on_id, new.created_by, new.created_at)
       is distinct from
       row(old.question_set_id, old.cohort_id, old.session_id, old.version,
           old.schema_version, old.content, old.content_sha256, old.source,
           old.source_ref, old.based_on_id, old.created_by, old.created_at)
    then
        raise exception
            'part_a_question_set is append-only; save a new version instead of '
            'editing this one (question_set_id=%)', old.question_set_id
            using errcode = 'restrict_violation';
    end if;

    if (old.superseded_at is not null and new.superseded_at is distinct from old.superseded_at)
       or (old.superseded_by is not null and new.superseded_by is distinct from old.superseded_by)
       or (old.retired_by is not null and new.retired_by is distinct from old.retired_by)
    then
        raise exception
            'a superseded question set stays superseded (question_set_id=%)',
            old.question_set_id
            using errcode = 'restrict_violation';
    end if;

    if new.superseded_by is not null and new.superseded_at is null then
        raise exception
            'superseded_by needs superseded_at (question_set_id=%)', old.question_set_id
            using errcode = 'check_violation';
    end if;

    return new;
end;
$$;

drop trigger if exists part_a_question_set_no_mutation on part_a_question_set;
create trigger part_a_question_set_no_mutation
    before update or delete on part_a_question_set
    for each row execute function part_a_question_set_append_only();

-- ---------------------------------------------------------------------------
-- part_a_form_question — questionId -> question, per provisioned form
-- ---------------------------------------------------------------------------

create table if not exists part_a_form_question (
    id              uuid        primary key default gen_random_uuid(),
    form_id         text        not null,
    question_id     text        not null,
    item_id         text,
    question_key    text        not null check (question_key ~ '^q_[a-z0-9_]+$'),
    -- The item's position on the form, counting section breaks and text blocks
    -- (which are items without a question id, so they have no row here).
    item_index      integer     not null check (item_index >= 0),
    kind            text        not null
                    check (kind in ('short_answer', 'paragraph', 'multiple_choice',
                                    'checkboxes', 'dropdown', 'linear_scale')),
    -- What the API reported the question as, read back after it was created.
    question_text   text        not null,
    -- The rendered question exactly as it was sent (options, scale, required).
    spec            jsonb       not null default '{}'::jsonb,
    question_set_id uuid        references part_a_question_set (question_set_id),
    recorded_at     timestamptz not null default now()
);

create unique index if not exists part_a_form_question_form_question_uniq
    on part_a_form_question (form_id, question_id);

create unique index if not exists part_a_form_question_form_key_uniq
    on part_a_form_question (form_id, question_key);

create index if not exists part_a_form_question_form_idx on part_a_form_question (form_id);

comment on table part_a_form_question is
    'Part A''s questionId -> question map, recorded per form by reading the form '
    'back after provisioning. Unlike Part B''s form_question_map, ingest never '
    'refuses on it: answers are stored raw by questionId on the immutable checkin '
    'row and resolved through this table at READ time, so a missing map is a '
    'warning that re-provisioning repairs. Replaced wholesale on re-record.';

-- ---------------------------------------------------------------------------
-- session_form — which set a Part A form was built from, and verified email
-- ---------------------------------------------------------------------------

alter table session_form
    add column if not exists question_set_id uuid
        references part_a_question_set (question_set_id);

alter table session_form
    add column if not exists email_collection_verified_at timestamptz;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid = 'session_form'::regclass
           and conname = 'session_form_question_set_part_a_only'
    ) then
        alter table session_form
            add constraint session_form_question_set_part_a_only
            check (part = 'a' or question_set_id is null);
    end if;
end
$$;

comment on column session_form.question_set_id is
    'Part A only: the question set this form was built from. The published form '
    'keeps these questions even if the cohort default is edited later.';

comment on column session_form.email_collection_verified_at is
    'When THIS form (not only its template) was read back as collecting '
    'VERIFIED email. Attendance rests on the Google-confirmed address, so it is '
    'checked on the copy that fellows actually answer.';

-- ---------------------------------------------------------------------------
-- checkin — the raw answers; the passphrase becomes legacy
-- ---------------------------------------------------------------------------

alter table checkin add column if not exists form_id text;
alter table checkin add column if not exists answers jsonb not null default '{}'::jsonb;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid = 'checkin'::regclass and conname = 'checkin_answers_is_object'
    ) then
        alter table checkin
            add constraint checkin_answers_is_object check (jsonb_typeof(answers) = 'object');
    end if;
end
$$;

-- Nullable and without a default: a row written from now on has no passphrase
-- and says so with NULL, rather than with an empty string that reads as "typed
-- nothing".
alter table checkin alter column passphrase_raw drop not null;
alter table checkin alter column passphrase_raw drop default;
alter table checkin alter column passphrase_match drop not null;

comment on column checkin.form_id is
    'The form the response came from (API path). NULL on the CSV path and on '
    'rows ingested before Part A became the exit ticket.';

comment on column checkin.answers is
    '{"<questionId>": {"values": ["…", …], "title": "…"|null}} exactly as the Forms '
    'API returned them: multi-select answers stay separate values, never joined. '
    'Keyed by questionId and resolved to question keys at read time through '
    'part_a_form_question (v_checkin_answer). Counted, never graded; no AI reads it.';

comment on column checkin.passphrase_raw is
    'LEGACY (passphrase era, before the exit ticket). NULL on every row written '
    'since. Kept so decisions made on it stay explainable.';

comment on column checkin.passphrase_match is
    'LEGACY (passphrase era). NULL on every row written since the exit ticket.';

comment on column checkin.edit_distance is
    'LEGACY (passphrase era). NULL on every row written since the exit ticket.';

comment on column "session".passphrase is
    'LEGACY. Part A no longer uses a passphrase; kept so passphrase-era '
    'check-ins remain explainable. Nothing writes it any more.';

comment on column form_template.part is
    'a = Part A, the exit ticket; b = the end-of-session check-in. Each part has '
    'its own template form and its own Verified-email confirmation, because the '
    'setting lives on the form and is only carried by a Drive copy. The Part A '
    'template holds only a title and notice; its questions are written onto each '
    'session''s copy.';

-- The immutability trigger, extended to the two new observed columns. Same
-- function, same exemption (latency_seconds), two more fields opted in.
create or replace function checkin_reject_mutation() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        raise exception
            'checkin rows are immutable and are never deleted (checkin_id=%). '
            'A dropped observation is unrecoverable.', old.checkin_id
            using errcode = 'restrict_violation';
    end if;

    -- Every observed column is listed explicitly. Adding a column to `checkin`
    -- means adding it here too — which is the point: a new observation field
    -- should have to opt in to being protected, visibly.
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
            'checkin rows are immutable; only latency_seconds may be recomputed '
            '(checkin_id=%)', old.checkin_id
            using errcode = 'restrict_violation';
    end if;

    return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- v_checkin_resolved — same columns as 20260925000100_slack_bot.sql, plus the
-- exit ticket's. Appended at the end so `create or replace` keeps every
-- existing column (and everything built on the view) exactly as it was.
--
-- The window mirrors cufa.timeutil.session_window: inclusive
-- [scheduled_at_utc - grace, scheduled_at_utc + duration + grace]. Timing is an
-- observation here; what it means for attendance is attendance_decision's job.
-- ---------------------------------------------------------------------------

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
    d.created_at       as decided_at,
    c.form_id,
    c.answers,
    s.scheduled_at_utc - make_interval(mins => s.grace_minutes)
                       as window_start_utc,
    s.scheduled_at_utc + make_interval(mins => s.duration_minutes + s.grace_minutes)
                       as window_end_utc,
    c.submitted_at_utc between
        s.scheduled_at_utc - make_interval(mins => s.grace_minutes)
    and s.scheduled_at_utc + make_interval(mins => s.duration_minutes + s.grace_minutes)
                       as in_session_window,
    -- How many questions got a non-blank answer. A count, never a score: it
    -- says nothing about what was written.
    -- CASE rather than AND: SQL does not promise to evaluate the type check
    -- before the element scan, and scanning a scalar raises.
    (select count(*)
       from jsonb_each(c.answers) a
      where case when jsonb_typeof(a.value -> 'values') = 'array'
                 then exists (select 1
                                from jsonb_array_elements_text(a.value -> 'values') v
                               where btrim(v) <> '')
                 else false
            end)::integer
                       as questions_answered
from checkin c
left join "session" s on s.session_id = c.session_id
left join load_run lr on lr.load_id = c.load_id
left join v_fellow_email e
       on e.email = lower(c.submitted_email)
      and (s.cohort_id is null or e.cohort_id = s.cohort_id)
left join fellow f on f.fellow_id = e.fellow_id
left join v_current_decision d on d.checkin_id = c.checkin_id;

comment on view v_checkin_resolved is
    'Every check-in with its roster identity, current decision and session window '
    'attached. Read-time resolution, so NULL fellow_id means "not on the roster", '
    'not "row missing". in_session_window is NULL when no session matched.';

-- ---------------------------------------------------------------------------
-- v_checkin_answer — one row per (check-in, question)
--
-- Every answer the response carried, plus one empty row for each mapped
-- question it left unanswered, so "answered N of M" is a GROUP BY. An answer to
-- a question the map does not know (a teacher added one in the Forms UI, or the
-- map is missing) is still a row — with NULL question_key — never dropped.
-- ---------------------------------------------------------------------------

create or replace view v_checkin_answer
with (security_invoker = true) as
select
    c.checkin_id,
    c.session_id,
    c.form_id,
    x.question_id,
    m.question_key,
    m.item_index,
    m.kind,
    coalesce(m.question_text, x.answer ->> 'title') as question_text,
    m.question_set_id,
    case when jsonb_typeof(x.answer -> 'values') = 'array'
         then array(select jsonb_array_elements_text(x.answer -> 'values'))
         else array[]::text[]
    end as answer_values,
    case when jsonb_typeof(x.answer -> 'values') = 'array'
         then exists (select 1
                        from jsonb_array_elements_text(x.answer -> 'values') v
                       where btrim(v) <> '')
         else false
    end as has_content
from checkin c
cross join lateral (
    select a.key as question_id, a.value as answer
      from jsonb_each(c.answers) a
    union all
    select q.question_id, null::jsonb
      from part_a_form_question q
     where q.form_id = c.form_id
       and not (c.answers ? q.question_id)
) x
left join part_a_form_question m
       on m.form_id = c.form_id and m.question_id = x.question_id;

comment on view v_checkin_answer is
    'Part A answers, one row per (check-in, question), resolved through '
    'part_a_form_question at read time. answer_values keeps multi-select answers '
    'as separate values. For display and counting only — nothing here is scored.';

-- ---------------------------------------------------------------------------
-- Row Level Security: the same non-permissive stub shape as every other table.
-- Question sets are staff-authored rather than fellow data, but the rule is
-- "everything off until CU decides", not "decide table by table".
-- ---------------------------------------------------------------------------

alter table part_a_question_set  enable row level security;
alter table part_a_form_question enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'part_a_question_set'
          and policyname = 'part_a_question_set_read_todo'
    ) then
        create policy part_a_question_set_read_todo on part_a_question_set
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'part_a_form_question'
          and policyname = 'part_a_form_question_read_todo'
    ) then
        create policy part_a_form_question_read_todo on part_a_form_question
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Tier 2 is gone. Its cache stays — dropping it would destroy the record of
-- what the model said about passphrase-era rows — but nothing writes it.
-- ---------------------------------------------------------------------------

comment on table ai_adjudication_cache is
    'RETIRED. Tier 2 (AI judgment of a typed passphrase) was removed when Part A '
    'became the exit ticket; attendance no longer calls a model. Kept read-only '
    'so passphrase-era AI decisions stay explainable. Nothing writes it.';
