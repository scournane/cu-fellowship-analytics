"""The bot noticing its own failures, and saying so in the staff channel.

Three things live here, and they are one idea: silent failure is the failure.
This install moved off a laptop onto Vercel because when the laptop slept the
reminders stopped and nobody found out until a fellow did not get one. Moving
host removed the sleeping laptop; it did not add anybody watching.

* **A dead-man switch.** When nothing has reached the bot for
  ``CUFA_ALERT_SILENCE_HOURS``, the staff channel is told — once per outage,
  with a recovery notice when data resumes.
* **A scheduler-gap notice.** When the external ``pg_cron`` job stops calling
  ``POST /bot/cron/tick`` and later resumes, the tick that resumes says how
  long it was gone. That is the laptop-sleep failure, reported out loud.
* **Tick errors in the channel, not only in a log.** ``tick`` already collects
  a list of step failures and the cron route already answers 200 with them in
  the body so the scheduler keeps calling. Nobody reads that body. These go to
  Slack, fingerprinted and rate limited, so a persistent error says it is
  persisting instead of arriving every minute.
* **Automatic backfill**, so a gap heals itself rather than waiting for
  somebody to remember ``cufa slack backfill``.

Why not ``load_run`` for liveness
---------------------------------
``EventProcessor.start()`` opens a ``load_run`` row and leaves it ``running``
until ``stop()``. On a long-lived process a still-``running`` row with no newer
one IS the record that the collector died. On serverless it means nothing of
the kind: every cold start opens a row, ``atexit`` closes it only on a graceful
shutdown, and an idle instance the platform reclaims without warning leaves it
open forever. The table therefore accumulates rows that look exactly like
crashes and are not — FINDINGS F-14, and RUNBOOK section 9 says to stop reading
it. An alert built on that signal would fire on the ordinary case and teach
staff to ignore it, which is worse than no alert.

So liveness here is measured from two things that cannot lie on this
deployment:

1. **The freshness of really ingested data** — ``max(slack_event.received_at)``
   over rows that did NOT come from a backfill. ``received_at`` is when the bot
   wrote the row, so it answers "has anything reached us", and excluding
   backfill rows keeps a recovery walk from masking a broken event delivery.
   Before any event exists the anchor is ``slack_workspace.connected_at`` —
   nothing has arrived since the bot was installed, which is the true statement
   — rather than ``last_seen_at``, which every cold start refreshes and which
   would therefore look healthy exactly when delivery is broken.
2. **The heartbeat of the last completed tick** — a row this module writes only
   after a tick has got this far. It is the only in-band evidence that the
   external scheduler is still calling, and unlike ``load_run`` it is written
   on success and never left half-open.

Neither can be checked by a process that is not running, and that limit is real:
if the tick stops entirely, nothing here can post. What the heartbeat buys is
that the outage is reported when the tick comes back, instead of never. A truly
external watchdog (an uptime pinger against ``/bot/health``) is the missing
third leg and is not something this codebase can install for itself.

Every message posted from here goes through :func:`scrub`. Alerts are
operational and no operational message in this codebase names a fellow's email
address; an error string that happens to carry one must not be the exception.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import psycopg

from ..config import Settings, get_settings
from ..db import execute, fetch_all, fetch_one
from ..errors import CufaError
from ..logging_setup import get_logger
from .client import SlackApiError, SlackClient

log = get_logger(__name__)

#: The dead-man switch. One row, one outage at a time.
ALERT_LIVENESS = "liveness"
#: One key per fingerprinted tick error: ``tick_error:<16 hex>``.
ERROR_PREFIX = "tick_error:"
#: One key per observed scheduler gap, keyed by when the gap started so that
#: the primary key itself guarantees a gap is reported once.
GAP_PREFIX = "tick_gap:"

#: Heartbeat names.
BEAT_TICK = "tick"
BEAT_BACKFILL = "auto_backfill"

#: Anything shaped like an address. Deliberately greedy about the local part:
#: over-redacting an operational message costs nothing, under-redacting it puts
#: a young person's address in a channel that is searchable and exportable.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
REDACTED = "<address withheld>"

#: Digits and uuids are stripped before an error is fingerprinted, so "session
#: 4f2c… failed" and "session 91ab… failed" are one recurring error rather than
#: an unbounded stream of new ones.
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_DIGITS = re.compile(r"\d+")


def scrub(text: str) -> str:
    """An email address never reaches Slack, a log line or the alert table."""
    return _EMAIL.sub(REDACTED, text)


def fingerprint(error: str) -> str:
    """A stable id for "the same error again".

    Ids, counts and timestamps inside the message are normalised away first:
    without that, a failing step that names a session id produces a fresh
    fingerprint every tick and the rate limit never engages.
    """
    normalised = _DIGITS.sub("#", _UUID.sub("<id>", scrub(error).strip().lower()))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def _hours(delta: timedelta) -> str:
    """A duration a human reads at a glance: "45m", "3.2h", "2d 4h"."""
    seconds = max(0.0, delta.total_seconds())
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f}h"
    days, rest = divmod(seconds, 86400)
    return f"{days:.0f}d {rest / 3600:.0f}h"


def _stamp(at: datetime) -> str:
    """UTC, to the minute. Staff read this next to a SQL query, not a calendar."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# state: the suppression that makes one alert one alert
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertState:
    """The row as it stood BEFORE this observation."""

    exists: bool
    firing: bool
    occurrences: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    last_posted_at: datetime | None


