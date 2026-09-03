"""Q&A channels: "this was asked before", and a per-session summary for the teacher.

Two things the bot does in the channels named by ``CUFA_SLACK_QA_CHANNELS``,
and nowhere else:

* **Point at the earlier answer.** When a new top-level question resembles an
  earlier question that was answered, the bot replies in the new thread with a
  link to the earlier thread (or straight to the reply somebody marked ✅), and
  names the session it came from: *"asked during Sep 2 · Voting systems"*.
  Only answered questions are pointed at — a pointer to an unanswered thread
  helps nobody.
* **Summarise a session's Q&A.** ``cufa slack qa summary`` (or
  ``@bot summary sept 2`` in Slack) collects the questions asked around one
  session, with their replies, and writes a digest for the teacher: what was
  asked, what got settled, what is still open, each with a link.

**What the model sees, when there is one.** Numbered anonymous strings — the
question texts and reply texts — and nothing else. No names, no user ids, no
addresses, no counts per person. That is ADR-027 applied to Slack: the model
clusters and matches *content*; it never characterises a person. Without a
``GEMINI_API_KEY`` both features still work: matching falls back to token
overlap (tier 1, deterministic), and the summary is the plain digest.

**Why text is stored here at all**, given ADR-031: a Q&A channel is different in
kind from #general. A question is posted so that it can be found and answered,
and the whole value of an answer is that the next person can be pointed at it.
Neither is possible without the words. So the text of these channels — and
only these — lives in ``slack_qa_question`` / ``slack_qa_answer`` (ADR-032).
``slack_event.text`` stays NULL for them like everywhere else.

Two tiers, same shape as passphrase adjudication: everything token overlap can
decide, it decides; the model is asked only about the candidates overlap could
not settle, and only when a key is configured.
"""

from __future__ import annotations

import html
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol, Sequence

import psycopg
from slack_sdk.errors import SlackApiError

from ..config import Settings, get_settings
from ..db import execute, fetch_all, fetch_one
from ..errors import AiUnavailable
from ..logging_setup import get_logger
from ..text import normalize_answer
from ..timeutil import get_zone
from .events import history_message_to_event, ts_to_utc

log = get_logger(__name__)

#: Bump whenever a prompt or a schema changes; stored on every summary row.
PROMPT_VERSION = "qa1"

#: A question posted up to this long BEFORE a session starts belongs to it:
#: "what's tonight about?" arrives before the call opens.
SESSION_LEAD = timedelta(minutes=60)

#: Reactions that mean "this is the answer" (on a reply) or "resolved" (on the
#: question). Slack's own convention, and the only one fellows need to learn.
ACCEPT_REACTIONS = frozenset({"white_check_mark", "heavy_check_mark", "ballot_box_with_check"})

#: Tier 1: this much token overlap, with at least two shared content words, is
#: a match without asking anyone. Deliberately hedged in the reply wording.
LEXICAL_MATCH = 0.5
LEXICAL_MIN_SHARED = 2
#: Below this a candidate is not shown to the model at all; above it, the model
#: decides. Paraphrases that share not one content word are missed on purpose:
#: sending every question ever asked on every new question is the alternative.
LEXICAL_CANDIDATE = 0.15
MAX_CANDIDATES = 8
#: Tier 2 must be at least this sure, or nothing is posted.
MODEL_MIN_CONFIDENCE = 0.6

#: How much of a question shows in a link or a digest line.
SNIPPET = 120


# ---------------------------------------------------------------------------
# Text: Slack markup → words → tokens
# ---------------------------------------------------------------------------

_MENTION_RE = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
_CHANNEL_RE = re.compile(r"<#[CG][A-Z0-9]+(?:\|([^>]*))?>")
_SPECIAL_RE = re.compile(r"<!([a-z_]+)(?:\^[^>|]*)?(?:\|([^>]*))?>")
_LINK_RE = re.compile(r"<((?:https?|mailto):[^>|]*)(?:\|([^>]*))?>")
_URL_RE = re.compile(r"(?:https?://|mailto:)\S+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

#: Words that carry no subject matter. Question words are here on purpose —
#: "what does quorum mean" and "quorum meaning?" are the same question, and
#: "what does X mean" / "what does Y mean" must NOT match on the frame alone.
STOPWORDS = frozenset(
    """
    a an the and or but if then so to of in on at for from with by about as into
    like than too very just not no yes ok okay please thanks thank hi hey hello
    i me my mine we us our ours you your yours he him his she her hers it its they
    them their theirs this that these those there here
    what when where who whom whose which why how
    is are was were be been being am do does did done doing have has had having
    can could will would should shall may might must
    anyone someone anybody somebody everyone everybody nobody
    know knows knew think thought get got gets
    still also any some all one ones more most much many few
    up down out over again quick question questions wondering wonder
    re s t d ll ve m
    """.split()
)


def clean_text(text: str | None) -> str:
    """Slack markup rendered for a human: mentions become ``@someone``,
    channel links ``#name``, ``<url|label>`` its label, entities unescaped.

    ``@someone`` rather than the user's name is deliberate — this is what
    reaches the model and the digest, and neither should carry a name.
    """
    body = text or ""
    body = _MENTION_RE.sub("@someone", body)
    body = _CHANNEL_RE.sub(lambda m: "#" + (m.group(1) or "channel"), body)
    body = _SPECIAL_RE.sub(lambda m: "@" + (m.group(2) or m.group(1)), body)
    body = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), body)
    body = html.unescape(body)
    return _WS_RE.sub(" ", body).strip()


def normalize_question(text: str | None) -> str:
    """What the lexical matcher compares: cleaned, URL-free, casefolded,
    punctuation stripped. Stored on the row so a stored question and a live
    one are normalized by the same code at the same version."""
    body = _URL_RE.sub(" ", clean_text(text))
    return normalize_answer(body)


def _stem(word: str) -> str:
    """Just enough to make "slides"/"slide" and "presenting"/"present" agree.
    Conservative on purpose: tier 1 only handles the obvious cases."""
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def token_set(normalized: str) -> frozenset[str]:
    """Content words of a normalized question."""
    return frozenset(
        _stem(w) for w in normalized.split() if len(w) > 1 and w not in STOPWORDS
    )


def lexical_similarity(a: str, b: str) -> tuple[float, int]:
    """``(score, shared)`` for two normalized questions.

    The score is the mean of the overlap coefficient (a short question inside
    a longer one scores high) and Jaccard (a long question with one word in
    common does not). ``shared`` is how many content words they have in
    common, which the caller uses as a floor: one shared word is a topic, not
    a match.
    """
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return 0.0, 0
    shared = len(ta & tb)
    if shared == 0:
        return 0.0, 0
    overlap = shared / min(len(ta), len(tb))
    jaccard = shared / len(ta | tb)
    return round((overlap + jaccard) / 2, 3), shared


def snippet(text: str | None, limit: int = SNIPPET) -> str:
    body = clean_text(text)
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "…"


def mrkdwn_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mrkdwn_link(url: str | None, text: str) -> str:
    label = mrkdwn_escape(text).replace("|", "¦")
    return f"<{url}|{label}>" if url else label


