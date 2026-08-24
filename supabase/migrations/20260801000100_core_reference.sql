-- Core reference data: who the fellows are, which cohort they belong to, and
-- which live sessions exist.
--
-- Everything downstream is cohort-keyed so that a second year of the fellowship
-- can be compared against the first without disentangling it from this one.

create table if not exists cohort (
    cohort_id   text primary key,
    label       text        not null,
    starts_on   date,
    ends_on     date,
    created_at  timestamptz not null default now(),
    constraint cohort_dates_ordered check (
        starts_on is null or ends_on is null or starts_on <= ends_on
    )
);

comment on table cohort is
    'A fellowship cohort. Every observation and decision is keyed to one.';

-- The roster. `fellow_id` is CU-issued and stable across years; we never mint
-- our own identifier for a person, because CU already has one and two competing
-- identifiers is how rosters drift.
create table if not exists fellow (
    fellow_id     text primary key,
    cohort_id     text        not null references cohort (cohort_id) on delete restrict,
    full_name     text        not null,
    primary_email text        not null,
    status        text        not null default 'active'
                  check (status in ('active', 'withdrawn', 'deferred', 'alumni')),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- One address per fellow per cohort. Identity resolution joins on this, so a
-- duplicate here would make attribution ambiguous rather than merely wrong.
create unique index if not exists fellow_cohort_email_uniq
    on fellow (cohort_id, lower(primary_email));

create index if not exists fellow_cohort_idx on fellow (cohort_id);

comment on column fellow.primary_email is
    'Normalized (trimmed, lowercased) at write time. Gmail dots and +suffixes '
    'are preserved verbatim — collapsing them is a lossy guess that can merge '
    'two people.';

-- A live lesson. Local wall-clock values are kept alongside the UTC values they
-- were derived from: the local values are what a human typed and can verify,
-- the UTC values are what every comparison actually uses.
create table if not exists "session" (
    session_id         uuid        primary key default gen_random_uuid(),
    cohort_id          text        not null references cohort (cohort_id) on delete restrict,
    title              text        not null,
    scheduled_at_local timestamp   not null,
    timezone           text        not null,
    scheduled_at_utc   timestamptz not null,
    duration_minutes   integer     not null check (duration_minutes > 0),
    grace_minutes      integer     not null default 15 check (grace_minutes >= 0),
    passphrase         text,
    announced_at_utc   timestamptz,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create index if not exists session_cohort_scheduled_idx
    on "session" (cohort_id, scheduled_at_utc);

comment on column "session".passphrase is
    'Nullable on purpose: a session with no passphrase is legal and adjudicates '
    'as not_set rather than as a failure.';

comment on column "session".announced_at_utc is
    'Stamped by the console''s "Announce now" button. Latency is measured from '
    'this when set, and from the earliest matched submission when it is not.';

comment on column "session".grace_minutes is
    'Widens the matching window on BOTH sides of the scheduled block.';
