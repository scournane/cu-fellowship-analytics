"""Data subject access, export and erasure.

docs/vision.md §12 asks for "data subject access, export and deletion, on
request, by a fellow or a parent", and for "the privacy notice inside the app,
not only on a form". §6 asks for "export my data". This module is all three:
``export_fellow`` assembles everything held about one person, ``erase_fellow``
takes it away, and ``COLLECTION_NOTICE`` is the plain-language version of the
first that the fellow's own page shows without being asked.

The records here are about teenagers, so two rules shape every query below.

**One person, and nobody else.** Every section is keyed to one fellow through
``fellow_id`` or through ``v_fellow_email`` — the same read-time join the rest
of the system uses, so an alias counts and a corrected roster re-attributes
history. Where a row is about two people at once, the export takes the side that
belongs to the subject and says in the output what it left behind and why. There
are four of those and they are the interesting part:

* **A shoutout they wrote** is theirs: their words, in full, including the name
  they typed. They already know who they praised.
* **A shoutout somebody wrote about them** is not. The count and the dates are
  in; the words and the author are out. The words are another fellow's
  statement, and in a cohort this size "great work on the housing project" names
  its author to anyone who was there.
* **A staff intervention note** is out. That a record exists, what kind it is
  and when it was made are in, so the person can see there is something to ask
  about; the note itself can carry a third party's account and can be
  safeguarding material, and the console already gates that kind of record behind
  a separate access list (ADR-025). Withholding it visibly beats withholding it
  silently.
* **Who read their record** is in as dates and doors, not as names. See
  ``access_log.fellow_facing_label``.

**Erasure keeps the shape of the database.** See the header of
``supabase/migrations/20261001000200_data_rights.sql``: every observation here
carries an idempotency key and every ingest is ``on conflict do nothing``, so a
deleted observation comes straight back on the next ``cufa pull``. What erasure
does instead is empty the rows of the person while leaving the key that stops
them being written again, and delete outright only the rows that are derived
conveniences. ``erase_fellow`` reports which it did to every table it touched,
and names what it could not reach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .errors import CufaError
from .logging_setup import get_logger, mask_email
from . import help_requests

log = get_logger(__name__)

ERASED_NAME = "[erased]"
ERASED_EMAIL_DOMAIN = "erased.invalid"
ERASED_SHOUTOUT_TEXT = "[name removed at the request of the person named]"

_EMAIL_SAFE = set("abcdefghijklmnopqrstuvwxyz0123456789.-_")


def erasure_email(fellow_id: str) -> str:
    """The address that replaces a fellow's own once they have been erased.

    Per-fellow rather than a single shared sentinel, for two reasons that both
    matter. ``fellow_cohort_email_uniq`` is a unique index on
    ``(cohort_id, lower(primary_email))``, so one sentinel could only ever erase
    one fellow per cohort. And the check-in rows keep resolving through
    ``v_fellow_email`` to the tombstone, which keeps a session's attendance count
    honest instead of quietly dropping the attendance of anyone who asked to be
    erased.

    The shape matches the pattern the ``checkin`` immutability trigger allows —
    ``erased-%@erased.invalid`` — so changing this means changing the migration.
    """
    slug = "".join(c if c in _EMAIL_SAFE else "-" for c in (fellow_id or "").strip().lower())
    if not slug:
        raise CufaError("a fellow id is needed to build an erasure address")
    return f"erased-{slug}@{ERASED_EMAIL_DOMAIN}"


# ---------------------------------------------------------------------------
# The privacy notice, in the words a sixteen-year-old would use
# ---------------------------------------------------------------------------

#: What is collected, one line each. This is §12's "privacy notice inside the
#: app": it is rendered on the fellow's own page, not kept in a policy document
#: nobody opens. Each entry is (heading, what and where it comes from) and is
#: deliberately about the *act* rather than the metric, because the acts are what
#: a person can recognise as things they did.
COLLECTION_NOTICE: tuple[tuple[str, str], ...] = (
    (
        "Who you are",
        "Your name, your fellowship id, your email address, your cohort and your "
        "time zone. This comes from CU's roster, not from anything you did here.",
    ),
    (
        "Whether you checked in",
        "For each session: whether your mid-session check-in arrived, when it "
        "arrived, and whether the passphrase you typed matched. If a person or a "
        "model had to decide a borderline one, that decision is kept with the "
        "reason for it.",
    ),
    (
        "What you wrote on an exit ticket",
        "Your confidence rating, your takeaway, your answer to the rotating "
        "question, and the shoutout you gave — in your own words, as you typed "
        "them.",
    ),
    (
        "What you did in Slack",
        "Counts of acts: messages sent, replies, reactions given, channels "
        "joined, files and links attached, and the questions and answers you "
        "posted in a Q&A channel. The text of an ordinary message is not stored.",
    ),
    (
        "Assignments and badges",
        "Whether you submitted, any score a staff member entered, and the badges "
        "you have earned.",
    ),
    (
        "Reminders",
        "Your reminder settings, and a record of which reminders the bot sent you "
        "and when.",
    ),
    (
        "Speaking time on a recording",
        "If a session recording was transcribed, how many turns you took and how "
        "long they were. The words are not kept.",
    ),
    (
        "If staff reached out",
        "That a staff member contacted you or that you asked to be checked in "
        "with, and when. The note a staff member wrote is not shown here.",
    ),
    (
        "Who opened your record",
        "Every time a staff member opened your page, with the date and how they "
        "signed in.",
    ),
)

#: What this system deliberately does not collect. Saying so is half the notice:
#: teenagers assume the worst about monitoring software, usually correctly, and
#: the specific reassurance is the one that lands. Each of these is a decision
#: recorded in docs/vision.md §1 and §15, not an oversight.
NOT_COLLECTED: tuple[str, ...] = (
    "The text of your Slack messages and DMs. Acts are counted; nothing is read.",
    "Whether you are online or away. Presence is available from Slack and is not collected.",
    "Anything from your camera or microphone beyond a transcript's speaking times.",
    "Any sentiment, mood or attention score about you as an individual.",
    "Any comparison of you against other fellows shown to you as a rank or a score.",
)

#: Where an erasure or a correction request goes. The system cannot honour one by
#: itself — a person has to check who is asking — so the notice says who to ask
#: rather than offering a button that would have to be gated anyway.
HOW_TO_ASK = (
    "To get this as a full archive, use the links on this page. To have something "
    "corrected, or to have your record deleted, ask a member of the fellowship "
    "team in Slack, or press the check-in button and say so. A parent or guardian "
    "can ask on your behalf."
)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One part of the archive: what it is, why it is held, and the rows."""

    key: str
    title: str
    lead: str
    rows: tuple[dict[str, Any], ...] = ()
    note: str | None = None
    empty: str = "Nothing recorded."

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "lead": self.lead,
            "rows": [dict(r) for r in self.rows],
            "note": self.note,
        }


