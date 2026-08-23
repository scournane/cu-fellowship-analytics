-- Invariant 2, enforced rather than asserted.
--
-- `checkin` is the observation: what arrived, exactly as it arrived. Nothing in
-- the application is allowed to rewrite it — a corrected judgment is a new
-- `attendance_decision`, not an edited observation. A comment saying so is not
-- an enforcement mechanism, so this is a trigger.
--
-- One column is exempt. `latency_seconds` is *derived* from session state
-- (`announced_at_utc`, or the earliest matched submission when it is unset),
-- and that state legitimately changes after a row is written: a teacher presses
-- "Announce now" after the first fellow has already submitted, or a later
-- backfill introduces an earlier T0. It is a cached derivation of other rows,
-- not an observation, so recomputing it is not mutating what was seen.

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
           new.load_id, new.ingested_at)
       is distinct from
       row(old.checkin_id, old.source_event_id, old.source, old.submitted_email,
           old.submitted_at_utc, old.submitted_at_raw, old.source_timezone,
           old.session_id, old.session_match, old.passphrase_raw,
           old.passphrase_match, old.edit_distance, old.extra_fields,
           old.load_id, old.ingested_at)
    then
        raise exception
            'checkin rows are immutable; only latency_seconds may be recomputed '
            '(checkin_id=%)', old.checkin_id
            using errcode = 'restrict_violation';
    end if;

    return new;
end;
$$;

drop trigger if exists checkin_no_mutation on checkin;
create trigger checkin_no_mutation
    before update or delete on checkin
    for each row execute function checkin_reject_mutation();

comment on function checkin_reject_mutation() is
    'Blocks every UPDATE to an observed column and every DELETE on checkin. '
    'latency_seconds is exempt because it is derived from session state that '
    'legitimately changes after ingest.';
