-- Decisions: the judgment about an observation. Append-only and versioned.
--
-- Invariant 3: every decision carries its provenance — which rule, which model,
-- which human, and when. "Why is this fellow marked absent?" has to be
-- answerable months later without guessing.

create table if not exists attendance_decision (
    decision_id      uuid        primary key default gen_random_uuid(),
    checkin_id       uuid        not null references checkin (checkin_id) on delete cascade,

    -- Nullable on purpose. NULL means "we do not know", which is exactly what
    -- needs_review means. Absent evidence is not evidence of absence.
    attended         boolean,
    status           text        not null
                     check (status in ('attended', 'not_attended', 'needs_review')),
    confidence       numeric(4, 3) check (confidence is null or (confidence >= 0 and confidence <= 1)),

    decided_by       text        not null check (decided_by in ('rule', 'ai', 'human')),
    rule_name        text,
    ai_model         text,
    ai_prompt_version text,
    ai_reasoning     text,
    human_email      text,
    note             text,

    superseded_at    timestamptz,
    created_at       timestamptz not null default now(),

    -- Keeps status and attended from drifting apart.
    constraint decision_status_matches_attended check (
        (status = 'attended'     and attended is true)  or
        (status = 'not_attended' and attended is false) or
        (status = 'needs_review' and attended is null)
    ),

    -- Provenance must actually be present for the tier that claims it.
    constraint decision_provenance_present check (
        (decided_by = 'rule'  and rule_name   is not null) or
        (decided_by = 'ai'    and ai_model    is not null and ai_prompt_version is not null) or
        (decided_by = 'human' and human_email is not null)
    )
);

-- Exactly one *current* decision per check-in. Superseding is an UPDATE of
-- superseded_at followed by an INSERT — never an in-place edit of a decision.
create unique index if not exists attendance_decision_one_current
    on attendance_decision (checkin_id)
    where superseded_at is null;

create index if not exists attendance_decision_checkin_idx
    on attendance_decision (checkin_id, created_at desc);

create index if not exists attendance_decision_current_status_idx
    on attendance_decision (status)
    where superseded_at is null;

comment on table attendance_decision is
    'Append-only judgment over an immutable observation. Versioning is what '
    'makes a human override auditable and lets a changed definition of '
    '"attended" be re-run over history.';

comment on column attendance_decision.superseded_at is
    'NULL means this is the current decision. The partial unique index enforces '
    'that there is exactly one such row per check-in.';

-- Tier 2 is a paid, rate-limited, non-deterministic call. The same pair of
-- strings must never be sent twice.
create table if not exists ai_adjudication_cache (
    expected_normalized  text          not null,
    submitted_normalized text          not null,
    prompt_version       text          not null,
    model                text          not null,
    verdict              boolean       not null,
    confidence           numeric(4, 3) not null,
    reasoning            text          not null,
    created_at           timestamptz   not null default now(),
    primary key (expected_normalized, submitted_normalized, prompt_version, model)
);

comment on table ai_adjudication_cache is
    'Keyed on the two normalized strings plus prompt version and model — the '
    'complete input to the call. Bumping PROMPT_VERSION invalidates cleanly '
    'instead of silently reusing verdicts from a different prompt.';
