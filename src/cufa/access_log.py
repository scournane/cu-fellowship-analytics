"""Who opened a fellow's record, and when.

docs/vision.md §12 asks for "an access audit log: who looked at whose record,
when". This is it, and the interesting part is what it does about the three
sign-in doors in RUNBOOK §8.

Google sign-in names a person. The dev bypass names whatever address somebody
typed. The **shared site password** names nobody at all: every session it issues
carries ``shared-password@console.local``, which is not a mailbox and is not
meant to look like one. A log that wrote that string into a "who" column would
be reporting a person who does not exist, and the deployed console is currently
on exactly that door — so the honest answer has to be first-class rather than a
footnote. It is:

* ``actor_kind = 'shared_password'`` with ``actor_email`` NULL, which a CHECK
  constraint makes the only writable combination for that door.
* ``render_text`` counts those reads and says out loud how many of the reads in
  front of you cannot be pinned on anybody. That number is the cost of door 2,
  and it is meant to be annoying enough to be worth removing.

**The fellow's address is not in this table.** Not in a column, not in the
route, not in the log line. That is the same rule ``cufa slack report``, every
DM and every digest follow, and the reason is the same: an audit trail is the
one thing you keep forever, so it is the last place a roster dump belongs. The
fellow is identified by ``fellow_id``; anyone who needs the address has the
roster.

Nothing here updates or deletes a row. A read that happened cannot be
un-happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from .db import fetch_all, fetch_one
from .logging_setup import get_logger, mask_email

log = get_logger(__name__)

ACTOR_KINDS = ("person", "dev_bypass", "shared_password", "cli")

#: What the shared-password door is called on screen and in an export. Deliberately
#: a sentence rather than a name: the reader should not be able to skim it as one.
SHARED_PASSWORD_LABEL = "the shared site password — no named person"

#: The cost of door 2, in one line, for wherever the log is shown.
SHARED_PASSWORD_NOTE = (
    "Reads through the shared site password cannot be attributed to anybody: "
    "everyone who has it signs in as the same nobody. Register a Google OAuth "
    "client for this origin and unset CUFA_CONSOLE_PASSWORD (RUNBOOK section 8) "
    "and this number goes to zero."
)


@dataclass(frozen=True)
class Actor:
    """Who did the reading, and how much the log may claim about it."""

    kind: str
    email: str | None

    def __post_init__(self) -> None:
        if self.kind not in ACTOR_KINDS:
            raise ValueError(f"unknown actor kind {self.kind!r}")

    @property
    def is_named(self) -> bool:
        """Whether this read can be pinned on one human being."""
        return self.kind != "shared_password"

    @property
    def label(self) -> str:
        """Staff-facing. Names the person when there is one, says so when not."""
        if self.kind == "shared_password":
            return SHARED_PASSWORD_LABEL
        if self.kind == "dev_bypass":
            return f"{self.email} (developer bypass)"
        if self.kind == "cli":
            return f"{self.email} (command line)"
        return str(self.email)

    @property
    def masked_label(self) -> str:
        """The same, safe to put in a log line at INFO."""
        if self.kind == "shared_password":
            return SHARED_PASSWORD_LABEL
        return f"{mask_email(self.email or '')} ({self.kind})"


def actor_from_console_user(user: Any) -> Actor:
    """Translate a ``console.auth.ConsoleUser`` into an actor.

    The mapping is the whole honesty question in four lines, so it lives in one
    place rather than at each call site: ``via='password'`` becomes
    ``shared_password`` with no address, whatever address the cookie happens to
    be carrying.
    """
    via = getattr(user, "via", "google")
    if via == "password":
        return Actor(kind="shared_password", email=None)
    if via == "dev":
        return Actor(kind="dev_bypass", email=(getattr(user, "email", "") or "").strip().lower())
    return Actor(kind="person", email=(getattr(user, "email", "") or "").strip().lower())


def cli_actor(who: str) -> Actor:
    """An actor for a command run against the database directly.

    ``cufa fellow export`` is the largest single read of one person's record
    there is, so it is logged like any other. The operator names themselves with
    ``--by``; there is no OS identity worth trusting here.
    """
    address = (who or "").strip().lower()
    if not address:
        raise ValueError("a command-line read has to say who ran it")
    return Actor(kind="cli", email=address)


def record_read(
    conn: psycopg.Connection,
    fellow_id: str,
    *,
    actor: Actor,
    route: str,
    at: datetime | None = None,
) -> str | None:
    """Append one read. Returns the row id, or None if there is no such fellow.

    Silent on an unknown fellow rather than raising: every caller is a read path
    that is already about to answer 404, and a guard that turns a mistyped URL
    into a 500 would make the audit log a way to break the console.
    """
    if fetch_one(conn, "select 1 from fellow where fellow_id = %s", (fellow_id,)) is None:
        return None
    row = fetch_one(
        conn,
        """
        insert into fellow_access_log (fellow_id, actor_kind, actor_email, route, at)
        values (%s, %s, %s, %s, coalesce(%s, now()))
        returning access_id
        """,
        (fellow_id, actor.kind, actor.email, route, at),
    )
    assert row is not None
    log.info(
        "fellow record read fellow=%s route=%s by=%s", fellow_id, route, actor.masked_label
    )
    return str(row["access_id"])


def reads_for_fellow(
    conn: psycopg.Connection, fellow_id: str, *, limit: int = 500
) -> list[dict[str, Any]]:
    """Every recorded read of one fellow's record, newest first."""
    return fetch_all(
        conn,
        """
        select access_id, fellow_id, actor_kind, actor_email, route, at
          from fellow_access_log
         where fellow_id = %s
         order by at desc, access_id
         limit %s
        """,
        (fellow_id, limit),
    )