# ---------------------------------------------------------------------------
# Parsing: what an event means in a Q&A channel. Pure.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QaObservation:
    """One thing that happened in a Q&A channel."""

    #: question | answer | edit | delete | reaction
    kind: str
    team_id: str
    channel_id: str
    message_ts: str
    #: For an answer or an edit of one: the question's ts.
    thread_ts: str | None
    slack_user_id: str | None
    text: str
    at: datetime
    reaction: str | None = None
    removed: bool = False


_QA_SUBTYPES = (None, "file_share", "thread_broadcast")


def parse_qa_event(event: dict[str, Any], team_id: str) -> QaObservation | None:
    """Reduce an Events API ``event`` to a Q&A observation, or None if it is
    nothing a Q&A channel cares about. Bots and system messages are None."""
    etype = event.get("type")
    if etype == "message":
        channel = event.get("channel")
        if not channel:
            return None
        subtype = event.get("subtype")
        if subtype == "message_changed":
            inner = event.get("message") or {}
            ts = inner.get("ts")
            if not ts or inner.get("bot_id"):
                return None
            thread = inner.get("thread_ts")
            is_reply = bool(thread) and str(thread) != str(ts)
            edited_ts = (inner.get("edited") or {}).get("ts") or event.get("ts") or ts
            return QaObservation(
                "edit", team_id, channel, str(ts), str(thread) if is_reply else None,
                inner.get("user"), inner.get("text") or "", ts_to_utc(edited_ts),
            )
        if subtype == "message_deleted":
            previous = event.get("previous_message") or {}
            deleted = event.get("deleted_ts") or previous.get("ts")
            if not deleted:
                return None
            thread = previous.get("thread_ts")
            is_reply = bool(thread) and str(thread) != str(deleted)
            return QaObservation(
                "delete", team_id, channel, str(deleted), str(thread) if is_reply else None,
                previous.get("user"), "", ts_to_utc(event.get("ts") or event.get("event_ts") or deleted),
            )
        if subtype not in _QA_SUBTYPES:
            return None
        if event.get("bot_id") or not event.get("user") or not event.get("ts"):
            return None
        ts = str(event["ts"])
        thread = event.get("thread_ts")
        is_reply = bool(thread) and str(thread) != ts
        return QaObservation(
            "answer" if is_reply else "question", team_id, channel, ts,
            str(thread) if is_reply else None, event["user"], event.get("text") or "", ts_to_utc(ts),
        )

    if etype in ("reaction_added", "reaction_removed"):
        name = event.get("reaction")
        item = event.get("item") or {}
        if name not in ACCEPT_REACTIONS or item.get("type") not in (None, "message"):
            return None
        if not (item.get("channel") and item.get("ts")):
            return None
        return QaObservation(
            "reaction", team_id, item["channel"], str(item["ts"]), None, event.get("user"), "",
            ts_to_utc(event.get("event_ts") or item["ts"]),
            reaction=name, removed=(etype == "reaction_removed"),
        )
    return None


def event_channel(event: dict[str, Any]) -> str | None:
    """The channel an event is about, for any event type this package handles."""
    return event.get("channel") or (event.get("item") or {}).get("channel")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def record_question(conn: psycopg.Connection, obs: QaObservation) -> tuple[str, bool]:
    """Upsert a question. Returns ``(question_id, inserted)``.

    An upsert rather than an insert-or-ignore because the backfill re-reads
    history and a re-read carries the *current* text, which after an edit is
    the one to keep.
    """
    row = fetch_one(
        conn,
        """
        insert into slack_qa_question
            (team_id, channel_id, message_ts, slack_user_id, text, normalized_text, asked_at_utc)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (team_id, channel_id, message_ts) do update
           set text = excluded.text,
               normalized_text = excluded.normalized_text,
               updated_at = now()
        returning question_id, (xmax = 0) as inserted
        """,
        (
            obs.team_id, obs.channel_id, obs.message_ts, obs.slack_user_id or "",
            obs.text, normalize_question(obs.text), obs.at,
        ),
    )
    assert row is not None
    return str(row["question_id"]), bool(row["inserted"])


def question_by_ts(conn: psycopg.Connection, team_id: str, channel_id: str, ts: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        "select * from slack_qa_question where team_id = %s and channel_id = %s and message_ts = %s",
        (team_id, channel_id, ts),
    )


def get_question(conn: psycopg.Connection, question_id: str) -> dict[str, Any] | None:
    return fetch_one(conn, "select * from slack_qa_question where question_id = %s", (question_id,))


def record_answer(
    conn: psycopg.Connection,
    obs: QaObservation,
    *,
    fetch_parent: Callable[[str, str], QaObservation | None] | None = None,
) -> str | None:
    """Upsert a reply under its question.

    A reply can arrive for a question the bot never saw — it joined the channel
    after the question was posted. ``fetch_parent`` reads the parent back from
    Slack so the answer has somewhere to hang; without one, the reply is
    skipped and logged rather than stored orphaned.
    """
    assert obs.thread_ts
    parent = question_by_ts(conn, obs.team_id, obs.channel_id, obs.thread_ts)
    if parent is None:
        fetched = fetch_parent(obs.channel_id, obs.thread_ts) if fetch_parent else None
        if fetched is None or fetched.kind != "question":
            log.info("qa reply to an unknown parent channel=%s ts=%s — skipped", obs.channel_id, obs.thread_ts)
            return None
        question_id, _ = record_question(conn, fetched)
    else:
        question_id = str(parent["question_id"])

    row = fetch_one(
        conn,
        """
        insert into slack_qa_answer
            (question_id, team_id, channel_id, message_ts, slack_user_id, text, answered_at_utc)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (team_id, channel_id, message_ts) do update
           set text = excluded.text, updated_at = now()
        returning answer_id
        """,
        (question_id, obs.team_id, obs.channel_id, obs.message_ts, obs.slack_user_id or "", obs.text, obs.at),
    )
    assert row is not None
    return str(row["answer_id"])


def apply_edit(conn: psycopg.Connection, obs: QaObservation) -> str | None:
    """Update the text of whichever row the edited message is. Returns
    'question', 'answer', or None when the message was never recorded."""
    n = execute(
        conn,
        """
        update slack_qa_question
           set text = %s, normalized_text = %s, edited_at_utc = %s, updated_at = now()
         where team_id = %s and channel_id = %s and message_ts = %s
        """,
        (obs.text, normalize_question(obs.text), obs.at, obs.team_id, obs.channel_id, obs.message_ts),
    )
    if n:
        return "question"
    n = execute(
        conn,
        """
        update slack_qa_answer
           set text = %s, edited_at_utc = %s, updated_at = now()
         where team_id = %s and channel_id = %s and message_ts = %s
        """,
        (obs.text, obs.at, obs.team_id, obs.channel_id, obs.message_ts),
    )
    return "answer" if n else None


