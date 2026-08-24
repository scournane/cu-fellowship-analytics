-- Read-time views for Part B.
--
-- Identity resolves HERE, exactly as it does for Part A: `checkin_b` stores an
-- email and the join to `fellow` happens on every read, so fixing a roster typo
-- re-attributes every historical response with no backfill.
--
-- `help_request` appears in NONE of these views. That is not an oversight to be
-- corrected later — it is design invariant 1. A view is the easiest possible way
-- for the help checkbox to leak into a count, because a view looks like a table
-- and gets joined to without anyone re-reading its definition.

create or replace view v_checkin_b_resolved
with (security_invoker = true) as
select
    b.checkin_b_id,
    b.source,
    b.source_event_id,
    b.submitted_email,
    b.submitted_at_utc,
    b.session_id,
    b.session_match,
    b.confidence_raw,
    b.takeaway_text,
    b.rotating_kind,
    b.rotating_text,
    b.shoutout_text,
    b.latency_seconds,
    b.extra_fields,
    b.ingested_at,
    s.title            as session_title,
    s.week_index,
    s.scheduled_at_utc,
    s.announced_at_utc,
    f.fellow_id,
    f.full_name,
    lr.cohort_id       as load_cohort_id,
    -- Same three fallbacks as Part A, in order of specificity, so a response
    -- that matched neither a session nor a roster entry still belongs to a
    -- cohort and cannot vanish from every cohort-scoped report.
    coalesce(f.cohort_id, s.cohort_id, lr.cohort_id) as cohort_id,
    -- Counted, never graded. A "substantive" takeaway is one with any
    -- non-whitespace content — that is the whole test, and it is deliberately
    -- not a quality judgment: grading writing penalises ESL and neurodivergent
    -- fellows for reasons that have nothing to do with engagement.
    (btrim(coalesce(b.takeaway_text, '')) <> '') as has_takeaway,
    (btrim(coalesce(b.rotating_text, '')) <> '') as has_rotating_answer,
    (btrim(coalesce(b.shoutout_text, '')) <> '') as has_shoutout
from checkin_b b
left join "session" s on s.session_id = b.session_id
left join load_run lr on lr.load_id = b.load_id
left join fellow f
       on lower(f.primary_email) = lower(b.submitted_email)
      and (s.cohort_id is null or f.cohort_id = s.cohort_id);

comment on view v_checkin_b_resolved is
    'Every Part B response with its roster identity attached. NULL fellow_id '
    'means "not on the roster", not "row missing". Carries no help-request '
    'column and never will.';

-- ---------------------------------------------------------------------------
-- Confidence, per fellow per session — the raw value, untouched.
-- ---------------------------------------------------------------------------

create or replace view v_confidence_by_fellow
with (security_invoker = true) as
select
    v.cohort_id,
    v.fellow_id,
    v.full_name,
    v.session_id,
    v.session_title,
    v.week_index,
    v.scheduled_at_utc,
    v.confidence_raw,
    v.submitted_at_utc
from v_checkin_b_resolved v
where v.confidence_raw is not null;

comment on view v_confidence_by_fellow is
    'The 1-7 value as submitted. Never rescaled and never converted to a '
    'percentage: the number means "where this person put themselves on a '
    'seven-point scale", and a percentage implies a ratio the scale does not have.';

-- ---------------------------------------------------------------------------
-- Confidence, per cohort per week — median and interquartile range.
--
-- Median, not mean. A 7-point Likert scale is ORDINAL: the distance between 3
-- and 4 is not known to equal the distance between 6 and 7, so summing the
-- values and dividing produces a number with no defined meaning. percentile_disc
-- rather than percentile_cont for the same reason — it returns an actual point
-- on the scale rather than interpolating a 4.5 that nobody could have selected.
-- ---------------------------------------------------------------------------