@dataclass(frozen=True)
class FellowExport:
    """Everything held about one fellow, ready to render or serialise."""

    fellow_id: str
    generated_at: datetime
    fellow: dict[str, Any]
    sections: tuple[Section, ...] = ()
    withheld: tuple[str, ...] = ()
    not_collected: tuple[str, ...] = NOT_COLLECTED

    def section(self, key: str) -> Section | None:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fellow_id": self.fellow_id,
            "generated_at": self.generated_at,
            "fellow": dict(self.fellow),
            "sections": [s.to_dict() for s in self.sections],
            "withheld": list(self.withheld),
            "not_collected": list(self.not_collected),
        }


#: What the export leaves out, said out loud. A subject access request that
#: quietly drops the awkward rows is worse than one that names them, because the
#: person cannot ask for what they do not know exists.
WITHHELD: tuple[str, ...] = (
    "The words of a shoutout another fellow wrote about you, and who wrote it. "
    "The count and the dates are included. The words are somebody else's "
    "statement, and in a cohort this size they would usually identify their "
    "author.",
    "The note a staff member wrote on an outreach or a check-in request, and "
    "which staff member wrote it. That the record exists, what kind it is and "
    "when it was made are included, so you can ask about it.",
    "The email address of the staff member who opened your record or decided a "
    "borderline check-in. The date and the way they signed in are included.",
    "Other fellows' records, in every section. Every row here is keyed to you.",
)


#: The one erasure this module does not write itself. `help_requests` owns
#: every query against its table; see ADR-025 and test_19b.
_HELP_ERASE_SQL, _HELP_ERASE_WHERE = help_requests.erasure_predicate()


def _emails(conn: psycopg.Connection, fellow_id: str) -> list[str]:
    """Every address that resolves to this fellow — primary and aliases.

    Read from ``v_fellow_email`` rather than ``fellow.primary_email``, because
    the observation tables are keyed by the address that was typed into a form
    and an alias is exactly the case where those differ.
    """
    return [
        str(r["email"])
        for r in fetch_all(
            conn, "select email from v_fellow_email where fellow_id = %s", (fellow_id,)
        )
    ]


def _slack_ids(conn: psycopg.Connection, fellow_id: str) -> list[str]:
    return [
        str(r["slack_user_id"])
        for r in fetch_all(
            conn,
            "select slack_user_id from v_slack_user_resolved where fellow_id = %s",
            (fellow_id,),
        )
    ]


def _checkin_b_ids(conn: psycopg.Connection, emails: list[str]) -> list[str]:
    if not emails:
        return []
    return [
        str(r["checkin_b_id"])
        for r in fetch_all(
            conn,
            "select checkin_b_id from checkin_b where lower(submitted_email) = any(%s)",
            (emails,),
        )
    ]