def _prior(conn: psycopg.Connection, key: str) -> AlertState:
    row = fetch_one(
        conn,
        "select state, occurrences, first_seen_at, last_seen_at, last_posted_at "
        "from ops_alert where alert_key = %s",
        (key,),
    )
    if row is None:
        return AlertState(False, False, 0, None, None, None)
    return AlertState(
        exists=True,
        firing=row["state"] == "firing",
        occurrences=int(row["occurrences"]),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_posted_at=row["last_posted_at"],
    )


def _observe(conn: psycopg.Connection, key: str, *, now: datetime, detail: str) -> AlertState:
    """Record that the condition is true, and return what the row said before.

    ``last_posted_at`` is cleared on the ok -> firing edge. That is what makes
    the claim below mean "not yet announced for THIS episode" rather than "not
    yet announced ever" — a second outage in November has to be able to speak
    even though October's was announced.
    """
    prior = _prior(conn, key)
    execute(
        conn,
        """
        insert into ops_alert (alert_key, state, detail, first_seen_at, last_seen_at,
                               occurrences, last_posted_at, posted_count)
        values (%(key)s, 'firing', %(detail)s, %(now)s, %(now)s, 1, null, 0)
        on conflict (alert_key) do update
           set state = 'firing',
               detail = excluded.detail,
               last_seen_at = excluded.last_seen_at,
               occurrences = case when ops_alert.state = 'firing'
                                  then ops_alert.occurrences + 1 else 1 end,
               first_seen_at = case when ops_alert.state = 'firing'
                                    then ops_alert.first_seen_at else excluded.first_seen_at end,
               last_posted_at = case when ops_alert.state = 'firing'
                                     then ops_alert.last_posted_at else null end
        """,
        {"key": key, "detail": scrub(detail)[:2000], "now": now},
    )
    return prior


def _claim(conn: psycopg.Connection, key: str, *, now: datetime, cooldown: timedelta | None) -> bool:
    """Win the right to post about ``key``, or lose it quietly.

    A single conditional UPDATE, for the same reason ``digest._post`` claims its
    ``digest_log`` row before calling Slack: two ticks that overlap must produce
    one message. The loser's WHERE is re-evaluated against the winner's row
    version and matches nothing, so it returns no row and says nothing.

    ``cooldown=None`` means once per episode — the dead-man switch. A duration
    means "not again until this has elapsed", which is how a persisting error
    gets a periodic "still failing" line instead of a per-minute copy.
    """
    cutoff = None if cooldown is None else now - cooldown
    row = fetch_one(
        conn,
        """
        update ops_alert
           set last_posted_at = %(now)s,
               posted_count = posted_count + 1
         where alert_key = %(key)s
           and (last_posted_at is null
                or (%(cutoff)s::timestamptz is not null and last_posted_at <= %(cutoff)s::timestamptz))
        returning posted_count
        """,
        {"key": key, "now": now, "cutoff": cutoff},
    )
    return row is not None


