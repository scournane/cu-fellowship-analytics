-- Read-time convenience views.
--
-- Identity resolves HERE rather than at write time: `checkin` stores an email,
-- and the join to `fellow` happens on every read. Fixing a roster typo
-- re-attributes every historical check-in with no backfill.

create or replace view v_current_decision
with (security_invoker = true) as
select d.*
from attendance_decision d
where d.superseded_at is null;

comment on view v_current_decision is
    'The one live decision per check-in, per the partial unique index.';

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
    -- Three fallbacks, in order of how specific they are. The load run is the
    -- last of them and the reason it exists here: a check-in that matched no
    -- session AND no roster entry would otherwise have no cohort at all, and
    -- would vanish from every cohort-scoped report — which is precisely the
    -- row a person most needs to see.
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
    d.created_at       as decided_at
from checkin c
left join "session" s on s.session_id = c.session_id
left join load_run lr on lr.load_id = c.load_id
-- Left join, and only within the session's cohort: an unrecognized address
-- still comes back as a row with a NULL fellow_id rather than vanishing.
left join fellow f
       on lower(f.primary_email) = lower(c.submitted_email)
      and (s.cohort_id is null or f.cohort_id = s.cohort_id)
left join v_current_decision d on d.checkin_id = c.checkin_id;

comment on view v_checkin_resolved is
    'Every check-in with its roster identity and current decision attached. '
    'Read-time resolution, so NULL fellow_id means "not on the roster", not '
    '"row missing".';
