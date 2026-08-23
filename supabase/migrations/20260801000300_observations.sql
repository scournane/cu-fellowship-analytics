-- Observations: what was actually seen. Immutable.
--
-- Invariant 1 (never drop a submission) and invariant 2 (separate the
-- observation from the decision) both live here. A `checkin` row is a fact
-- about what arrived; it carries no judgment about whether it counts.

-- Provenance for every batch of rows written, so "where did this come from?"
-- is answerable without archaeology.
create table if not exists load_run (
    load_id       uuid        primary key default gen_random_uuid(),
    source        text        not null check (source in ('forms_api', 'csv')),
    origin        text        not null,
    input_sha256  text,
    cohort_id     text        references cohort (cohort_id),
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    rows_read     integer     not null default 0,
    rows_written  integer     not null default 0,
    rows_skipped  integer     not null default 0,
    status        text        not null default 'running'
                  check (status in ('running', 'succeeded', 'failed')),
    error         text
);

comment on column load_run.input_sha256 is
    'SHA-256 of the input bytes on the CSV path, so a re-import of a changed '
    'file is distinguishable from a re-import of the same file.';

comment on column load_run.origin is
    'Form id on the API path, file path on the CSV path.';

-- One row per response. Never updated, never deleted.
create table if not exists checkin (
    checkin_id       uuid        primary key default gen_random_uuid(),

    -- Idempotency key: SHA-256 of (form_id_or_file, normalized_email,
    -- submitted_at_utc_iso). Deliberately NOT the row number and NOT the Forms
    -- responseId, so the same response ingested by either path collides here.
    source_event_id  text        not null unique,
    source           text        not null check (source in ('forms_api', 'csv')),

    submitted_email  text        not null,
    submitted_at_utc timestamptz not null,
    submitted_at_raw text        not null,
    -- NULL on the API path: forms.responses.list returns RFC3339 UTC already,
    -- so there is no zone to record and no conversion to audit.
    source_timezone  text,

    -- restrict, not set null: `checkin` is immutable, so a cascading UPDATE of
    -- this column could not run anyway — it would fail inside the trigger with
    -- a message about immutability instead of about the session being deleted.
    -- A session with observations attached cannot be deleted, and the error
    -- should say so.
    session_id       uuid        references "session" (session_id) on delete restrict,
    session_match    text        not null check (session_match in ('matched', 'none', 'ambiguous')),

    passphrase_raw   text        not null default '',
    passphrase_match text        not null
                     check (passphrase_match in ('exact', 'fuzzy', 'mismatch', 'not_set', 'no_session')),
    edit_distance    integer,
    latency_seconds  integer,

    extra_fields     jsonb       not null default '{}'::jsonb,
    load_id          uuid        references load_run (load_id) on delete set null,
    ingested_at      timestamptz not null default now()
);

comment on table checkin is
    'Immutable observation. Stores the EMAIL, not a fellow_id: identity resolves '
    'at read time by joining the roster, so correcting a roster entry '
    're-attributes all history with no backfill.';

comment on column checkin.session_match is
    'matched | none | ambiguous. The row is written in all three cases — a '
    'dropped row is an unrecoverable observation, and hides exactly the cases '
    'worth looking at.';

comment on column checkin.latency_seconds is
    'Seconds between announcement and submission. Stored, never interpreted: '
    'no thresholds, no flags. NULL when no session matched.';

create index if not exists checkin_session_idx  on checkin (session_id);
create index if not exists checkin_email_idx    on checkin (lower(submitted_email));
create index if not exists checkin_load_idx     on checkin (load_id);
create index if not exists checkin_submitted_idx on checkin (submitted_at_utc);

-- Invariant 6: identity never blocks ingest. An address we cannot place still
-- produces a checkin row; it lands here for a human to reconcile.
create table if not exists identity_unresolved (
    unresolved_id    uuid        primary key default gen_random_uuid(),
    cohort_id        text        not null references cohort (cohort_id) on delete cascade,
    email            text        not null,
    first_seen_at    timestamptz not null default now(),
    last_seen_at     timestamptz not null default now(),
    occurrence_count integer     not null default 1,
    best_guess_fellow_id text    references fellow (fellow_id) on delete set null,
    best_guess_score numeric(4, 3),
    resolved_at      timestamptz
);

create unique index if not exists identity_unresolved_cohort_email_uniq
    on identity_unresolved (cohort_id, email);

comment on column identity_unresolved.best_guess_fellow_id is
    'Advisory only. We never auto-link on a fuzzy name guess — a wrong link is '
    'worse than an unmatched address because it is invisible.';

-- Every provisioning attempt, successful or not. Trap handling is only
-- trustworthy if the failures are visible.
create table if not exists provisioning_log (
    log_id          uuid        primary key default gen_random_uuid(),
    session_id      uuid        references "session" (session_id) on delete cascade,
    action          text        not null,
    request_summary jsonb       not null default '{}'::jsonb,
    outcome         text        not null check (outcome in ('success', 'failure', 'skipped', 'dry_run')),
    error           text,
    at              timestamptz not null default now()
);

create index if not exists provisioning_log_session_idx on provisioning_log (session_id, at desc);