def _resolve(conn: psycopg.Connection, key: str, *, now: datetime) -> AlertState | None:
    """Move a firing key back to ok, and return what it looked like while firing.

    ``None`` when it was not firing, or when another tick got there first —
    ``state = 'firing'`` in the WHERE is the claim, so exactly one recovery
    notice is posted per outage.
    """
    prior = _prior(conn, key)
    row = fetch_one(
        conn,
        """
        update ops_alert
           set state = 'ok',
               last_seen_at = %(now)s,
               last_posted_at = %(now)s,
               posted_count = posted_count + 1
         where alert_key = %(key)s and state = 'firing'
        returning posted_count
        """,
        {"key": key, "now": now},
    )
    return prior if row is not None else None


def _beat(conn: psycopg.Connection, name: str, *, now: datetime, detail: str | None = None) -> datetime | None:
    """Record a pulse and return the previous one, or None if this is the first."""
    row = fetch_one(conn, "select beat_at from ops_heartbeat where name = %s", (name,))
    previous = row["beat_at"] if row else None
    execute(
        conn,
        """
        insert into ops_heartbeat (name, beat_at, detail) values (%(name)s, %(now)s, %(detail)s)
        on conflict (name) do update set beat_at = excluded.beat_at, detail = excluded.detail
        """,
        {"name": name, "now": now, "detail": (scrub(detail)[:500] if detail else None)},
    )
    return previous


def last_beat(conn: psycopg.Connection, name: str) -> datetime | None:
    """When a named pulse last fired. The health probe's read side."""
    row = fetch_one(conn, "select beat_at from ops_heartbeat where name = %s", (name,))
    return row["beat_at"] if row else None


# ---------------------------------------------------------------------------
# the signal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Liveness:
    """What the bot last received, and how we know."""

    #: None only when there is no workspace at all: nothing to be alive about.
    last_at: datetime | None
    #: Human phrase naming the evidence, for the message.
    evidence: str
    silent_for: timedelta | None
    stale: bool


def last_live_signal(conn: psycopg.Connection, *, team_id: str | None = None) -> tuple[datetime | None, str]:
    """When something last reached the bot, and what that something was.

    Backfilled rows are excluded on purpose. A backfill re-reads what the live
    path missed, so counting it would let the automatic recovery walk below
    hold the switch open-eyed while Slack delivery has been broken for a week.
    ``load_run.source`` is read here only as a LABEL — which is reliable — and
    never as a status, which is the part F-14 says cannot be trusted.
    """
    row = fetch_one(
        conn,
        """
        select max(e.received_at) as t
          from slack_event e
          left join load_run r on r.load_id = e.load_id
         where (%(team)s::text is null or e.team_id = %(team)s::text)
           and coalesce(r.source, '') <> 'slack_backfill'
        """,
        {"team": team_id},
    )
    if row and row["t"] is not None:
        return row["t"], "a Slack event the bot recorded"
    row = fetch_one(
        conn,
        "select min(connected_at) as t from slack_workspace "
        "where (%(team)s::text is null or team_id = %(team)s::text)",
        {"team": team_id},
    )
    if row and row["t"] is not None:
        return row["t"], "the bot being connected to the workspace (no event has ever arrived)"
    return None, "nothing: no workspace is connected"


def liveness(
    conn: psycopg.Connection, *, settings: Settings, team_id: str | None = None, now: datetime | None = None
) -> Liveness:
    """Evaluate the dead-man switch without posting anything."""
    now = now or datetime.now(timezone.utc)
    threshold = timedelta(hours=max(1, settings.alert_silence_hours))
    last_at, evidence = last_live_signal(conn, team_id=team_id)
    if last_at is None:
        return Liveness(None, evidence, None, stale=False)
    silent_for = now - last_at
    return Liveness(last_at, evidence, silent_for, stale=silent_for >= threshold)


# ---------------------------------------------------------------------------
# posting
# ---------------------------------------------------------------------------


def _post(conn: psycopg.Connection, client: SlackClient, channel_id: str, text: str) -> bool:
    """Post one scrubbed message. Failure is logged, never raised.

    An alert that cannot be delivered must not take the tick down with it: the
    reminders matter more than the report about the reminders.
    """
    try:
        client.post_message(channel_id, scrub(text))
        return True
    except SlackApiError as exc:
        log.warning("alert post failed: %s", exc.error)
        return False
    except Exception:  # noqa: BLE001 - a transport fault is not worth a dead tick
        log.exception("alert post failed")
        return False