def apply_delete(conn: psycopg.Connection, obs: QaObservation) -> str | None:
    """Stamp deleted_at. A deleted question is never pointed at or summarised;
    a deleted reply stops counting as an answer. The row stays — a pointer
    that was already posted still refers to it."""
    n = execute(
        conn,
        """
        update slack_qa_question set deleted_at_utc = %s, updated_at = now()
         where team_id = %s and channel_id = %s and message_ts = %s
        """,
        (obs.at, obs.team_id, obs.channel_id, obs.message_ts),
    )
    if n:
        return "question"
    n = execute(
        conn,
        """
        update slack_qa_answer set deleted_at_utc = %s, updated_at = now()
         where team_id = %s and channel_id = %s and message_ts = %s
        """,
        (obs.at, obs.team_id, obs.channel_id, obs.message_ts),
    )
    return "answer" if n else None


def apply_reaction(conn: psycopg.Connection, obs: QaObservation) -> str | None:
    """A ✅ on a reply marks it accepted; on a question, resolved. Removing
    the reaction clears the flag. (Two people's checkmarks are one flag —
    a count of who accepted what is exactly what this must not become.)"""
    flag = not obs.removed
    n = execute(
        conn,
        """
        update slack_qa_answer set accepted = %s, updated_at = now()
         where team_id = %s and channel_id = %s and message_ts = %s
        """,
        (flag, obs.team_id, obs.channel_id, obs.message_ts),
    )
    if n:
        return "answer"
    n = execute(
        conn,
        """
        update slack_qa_question set resolved = %s, updated_at = now()
         where team_id = %s and channel_id = %s and message_ts = %s
        """,
        (flag, obs.team_id, obs.channel_id, obs.message_ts),
    )
    return "question" if n else None


#: The answered test, in one place. A question counts as answered when
#: somebody ✅'d it, or a reply exists that was ✅'d or came from someone other
#: than the asker. The asker's own "bump" is not an answer.
_ANSWERED_SQL = """
    (q.resolved or exists (
        select 1 from slack_qa_answer a
         where a.question_id = q.question_id
           and a.deleted_at_utc is null
           and (a.accepted or a.slack_user_id <> q.slack_user_id)))
"""


def answers_for(conn: psycopg.Connection, question_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select answer_id, channel_id, message_ts, slack_user_id, text, answered_at_utc, accepted, permalink
          from slack_qa_answer
         where question_id = %s and deleted_at_utc is null
         order by answered_at_utc, message_ts
        """,
        (question_id,),
    )


def answered_questions_before(
    conn: psycopg.Connection, team_id: str, before: datetime, *, exclude: str
) -> list[dict[str, Any]]:
    """Earlier, live, answered questions in this workspace — what a new question
    is compared against. Any Q&A channel: a question asked in #questions and
    answered there is still the answer for the same question in #q-and-a."""
    return fetch_all(
        conn,
        f"""
        select q.question_id, q.channel_id, q.message_ts, q.slack_user_id, q.text,
               q.normalized_text, q.asked_at_utc, q.resolved, q.permalink
          from slack_qa_question q
         where q.team_id = %s
           and q.deleted_at_utc is null
           and q.asked_at_utc < %s
           and q.question_id <> %s
           and {_ANSWERED_SQL}
         order by q.asked_at_utc desc
         limit 500
        """,
        (team_id, before, exclude),
    )


def accepted_answer(conn: psycopg.Connection, question_id: str) -> dict[str, Any] | None:
    """The reply somebody marked ✅, earliest first if several."""
    return fetch_one(
        conn,
        """
        select answer_id, channel_id, message_ts, text, permalink
          from slack_qa_answer
         where question_id = %s and deleted_at_utc is null and accepted
         order by answered_at_utc limit 1
        """,
        (question_id,),
    )


# ---------------------------------------------------------------------------
# Sessions: which lesson a question belongs to
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionRef:
    session_id: str
    cohort_id: str
    title: str
    scheduled_at_utc: datetime
    scheduled_at_local: datetime
    timezone: str
    duration_minutes: int

    @property
    def label(self) -> str:
        """``Sep 2 · Voting systems`` — the way a teacher refers to a lesson."""
        local = self.scheduled_at_local
        return f"{local:%b} {local.day} · {self.title}"

    @property
    def local_date(self) -> date:
        return self.scheduled_at_local.date()


def cohort_sessions(conn: psycopg.Connection, cohort_id: str) -> list[SessionRef]:
    rows = fetch_all(
        conn,
        """
        select session_id, cohort_id, title, scheduled_at_utc, scheduled_at_local, timezone, duration_minutes
          from "session" where cohort_id = %s order by scheduled_at_utc, session_id
        """,
        (cohort_id,),
    )
    return [_session_ref(r) for r in rows]


def _session_ref(row: dict[str, Any]) -> SessionRef:
    return SessionRef(
        session_id=str(row["session_id"]), cohort_id=row["cohort_id"], title=row["title"],
        scheduled_at_utc=row["scheduled_at_utc"], scheduled_at_local=row["scheduled_at_local"],
        timezone=row["timezone"], duration_minutes=int(row["duration_minutes"]),
    )


def session_ref(conn: psycopg.Connection, session_id: str) -> SessionRef | None:
    row = fetch_one(
        conn,
        """
        select session_id, cohort_id, title, scheduled_at_utc, scheduled_at_local, timezone, duration_minutes
          from "session" where session_id = %s
        """,
        (session_id,),
    )
    return _session_ref(row) if row else None


def question_window(sessions: Sequence[SessionRef], session_id: str) -> tuple[datetime, datetime | None]:
    """Questions asked from ``SESSION_LEAD`` before this session until
    ``SESSION_LEAD`` before the next one belong to this session. The last
    session's window is open-ended: "since the last lesson" is still that
    lesson's Q&A."""
    for index, session in enumerate(sessions):
        if session.session_id == session_id:
            start = session.scheduled_at_utc - SESSION_LEAD
            nxt = sessions[index + 1] if index + 1 < len(sessions) else None
            return start, (nxt.scheduled_at_utc - SESSION_LEAD) if nxt else None
    raise LookupError(f"session {session_id} is not in this cohort's list")


def session_in_effect(sessions: Sequence[SessionRef], at: datetime) -> SessionRef | None:
    """The session a question asked at ``at`` belongs to: the latest one whose
    window has opened. None before the first session's window."""
    current: SessionRef | None = None
    for session in sessions:
        if session.scheduled_at_utc - SESSION_LEAD <= at:
            current = session
        else:
            break
    return current


def session_on_date(sessions: Sequence[SessionRef], day: date) -> SessionRef | None:
    """The session on a local date; failing an exact year, the most recent
    session on that month and day (a teacher says "sept 2", not "2026-09-02")."""
    exact = [s for s in sessions if s.local_date == day]
    if exact:
        return exact[0]
    same_day = [s for s in sessions if (s.local_date.month, s.local_date.day) == (day.month, day.day)]
    return same_day[-1] if same_day else None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)