def export_fellow(
    conn: psycopg.Connection, fellow_id: str, *, now: datetime | None = None
) -> FellowExport:
    """Assemble the archive for one fellow.

    Queries the tables rather than the dashboard's aggregations. The dashboard
    shows what staff need to act; an access request has to show what is held,
    which is a different and larger set — a row nobody looks at is still a row
    about a person.
    """
    now = now or datetime.now(timezone.utc)
    fellow = fetch_one(
        conn,
        """
        select fellow_id, cohort_id, full_name, primary_email, status, timezone,
               accepted_on, completed_at, created_at, updated_at, erased_at
          from fellow where fellow_id = %s
        """,
        (fellow_id,),
    )
    if fellow is None:
        raise CufaError(f"No fellow with id {fellow_id}")

    emails = _emails(conn, fellow_id)
    slack_ids = _slack_ids(conn, fellow_id)
    ticket_ids = _checkin_b_ids(conn, emails)
    full_name = str(fellow["full_name"])
    sections: list[Section] = []

    # --- who this is about -------------------------------------------------
    sections.append(
        Section(
            key="roster",
            title="Who this record is about",
            lead="From CU's roster. Nothing here was observed; it was loaded.",
            rows=(
                {
                    "fellow_id": fellow["fellow_id"],
                    "full_name": fellow["full_name"],
                    "primary_email": fellow["primary_email"],
                    "cohort_id": fellow["cohort_id"],
                    "status": fellow["status"],
                    "timezone": fellow["timezone"],
                    "accepted_on": fellow["accepted_on"],
                    "completed_at": fellow["completed_at"],
                    "record_created_at": fellow["created_at"],
                    "record_updated_at": fellow["updated_at"],
                },
            ),
        )
    )

    aliases = fetch_all(
        conn,
        "select email, kind, note, created_at from fellow_alias where fellow_id = %s order by created_at",
        (fellow_id,),
    )
    sections.append(
        Section(
            key="other_addresses",
            title="Other email addresses on this record",
            lead=(
                "A second address linked to this record by a staff member, so a "
                "check-in or a Slack account under it is attributed to you. Who "
                "linked it is not shown."
            ),
            rows=tuple({k: r[k] for k in ("email", "kind", "note", "created_at")} for r in aliases),
            empty="No second address is linked.",
        )
    )

    unplaced = fetch_all(
        conn,
        """
        select email, first_seen_at, last_seen_at, occurrence_count, resolved_at
          from identity_unresolved
         where lower(email) = any(%s) or best_guess_fellow_id = %s
         order by first_seen_at
        """,
        (emails, fellow_id),
    )
    sections.append(
        Section(
            key="unplaced_addresses",
            title="Addresses seen that could not be placed on the roster",
            lead=(
                "A form submitted from an address the roster did not recognise. It "
                "is queued for a person rather than guessed at or dropped."
            ),
            rows=tuple(dict(r) for r in unplaced),
            empty="None.",
        )
    )

    # --- attendance --------------------------------------------------------
    checkins = fetch_all(
        conn,
        """
        select session_title, scheduled_at_utc, submitted_at_utc, submitted_at_raw,
               source_timezone, source, passphrase_raw, passphrase_match,
               edit_distance, latency_seconds, session_match,
               status, attended, confidence, decided_by, rule_name,
               ai_model, ai_reasoning, note, decided_at
          from v_checkin_resolved
         where fellow_id = %s
         order by scheduled_at_utc nulls last, submitted_at_utc
        """,
        (fellow_id,),
    )
    for row in checkins:
        # `decided_by` already says whether a rule, a model or a person made the
        # call; the person's address is theirs, not the subject's.
        row["decided_by"] = {
            "rule": "an automatic rule",
            "ai": "a model, checked against a rule first",
            "human": "a member of staff",
        }.get(str(row["decided_by"]), row["decided_by"])
    sections.append(
        Section(
            key="attendance",
            title="Mid-session check-ins, and the attendance decision on each",
            lead=(
                "One row per check-in you submitted. `passphrase_raw` is what you "
                "typed. Where a check-in was borderline, the decision is kept with "
                "the reason, because a judgment about you is as much your record "
                "as the timestamp it was made from."
            ),
            rows=tuple(dict(r) for r in checkins),
            empty="No mid-session check-in has been recorded.",
        )
    )

    # --- exit tickets ------------------------------------------------------
    tickets = fetch_all(
        conn,
        """
        select session_title, week_index, submitted_at_utc, confidence_raw,
               takeaway_text, rotating_kind, rotating_text, shoutout_text,
               latency_seconds, source
          from v_checkin_b_resolved
         where fellow_id = %s
         order by submitted_at_utc
        """,
        (fellow_id,),
    )
    sections.append(
        Section(
            key="exit_tickets",
            title="Exit tickets — your own answers",
            lead=(
                "Everything you wrote on an end-of-session form, as you wrote it. "
                "The confidence number is the 1-7 you picked, never rescaled."
            ),
            rows=tuple(dict(r) for r in tickets),
            empty="No exit ticket has been recorded.",
        )
    )

    # --- shoutouts given ---------------------------------------------------
    given = fetch_all(
        conn,
        """
        select s.raw_text, s.match_method,
               (s.named_fellow_id is not null) as matched_to_someone_on_the_roster,
               b.submitted_at_utc, ses.title as session_title
          from peer_shoutout s
          join checkin_b b on b.checkin_b_id = s.checkin_b_id
          left join "session" ses on ses.session_id = b.session_id
         where s.checkin_b_id = any(%s)
         order by b.submitted_at_utc
        """,
        (ticket_ids,),
    ) if ticket_ids else []
    sections.append(
        Section(
            key="shoutouts_given",
            title="Shoutouts you gave",
            lead=(
                "The name you typed, in full, and whether it was matched to "
                "somebody on the roster. Your words are yours; you already know "
                "who you named. The other fellow's id and address are not here."
            ),
            rows=tuple(dict(r) for r in given),
            empty="You have not given a shoutout yet.",
        )
    )

    # --- shoutouts received (counts only) ---------------------------------
    received = fetch_all(
        conn,
        """
        select ses.title as session_title, b.submitted_at_utc::date as on_date,
               count(*) as shoutouts
          from peer_shoutout s
          join checkin_b b on b.checkin_b_id = s.checkin_b_id
          left join "session" ses on ses.session_id = b.session_id
         where s.named_fellow_id = %s
         group by 1, 2
         order by 2
        """,
        (fellow_id,),
    )
    sections.append(
        Section(
            key="shoutouts_received",
            title="Times another fellow named you in a shoutout",
            lead="How many, and when.",
            rows=tuple(dict(r) for r in received),
            note=(
                "The words and the author are withheld: the words are another "
                "fellow's statement about you, and in a cohort this size they "
                "would usually identify who wrote them. Ask the fellowship team "
                "if you want them."
            ),
            empty="None recorded.",
        )
    )

    # --- assignments -------------------------------------------------------
    submissions = fetch_all(
        conn,
        """
        select a.title, a.kind, a.due_at_utc, a.max_score,
               s.submitted_at_utc, s.score, s.graded_at, s.note, s.created_at
          from assignment_submission s
          join assignment a on a.assignment_id = s.assignment_id
         where s.fellow_id = %s
         order by a.due_at_utc
        """,
        (fellow_id,),
    )
    sections.append(
        Section(
            key="assignments",
            title="Assignments",
            lead=(
                "What you submitted and any score a staff member entered. Which "
                "staff member entered it is not shown."
            ),
            rows=tuple(dict(r) for r in submissions),
            empty="Nothing recorded.",
        )
    )

    # --- badges ------------------------------------------------------------
    badges = fetch_all(
        conn,
        """
        select badge_key, level, evidence, awarded_at, notified_at
          from badge_award where fellow_id = %s order by awarded_at
        """,
        (fellow_id,),
    )
    sections.append(
        Section(
            key="badges",
            title="Badges",
            lead="What you earned, when, and the counts it was earned from.",
            rows=tuple(dict(r) for r in badges),
            empty="None yet.",
        )
    )

    # --- slack accounts ----------------------------------------------------
    accounts = fetch_all(
        conn,
        """
        select slack_user_id, team_id, display_name, real_name, tz, is_admin,
               deleted, joined_at_utc, first_seen_at, last_seen_at, match_method
          from v_slack_user_resolved where fellow_id = %s order by first_seen_at
        """,
        (fellow_id,),
    )
    alerts = fetch_all(
        conn,
        """
        select kind, posted_at, resolved_at, resolution, created_at
          from roster_alert where slack_user_id = any(%s) order by created_at
        """,
        (slack_ids,),
    ) if slack_ids else []
    sections.append(
        Section(
            key="slack_accounts",
            title="Slack accounts matched to you",
            lead=(
                "`match_method` says how the match was made: `manual` is a staff "
                "member linking it by hand, `primary_email` or `alias` is the "
                "address matching by itself."
            ),
            rows=tuple(dict(r) for r in accounts),
            note=(
                f"{len(alerts)} roster alert(s) were raised about one of these "
                "accounts joining the workspace without being on the roster."
            )
            if alerts
            else None,
            empty="No Slack account is matched to you.",
        )
    )

    # --- slack activity, counted ------------------------------------------
    activity = fetch_all(
        conn,
        """
        select e.event_type,
               coalesce(c.name, e.channel_id, '(direct or unknown)') as channel,
               coalesce(c.is_staff, false) as staff_channel,
               count(*) as events,
               sum(coalesce(e.word_count, 0)) as words,
               count(*) filter (where e.is_thread_reply) as thread_replies,
               count(*) filter (where e.has_link) as with_a_link,
               count(*) filter (where e.has_attachment) as with_an_attachment,
               min(e.event_time_utc) as first_at,
               max(e.event_time_utc) as last_at
          from slack_event e
          left join slack_channel c on c.channel_id = e.channel_id
         where e.slack_user_id = any(%s) or lower(e.user_email) = any(%s)
         group by 1, 2, 3
         order by 1, 2
        """,
        (slack_ids, emails),
    )
    sections.append(
        Section(
            key="slack_activity",
            title="What you did in Slack, counted",
            lead=(
                "Counts of acts, per channel and per kind of act. The text of an "
                "ordinary message is not stored, so there is none to give you."
            ),
            rows=tuple(dict(r) for r in activity),
            empty="No Slack activity recorded.",
        )
    )

    questions = fetch_all(
        conn,
        """
        select channel_id, text, asked_at_utc, edited_at_utc, deleted_at_utc,
               resolved, permalink
          from slack_qa_question where slack_user_id = any(%s) order by asked_at_utc
        """,
        (slack_ids,),
    ) if slack_ids else []
    answers = fetch_all(
        conn,
        """
        select channel_id, text, answered_at_utc, edited_at_utc, deleted_at_utc,
               accepted, permalink
          from slack_qa_answer where slack_user_id = any(%s) order by answered_at_utc
        """,
        (slack_ids,),
    ) if slack_ids else []
    sections.append(
        Section(
            key="slack_qa",
            title="Questions you asked and answers you gave in a Q&A channel",
            lead=(
                "These are kept in full, unlike an ordinary message: a designated "
                "Q&A channel is summarised for the teacher, so the text has to be "
                "stored. It is your own writing."
            ),
            rows=tuple(
                [{"kind": "question", **dict(r)} for r in questions]
                + [{"kind": "answer", **dict(r)} for r in answers]
            ),
            empty="None recorded.",
        )
    )

    # --- reminders ---------------------------------------------------------
    prefs = fetch_one(
        conn,
        """
        select mode, timezone, quiet_start_local, quiet_end_local, updated_at
          from fellow_reminder_preference where fellow_id = %s
        """,
        (fellow_id,),
    )
    slack_prefs = fetch_all(
        conn,
        """
        select slack_user_id, session_reminders, assignment_reminders, gamification, updated_at
          from slack_preference where slack_user_id = any(%s)
        """,
        (slack_ids,),
    ) if slack_ids else []
    deliveries = fetch_all(
        conn,
        """
        select kind, nudge_number, scheduled_for_utc, status, delivered_at, attempt_count
          from bot_delivery where fellow_id = %s order by scheduled_for_utc
        """,
        (fellow_id,),
    )
    sent = fetch_all(
        conn,
        """
        select target_kind, offset_minutes, sent_at
          from reminder_sent where slack_user_id = any(%s) order by sent_at
        """,
        (slack_ids,),
    ) if slack_ids else []
    sections.append(
        Section(
            key="reminders",
            title="Your reminder settings, and what the bot sent you",
            lead="What you chose, and every message the bot sent you because of it.",
            rows=tuple(
                ([{"kind": "settings", **dict(prefs)}] if prefs else [])
                + [{"kind": "slack settings", **dict(r)} for r in slack_prefs]
                + [{"kind": "delivery", **dict(r)} for r in deliveries]
                + [{"kind": "reminder sent", **dict(r)} for r in sent]
            ),
            empty="Nothing recorded.",
        )
    )

    # --- airtime -----------------------------------------------------------
    airtime = fetch_all(
        conn,
        """
        select s.title as session_title, count(*) as turns,
               sum(t.word_count) as words,
               round(sum(t.ended_at_s - t.started_at_s)) as seconds_speaking
          from zoom_transcript_turn t
          join "session" s on s.session_id = t.session_id
         where lower(btrim(t.speaker_name)) = lower(btrim(%s))
         group by 1
         order by 1
        """,
        (full_name,),
    )
    sections.append(
        Section(
            key="airtime",
            title="Speaking time on a session recording",
            lead=(
                "From a transcript, matched on the name the meeting software "
                "showed. Turns and seconds only; the words are not stored."
            ),
            rows=tuple(dict(r) for r in airtime),
            empty="No transcript has been matched to you.",
        )
    )

    # --- interventions and help requests (existence only) ------------------
    interventions = fetch_all(
        conn,
        """
        select kind, source, created_at, resolved_at, (note is not null) as a_note_exists
          from intervention where fellow_id = %s order by created_at
        """,
        (fellow_id,),
    )
    sections.append(
        Section(
            key="interventions",
            title="Times a staff member recorded reaching out, or you asked to be checked in with",
            lead=(
                "What kind of record, when it was made, and whether it has been "
                "closed. `check_in_request` is you pressing the bot's button."
            ),
            rows=tuple(dict(r) for r in interventions),
            note=(
                "The note a staff member wrote, and which staff member wrote it, "
                "are withheld. A note can carry somebody else's account of what "
                "happened and can be safeguarding material, so releasing it is a "
                "decision for the fellowship team rather than for this tool. That "
                "a note exists is shown above so you can ask for it."
            ),
            empty="None recorded.",
        )
    )

    # Through `help_requests`, not by querying the table. ADR-025 keeps that
    # table down to one reader so a change to who can see a safeguarding
    # disclosure has one place to audit, and test_19b enforces it.
    help_rows = help_requests.for_subject_access(conn, fellow_id, emails)
    sections.append(
        Section(
            key="help_requests",
            title="Times you ticked the box asking to be checked in with",
            lead=(
                "You did this on an exit ticket. It is routed straight to a named "
                "person and is deliberately kept out of every metric and off every "
                "dashboard — including your own page. It is here because it is "
                "yours and you are entitled to know it is held."
            ),
            rows=tuple(dict(r) for r in help_rows),
            note=(
                "Who picked it up and any note they wrote are withheld, for the "
                "same reason as the interventions above."
            ),
            empty="None recorded.",
        )
    )

    # --- journey -----------------------------------------------------------
    funnel = fetch_one(
        conn,
        """
        select accepted_at, slack_joined_at, first_message_at, first_checkin_at, completed_at
          from v_fellow_funnel where fellow_id = %s
        """,
        (fellow_id,),
    )
    sections.append(
        Section(
            key="journey",
            title="Your journey through the fellowship",
            lead=(
                "Five dates, each worked out at read time from what was observed. "
                "Only the accepted and completed dates are typed in by a person."
            ),
            rows=(dict(funnel),) if funnel else (),
            empty="Nothing recorded.",
        )
    )

    # --- who read the record ----------------------------------------------
    from .access_log import fellow_facing_label, reads_for_fellow

    reads = reads_for_fellow(conn, fellow_id)
    sections.append(
        Section(
            key="access_log",
            title="Who opened your record, and when",
            lead=(
                "Every time a staff member opened your page or exported this "
                "archive. Kept since the access log was switched on; anything "
                "before that was not recorded."
            ),
            rows=tuple(
                {"at": r["at"], "route": r["route"], "opened_by": fellow_facing_label(r)}
                for r in reads
            ),
            note=(
                "The staff member's email address is not shown. The date and the "
                "way they signed in are. Ask the fellowship team if you need the "
                "name."
            ),
            empty="Nothing recorded.",
        )
    )

    log.info(
        "export assembled fellow=%s sections=%d", fellow_id, len(sections)
    )
    return FellowExport(
        fellow_id=fellow_id,
        generated_at=now,
        fellow=dict(fellow),
        sections=tuple(sections),
        withheld=WITHHELD,
    )