def check_liveness(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    channel_id: str,
    settings: Settings,
    team_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """The dead-man switch. Returns what it did: ``fired``, ``recovered`` or ``''``.

    One alert per outage, one recovery notice per outage. The suppression is in
    ``ops_alert`` rather than in memory because on serverless the next tick is
    very likely a different instance with no memory of this one.
    """
    now = now or datetime.now(timezone.utc)
    state = liveness(conn, settings=settings, team_id=team_id, now=now)
    if state.last_at is None:
        return ""

    if state.stale:
        prior = _observe(
            conn,
            ALERT_LIVENESS,
            now=now,
            detail=f"silent since {_stamp(state.last_at)} ({state.evidence})",
        )
        if prior.firing or not _claim(conn, ALERT_LIVENESS, now=now, cooldown=None):
            return ""
        assert state.silent_for is not None
        _post(
            conn,
            client,
            channel_id,
            "\n".join(
                [
                    f"*Nothing has reached the bot for {_hours(state.silent_for)}.*",
                    f"The last thing it received was {state.evidence}, at {_stamp(state.last_at)}. "
                    f"The threshold is {settings.alert_silence_hours}h "
                    "(`CUFA_ALERT_SILENCE_HOURS`).",
                    "If fellows have been active, delivery is broken rather than quiet: check that "
                    "Slack's request URL still points at this deployment, that the bot token has not "
                    "been revoked, and that the bot is still in the channels. RUNBOOK section 9 has "
                    "the queries.",
                    "_This is said once per outage. A recovery notice follows when data resumes._",
                ]
            ),
        )
        return "fired"

    was = _resolve(conn, ALERT_LIVENESS, now=now)
    if was is None:
        return ""
    since = was.first_seen_at or now
    _post(
        conn,
        client,
        channel_id,
        "\n".join(
            [
                "*Receiving again.*",
                f"The last thing the bot received is now {_stamp(state.last_at)}. "
                f"The outage was noticed at {_stamp(since)} and lasted {_hours(now - since)} "
                f"as far as this switch could see it.",
                "Anything Slack still holds is being walked by the automatic backfill; "
                "`cufa slack backfill --days 90` reaches further back.",
            ]
        ),
    )
    return "recovered"


def check_scheduler_gap(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    channel_id: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> str | None:
    """Record this tick's heartbeat, and report a gap since the last one.

    This is the failure that moved the bot off the laptop: the minute hand stops
    and the first symptom is a fellow not getting a reminder. Nothing inside a
    process that is not running can complain, so the complaint is made by the
    tick that comes BACK — which is at least a complaint, and is how a resumed
    ``pg_cron`` job announces the hole it left.

    The gap's own start time is part of the alert key, so the primary key makes
    each gap reportable exactly once and a tick that retries cannot double up.
    """
    now = now or datetime.now(timezone.utc)
    previous = _beat(conn, BEAT_TICK, now=now)
    if previous is None:
        return None  # first tick ever: no gap to describe
    gap = now - previous
    threshold = timedelta(minutes=max(2, settings.alert_tick_gap_minutes))
    if gap < threshold:
        return None

    key = f"{GAP_PREFIX}{previous.astimezone(timezone.utc).isoformat()}"
    claimed = fetch_one(
        conn,
        """
        insert into ops_alert (alert_key, state, detail, first_seen_at, last_seen_at,
                               occurrences, last_posted_at, posted_count)
        values (%(key)s, 'ok', %(detail)s, %(now)s, %(now)s, 1, %(now)s, 1)
        on conflict (alert_key) do nothing
        returning alert_key
        """,
        {"key": key, "detail": f"gap of {_hours(gap)}", "now": now},
    )
    if claimed is None:
        return None
    log.warning("scheduler gap of %s between %s and %s", _hours(gap), previous, now)
    if channel_id:
        _post(
            conn,
            client,
            channel_id,
            "\n".join(
                [
                    f"*The scheduler stopped calling for {_hours(gap)}.*",
                    f"No tick completed between {_stamp(previous)} and {_stamp(now)}; the threshold "
                    f"is {settings.alert_tick_gap_minutes}m (`CUFA_ALERT_TICK_GAP_MINUTES`). "
                    "Reminders whose moment fell inside that window were not sent at that moment.",
                    "Check the `cufa-tick` job's recent runs (RUNBOOK section 9). A tick is running "
                    "again now, or this message could not have been written.",
                ]
            ),
        )
    return key


def report_errors(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    channel_id: str,
    errors: Sequence[str],
    settings: Settings,
    now: datetime | None = None,
) -> list[str]:
    """Put tick errors in the channel, deduplicated. Returns the keys reported.

    The shape of the problem: ``tick`` runs once a minute and the cron route
    answers 200 with its errors so the scheduler keeps calling, which means a
    single broken step produces 1,440 identical errors a day. Posting each one
    would make the staff channel useless within an hour, and a channel nobody
    can read is the same as the log nobody reads.

    So each distinct error is announced once, then suppressed for
    ``CUFA_ALERT_ERROR_COOLDOWN_MINUTES``, after which it is announced again as
    a COUNT rather than as itself. An error that has stopped appearing for a
    cooldown is reported as cleared, so staff can tell "fixed" from "still
    broken but quiet".

    Everything is one message per tick, not one per error: fewer Slack calls,
    and a reader sees the whole state of things in one place.
    """
    now = now or datetime.now(timezone.utc)
    cooldown = timedelta(minutes=max(1, settings.alert_error_cooldown_minutes))
    limit = max(1, settings.alert_max_errors_per_post)

    lines: list[str] = []
    reported: list[str] = []
    suppressed = 0
    seen_keys: set[str] = set()

    for error in errors:
        key = f"{ERROR_PREFIX}{fingerprint(error)}"
        if key in seen_keys:
            continue  # the same error twice in one tick is one error
        seen_keys.add(key)
        prior = _observe(conn, key, now=now, detail=error)
        if not _claim(conn, key, now=now, cooldown=cooldown):
            suppressed += 1
            continue
        reported.append(key)
        if len(lines) >= limit:
            continue
        if prior.firing and prior.occurrences:
            since = prior.first_seen_at or now
            lines.append(
                f"• *still failing* — {prior.occurrences + 1} times since {_stamp(since)}: "
                f"{scrub(error)}"
            )
        else:
            lines.append(f"• {scrub(error)}")

    cleared = _clear_stale_errors(conn, now=now, cooldown=cooldown, still_failing=seen_keys)
    for detail in cleared[:limit]:
        lines.append(f"• *cleared* — no longer failing: {detail}")

    if not lines:
        return reported
    header = "*Tick errors.*" if len(lines) > 1 else "*Tick error.*"
    tail = [
        f"_{len(reported) - len(lines) + len(cleared)} further repeat(s) suppressed._"
        if len(reported) > limit
        else "",
        f"_Repeats are held for {settings.alert_error_cooldown_minutes}m "
        "(`CUFA_ALERT_ERROR_COOLDOWN_MINUTES`); the tick itself keeps running._",
    ]
    if suppressed:
        tail.insert(0, f"_{suppressed} known error(s) still occurring, not repeated here._")
    _post(conn, client, channel_id, "\n".join([header, *lines, *[t for t in tail if t]]))
    return reported


def _clear_stale_errors(
    conn: psycopg.Connection, *, now: datetime, cooldown: timedelta, still_failing: Iterable[str]
) -> list[str]:
    """Close out error keys that have not been seen for a whole cooldown.

    A grace period rather than "absent this tick", because an intermittent
    failure would otherwise flap between announced and cleared every minute,
    which is the firehose again wearing a different hat.
    """
    active = set(still_failing)
    rows = fetch_all(
        conn,
        """
        select alert_key, detail from ops_alert
         where state = 'firing' and alert_key like %(prefix)s and last_seen_at <= %(cutoff)s
         order by last_seen_at
        """,
        {"prefix": f"{ERROR_PREFIX}%", "cutoff": now - cooldown},
    )
    cleared: list[str] = []
    for row in rows:
        if row["alert_key"] in active:
            continue
        if _resolve(conn, row["alert_key"], now=now) is not None:
            cleared.append(row["detail"] or row["alert_key"])
    return cleared


# ---------------------------------------------------------------------------
# automatic backfill
# ---------------------------------------------------------------------------


def maybe_backfill(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    settings: Settings,
    now: datetime | None = None,
    force: bool = False,
) -> str | None:
    """Walk Slack's own history on a cadence, so a gap heals itself.

    **Where this belongs, and why here.** ``backfill.py`` has worked and been
    idempotent since it was written; what it lacked was anybody calling it. The
    obvious hook, "on restart", does not exist on this deployment:

    * *Not a cold start.* Under serverless there is no restart — there are cold
      starts, and they are both far too frequent and entirely absent at the
      worst moment. A busy afternoon produces many of them (each would walk
      every channel's history and spend the ``conversations.history`` rate
      limit on data it already has), while an idle weekend produces none at all
      — and an idle weekend is exactly when an undetected gap sits waiting.
      Cold starts are also invisible to the database, so a cadence could not be
      enforced across them.
    * *Not "on detecting a watermark gap".* A watermark cannot show a gap. It
      is a high-water mark, not a range: it says where reading stopped, never
      that something between here and there was missed. Finding out requires
      asking Slack, which is the backfill itself. Detection and cure are the
      same call, so there is nothing to detect first.
    * *So: off the tick.* The tick is the one heartbeat this deployment has,
      it already holds an advisory lock so two backfills cannot overlap, and
      its cadence can be recorded in ``ops_heartbeat`` where the next instance
      can see it. A gap therefore heals within ``CUFA_AUTO_BACKFILL_MINUTES``
      of appearing, with no human in the loop — which is what happened on
      2026-09-14, except that a human was in the loop and got lucky.

    It reads the last ``CUFA_AUTO_BACKFILL_LOOKBACK_HOURS`` of every channel the
    bot can read, ignoring the watermark. Ignoring it is the point: the per-tick
    ``sync_messages`` advances that same watermark every minute, so a walk that
    respected it would be a no-op and would never recover the things only
    history carries — aggregated reactions, Q&A thread replies, and channels
    that are readable but not tracked. Re-reading is free, because every row is
    keyed by the act rather than by the delivery.

    Returns a one-line summary when it ran, or None when it was not due.
    """
    now = now or datetime.now(timezone.utc)
    cadence_minutes = settings.auto_backfill_minutes
    if cadence_minutes <= 0:
        return None

    # backfill.py speaks to the Slack Web API directly (conversations.history,
    # .replies, .list) rather than through the SlackClient protocol, because
    # aggregated reactions and thread replies are not in the protocol. The
    # adapter exposes the client underneath it; the in-memory FakeSlackClient
    # has none, and there nothing is backfilled rather than something failing.
    web = getattr(client, "web", None)
    if web is None:
        log.debug("auto-backfill skipped: this client has no Slack Web API underneath it")
        return None

    previous = last_beat(conn, BEAT_BACKFILL)
    due = force or previous is None or (now - previous) >= timedelta(minutes=cadence_minutes)
    if not due:
        return None

    from .backfill import backfill_workspace

    lookback = timedelta(hours=max(1, settings.auto_backfill_lookback_hours))
    team_id = client.team_id
    try:
        result = backfill_workspace(
            conn,
            web,
            team_id,
            since=now - lookback,
            override_watermark=True,
            store_text=settings.slack_store_text,
            qa=_qa_service(conn, settings, web, team_id),
        )
    except CufaError as exc:
        raise
    except Exception as exc:  # noqa: BLE001 - reported as a tick error, never fatal
        raise CufaError(f"automatic backfill failed: {type(exc).__name__}: {exc}") from exc

    summary = (
        f"channels={result.channels} read={result.messages_read} "
        f"recovered={result.events_written} duplicate={result.events_duplicate}"
    )
    _beat(conn, BEAT_BACKFILL, now=now, detail=summary)
    log.info("auto-backfill %s", summary)
    return summary


def _qa_service(conn: psycopg.Connection, settings: Settings, web: Any, team_id: str) -> Any:
    """A ``QaService`` for the backfill, or None when no Q&A channel is set.

    Built from the stored workspace row rather than from a live ``auth.test``,
    so this costs no API call. Without it a recovered Q&A thread would land in
    ``slack_event`` and not in the Q&A tables, which is the half-healed state
    that is hardest to notice later.
    """
    if not settings.slack_qa_channels:
        return None
    row = fetch_one(
        conn,
        "select team_id, team_name, cohort_id, bot_user_id from slack_workspace where team_id = %s",
        (team_id,),
    )
    if row is None:
        return None
    from .qa import build_qa_service
    from .store import WorkspaceInfo

    workspace = WorkspaceInfo(
        team_id=row["team_id"],
        team_name=row["team_name"] or row["team_id"],
        bot_user_id=row["bot_user_id"] or "",
        cohort_id=row["cohort_id"],
    )
    try:
        return build_qa_service(settings, web, workspace)
    except CufaError as exc:
        log.warning("auto-backfill running without the Q&A service: %s", exc)
        return None


# ---------------------------------------------------------------------------
# the one call the tick makes
# ---------------------------------------------------------------------------


@dataclass
class AlertingResult:
    """What the watchdog did, for the tick's own log line and the cron body."""

    liveness: str = ""
    gap: str | None = None
    errors_reported: list[str] = field(default_factory=list)
    backfilled: str | None = None
    #: Failures of the watchdog itself. Fed back into the tick's error list, so
    #: next tick reports them the same way it reports everything else.
    failures: list[str] = field(default_factory=list)

    @property
    def posted(self) -> int:
        return (1 if self.liveness else 0) + (1 if self.gap else 0) + (1 if self.errors_reported else 0)

    def __str__(self) -> str:  # pragma: no cover - display only
        return (
            f"liveness={self.liveness or 'ok'} gap={'yes' if self.gap else 'no'} "
            f"errors_reported={len(self.errors_reported)} "
            f"backfill={'yes' if self.backfilled else 'no'}"
        )


def run_alerting(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    settings: Settings | None = None,
    staff_channel: str | None,
    errors: Sequence[str] = (),
    team_id: str | None = None,
    now: datetime | None = None,
) -> AlertingResult:
    """Everything in this module, in the order that makes sense, failing softly.

    Called at the end of ``digest.tick`` so every transport gets it: the cron
    route, ``cufa slack tick``, and the scheduler thread of a long-lived
    process. Each step is independent; the watchdog must never be the reason a
    reminder did not go out, so its own failures are collected and reported
    rather than raised.
    """
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    result = AlertingResult()
    if not settings.alerts_enabled:
        return result

    def step(name: str, fn: Any) -> Any:
        try:
            return fn()
        except CufaError as exc:
            result.failures.append(f"alerting {name}: {exc}")
            log.warning("alerting step %s failed: %s", name, exc)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            result.failures.append(f"alerting {name}: {type(exc).__name__}: {exc}")
            log.exception("alerting step %s crashed", name)
        return None

    # The gap notice goes first: it is the only step whose evidence (the
    # previous heartbeat) this very call is about to overwrite.
    result.gap = step(
        "scheduler_gap",
        lambda: check_scheduler_gap(conn, client, channel_id=staff_channel, settings=settings, now=now),
    )

    if staff_channel:
        result.liveness = step(
            "liveness",
            lambda: check_liveness(
                conn, client, channel_id=staff_channel, settings=settings, team_id=team_id, now=now
            ),
        ) or ""
        result.errors_reported = step(
            "errors",
            lambda: report_errors(
                conn, client, channel_id=staff_channel, errors=errors, settings=settings, now=now
            ),
        ) or []
    elif errors:
        log.warning(
            "%d tick error(s) have nowhere to go: CUFA_SLACK_STAFF_CHANNEL is unset", len(errors)
        )

    # Backfill last, and immediately on either edge of an outage: the moment a
    # gap is known to exist or to have just ended is the moment to close it.
    result.backfilled = step(
        "auto_backfill",
        lambda: maybe_backfill(
            conn, client, settings=settings, now=now, force=result.liveness in ("fired", "recovered")
        ),
    )
    log.info("alerting %s", result)
    return result


__all__ = [
    "ALERT_LIVENESS",
    "BEAT_BACKFILL",
    "BEAT_TICK",
    "ERROR_PREFIX",
    "GAP_PREFIX",
    "REDACTED",
    "AlertState",
    "AlertingResult",
    "Liveness",
    "check_liveness",
    "check_scheduler_gap",
    "fingerprint",
    "last_beat",
    "last_live_signal",
    "liveness",
    "maybe_backfill",
    "report_errors",
    "run_alerting",
    "scrub",
]
