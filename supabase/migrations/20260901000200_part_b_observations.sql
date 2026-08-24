-- Part B observations: what a fellow said at the end of the session.
--
-- Same shape as Part A's `checkin`, and for the same reasons: one row per
-- submission, immutable, never dropped, identity resolved at read time by
-- joining the roster on the stored email rather than by writing a fellow_id in.
--
-- Part A and Part B are INDEPENDENT observations. A fellow may submit one and
-- not the other; both cases are valid data, not errors. Nothing here references
-- `checkin`, and nothing may ever be used to backfill it — the two are joined
-- on (fellow, session) when read, never merged when written.

create table if not exists checkin_b (
    checkin_b_id     uuid        primary key default gen_random_uuid(),

    -- Same hashing as Part A: SHA-256 of (form_id_or_cohort, normalized email,
    -- UTC second). Part B forms have their own form ids, so a Part B response
    -- can never collide with a Part A one — and a CSV re-import of responses
    -- already pulled from the API collides with itself, which is the point.
    source_event_id  text        not null unique,
    source           text        not null check (source in ('forms_api', 'csv')),

    submitted_email  text        not null,
    submitted_at_utc timestamptz not null,

    session_id       uuid        references "session" (session_id) on delete restrict,
    session_match    text        not null check (session_match in ('matched', 'none', 'ambiguous')),

    -- 1..7 as submitted. Never rescaled, never normalized, never converted to a
    -- percentage on write. Out-of-range values land as NULL with the raw string
    -- kept in extra_fields — clamping silently would turn a broken form into
    -- plausible data.
    confidence_raw   integer     check (confidence_raw is null or (confidence_raw between 1 and 7)),

    takeaway_text    text,
    rotating_kind    text
                     check (rotating_kind is null or
                            rotating_kind in ('teacher_question', 'muddiest_point', 'application')),
    rotating_text    text,
    shoutout_text    text,

    latency_seconds  integer,

    extra_fields     jsonb       not null default '{}'::jsonb,
    load_id          uuid        references load_run (load_id) on delete set null,
    ingested_at      timestamptz not null default now()
);

comment on table checkin_b is
    'Immutable Part B observation. Note what is NOT here: the help checkbox. It '
    'lives in its own table with its own access rules and is excluded from every '
    'count, score, rate and aggregate — see the help_request migration.';

comment on column checkin_b.confidence_raw is
    'The integer 1-7 exactly as submitted. Read-time views expose median and IQR; '
    'a mean is never taken, because a 7-point Likert scale is ordinal and the mean '
    'of ordinal data is not a meaningful quantity.';

comment on column checkin_b.takeaway_text is
    'Stored verbatim, whitespace included. Counted, never graded: recording that a '
    'substantive response exists is fair, rating how well written it is penalises '
    'ESL and neurodivergent fellows for reasons unrelated to engagement.';

comment on column checkin_b.rotating_kind is
    'Which of the three dimensions this week''s slot asked. Travels with the answer '
    'so a later config change cannot re-label historical responses.';

create index if not exists checkin_b_session_idx   on checkin_b (session_id);
create index if not exists checkin_b_email_idx     on checkin_b (lower(submitted_email));
create index if not exists checkin_b_load_idx      on checkin_b (load_id);
create index if not exists checkin_b_submitted_idx on checkin_b (submitted_at_utc);
create index if not exists checkin_b_rotating_idx  on checkin_b (session_id, rotating_kind);

-- ---------------------------------------------------------------------------
-- peer_shoutout — one row per NAME extracted, because a fellow may name several.
--
-- A shoutout is data about a THIRD PARTY who did not submit it. It gets the same
-- protection as the submitter's own data, and it is never surfaced to the person
-- named without an explicit decision by the data owner.
-- ---------------------------------------------------------------------------