def parse_date_hint(text: str, *, year: int) -> date | None:
    """A date a person typed: ``2026-09-02``, ``9/2``, ``sept 2``, ``2 Sept``.
    ``year`` fills in when none was typed."""
    body = text or ""
    try:
        if m := _ISO_DATE_RE.search(body):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if m := _SLASH_DATE_RE.search(body):
            y = m.group(3)
            yy = int(y) if y else year
            if y and len(y) == 2:
                yy += 2000
            return date(yy, int(m.group(1)), int(m.group(2)))
        if m := _MONTH_DAY_RE.search(body):
            return date(year, _MONTHS[m.group(1).lower()], int(m.group(2)))
        if m := _DAY_MONTH_RE.search(body):
            return date(year, _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None
    return None


def resolve_session(
    conn: psycopg.Connection,
    cohort_id: str,
    *,
    session_id: str | None = None,
    day: date | None = None,
    latest: bool = False,
    now: datetime | None = None,
) -> SessionRef | None:
    """One of three ways to name a session: by id, by local date, or "the one
    in effect now"."""
    if session_id:
        return session_ref(conn, session_id)
    sessions = cohort_sessions(conn, cohort_id)
    if day is not None:
        return session_on_date(sessions, day)
    if latest:
        return session_in_effect(sessions, now or datetime.now(timezone.utc))
    return None


# ---------------------------------------------------------------------------
# The questions around one session, with their answers — the summary's input
# ---------------------------------------------------------------------------

def questions_for_session(conn: psycopg.Connection, session_id: str) -> list[dict[str, Any]]:
    """Every live question in the session's window across the cohort's Q&A
    channels, with its live replies. Text and links, no identities: the
    SELECT list is the privacy boundary, as in ``themes.muddiest_answers``."""
    session = session_ref(conn, session_id)
    if session is None:
        raise LookupError(f"No session with id {session_id}")
    sessions = cohort_sessions(conn, session.cohort_id)
    start, end = question_window(sessions, session_id)
    rows = fetch_all(
        conn,
        f"""
        select q.question_id, q.team_id, q.channel_id, q.message_ts, q.text, q.normalized_text,
               q.asked_at_utc, q.resolved, q.permalink,
               {_ANSWERED_SQL} as answered
          from slack_qa_question q
          join slack_workspace w on w.team_id = q.team_id
         where w.cohort_id = %s
           and q.deleted_at_utc is null
           and q.asked_at_utc >= %s
           and (%s::timestamptz is null or q.asked_at_utc < %s::timestamptz)
         order by q.asked_at_utc, q.message_ts
        """,
        (session.cohort_id, start, end, end),
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        if not token_set(row["normalized_text"]):
            continue  # "thanks!" is not a question; nothing to summarise or match
        answers = [
            {k: a[k] for k in ("answer_id", "channel_id", "message_ts", "text", "answered_at_utc", "accepted", "permalink")}
            for a in answers_for(conn, str(row["question_id"]))
        ]
        items.append(
            {
                "question_id": str(row["question_id"]),
                "team_id": row["team_id"],
                "channel_id": row["channel_id"],
                "message_ts": row["message_ts"],
                "text": row["text"],
                "asked_at_utc": row["asked_at_utc"],
                "resolved": bool(row["resolved"]),
                "answered": bool(row["answered"]),
                "permalink": row["permalink"],
                "answers": answers,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Gemini, shared by the matcher and the summariser
# ---------------------------------------------------------------------------

def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate limit" in text


class _GeminiJson:
    """One structured-output call with backoff on 429. Same conventions as
    ``adjudicate.ai`` and ``themes``: temperature 0, a response schema, and
    ``AiUnavailable`` for every failure so callers degrade instead of crash."""

    prompt_version = PROMPT_VERSION

    def __init__(self, settings: Settings | None = None, *, what: str) -> None:
        settings = settings or get_settings()
        if not settings.gemini_api_key:
            raise AiUnavailable(f"GEMINI_API_KEY is not set, so {what} cannot use a model.")
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise AiUnavailable(f"google-genai is not importable: {exc}") from exc
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = settings.ai_model

    def generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=types.Schema(**schema),
        )
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self._client.models.generate_content(
                    model=self.model_name, contents=prompt, config=config
                )
                return _parse_json(response.text)
            except AiUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if _is_rate_limit(exc) and attempt < 3:
                    delay = min(2**attempt, 16) * (0.5 + random.random() / 2)
                    log.warning("gemini rate limited; retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                break
        raise AiUnavailable(f"Gemini call failed: {last_error}") from last_error


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        raise AiUnavailable("Gemini returned an empty response")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise AiUnavailable(f"Gemini returned non-JSON: {text[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise AiUnavailable("Gemini returned JSON that is not an object")
    return payload


# ---------------------------------------------------------------------------
# "Asked before": tier 1 (tokens) and tier 2 (the model, among candidates)
# ---------------------------------------------------------------------------

MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "match_number": {"type": "integer"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["match_number", "confidence", "reasoning"],
}

MATCH_PROMPT = """\
A student posted a question in a class Q&A channel. Below it are earlier \
questions from the same channel that were already answered. Decide whether the \
new question asks the SAME thing as one of them — such that the earlier answer \
would answer it.

New question: {new}

Earlier questions:
{candidates}

Rules:
- "Same thing" means the earlier answer would satisfy the new question. Same \
topic but a different ask is NOT a match ("when does the session start" vs \
"when does the session end").
- These are anonymous strings. Do not describe or characterise anyone.
- If none matches, match_number is 0.

Reply with JSON only: match_number (integer; 0 for none), confidence (0.0-1.0), \
reasoning (one sentence).\
"""


@dataclass(frozen=True)
class MatchVerdict:
    """What tier 2 said: which candidate (1-based; 0 = none), how sure, why."""

    match_number: int
    confidence: float
    reasoning: str


class Matcher(Protocol):
    model_name: str
    prompt_version: str

    def match(self, new_question: str, candidates: Sequence[str]) -> MatchVerdict:
        ...


def build_match_prompt(new_question: str, candidates: Sequence[str]) -> str:
    """The exact string sent. Pure, so a test can assert what leaves."""
    numbered = "\n".join(f"{i}. {clean_text(c)}" for i, c in enumerate(candidates, start=1))
    return MATCH_PROMPT.format(new=clean_text(new_question), candidates=numbered)


def parse_match(payload: dict[str, Any], candidate_count: int) -> MatchVerdict:
    """A number outside the list shown is 0, not trusted."""
    try:
        number = int(payload.get("match_number", 0))
    except (TypeError, ValueError):
        number = 0
    if not 1 <= number <= candidate_count:
        number = 0
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return MatchVerdict(number, max(0.0, min(1.0, confidence)), str(payload.get("reasoning", "")).strip())


class GeminiMatcher(_GeminiJson):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings, what="the asked-before check")

    def match(self, new_question: str, candidates: Sequence[str]) -> MatchVerdict:
        payload = self.generate(build_match_prompt(new_question, candidates), MATCH_SCHEMA)
        return parse_match(payload, len(candidates))


def build_matcher(settings: Settings | None = None) -> Matcher | None:
    try:
        return GeminiMatcher(settings or get_settings())
    except AiUnavailable as exc:
        log.info("qa matcher: tier 1 only — %s", str(exc).splitlines()[0])
        return None


@dataclass(frozen=True)
class EarlierMatch:
    question: dict[str, Any]
    method: str
    similarity: float
    reasoning: str


def find_earlier_answered(
    conn: psycopg.Connection,
    team_id: str,
    question: dict[str, Any],
    *,
    matcher: Matcher | None = None,
) -> EarlierMatch | None:
    """The earlier answered question this one repeats, or None.

    Tier 1 decides on token overlap; identical normalized text always matches.
    Tier 2 sees at most ``MAX_CANDIDATES`` earlier questions that share at
    least a word, and only when a matcher exists. The model never sees a
    question that overlap ruled out entirely.
    """
    normalized = question["normalized_text"]
    if not token_set(normalized):
        return None
    earlier = answered_questions_before(conn, team_id, question["asked_at_utc"], exclude=question["question_id"])
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for row in earlier:
        if row["normalized_text"] == normalized:
            return EarlierMatch(row, "lexical", 1.0, "identical wording")
        score, shared = lexical_similarity(normalized, row["normalized_text"])
        if shared:
            scored.append((score, shared, row))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[2]["asked_at_utc"]))
    best_score, best_shared, best = scored[0]
    if best_score >= LEXICAL_MATCH and best_shared >= LEXICAL_MIN_SHARED:
        return EarlierMatch(best, "lexical", best_score, f"{best_shared} shared words, overlap {best_score:.2f}")

    if matcher is None:
        return None
    candidates = [row for score, _, row in scored if score >= LEXICAL_CANDIDATE][:MAX_CANDIDATES]
    if not candidates:
        return None
    try:
        verdict = matcher.match(question["text"], [c["text"] for c in candidates])
    except AiUnavailable as exc:
        log.warning("qa matcher unavailable, tier 1 only: %s", str(exc).splitlines()[0])
        return None
    if verdict.match_number and verdict.confidence >= MODEL_MIN_CONFIDENCE:
        chosen = candidates[verdict.match_number - 1]
        return EarlierMatch(chosen, "gemini", round(verdict.confidence, 3), verdict.reasoning)
    return None


# ---------------------------------------------------------------------------
# Permalinks and the pointer reply
# ---------------------------------------------------------------------------

def permalink_for(client: Any, workspace_url: str | None, channel_id: str, ts: str, *, thread_ts: str | None = None) -> str:
    """``chat.getPermalink``, with the documented URL shape as the fallback so
    a pointer is still a link when the call fails."""
    try:
        response = client.chat_getPermalink(channel=channel_id, message_ts=ts)
        link = response.get("permalink")
        if link:
            return str(link)
    except SlackApiError as exc:
        error = (getattr(exc, "response", None) or {}).get("error", str(exc))
        log.info("chat.getPermalink failed (%s); building the URL instead", error)
    except Exception as exc:  # noqa: BLE001 - network; a link must still come back
        log.info("chat.getPermalink failed (%s); building the URL instead", exc)
    base = (workspace_url or "https://slack.com/").rstrip("/")
    link = f"{base}/archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel_id}"
    return link


def _ensure_question_permalink(conn: psycopg.Connection, client: Any, workspace_url: str | None, question: dict[str, Any]) -> str:
    if question.get("permalink"):
        return str(question["permalink"])
    link = permalink_for(client, workspace_url, question["channel_id"], question["message_ts"])
    execute(conn, "update slack_qa_question set permalink = %s where question_id = %s", (link, question["question_id"]))
    question["permalink"] = link
    return link


def _ensure_answer_permalink(conn: psycopg.Connection, client: Any, workspace_url: str | None, answer: dict[str, Any], question: dict[str, Any]) -> str:
    if answer.get("permalink"):
        return str(answer["permalink"])
    link = permalink_for(client, workspace_url, answer["channel_id"], answer["message_ts"], thread_ts=question["message_ts"])
    execute(conn, "update slack_qa_answer set permalink = %s where answer_id = %s", (link, answer["answer_id"]))
    answer["permalink"] = link
    return link


def render_pointer(
    *,
    session: SessionRef | None,
    asked_at: datetime,
    thread_link: str,
    answer_link: str | None,
) -> str:
    """The reply. Hedged on purpose — overlap is not understanding — and clear
    that a person, not the bot, answers here."""
    when = f"during *{mrkdwn_escape(session.label)}*" if session else f"on {asked_at:%b} {asked_at.day}"
    if answer_link:
        where = f"The reply that was marked ✅ is here: {mrkdwn_link(answer_link, 'see the answer')}."
    else:
        where = f"See the earlier thread: {mrkdwn_link(thread_link, 'open thread')}."
    return (
        f"👋 This looks like a question that came up before, {when}. {where}\n"
        "_If it's a different question, carry on — a person will answer here._"
    )


# ---------------------------------------------------------------------------
# The summary for the teacher
# ---------------------------------------------------------------------------

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "question_numbers": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["label", "question_numbers"],
            },
        },
    },
    "required": ["summary", "topics"],
}