def _value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    text = str(value)
    return text.replace("\r\n", " ").replace("\n", " ").strip() or "-"


def _wrap(text: str, width: int = 76, indent: str = "  ") -> list[str]:
    out: list[str] = []
    for paragraph in text.split("\n"):
        line = indent
        for word in paragraph.split():
            if len(line) + len(word) + 1 > width + len(indent) and line.strip():
                out.append(line.rstrip())
                line = indent
            line += word + " "
        out.append(line.rstrip())
    return out


def render_text(export: FellowExport) -> str:
    """The archive as a person reads it.

    Plain text with one block per row rather than a table: the rows are wide
    (twenty columns on a check-in) and a table that wraps is not human-readable,
    which is the whole requirement. The CSV on the fellow's page stays as the
    spreadsheet-shaped answer.
    """
    name = export.fellow.get("full_name") or export.fellow_id
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"Everything held about {name}")
    lines.append(f"Fellowship id {export.fellow_id} · cohort {export.fellow.get('cohort_id')}")
    lines.append(f"Prepared {export.generated_at:%Y-%m-%d %H:%M} UTC")
    lines.append("=" * 78)
    lines.append("")
    lines.extend(
        _wrap(
            "This is every record this system holds about one person, across every "
            "table it has. It was assembled by the tool itself rather than typed "
            "out by hand, so it does not depend on anybody remembering a table.",
            indent="",
        )
    )
    lines.append("")

    for section in export.sections:
        lines.append("-" * 78)
        lines.append(section.title.upper())
        lines.append("-" * 78)
        lines.extend(_wrap(section.lead))
        lines.append("")
        if not section.rows:
            lines.append(f"  {section.empty}")
        else:
            width = max(
                (len(k) for row in section.rows for k in row), default=0
            )
            for index, row in enumerate(section.rows, start=1):
                if len(section.rows) > 1:
                    lines.append(f"  [{index}]")
                for key, value in row.items():
                    lines.append(f"    {key.replace('_', ' '):<{width}}  {_value(value)}")
                lines.append("")
        if section.note:
            lines.extend(_wrap(section.note, indent="  ! "))
        lines.append("")

    lines.append("-" * 78)
    lines.append("WHAT THIS ARCHIVE LEAVES OUT, AND WHY")
    lines.append("-" * 78)
    for item in export.withheld:
        lines.extend(_wrap(item, indent="  - "))
        lines.append("")

    lines.append("-" * 78)
    lines.append("WHAT IS NEVER COLLECTED AT ALL")
    lines.append("-" * 78)
    for item in export.not_collected:
        lines.extend(_wrap(item, indent="  - "))
    lines.append("")
    lines.extend(_wrap(HOW_TO_ASK))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EraseStep:
    """One table, and what erasure does to it."""

    table: str
    action: str  # delete | anonymise | keep
    rows: int
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "action": self.action, "rows": self.rows, "why": self.why}