create or replace view v_confidence_trend
with (security_invoker = true) as
select
    cohort_id,
    week_index,
    session_id,
    session_title,
    min(scheduled_at_utc)                                                as scheduled_at_utc,
    count(*)                                                             as responses,
    count(distinct fellow_id)                                            as fellows,
    percentile_disc(0.5)  within group (order by confidence_raw)::int    as median,
    percentile_disc(0.25) within group (order by confidence_raw)::int    as q1,
    percentile_disc(0.75) within group (order by confidence_raw)::int    as q3,
    (percentile_disc(0.75) within group (order by confidence_raw)
     - percentile_disc(0.25) within group (order by confidence_raw))::int as iqr,
    min(confidence_raw)                                                  as lowest,
    max(confidence_raw)                                                  as highest
from v_confidence_by_fellow
group by cohort_id, week_index, session_id, session_title;

comment on view v_confidence_trend is
    'Cohort confidence by week: median and IQR, never a mean. Absolute self-rated '
    'confidence is noisy and weakly calibrated — the signal is the TREND and the '
    'DIP, not the level. A fellow moving 6 to 3 across two sessions is '
    'informative; a fellow sitting flat at 4 mostly is not.';

-- ---------------------------------------------------------------------------
-- Straight-lining — a DATA QUALITY flag on the response, not a judgment about
-- the person.
--
-- Identifies runs of an identical confidence value across consecutive Part B
-- submissions by one fellow, ordered by session time. "Consecutive" means
-- consecutive among the sessions they actually answered, which is the only
-- definition that survives a fellow missing a week.
--
-- Gaps-and-islands: subtracting a per-value row number from a per-fellow row
-- number gives a constant for each run of equal values, which is then grouped.
--
-- This never enters a participation signal. It surfaces in the review screen so
-- a human can decide whether the responses are worth reading, and nowhere else.
-- ---------------------------------------------------------------------------

create or replace view v_confidence_straightline
with (security_invoker = true) as
with ordered as (
    select
        cohort_id,
        fellow_id,
        full_name,
        session_id,
        session_title,
        scheduled_at_utc,
        confidence_raw,
        row_number() over (partition by cohort_id, fellow_id
                           order by scheduled_at_utc, session_id)
          - row_number() over (partition by cohort_id, fellow_id, confidence_raw
                               order by scheduled_at_utc, session_id) as run_key
    from v_confidence_by_fellow
    where fellow_id is not null
),
runs as (
    select
        cohort_id,
        fellow_id,
        full_name,
        confidence_raw,
        count(*)                  as run_length,
        min(scheduled_at_utc)     as first_session_at,
        max(scheduled_at_utc)     as last_session_at,
        array_agg(session_title order by scheduled_at_utc) as session_titles
    from ordered
    group by cohort_id, fellow_id, full_name, confidence_raw, run_key
)
select *
from runs
where run_length >= 4;

comment on view v_confidence_straightline is
    'Fellows who submitted the same confidence value four or more sessions in a '
    'row. A DATA QUALITY flag on the responses, not a judgment about the person, '
    'and never an input to any participation signal — fatigued respondents '
    'straight-line roughly a third more often, which is a fact about the survey
     rather than about them.';

-- ---------------------------------------------------------------------------
-- Shoutouts awaiting a human
-- ---------------------------------------------------------------------------

create or replace view v_shoutout_review
with (security_invoker = true) as
select
    p.shoutout_id,
    p.checkin_b_id,
    p.raw_text,
    p.match_method,
    p.created_at,
    b.session_id,
    s.title      as session_title,
    s.cohort_id,
    b.submitted_at_utc
from peer_shoutout p
join checkin_b b on b.checkin_b_id = p.checkin_b_id
left join "session" s on s.session_id = b.session_id
where p.match_method = 'unresolved';

comment on view v_shoutout_review is
    'Shoutout fragments no exact roster match resolved: ambiguous names, and '
    'names matching nobody. The second kind is LEGAL, not an error — guest '
    'speakers and staff get thanked too.';

-- ---------------------------------------------------------------------------
-- Current themes
-- ---------------------------------------------------------------------------

create or replace view v_muddiest_theme_current
with (security_invoker = true) as
select t.*, (select count(*) from muddiest_theme_member m where m.theme_id = t.theme_id) as members
from muddiest_theme t
where t.superseded_at is null;

comment on view v_muddiest_theme_current is
    'The live clustering per session. Regenerating supersedes rather than '
    'overwrites, so this is a filter and not the whole table.';
