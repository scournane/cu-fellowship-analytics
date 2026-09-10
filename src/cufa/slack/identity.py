"""Who a Slack account is, and what to do when nobody knows.

Three ways an account resolves to a roster record, in order:

1. **A manual link** a staffer made (``slack_user.linked_fellow_id``).
2. **The primary address** on the roster.
3. **An alias** — a second address staff attached to the record.

Resolution happens in ``v_slack_user_resolved`` at read time, so linking an
alias today re-attributes every message from last month. Nothing here writes a
``fellow_id`` onto an observation.

Aliases exist because fellows join Slack from a school address and fill in
forms from a personal one, and CU does not reliably hold both. ``merge`` is the
manual path the Director asked for: attach the address one system saw to the
record the other system knows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from ..db import execute, fetch_all, fetch_one
from ..errors import CufaError
from ..logging_setup import get_logger, mask_email
from ..text import normalize_email

log = get_logger(__name__)


class UnknownFellow(CufaError):
    """No roster row matches the id, name or address given."""


class AmbiguousFellow(CufaError):
    """A name matched more than one roster row. Use the id."""


@dataclass(frozen=True)
class Resolved:
    slack_user_id: str
    fellow_id: str | None
    match_method: str | None
    full_name: str | None
    cohort_id: str | None
    is_admin: bool
    email: str | None


def resolve_slack_user(conn: psycopg.Connection, slack_user_id: str) -> Resolved | None:
    row = fetch_one(
        conn,
        """
        select slack_user_id, fellow_id, match_method, full_name, cohort_id, is_admin, email
          from v_slack_user_resolved where slack_user_id = %s
        """,
        (slack_user_id,),
    )
    if row is None:
        return None
    return Resolved(
        slack_user_id=row["slack_user_id"],
        fellow_id=row["fellow_id"],
        match_method=row["match_method"],
        full_name=row["full_name"],
        cohort_id=row["cohort_id"],
        is_admin=bool(row["is_admin"]),
        email=row["email"],
    )


def fellow_for_slack_user(conn: psycopg.Connection, slack_user_id: str) -> str | None:
    resolved = resolve_slack_user(conn, slack_user_id)
    return resolved.fellow_id if resolved else None


def find_fellow(
    conn: psycopg.Connection, query: str, *, cohort_id: str | None = None
) -> dict[str, Any]:
    """A roster row by id, address, or (case-insensitive, whole or partial) name.

    Raises rather than guessing when a name is ambiguous — ``/fellow sam``
    matching two people must not silently pick one.
    """
    needle = (query or "").strip()
    if not needle:
        raise UnknownFellow("Give a fellow id, an email address, or a name.")
    scope = "and (%s::text is null or f.cohort_id = %s::text)"
    by_id = fetch_one(
        conn,
        f"select f.* from fellow f where f.fellow_id = %s {scope}",
        (needle, cohort_id, cohort_id),
    )
    if by_id:
        return by_id
    if "@" in needle:
        by_email = fetch_one(
            conn,
            f"""
            select f.* from fellow f
              join v_fellow_email e on e.fellow_id = f.fellow_id
             where e.email = %s {scope}
             limit 1
            """,
            (normalize_email(needle), cohort_id, cohort_id),
        )
        if by_email:
            return by_email
        raise UnknownFellow(f"No fellow has the address {needle}.")
    exact = fetch_all(
        conn,
        f"select f.* from fellow f where lower(f.full_name) = lower(%s) {scope}",
        (needle, cohort_id, cohort_id),
    )
    if len(exact) == 1:
        return exact[0]
    partial = fetch_all(
        conn,
        f"select f.* from fellow f where f.full_name ilike %s {scope} order by f.full_name",
        (f"%{needle}%", cohort_id, cohort_id),
    )
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(f"{r['full_name']} ({r['fellow_id']})" for r in partial[:8])
        raise AmbiguousFellow(f"{needle!r} matches more than one fellow: {names}. Use the id.")
    raise UnknownFellow(f"No fellow matches {needle!r}.")


# ---------------------------------------------------------------------------
# aliases and manual links
# ---------------------------------------------------------------------------


def list_aliases(conn: psycopg.Connection, fellow_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        "select alias_id, email, kind, added_by, note, created_at from fellow_alias "
        "where fellow_id = %s order by created_at",
        (fellow_id,),
    )


def add_alias(
    conn: psycopg.Connection,
    fellow_id: str,
    email: str,
    *,
    by: str,
    kind: str = "other",
    note: str | None = None,
) -> dict[str, Any]:
    """Attach an address to a roster record. Idempotent for the same pair."""
    address = normalize_email(email)
    if not address or "@" not in address:
        raise CufaError(f"{email!r} is not an email address.")
    if kind not in ("school", "personal", "other"):
        raise CufaError("alias kind must be school, personal or other")
    if fetch_one(conn, "select 1 from fellow where fellow_id = %s", (fellow_id,)) is None:
        raise UnknownFellow(f"No fellow with id {fellow_id}.")
    own = fetch_one(
        conn, "select 1 from fellow where fellow_id = %s and lower(primary_email) = %s",
        (fellow_id, address),
    )
    if own:
        raise CufaError(f"{address} is already this fellow's primary address.")
    existing = fetch_one(conn, "select fellow_id from fellow_alias where lower(email) = %s", (address,))
    if existing and existing["fellow_id"] != fellow_id:
        raise CufaError(
            f"{address} is already an alias of fellow {existing['fellow_id']}; remove it there first."
        )
    try:
        row = fetch_one(
            conn,
            """
            insert into fellow_alias (fellow_id, email, kind, added_by, note)
            values (%s, %s, %s, %s, %s)
            on conflict (lower(email)) do update set kind = excluded.kind
            returning alias_id, fellow_id, email, kind
            """,
            (fellow_id, address, kind, (by or "").strip().lower() or None, note),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise CufaError(str(exc).splitlines()[0]) from exc
    log.info("alias added fellow=%s email=%s", fellow_id, mask_email(address))
    assert row is not None
    return row


def remove_alias(conn: psycopg.Connection, email: str) -> bool:
    return execute(conn, "delete from fellow_alias where lower(email) = %s", (normalize_email(email),)) > 0


def merge(
    conn: psycopg.Connection, keep_fellow_id: str, other_email: str, *, by: str, kind: str = "other"
) -> dict[str, Any]:
    """The manual merge: "this address in the forms is that person on Slack".

    An alias, not a roster rewrite. The roster row stays exactly as CU issued
    it; the other address becomes a second key to it, and every historical row
    stored under that address re-attributes on the next read.
    """
    return add_alias(conn, keep_fellow_id, other_email, by=by, kind=kind, note="manual merge")


def link_slack_user(
    conn: psycopg.Connection, slack_user_id: str, fellow_id: str, *, by: str, add_email_as_alias: bool = True
) -> None:
    """Point a Slack account at a roster record by hand.

    Also records the account's address as an alias when it has one, so the
    form side of the system benefits from the same decision.
    """
    if fetch_one(conn, "select 1 from fellow where fellow_id = %s", (fellow_id,)) is None:
        raise UnknownFellow(f"No fellow with id {fellow_id}.")
    user = fetch_one(conn, "select email from slack_user where slack_user_id = %s", (slack_user_id,))
    if user is None:
        raise CufaError(f"Slack user {slack_user_id} has not been synced yet; run `cufa slack sync`.")
    execute(
        conn,
        """
        update slack_user
           set linked_fellow_id = %s, linked_by = %s, linked_at = now()
         where slack_user_id = %s
        """,
        (fellow_id, (by or "").strip().lower() or None, slack_user_id),
    )
    if add_email_as_alias and user.get("email"):
        address = normalize_email(user["email"])
        already = fetch_one(
            conn, "select 1 from v_fellow_email where email = %s and fellow_id = %s", (address, fellow_id)
        )
        if not already:
            try:
                add_alias(conn, fellow_id, address, by=by, note=f"linked from Slack {slack_user_id}")
            except CufaError as exc:
                log.warning("could not record Slack address as alias: %s", exc)
    resolve_alert(conn, slack_user_id, by=by, resolution="linked")
    log.info("slack user %s linked to fellow %s", slack_user_id, fellow_id)


# ---------------------------------------------------------------------------
# roster alerts
# ---------------------------------------------------------------------------


def open_alerts(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select a.alert_id, a.slack_user_id, a.email, a.created_at, a.posted_at,
               u.display_name, u.real_name, u.joined_at_utc
          from roster_alert a
          join slack_user u on u.slack_user_id = a.slack_user_id
         where a.resolved_at is null
         order by a.created_at
        """,
    )