@dataclass(frozen=True)
class ErasePlan:
    """What an erasure did, or would do. Dry runs and real runs share the shape."""

    fellow_id: str
    full_name: str
    applied: bool
    already_erased_at: datetime | None = None
    steps: tuple[EraseStep, ...] = ()
    residue: tuple[str, ...] = ()
    erasure_email: str = ""

    @property
    def rows_touched(self) -> int:
        return sum(s.rows for s in self.steps if s.action != "keep")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fellow_id": self.fellow_id,
            "full_name": self.full_name,
            "applied": self.applied,
            "already_erased_at": self.already_erased_at,
            "steps": [s.to_dict() for s in self.steps],
            "residue": list(self.residue),
            "erasure_email": self.erasure_email,
            "rows_touched": self.rows_touched,
        }


#: What erasure cannot reach, said plainly. Every line here is a hole somebody
#: could otherwise discover later and reasonably call a lie.
RESIDUE: tuple[str, ...] = (
    "The roster row survives as a tombstone: the CU-issued fellow id, the "
    "cohort, and '[erased]' where the name was. It cannot be dropped — "
    "bot_delivery references it with ON DELETE RESTRICT and intervention with ON "
    "DELETE CASCADE, so dropping it would either fail or destroy the record of "
    "what staff did. This is pseudonymisation: CU can still map that id to a "
    "person from its own records, outside this database.",
    "checkin and checkin_b rows are kept and emptied rather than deleted. Both "
    "carry an idempotency key and both ingest paths are ON CONFLICT DO NOTHING, "
    "so a deleted row would be written back, name and address included, by the "
    "next `cufa pull`. The surviving key is what makes the erasure stick.",
    "attendance_decision rows are kept. They are the append-only ledger of "
    "judgments, they hold no identifier for the fellow beyond the check-in id, "
    "and the check-in they point at is now pseudonymised.",
    "fellow_access_log rows are kept. An audit trail of who read a record is the "
    "evidence that it was handled properly, and it never held the fellow's "
    "address.",
    "digest_log and slack_qa_summary hold text that was already posted into "
    "Slack. Neither has a roster key to erase by, and deleting a copy here would "
    "not unsend the Slack message. Retracting those is done in Slack.",
    "muddiest_theme summaries are generated from anonymous strings with no names "
    "in the payload, so there is nothing in them to erase.",
    "zoom_transcript_turn is matched on the exact speaker name. A transcript "
    "that spelled it differently keeps it, and no fuzzy match is attempted "
    "because a wrong one would erase somebody else.",
    "Slack still holds the messages themselves. This erases what this system "
    "recorded about them, not the workspace.",
)