SUMMARY_PROMPT = """\
Below are anonymous questions students asked in a class Q&A channel around one \
lesson, each followed by the replies it received, if any. Write a short summary \
for the teacher.

{questions}

Rules:
- Summarise WHAT WAS ASKED and what the replies settled, grouped by topic, in \
3 to 6 sentences.
- Say which question numbers got no reply, or a reply that does not settle \
them, so the teacher can follow up.
- These are anonymous strings. Do not describe, characterise or count the \
students, and never say who asked or replied.
- Do not comment on how well anything is written.
- Group the questions into 1 to 5 topics. A label is at most six words. Every \
question number belongs to exactly one topic.

Reply with JSON only: summary (string) and topics (each with label and \
question_numbers, using the numbers shown above).\
"""


@dataclass(frozen=True)
class SummaryDraft:
    summary: str
    topics: tuple[tuple[str, tuple[int, ...]], ...] = ()


class Summarizer(Protocol):
    model_name: str
    prompt_version: str

    def summarize(self, items: Sequence[dict[str, Any]]) -> SummaryDraft:
        ...


def build_summary_prompt(items: Sequence[dict[str, Any]]) -> str:
    """The exact string sent: numbered question texts and reply texts, cleaned
    of markup so a mention is ``@someone``. Nothing else from the rows."""
    blocks: list[str] = []
    for number, item in enumerate(items, start=1):
        lines = [f"Q{number}: {clean_text(item['text'])}"]
        answers = item.get("answers") or []
        if answers:
            lines.extend(f"   reply: {clean_text(a['text'])}" for a in answers)
        else:
            lines.append("   (no replies)")
        blocks.append("\n".join(lines))
    return SUMMARY_PROMPT.format(questions="\n\n".join(blocks))


def parse_summary(payload: dict[str, Any], question_count: int) -> SummaryDraft:
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise AiUnavailable("Gemini returned no summary text")
    topics: list[tuple[str, tuple[int, ...]]] = []
    for raw in payload.get("topics") or []:
        label = str((raw or {}).get("label", "")).strip()
        numbers = tuple(
            sorted({int(n) for n in ((raw or {}).get("question_numbers") or []) if isinstance(n, int) and 1 <= n <= question_count})
        )
        if label:
            topics.append((label, numbers))
    return SummaryDraft(summary=summary, topics=tuple(topics))


class GeminiSummarizer(_GeminiJson):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings, what="the Q&A summary")

    def summarize(self, items: Sequence[dict[str, Any]]) -> SummaryDraft:
        payload = self.generate(build_summary_prompt(items), SUMMARY_SCHEMA)
        return parse_summary(payload, len(items))


