-- Applied automatically by `supabase db reset`.
--
-- Deliberately thin: it creates the cohorts the demo and the console expect to
-- exist, and nothing else. Fellows and sessions arrive through
-- `cufa load-roster` / `cufa load-sessions`, because that is the path CU staff
-- actually use — seeding them here would exercise a path nobody runs.

insert into cohort (cohort_id, label, starts_on, ends_on)
values
    ('cu-2026', 'Civic Innovators Fellowship 2026', date '2026-09-01', date '2026-12-15'),
    ('demo',    'Demo cohort (synthetic data)',      date '2026-09-01', date '2026-10-31')
on conflict (cohort_id) do nothing;