def raise_alert(conn: psycopg.Connection, slack_user_id: str, email: str | None) -> bool:
    """Record that an unrostered account joined. True when new."""
    row = fetch_one(
        conn,
        """
        insert into roster_alert (slack_user_id, email)
        values (%s, %s)
        on conflict (slack_user_id, kind) do nothing
        returning alert_id
        """,
        (slack_user_id, email),
    )
    return row is not None


def resolve_alert(conn: psycopg.Connection, slack_user_id: str, *, by: str, resolution: str) -> bool:
    if resolution not in ("linked", "staff", "ignored"):
        raise CufaError("resolution must be linked, staff or ignored")
    return (
        execute(
            conn,
            """
            update roster_alert
               set resolved_at = now(), resolved_by = %s, resolution = %s
             where slack_user_id = %s and resolved_at is null
            """,
            ((by or "").strip().lower() or "unknown", resolution, slack_user_id),
        )
        > 0
    )


__all__ = [
    "AmbiguousFellow",
    "Resolved",
    "UnknownFellow",
    "add_alias",
    "fellow_for_slack_user",
    "find_fellow",
    "link_slack_user",
    "list_aliases",
    "merge",
    "open_alerts",
    "raise_alert",
    "remove_alias",
    "resolve_alert",
    "resolve_slack_user",
]