def _step(
    conn: psycopg.Connection,
    *,
    table: str,
    action: str,
    why: str,
    sql: str,
    where: str,
    params: tuple[Any, ...],
    apply: bool,
) -> EraseStep:
    """Run one step, or count what it would touch.

    ``sql`` is the write and ``where`` the same predicate as a count, so a dry
    run and a real run can never disagree about scope — which is the only
    property that makes a dry run worth offering.
    """
    if apply:
        rows = execute(conn, sql, params)
    else:
        row = fetch_one(conn, f"select count(*) as n from {table} where {where}", params)
        rows = int((row or {}).get("n", 0))
    return EraseStep(table=table, action=action, rows=rows, why=why)


def erase_fellow(
    conn: psycopg.Connection,
    fellow_id: str,
    *,
    requested_by: str,
    apply: bool = False,
) -> ErasePlan:
    """Honour a deletion request for one fellow.

    Dry run unless ``apply`` is true, and idempotent either way: every predicate
    is written so that a second run matches nothing. Running it twice on the same
    person reports zero rows and changes nothing, which is what makes it safe to
    re-run after an interrupted first attempt.
    """
    fellow = fetch_one(
        conn,
        "select fellow_id, full_name, cohort_id, erased_at from fellow where fellow_id = %s",
        (fellow_id,),
    )
    if fellow is None:
        raise CufaError(f"No fellow with id {fellow_id}")
    if not (requested_by or "").strip():
        raise CufaError(
            "An erasure has to say who asked for it. Pass --by with the address "
            "of the staff member honouring the request."
        )

    sentinel = erasure_email(fellow_id)
    full_name = str(fellow["full_name"])
    emails = _emails(conn, fellow_id)
    slack_ids = _slack_ids(conn, fellow_id)
    ticket_ids = _checkin_b_ids(conn, emails)
    steps: list[EraseStep] = []

    # Observations first, while the addresses still resolve. Anonymising the
    # roster row changes v_fellow_email, so the address list is captured above
    # rather than re-read per step.
    #
    # On a SECOND run that same list comes back holding the sentinel, because
    # the sentinel is now the roster's primary address — so every predicate
    # keyed on an address also excludes rows that are already erased. Without
    # that, a re-run matches its own output, reports rows touched and is not
    # idempotent, which matters because the first run is always the dry one and
    # re-running is the normal case rather than the exception.
    steps.append(
        _step(
            conn,
            table="checkin",
            action="anonymise",
            why="the address and every form answer go; the row and its idempotency key stay",
            sql="update checkin set submitted_email = %s, extra_fields = '{}'::jsonb, "
            "answers = '{}'::jsonb "
            "where lower(submitted_email) = any(%s) "
            "and submitted_email not like '%%@erased.invalid'",
            where="lower(submitted_email) = any(%s) "
            "and submitted_email not like '%%@erased.invalid'",
            params=(sentinel, emails) if apply else (emails,),
            apply=apply,
        )
    )
    steps.append(
        _step(
            conn,
            table="checkin_b",
            action="anonymise",
            why="the address and every free-text answer go; the row and its idempotency key stay",
            sql="update checkin_b set submitted_email = %s, takeaway_text = null, "
            "rotating_text = null, shoutout_text = null, extra_fields = '{}'::jsonb "
            "where lower(submitted_email) = any(%s) "
            "and submitted_email not like '%%@erased.invalid'",
            where="lower(submitted_email) = any(%s) "
            "and submitted_email not like '%%@erased.invalid'",
            params=(sentinel, emails) if apply else (emails,),
            apply=apply,
        )
    )
    if ticket_ids:
        steps.append(
            _step(
                conn,
                table="peer_shoutout",
                action="delete",
                why="shoutouts this person gave: their own words, and the act was theirs",
                sql="delete from peer_shoutout where checkin_b_id = any(%s)",
                where="checkin_b_id = any(%s)",
                params=(ticket_ids,),
                apply=apply,
            )
        )
        steps.append(
            _step(
                conn,
                table="muddiest_theme_member",
                action="delete",
                why="their answer's membership of a cohort theme; the theme itself carries no name",
                sql="delete from muddiest_theme_member where checkin_b_id = any(%s)",
                where="checkin_b_id = any(%s)",
                params=(ticket_ids,),
                apply=apply,
            )
        )
    steps.append(
        _step(
            conn,
            table="peer_shoutout",
            action="anonymise",
            why="shoutouts naming this person, written by somebody else: the name comes out, the other fellow's act stays",
            sql="update peer_shoutout set named_fellow_id = null, "
            "match_method = 'unresolved', raw_text = %s where named_fellow_id = %s",
            where="named_fellow_id = %s",
            params=(ERASED_SHOUTOUT_TEXT, fellow_id) if apply else (fellow_id,),
            apply=apply,
        )
    )
    steps.append(
        _step(
            conn,
            table="intervention",
            action="anonymise",
            why="the staff note goes; the row stays so the record of what staff did survives",
            sql="update intervention set note = null where fellow_id = %s and note is not null",
            where="fellow_id = %s and note is not null",
            params=(fellow_id,),
            apply=apply,
        )
    )
    steps.append(
        _step(
            conn,
            table="help_request",
            action="anonymise",
            why="the address and the note go; the row stays because a safeguarding request having been raised and answered is its own record",
            sql=_HELP_ERASE_SQL,
            where=_HELP_ERASE_WHERE,
            params=(sentinel, fellow_id, emails, sentinel) if apply else (fellow_id, emails, sentinel),
            apply=apply,
        )
    )
    steps.append(
        _step(
            conn,
            table="identity_unresolved",
            action="delete",
            why="a queue entry for an address nobody has placed; nothing depends on it",
            sql="delete from identity_unresolved where lower(email) = any(%s) or best_guess_fellow_id = %s",
            where="lower(email) = any(%s) or best_guess_fellow_id = %s",
            params=(emails, fellow_id),
            apply=apply,
        )
    )
    for table, why in (
        ("assignment_submission", "their submissions and scores"),
        ("badge_award", "derived recognition; recomputed from observations that no longer name them"),
        ("bot_delivery", "the log of messages the bot sent them"),
        ("fellow_reminder_preference", "their own settings"),
        ("fellow_alias", "their other addresses"),
    ):
        steps.append(
            _step(
                conn,
                table=table,
                action="delete",
                why=why,
                sql=f"delete from {table} where fellow_id = %s",
                where="fellow_id = %s",
                params=(fellow_id,),
                apply=apply,
            )
        )
    if slack_ids:
        for table, why in (
            ("slack_preference", "their reminder settings, kept on the Slack account"),
            ("reminder_sent", "the log of reminders sent to that account"),
            ("roster_alert", "a queue entry about that account joining the workspace"),
            ("slack_qa_answer", "answers they wrote in a Q&A channel: their own words"),
            ("slack_qa_question", "questions they asked in a Q&A channel: their own words"),
        ):
            steps.append(
                _step(
                    conn,
                    table=table,
                    action="delete",
                    why=why,
                    sql=f"delete from {table} where slack_user_id = any(%s)",
                    where="slack_user_id = any(%s)",
                    params=(slack_ids,),
                    apply=apply,
                )
            )
        steps.append(
            _step(
                conn,
                table="slack_event",
                action="anonymise",
                why="their acts stay as counts with nobody attached; address, any stored text and mentions go",
                sql="update slack_event set slack_user_id = null, user_email = null, "
                "text = null, mentions = null "
                "where slack_user_id = any(%s) or lower(user_email) = any(%s)",
                where="slack_user_id = any(%s) or lower(user_email) = any(%s)",
                params=(slack_ids, emails),
                apply=apply,
            )
        )
        steps.append(
            _step(
                conn,
                table="slack_event",
                action="anonymise",
                why="other people's acts that referenced them: a reaction to their message, a mention",
                sql="update slack_event set item_user_id = null, mentions = null "
                "where item_user_id = any(%s) or mentions && %s::text[]",
                where="item_user_id = any(%s) or mentions && %s::text[]",
                params=(slack_ids, slack_ids),
                apply=apply,
            )
        )
        if apply:
            execute(
                conn,
                "insert into erased_slack_user (slack_user_id) "
                "select unnest(%s::text[]) on conflict do nothing",
                (slack_ids,),
            )
        steps.append(
            _step(
                conn,
                table="slack_user",
                action="anonymise",
                why="name, address and time zone go; the workspace id stays on a suppression list so the next `cufa slack users` cannot write them back",
                sql="update slack_user set email = null, display_name = %s, real_name = %s, "
                "tz = null, tz_offset_s = null, raw = '{}'::jsonb, linked_fellow_id = null "
                "where slack_user_id = any(%s) and (email is not null or real_name is distinct from %s)",
                where="slack_user_id = any(%s) and (email is not null or real_name is distinct from %s)",
                params=(ERASED_NAME, ERASED_NAME, slack_ids, ERASED_NAME)
                if apply
                else (slack_ids, ERASED_NAME),
                apply=apply,
            )
        )
    if full_name != ERASED_NAME:
        steps.append(
            _step(
                conn,
                table="zoom_transcript_turn",
                action="anonymise",
                why="the speaker name from a transcript, matched exactly; turns and seconds stay",
                sql="update zoom_transcript_turn set speaker_name = %s "
                "where lower(btrim(speaker_name)) = lower(btrim(%s))",
                where="lower(btrim(speaker_name)) = lower(btrim(%s))",
                params=(ERASED_NAME, full_name) if apply else (full_name,),
                apply=apply,
            )
        )

    # The roster row last: anonymising it changes v_fellow_email, which the
    # steps above resolve through.
    steps.append(
        _step(
            conn,
            table="fellow",
            action="anonymise",
            why="name, address and time zone go; the id and the cohort stay as a tombstone so every foreign key and every ledger survives",
            sql="update fellow set full_name = %s, primary_email = %s, timezone = null, "
            "status = 'withdrawn', erased_at = now(), erased_by = %s, updated_at = now() "
            "where fellow_id = %s and erased_at is null",
            where="fellow_id = %s and erased_at is null",
            params=(ERASED_NAME, sentinel, (requested_by or "").strip().lower(), fellow_id)
            if apply
            else (fellow_id,),
            apply=apply,
        )
    )

    # Kept on purpose, reported so nobody has to take it on trust.
    for table, where, params, why in (
        (
            "fellow_access_log",
            "fellow_id = %s",
            (fellow_id,),
            "the audit trail of who read this record; it never held the fellow's address",
        ),
        (
            "attendance_decision",
            "checkin_id in (select checkin_id from checkin where lower(submitted_email) = any(%s))",
            ([*emails, sentinel],),
            "the append-only ledger of judgments; no fellow identifier beyond the check-in id",
        ),
    ):
        row = fetch_one(conn, f"select count(*) as n from {table} where {where}", params)
        steps.append(
            EraseStep(
                table=table, action="keep", rows=int((row or {}).get("n", 0)), why=why
            )
        )

    plan = ErasePlan(
        fellow_id=fellow_id,
        full_name=full_name,
        applied=apply,
        already_erased_at=fellow["erased_at"],
        steps=tuple(steps),
        residue=RESIDUE,
        erasure_email=sentinel,
    )

    if apply:
        execute(
            conn,
            "insert into data_erasure (fellow_id, requested_by, touched) values (%s, %s, %s::jsonb)",
            (
                fellow_id,
                (requested_by or "").strip().lower(),
                _touched_json(plan),
            ),
        )
        log.info(
            "erasure applied fellow=%s rows=%d by=%s",
            fellow_id,
            plan.rows_touched,
            mask_email(requested_by),
        )
    return plan