def build_summarizer(settings: Settings | None = None) -> Summarizer | None:
    try:
        return GeminiSummarizer(settings or get_settings())
    except AiUnavailable as exc:
        log.info("qa summary: plain digest — %s", str(exc).splitlines()[0])
        return None


def render_summary(
    session: SessionRef,
    items: Sequence[dict[str, Any]],
    draft: SummaryDraft | None,
    *,
    model_name: str | None,
    note: str = "",
) -> str:
    """Slack mrkdwn. The model's paragraph and topics when there was a model;
    the deterministic parts — counts, what is still open, every question with
    its link — always. Names nobody."""
    answered = sum(1 for i in items if i["answered"])
    open_items = [(n, i) for n, i in enumerate(items, start=1) if not i["answered"]]
    lines = [
        f"*Q&A summary — {mrkdwn_escape(session.label)}*",
        f"{len(items)} question{'s' if len(items) != 1 else ''} · {answered} answered · {len(open_items)} still open",
        "",
    ]
    if draft is not None:
        lines.append(mrkdwn_escape(draft.summary))
        if draft.topics:
            lines.append("")
            lines.append("*Topics*")
            for label, numbers in draft.topics:
                refs = ", ".join(f"Q{n}" for n in numbers) if numbers else "—"
                lines.append(f"• {mrkdwn_escape(label)} — {refs}")
    else:
        lines.append(
            "_No model was used, so this is the plain digest: every question with its "
            "link, and which are still open._"
        )
    if note:
        lines.append(f"_{mrkdwn_escape(note)}_")
    if open_items:
        lines.append("")
        lines.append("*Still open*")
        for number, item in open_items:
            lines.append(f"• Q{number} — {mrkdwn_link(item.get('permalink'), snippet(item['text']))}")
    lines.append("")
    lines.append("*All questions*")
    for number, item in enumerate(items, start=1):
        mark = "✅" if item["answered"] else "◻️"
        replies = len(item.get("answers") or [])
        tail = f"  ({replies} repl{'y' if replies == 1 else 'ies'})" if replies else ""
        lines.append(f"• Q{number} {mark} {mrkdwn_link(item.get('permalink'), snippet(item['text']))}{tail}")
    lines.append("")
    if model_name:
        lines.append(f"_Summarised by {mrkdwn_escape(model_name)} from the question and reply texts only. Nobody is named here._")
    else:
        lines.append("_Built from the question and reply texts only. Nobody is named here._")
    return "\n".join(lines)


@dataclass
class SummaryResult:
    session: SessionRef | None
    text: str = ""
    summary_id: str | None = None
    questions_considered: int = 0
    answered_count: int = 0
    generated: bool = False
    superseded: int = 0
    message: str = ""
    model: str | None = None
    posted_ts: str | None = None