create table if not exists peer_shoutout (
    shoutout_id     uuid        primary key default gen_random_uuid(),
    checkin_b_id    uuid        not null references checkin_b (checkin_b_id) on delete cascade,

    -- The fragment as typed, before any normalization. What someone actually
    -- wrote is the observation; the resolution below is the judgment about it.
    raw_text        text        not null,

    named_fellow_id text        references fellow (fellow_id) on delete set null,
    match_method    text        not null
                    check (match_method in ('exact_name', 'manual', 'unresolved')),
    confidence      numeric(4, 3) check (confidence is null or (confidence >= 0 and confidence <= 1)),
    resolved_by     text,
    resolved_at     timestamptz,
    created_at      timestamptz not null default now(),

    -- A link must say who or what made it. An automatic exact match and a human
    -- decision are both accountable; an unattributed link is not.
    constraint peer_shoutout_link_has_provenance check (
        (match_method = 'unresolved' and named_fellow_id is null) or
        (match_method = 'exact_name' and named_fellow_id is not null) or
        (match_method = 'manual'     and named_fellow_id is not null and resolved_by is not null)
    )
);

create index if not exists peer_shoutout_checkin_idx on peer_shoutout (checkin_b_id);
create index if not exists peer_shoutout_fellow_idx  on peer_shoutout (named_fellow_id);
create index if not exists peer_shoutout_unresolved_idx
    on peer_shoutout (created_at) where match_method = 'unresolved';

comment on table peer_shoutout is
    'Names a fellow typed when asked who helped them. Collected and resolved '
    'only — there is deliberately no leaderboard, ranking, points table or public '
    'display. See ADR-028 for the finding that recognition, if ever ranked, should '
    'be ranked by giving rather than receiving.';

comment on column peer_shoutout.match_method is
    'exact_name = one unambiguous roster match within the cohort. manual = a human '
    'linked it. unresolved = ambiguous, or matched nobody. Ambiguity is NEVER '
    'resolved by coin flip: attributing praise to the wrong person is worse than '
    'leaving it unattached, because a wrong link is invisible.';

-- ---------------------------------------------------------------------------
-- muddiest_theme — the one place a model touches Part B.
--
-- Clustering is about CONTENT, not people: the model receives anonymous strings
-- and returns themes. Regenerating supersedes rather than overwrites, so a
-- teacher who read last week's themes can still see what they read.
-- ---------------------------------------------------------------------------

create table if not exists muddiest_theme (
    theme_id       uuid        primary key default gen_random_uuid(),
    session_id     uuid        not null references "session" (session_id) on delete cascade,
    label          text        not null,
    summary        text        not null,
    model          text        not null,
    prompt_version text        not null,
    generated_at   timestamptz not null default now(),
    superseded_at  timestamptz
);

create index if not exists muddiest_theme_session_idx
    on muddiest_theme (session_id, generated_at desc);

create index if not exists muddiest_theme_current_idx
    on muddiest_theme (session_id) where superseded_at is null;

comment on table muddiest_theme is
    'AI clustering output, per session. Append-only: regenerating stamps '
    'superseded_at on the previous batch and inserts a new one, so what a teacher '
    'was shown last week is still recoverable.';

create table if not exists muddiest_theme_member (
    theme_id     uuid not null references muddiest_theme (theme_id) on delete cascade,
    checkin_b_id uuid not null references checkin_b (checkin_b_id) on delete cascade,
    primary key (theme_id, checkin_b_id)
);

comment on table muddiest_theme_member is
    'Which answers the model put in which theme. Stored so a teacher can read the '
    'actual sentences behind a label rather than trusting the label.';

-- ---------------------------------------------------------------------------
-- Immutability, enforced rather than asserted — the twin of Part A's trigger.
--
-- latency_seconds is exempt for the same reason it is exempt on `checkin`: it is
-- derived from session state that legitimately changes after a row is written.
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

    -- Every observed column listed explicitly, so adding one means visibly
    -- opting it in to protection.
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
            'checkin_b rows are immutable; only latency_seconds may be recomputed '
            '(checkin_b_id=%)', old.checkin_b_id
            using errcode = 'restrict_violation';
    end if;

    return new;
end;
$$;

drop trigger if exists checkin_b_no_mutation on checkin_b;
create trigger checkin_b_no_mutation
    before update or delete on checkin_b
    for each row execute function checkin_b_reject_mutation();

comment on function checkin_b_reject_mutation() is
    'Blocks every UPDATE to an observed column and every DELETE on checkin_b. '
    'latency_seconds is exempt because it is derived from session state that '
    'legitimately changes after ingest.';