def recent_reads(
    conn: psycopg.Connection, *, fellow_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """The log, newest first, optionally narrowed to one fellow."""
    return fetch_all(
        conn,
        """
        select l.access_id, l.fellow_id, f.full_name, l.actor_kind, l.actor_email,
               l.route, l.at
          from fellow_access_log l
          join fellow f on f.fellow_id = l.fellow_id
         where (%s::text is null or l.fellow_id = %s::text)
         order by l.at desc, l.access_id
         limit %s
        """,
        (fellow_id, fellow_id, limit),
    )


def unattributable(rows: list[dict[str, Any]]) -> int:
    """How many of these reads name nobody. The number RUNBOOK §8 costs you."""
    return sum(1 for r in rows if r["actor_kind"] == "shared_password")


def actor_label(row: dict[str, Any]) -> str:
    """Staff-facing label for one row."""
    return Actor(kind=str(row["actor_kind"]), email=row.get("actor_email")).label


def fellow_facing_label(row: dict[str, Any]) -> str:
    """The same row as the person whose record it is gets to see it.

    No address, ever — not even a staff one. A subject access request is
    entitled to know that their record was read and when; who read it is another
    person's data, and in a programme with three staff, naming them to a
    teenager is a step CU takes, not one this tool takes on CU's behalf. The
    export says as much and says how to ask.
    """
    kind = str(row["actor_kind"])
    if kind == "shared_password":
        return SHARED_PASSWORD_LABEL
    if kind == "cli":
        return "a member of staff, from the command line"
    if kind == "dev_bypass":
        return "a member of staff, through the developer sign-in"
    return "a member of staff, signed in with their CU Google account"


def render_text(rows: list[dict[str, Any]], *, heading: str = "Access log") -> str:
    """The log as staff read it on the command line."""
    lines = [heading, ""]
    if not rows:
        lines.append("  Nothing recorded yet.")
        return "\n".join(lines)
    for row in rows:
        who = actor_label(row)
        name = row.get("full_name") or row["fellow_id"]
        lines.append(f"  {row['at']:%Y-%m-%d %H:%M} UTC  {name:<24} {row['route']:<32} {who}")
    blind = unattributable(rows)
    lines.append("")
    lines.append(f"  {len(rows)} read(s) shown; {blind} name nobody.")
    if blind:
        lines.append("")
        for line in SHARED_PASSWORD_NOTE.split(". "):
            lines.append(f"  {line.rstrip('.')}.")
    return "\n".join(lines)


__all__ = [
    "ACTOR_KINDS",
    "SHARED_PASSWORD_LABEL",
    "SHARED_PASSWORD_NOTE",
    "Actor",
    "actor_from_console_user",
    "actor_label",
    "cli_actor",
    "fellow_facing_label",
    "reads_for_fellow",
    "recent_reads",
    "record_read",
    "render_text",
    "unattributable",
]