def _touched_json(plan: ErasePlan) -> str:
    import json

    counts: dict[str, int] = {}
    for step in plan.steps:
        if step.action == "keep":
            continue
        counts[f"{step.table}.{step.action}"] = counts.get(f"{step.table}.{step.action}", 0) + step.rows
    return json.dumps(counts, sort_keys=True)


def render_plan(plan: ErasePlan) -> str:
    """The erasure report. Says exactly what it touched, or would touch."""
    lines: list[str] = []
    heading = "Erasure applied" if plan.applied else "Erasure DRY RUN — nothing was changed"
    lines.append(heading)
    lines.append("")
    lines.append(f"  fellow            {plan.fellow_id}  ({plan.full_name})")
    lines.append(f"  address after     {plan.erasure_email}")
    if plan.already_erased_at:
        lines.append(f"  already erased    {plan.already_erased_at:%Y-%m-%d %H:%M} UTC")
    lines.append("")
    width = max((len(s.table) for s in plan.steps), default=0)
    for step in plan.steps:
        lines.append(f"  {step.action:<10} {step.table:<{width}}  {step.rows:>5} row(s)")
        lines.extend(_wrap(step.why, indent=" " * (14 + width) + "  "))
    lines.append("")
    lines.append(f"  {plan.rows_touched} row(s) changed or removed in total.")
    if not plan.applied:
        lines.append("")
        lines.extend(
            _wrap(
                "This was a dry run. Re-run with --apply to carry it out. Running "
                "it twice is safe: every step is written so a second run matches "
                "nothing.",
            )
        )
    lines.append("")
    lines.append("  What erasure cannot reach:")
    lines.append("")
    for item in plan.residue:
        lines.extend(_wrap(item, indent="    - "))
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "COLLECTION_NOTICE",
    "ERASED_NAME",
    "ERASED_SHOUTOUT_TEXT",
    "HOW_TO_ASK",
    "NOT_COLLECTED",
    "RESIDUE",
    "WITHHELD",
    "EraseStep",
    "ErasePlan",
    "FellowExport",
    "Section",
    "erase_fellow",
    "erasure_email",
    "export_fellow",
    "render_plan",
    "render_text",
]
