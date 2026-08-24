-- Row Level Security on the tables that were left out.
--
-- Found by probing the running database as the `authenticated` role rather than
-- by reading the migrations: `checkin_b` and `peer_shoutout` filtered correctly,
-- and `muddiest_theme` returned its rows. Reading a migration tells you what
-- somebody intended; asking the database tells you what is true.
--
-- Everything here follows the same shape as Part A's stub in
-- ..._rls.sql: RLS on, a non-permissive `TODO(access)` policy, and nothing
-- granted until CU decides the real rule. The service role bypasses RLS, so the
-- pipeline and the console are unaffected.

-- ---------------------------------------------------------------------------
-- muddiest_theme / muddiest_theme_member
--
-- These are AI-generated summaries of what fellows found confusing, plus the
-- links from each theme to the individual answers behind it. That is derived
-- fellow content, and it had no protection at all while the table it derives
-- from — checkin_b — was locked down. A theme label is aggregate, but
-- muddiest_theme_member points straight at single responses.
-- ---------------------------------------------------------------------------

alter table muddiest_theme        enable row level security;
alter table muddiest_theme_member enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'muddiest_theme'
          and policyname = 'muddiest_theme_read_todo'
    ) then
        create policy muddiest_theme_read_todo on muddiest_theme
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;

    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'muddiest_theme_member'
          and policyname = 'muddiest_theme_member_read_todo'
    ) then
        -- TODO(access): this one links a theme to a specific response. Whatever
        -- rule covers checkin_b has to cover this, or the join leaks what the
        -- row itself does not.
        create policy muddiest_theme_member_read_todo on muddiest_theme_member
            for select to authenticated
            using (false);
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- identity_unresolved
--
-- Part A's table, and the same oversight: `fellow` is protected and this is
-- not, while both hold email addresses. These are addresses that submitted a
-- check-in and matched nobody on the roster — so if anything they are *less*
-- accounted for than a roster entry, not more.
-- ---------------------------------------------------------------------------

alter table identity_unresolved enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public' and tablename = 'identity_unresolved'
          and policyname = 'identity_unresolved_read_todo'
    ) then
        create policy identity_unresolved_read_todo on identity_unresolved
            for select to authenticated
            using (false);  -- TODO(access): replace `false` with CU's rule.
    end if;
end
$$;

comment on table muddiest_theme is
    'AI clustering output, per session. Append-only: regenerating stamps '
    'superseded_at on the previous batch and inserts a new one, so what a teacher '
    'was shown last week is still recoverable. RLS on with a non-permissive stub, '
    'matching checkin_b — these are summaries of fellows'' own words.';