def current_summary(conn: psycopg.Connection, session_id: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        select summary_id, session_id, team_id, questions_considered, answered_count, summary_text,
               model, prompt_version, generated_at, posted_channel_id, posted_ts
          from slack_qa_summary
         where session_id = %s and superseded_at is null
         order by generated_at desc limit 1
        """,
        (session_id,),
    )


def generate_summary(
    conn: psycopg.Connection,
    client: Any,
    session_id: str,
    *,
    team_id: str,
    workspace_url: str | None = None,
    summarizer: Summarizer | None = None,
    use_model: bool = True,
    regenerate: bool = False,
) -> SummaryResult:
    """Summarise one session's Q&A. Returns the existing summary unless
    ``regenerate``; regenerating supersedes rather than overwrites, as the
    muddiest-point themes do. Degrades to the digest without a model."""
    session = session_ref(conn, session_id)
    if session is None:
        raise LookupError(f"No session with id {session_id}")

    existing = current_summary(conn, session_id)
    if existing and not regenerate:
        return SummaryResult(
            session=session, text=existing["summary_text"], summary_id=str(existing["summary_id"]),
            questions_considered=existing["questions_considered"], answered_count=existing["answered_count"],
            generated=False, model=existing["model"], posted_ts=existing["posted_ts"],
            message="showing the summary already generated; pass --regenerate to redo it",
        )

    items = questions_for_session(conn, session_id)
    if not items:
        return SummaryResult(
            session=session, generated=False,
            message=f"no questions in the Q&A channel(s) for {session.label}",
        )
    for item in items:
        _ensure_question_permalink(conn, client, workspace_url, item)

    draft: SummaryDraft | None = None
    note = ""
    model_name: str | None = None
    if use_model:
        summarizer = summarizer if summarizer is not None else build_summarizer()
        if summarizer is not None:
            try:
                draft = summarizer.summarize(items)
                model_name = summarizer.model_name
            except AiUnavailable as exc:
                note = f"The model was unavailable ({str(exc).splitlines()[0]}), so this is the plain digest."
                log.warning("qa summary degraded to digest: %s", str(exc).splitlines()[0])

    text = render_summary(session, items, draft, model_name=model_name, note=note)
    answered = sum(1 for i in items if i["answered"])
    superseded = execute(
        conn,
        "update slack_qa_summary set superseded_at = now() where session_id = %s and superseded_at is null",
        (session_id,),
    )
    row = fetch_one(
        conn,
        """
        insert into slack_qa_summary
            (session_id, team_id, questions_considered, answered_count, summary_text, model, prompt_version)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning summary_id
        """,
        (session_id, team_id, len(items), answered, text, model_name or "digest", PROMPT_VERSION),
    )
    assert row is not None
    log.info(
        "qa summary session=%s questions=%d answered=%d model=%s superseded=%d",
        session_id, len(items), answered, model_name or "digest", superseded,
    )
    return SummaryResult(
        session=session, text=text, summary_id=str(row["summary_id"]),
        questions_considered=len(items), answered_count=answered, generated=True,
        superseded=superseded, model=model_name or "digest",
        message=f"{len(items)} question(s), {answered} answered",
    )


def post_summary(
    conn: psycopg.Connection, client: Any, summary_id: str, channel_id: str, *, thread_ts: str | None = None
) -> str:
    """Post a stored summary and record where it went. Raises SlackApiError
    when Slack refuses — the caller decides whether that is fatal."""
    row = fetch_one(conn, "select summary_text from slack_qa_summary where summary_id = %s", (summary_id,))
    if row is None:
        raise LookupError(f"no summary {summary_id}")
    kwargs: dict[str, Any] = {"channel": channel_id, "text": row["summary_text"], "unfurl_links": False, "unfurl_media": False}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    response = client.chat_postMessage(**kwargs)
    ts = str(response.get("ts") or "")
    execute(
        conn,
        "update slack_qa_summary set posted_channel_id = %s, posted_ts = %s where summary_id = %s",
        (channel_id, ts, summary_id),
    )
    return ts


# ---------------------------------------------------------------------------
# Mentions: "@bot summary sept 2"
# ---------------------------------------------------------------------------

USAGE = (
    "I can summarise a session's Q&A for the teacher. Try:\n"
    "• `@{bot} summary` — the session in effect now\n"
    "• `@{bot} summary sept 2` — a session by date\n"
    "The summary is built from the question and reply texts only; nobody is named in it."
)

_SUMMARY_WORDS = re.compile(r"\b(summar\w*|digest|recap)\b", re.IGNORECASE)


def mention_intent(text: str, bot_user_id: str) -> tuple[str, str]:
    """``(intent, remainder)`` where intent is 'summary' or 'help'."""
    body = re.sub(rf"<@{re.escape(bot_user_id)}(?:\|[^>]*)?>", " ", text or "")
    body = clean_text(body)
    if _SUMMARY_WORDS.search(body):
        return "summary", body
    return "help", body


# ---------------------------------------------------------------------------
# The service the bot and the backfill drive
# ---------------------------------------------------------------------------

class QaService:
    """Everything above, wired to one workspace and one client.

    Never raises into the event path: a Q&A failure must not cost a
    participation row, so ``observe`` catches and logs. The pointer and the
    summary are best-effort features on top of the record, not part of it.
    """

    def __init__(
        self,
        settings: Settings,
        client: Any,
        *,
        team_id: str,
        bot_user_id: str,
        cohort_id: str | None,
        workspace_url: str | None = None,
        bot_handle: str | None = None,
        matcher: Matcher | None = None,
        summarizer: Summarizer | None = None,
        use_model: bool = True,
    ) -> None:
        self.settings = settings
        self.client = client
        self.team_id = team_id
        self.bot_user_id = bot_user_id
        self.bot_handle = bot_handle or "bot"
        self.cohort_id = cohort_id
        self.workspace_url = workspace_url
        self.configured: tuple[str, ...] = tuple(c.lstrip("#").strip() for c in settings.slack_qa_channels if c.strip())
        self._ids: set[str] = {c for c in self.configured if re.fullmatch(r"[CG][A-Z0-9]{6,}", c)}
        self._names: set[str] = {c.lower() for c in self.configured if c not in self._ids}
        self._known: dict[str, bool] = {}
        self.use_model = use_model
        self._matcher = matcher
        self._summarizer = summarizer

    # -- channels -------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.configured)

    @property
    def channel_ids(self) -> list[str]:
        return sorted(self._ids)

    def resolve_channels(self, conn: psycopg.Connection) -> set[str]:
        """Names → ids via conversations.list (which also refreshes
        ``slack_channel``). Called at start; unknown ids are looked up lazily."""
        from .backfill import sync_channels

        if not self.enabled:
            return set()
        found: set[str] = set()
        for ch in sync_channels(conn, self.client, self.team_id):
            name = (ch.get("name") or "").lower()
            is_qa = ch["id"] in self._ids or name in self._names
            self._known[ch["id"]] = is_qa
            if is_qa:
                self._ids.add(ch["id"])
                found.add(name)
        missing = sorted(self._names - found)
        if missing:
            log.warning("qa channels not found in this workspace: %s", ", ".join("#" + m for m in missing))
        return set(self._ids)

    def _channel_name(self, channel_id: str) -> str | None:
        try:
            return (self.client.conversations_info(channel=channel_id).get("channel") or {}).get("name")
        except Exception:  # noqa: BLE001
            return None

    def is_qa_channel(self, channel_id: str | None) -> bool:
        if not channel_id or not self.enabled:
            return False
        if channel_id in self._ids:
            return True
        if channel_id in self._known:
            return self._known[channel_id]
        name = (self._channel_name(channel_id) or "").lower()
        is_qa = bool(name) and name in self._names
        self._known[channel_id] = is_qa
        if is_qa:
            self._ids.add(channel_id)
        return is_qa

    # -- the model, lazily ---------------------------------------------------

    @property
    def matcher(self) -> Matcher | None:
        if self._matcher is None and self.use_model:
            self._matcher = build_matcher(self.settings)
            self.use_model = self._matcher is not None
        return self._matcher

    @property
    def summarizer(self) -> Summarizer | None:
        if self._summarizer is None and self.use_model:
            self._summarizer = build_summarizer(self.settings)
        return self._summarizer

    # -- the per-event path --------------------------------------------------

    def observe(self, conn: psycopg.Connection, event: dict[str, Any], team_id: str) -> str | None:
        """Record what an event means for the Q&A tables and, for a new
        question, look for an earlier answer. Returns a short label for the
        log and the tests, or None when the event was nothing to us."""
        if not self.is_qa_channel(event_channel(event)):
            return None
        obs = parse_qa_event(event, team_id)
        if obs is None:
            return None
        try:
            return self._apply(conn, obs, live=True)
        except Exception as exc:  # noqa: BLE001 - never into the event path
            log.exception("qa handling failed for %s in %s: %s", obs.kind, obs.channel_id, exc)
            return "error"

    def observe_history(self, conn: psycopg.Connection, message: dict[str, Any], channel_id: str) -> str | None:
        """The backfill path: a history or replies message, plus its aggregated
        reactions. No pointers are posted for history — the moment has passed."""
        event = history_message_to_event(message, channel_id)
        obs = parse_qa_event(event, self.team_id)
        if obs is None:
            return None
        label = self._apply(conn, obs, live=False)
        for block in message.get("reactions") or []:
            if block.get("name") in ACCEPT_REACTIONS and block.get("users"):
                apply_reaction(conn, QaObservation(
                    "reaction", self.team_id, channel_id, obs.message_ts, None, None, "", obs.at,
                    reaction=block["name"], removed=False,
                ))
        return label

    def _apply(self, conn: psycopg.Connection, obs: QaObservation, *, live: bool) -> str | None:
        if obs.kind == "question":
            question_id, inserted = record_question(conn, obs)
            if live and inserted:
                conn.commit()  # the question is safe before anything slow happens
                pointed = self.point_at_earlier(conn, question_id)
                return "question+pointer" if pointed else "question"
            return "question"
        if obs.kind == "answer":
            return "answer" if record_answer(conn, obs, fetch_parent=self.fetch_parent) else None
        if obs.kind == "edit":
            hit = apply_edit(conn, obs)
            return f"edit:{hit}" if hit else None
        if obs.kind == "delete":
            hit = apply_delete(conn, obs)
            return f"delete:{hit}" if hit else None
        if obs.kind == "reaction":
            hit = apply_reaction(conn, obs)
            return f"reaction:{hit}" if hit else None
        return None

    def fetch_parent(self, channel_id: str, thread_ts: str) -> QaObservation | None:
        """The parent of a thread, via conversations.replies (which returns the
        parent as its first message)."""
        try:
            response = self.client.conversations_replies(channel=channel_id, ts=thread_ts, limit=1)
        except SlackApiError as exc:
            error = (getattr(exc, "response", None) or {}).get("error", str(exc))
            log.info("conversations.replies failed for %s/%s: %s", channel_id, thread_ts, error)
            return None
        messages = response.get("messages") or []
        if not messages:
            return None
        return parse_qa_event(history_message_to_event(messages[0], channel_id), self.team_id)

    # -- the pointer ---------------------------------------------------------

    def point_at_earlier(self, conn: psycopg.Connection, question_id: str) -> bool:
        """Reply in a new question's thread with a link to the earlier answer,
        if there is one. Once per question, whatever happens after."""
        question = get_question(conn, question_id)
        if question is None or question.get("deleted_at_utc"):
            return False
        match = find_earlier_answered(conn, self.team_id, question, matcher=self.matcher)
        if match is None:
            return False
        earlier = match.question
        pointer = fetch_one(
            conn,
            """
            insert into slack_qa_pointer (question_id, earlier_question_id, method, similarity, reasoning)
            values (%s, %s, %s, %s, %s)
            on conflict (question_id) do nothing
            returning pointer_id
            """,
            (question_id, earlier["question_id"], match.method, match.similarity, match.reasoning[:500]),
        )
        if pointer is None:
            return False  # already pointed at, by an earlier delivery

        sessions = cohort_sessions(conn, self.cohort_id) if self.cohort_id else []
        session = session_in_effect(sessions, earlier["asked_at_utc"])
        thread_link = _ensure_question_permalink(conn, self.client, self.workspace_url, earlier)
        accepted = accepted_answer(conn, str(earlier["question_id"]))
        answer_link = _ensure_answer_permalink(conn, self.client, self.workspace_url, accepted, earlier) if accepted else None
        text = render_pointer(session=session, asked_at=earlier["asked_at_utc"], thread_link=thread_link, answer_link=answer_link)
        try:
            response = self.client.chat_postMessage(
                channel=question["channel_id"], thread_ts=question["message_ts"], text=text,
                unfurl_links=False, unfurl_media=False,
            )
            execute(conn, "update slack_qa_pointer set posted_ts = %s where pointer_id = %s", (str(response.get("ts") or ""), pointer["pointer_id"]))
            log.info("qa pointer posted method=%s similarity=%.2f channel=%s", match.method, match.similarity, question["channel_id"])
            return True
        except SlackApiError as exc:
            error = (getattr(exc, "response", None) or {}).get("error", str(exc))
            execute(conn, "update slack_qa_pointer set post_error = %s where pointer_id = %s", (error, pointer["pointer_id"]))
            log.warning("qa pointer not posted (%s) — grant chat:write and invite the bot to the channel", error)
            return False

    # -- mentions ------------------------------------------------------------

    def handle_mention(self, conn: psycopg.Connection, event: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """``@bot summary [date]`` → the summary, posted in the thread of the
        mention. Anything else → a usage line. Returns what happened."""
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        if not channel or not thread_ts:
            return {"intent": "ignored"}
        intent, remainder = mention_intent(event.get("text") or "", self.bot_user_id)
        bot_handle = self.bot_handle
        if intent != "summary":
            return self._reply(conn, channel, thread_ts, USAGE.format(bot=bot_handle), intent="help")
        if not self.cohort_id:
            return self._reply(conn, channel, thread_ts, "This workspace is not attached to a cohort, so I cannot find its sessions.", intent="summary")

        now = now or datetime.now(timezone.utc)
        sessions = cohort_sessions(conn, self.cohort_id)
        zone_name = sessions[-1].timezone if sessions else "UTC"
        try:
            local_now = now.astimezone(get_zone(zone_name))
        except Exception:  # noqa: BLE001 - a bad zone name on a session row
            local_now = now
        hint = parse_date_hint(remainder, year=local_now.year)
        session = session_on_date(sessions, hint) if hint else session_in_effect(sessions, now)
        if session is None:
            asked = f"on {hint:%b} {hint.day}" if hint else "in effect right now"
            return self._reply(conn, channel, thread_ts, f"I could not find a session {asked}. {USAGE.format(bot=bot_handle)}", intent="summary")

        result = generate_summary(
            conn, self.client, session.session_id, team_id=self.team_id, workspace_url=self.workspace_url,
            summarizer=self.summarizer, use_model=self.use_model, regenerate=True,
        )
        if not result.summary_id:
            return self._reply(conn, channel, thread_ts, f"Nothing to summarise yet: {result.message}.", intent="summary", session_id=session.session_id)
        try:
            ts = post_summary(conn, self.client, result.summary_id, channel, thread_ts=thread_ts)
        except SlackApiError as exc:
            error = (getattr(exc, "response", None) or {}).get("error", str(exc))
            log.warning("qa summary not posted (%s)", error)
            return {"intent": "summary", "session_id": session.session_id, "summary_id": result.summary_id, "posted": False, "error": error}
        return {"intent": "summary", "session_id": session.session_id, "summary_id": result.summary_id, "posted": True, "ts": ts}

    def _reply(self, conn: psycopg.Connection, channel: str, thread_ts: str, text: str, *, intent: str, **extra: Any) -> dict[str, Any]:
        try:
            response = self.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text, unfurl_links=False)
            return {"intent": intent, "posted": True, "ts": str(response.get("ts") or ""), **extra}
        except SlackApiError as exc:
            error = (getattr(exc, "response", None) or {}).get("error", str(exc))
            log.warning("qa reply not posted (%s)", error)
            return {"intent": intent, "posted": False, "error": error, **extra}


def channel_id_for(conn: psycopg.Connection, client: Any, team_id: str, name_or_id: str) -> tuple[str, str] | None:
    """``(channel_id, name)`` for a channel named by a person, refreshing
    ``slack_channel`` first so a channel created this week is found."""
    from .backfill import sync_channels

    wanted = name_or_id.lstrip("#").strip()
    for ch in sync_channels(conn, client, team_id):
        if ch["id"] == wanted or (ch.get("name") or "").lower() == wanted.lower():
            return ch["id"], ch.get("name") or ch["id"]
    return None


def build_qa_service(settings: Settings, client: Any, workspace: Any, **kwargs: Any) -> QaService | None:
    """A service for a connected workspace, or None when no Q&A channel is
    configured — in which case nothing here runs at all."""
    if not settings.slack_qa_channels:
        return None
    return QaService(
        settings, client,
        team_id=workspace.team_id, bot_user_id=workspace.bot_user_id, cohort_id=workspace.cohort_id,
        workspace_url=getattr(workspace, "url", None), bot_handle=getattr(workspace, "bot_name", None), **kwargs,
    )


__all__ = [
    "ACCEPT_REACTIONS",
    "LEXICAL_MATCH",
    "MODEL_MIN_CONFIDENCE",
    "PROMPT_VERSION",
    "SESSION_LEAD",
    "EarlierMatch",
    "GeminiMatcher",
    "GeminiSummarizer",
    "MatchVerdict",
    "Matcher",
    "QaObservation",
    "QaService",
    "SessionRef",
    "SummaryDraft",
    "SummaryResult",
    "Summarizer",
    "answers_for",
    "build_match_prompt",
    "build_qa_service",
    "build_summary_prompt",
    "clean_text",
    "cohort_sessions",
    "current_summary",
    "find_earlier_answered",
    "generate_summary",
    "lexical_similarity",
    "mention_intent",
    "mrkdwn_link",
    "normalize_question",
    "parse_date_hint",
    "parse_qa_event",
    "post_summary",
    "questions_for_session",
    "record_answer",
    "record_question",
    "render_pointer",
    "render_summary",
    "resolve_session",
    "session_in_effect",
    "session_on_date",
    "snippet",
    "token_set",
]
