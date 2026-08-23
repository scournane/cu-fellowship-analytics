-- Row Level Security.
--
-- The pipeline and the console connect as the service role, which bypasses RLS.
-- RLS is enabled here so that anything ELSE reaching this database — a Studio
-- session on an anon key, a future dashboard, a curious integration — is denied
-- by default rather than allowed by default.

-- Supabase provisions these roles. Created here if absent so the same
-- migrations apply to a plain Postgres instance without editing.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin noinherit bypassrls;
    end if;
end
$$;

alter table fellow              enable row level security;
alter table checkin             enable row level security;
alter table attendance_decision enable row level security;
alter table google_credential   enable row level security;

-- google_credential holds an encrypted refresh token. Nothing that is not the
-- service role has any reason to read it, so it gets no policy at all: RLS
-- enabled with zero policies denies every non-bypassing role.
revoke all on google_credential from anon, authenticated;

-- ---------------------------------------------------------------------------
-- TODO(access): define the real read policies.
--
-- CU has said the data should be visible to every full-time team member, but
-- has not defined granular permissions — and a derived attendance judgment
-- should not automatically be as open as a raw timestamp. Writing a policy now
-- would be inventing that distinction on CU's behalf, and an invented access
-- policy quietly becomes the real one.
--
-- What has to be decided before these are written:
--   * Is `fellow` (names + addresses) readable by the same people as `checkin`?
--   * Is a *decision* ("marked absent") more restricted than an *observation*
--     ("submitted at 10:14")?
--   * Do fellows themselves ever read their own rows? If so, on what claim?
--
-- The stubs below are deliberately non-permissive: they document the shape a
-- real policy takes here and grant nothing until someone fills in the predicate.
-- ---------------------------------------------------------------------------

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'fellow' and policyname = 'fellow_read_todo'
    ) then
        create policy fellow_read_todo on fellow
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'checkin' and policyname = 'checkin_read_todo'
    ) then
        create policy checkin_read_todo on checkin
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'attendance_decision' and policyname = 'attendance_decision_read_todo'
    ) then
        create policy attendance_decision_read_todo on attendance_decision
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;
end
$$;
