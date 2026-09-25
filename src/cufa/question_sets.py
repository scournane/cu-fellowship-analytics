"""Part A question sets: stored, versioned, resolved per session, locked on publish.

The pure rules — what a valid set looks like, how it maps onto Forms items —
are in ``part_a_questions``. This module is the part with a database: which set
a session gets, who changed it and when, and when it stops being changeable.

**Two scopes, one rule for choosing.** Every cohort has a *default* set; any
session may carry an *override*, which is a full snapshot rather than a diff. A
session uses its current override if it has one and the cohort's current
default otherwise (``resolve_for_session``). Full snapshots because a diff
against a default that later changes is a question nobody can answer from the
database alone: "what did week 3 actually ask?" has to be one row, not a
replay.

**Append-only.** Saving inserts a new version and stamps the previous one
``superseded_at``; nothing is updated in place and nothing is deleted — the
database refuses both (``part_a_question_set_append_only``). Saving content
identical to the current version is a no-op rather than a new version, which is
what makes ``seed-default`` safe to re-run.

**Locked once published.** A session's Part A questions can be edited until its
form is published. After that fellows may be answering, the published form is
never written to, and an edit could only make the stored questions disagree
with what was asked — so it is refused (``QuestionsLocked``). The cohort default
stays editable; its changes reach only the sessions not yet published, and
provisioning reports "edited after provisioning" for the ones that were.

**Staleness is checked, not hoped for.** Every save may name the version the
editor started from (``base_id``). If somebody else saved in the meantime the
second save is refused (``StaleQuestionSet``) rather than silently discarding
the first person's work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from . import part_a_questions
from .db import execute, fetch_all, fetch_one
from .errors import (
    CufaError,
    InvalidQuestionSet,
    QuestionSetMissing,
    QuestionsLocked,
    StaleQuestionSet,
)
from .google.base import FormDefinition, FormsClient, GoogleApiError
from .logging_setup import get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILE = ROOT / "config" / "part_a_default_questions.json"

SCOPE_SESSION = "session"
SCOPE_DEFAULT = "default"
SOURCES = ("seed_file", "import_form", "console", "cli")

_COLUMNS = """
    question_set_id, cohort_id, session_id, version, schema_version, content,
    content_sha256, source, source_ref, based_on_id, created_by, created_at,
    superseded_at, superseded_by, retired_by
"""


@dataclass(frozen=True)
class QuestionSet:
    """One stored version of a cohort default or a session override."""

    question_set_id: str
    cohort_id: str
    session_id: str | None
    version: int
    content: dict[str, Any]
    source: str
    source_ref: str | None
    based_on_id: str | None
    created_by: str | None
    created_at: Any
    superseded_at: Any
    content_sha256: str = ""
    superseded_by: str | None = None
    retired_by: str | None = None

    @property
    def scope(self) -> str:
        return SCOPE_SESSION if self.session_id else SCOPE_DEFAULT

    @property
    def is_current(self) -> bool:
        return self.superseded_at is None

    @property
    def label(self) -> str:
        """"default v3" / "custom v2" — what the console and CLI print."""
        return f"{'custom' if self.session_id else 'default'} v{self.version}"

    @property
    def question_count(self) -> int:
        """Answerable questions only; a section break is not a question."""
        return len(part_a_questions.answerable(self.content))

    @property
    def questions(self) -> list[dict[str, Any]]:
        return list(self.content.get("questions") or [])


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _row_to_set(row: dict[str, Any]) -> QuestionSet:
    content = row["content"]
    if isinstance(content, str):  # pragma: no cover - psycopg decodes jsonb already
        content = json.loads(content)
    return QuestionSet(
        question_set_id=str(row["question_set_id"]),
        cohort_id=row["cohort_id"],
        session_id=_str(row["session_id"]),
        version=int(row["version"]),
        content=content,
        source=row["source"],
        source_ref=row.get("source_ref"),
        based_on_id=_str(row.get("based_on_id")),
        created_by=row.get("created_by"),
        created_at=row.get("created_at"),
        superseded_at=row.get("superseded_at"),
        content_sha256=row.get("content_sha256") or "",
        superseded_by=_str(row.get("superseded_by")),
        retired_by=row.get("retired_by"),
    )


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def get(conn: psycopg.Connection, question_set_id: str) -> QuestionSet | None:
    """One version by id, current or not."""
    row = fetch_one(
        conn,
        f"select {_COLUMNS} from part_a_question_set where question_set_id = %s",
        (question_set_id,),
    )
    return _row_to_set(row) if row else None


def current_default(
    conn: psycopg.Connection, cohort_id: str, *, for_update: bool = False
) -> QuestionSet | None:
    """The cohort's current default, or None if none was ever saved."""
    row = fetch_one(
        conn,
        f"""
        select {_COLUMNS} from part_a_question_set
         where cohort_id = %s and session_id is null and superseded_at is null
        {'for update' if for_update else ''}
        """,
        (cohort_id,),
    )
    return _row_to_set(row) if row else None


def current_override(
    conn: psycopg.Connection, session_id: str, *, for_update: bool = False
) -> QuestionSet | None:
    """The session's current override, or None when it uses the default."""
    row = fetch_one(
        conn,
        f"""
        select {_COLUMNS} from part_a_question_set
         where session_id = %s and superseded_at is null
        {'for update' if for_update else ''}
        """,
        (session_id,),
    )
    return _row_to_set(row) if row else None


def _session(conn: psycopg.Connection, session_id: str) -> dict[str, Any]:
    row = fetch_one(
        conn,
        'select session_id, cohort_id, title, week_index from "session" where session_id = %s',
        (session_id,),
    )
    if row is None:
        raise LookupError(f"No session with id {session_id}")
    return row


def missing_message(cohort_id: str, session_title: str | None = None) -> str:
    """The QuestionSetMissing text: what is missing and the two ways to fix it."""
    what = f"session “{session_title}”" if session_title else "this cohort's sessions"
    return (
        f"Cohort “{cohort_id}” has no Part A exit-ticket questions, so there is "
        f"nothing to put on {what}'s form.\n\n"
        "Load the default (the team's week-1 exit ticket) with:\n"
        f"    cufa questions seed-default --cohort {cohort_id}\n"
        "or set it in the console under Templates → Default exit ticket questions.\n\n"
        "Provisioning is blocked until a set exists: a form built from questions "
        "nobody chose is not a safe fallback."
    )


def resolve_for_session(conn: psycopg.Connection, session_id: str) -> tuple[str, QuestionSet]:
    """``(scope, set)`` a session would be provisioned with *now*.

    ``scope`` is ``"session"`` for an override and ``"default"`` otherwise.
    Raises ``QuestionSetMissing`` when neither exists.

    Note what this is not: for a session whose form is already published, the
    questions fellows saw are the ones recorded on ``session_form`` — see
    ``provisioned_for_session`` — and may differ from this if the default has
    since moved on.
    """
    session = _session(conn, session_id)
    override = current_override(conn, str(session["session_id"]))
    if override is not None:
        return SCOPE_SESSION, override
    default = current_default(conn, session["cohort_id"])
    if default is not None:
        return SCOPE_DEFAULT, default
    raise QuestionSetMissing(missing_message(session["cohort_id"], session["title"]))


def provisioned_for_session(conn: psycopg.Connection, session_id: str) -> QuestionSet | None:
    """The set this session's Part A form was actually built from, if any.

    None for a session not yet provisioned, and for a passphrase-era form
    provisioned before question sets existed.
    """
    row = fetch_one(
        conn,
        "select question_set_id from session_form where session_id = %s and part = 'a'",
        (session_id,),
    )
    if not row or not row["question_set_id"]:
        return None
    return get(conn, str(row["question_set_id"]))


def is_locked(conn: psycopg.Connection, session_id: str) -> tuple[bool, str | None]:
    """Whether the session's Part A questions can still change, and why not.

    Locked from the moment publishing was *attempted* (``published_at``), not
    only once it was verified: a form whose publish read-back failed may still
    be taking responses, and rewriting its items would re-key them.
    """
    row = fetch_one(
        conn,
        """
        select s.title, sf.published_at, sf.publish_verified_at
          from "session" s
          left join session_form sf
            on sf.session_id = s.session_id and sf.part = 'a'
         where s.session_id = %s
        """,
        (session_id,),
    )
    if row is None:
        raise LookupError(f"No session with id {session_id}")
    published = row["publish_verified_at"] or row["published_at"]
    if published is None:
        return False, None
    return True, (
        f"The Part A form for “{row['title']}” was published at {published}, so "
        "fellows may already be answering it and its questions are locked. What "
        "they were asked is kept exactly as it was. Changes to the cohort default "
        "still reach every session that is not published yet."
    )


def history(
    conn: psycopg.Connection, *, cohort_id: str | None = None, session_id: str | None = None
) -> list[QuestionSet]:
    """Every version, newest first: a session's overrides, or a cohort's defaults."""
    if session_id:
        rows = fetch_all(
            conn,
            f"select {_COLUMNS} from part_a_question_set where session_id = %s "
            "order by version desc",
            (session_id,),
        )
    elif cohort_id:
        rows = fetch_all(
            conn,
            f"select {_COLUMNS} from part_a_question_set "
            "where cohort_id = %s and session_id is null order by version desc",
            (cohort_id,),
        )
    else:
        raise ValueError("history needs cohort_id or session_id")
    return [_row_to_set(row) for row in rows]


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _clean_or_raise(content: Any) -> dict[str, Any]:
    result = part_a_questions.validate(content)
    if result.errors:
        raise InvalidQuestionSet(result.errors)
    return result.clean


def _next_version(conn: psycopg.Connection, *, cohort_id: str, session_id: str | None) -> int:
    if session_id:
        row = fetch_one(
            conn,
            "select coalesce(max(version), 0) + 1 as v from part_a_question_set "
            "where session_id = %s",
            (session_id,),
        )
    else:
        row = fetch_one(
            conn,
            "select coalesce(max(version), 0) + 1 as v from part_a_question_set "
            "where cohort_id = %s and session_id is null",
            (cohort_id,),
        )
    return int(row["v"]) if row else 1


def _insert_replacing(
    conn: psycopg.Connection,
    *,
    current: QuestionSet | None,
    cohort_id: str,
    session_id: str | None,
    clean: dict[str, Any],
    digest: str,
    source: str,
    source_ref: str | None,
    based_on_id: str | None,
    created_by: str | None,
) -> QuestionSet:
    """Supersede ``current`` (if any) and insert the new version, atomically.

    Three statements, in this order, because the partial unique index allows
    one current row per scope at any instant: stamp the old one superseded,
    insert the new one, then point the old one at it.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    try:
        with conn.transaction():
            if current is not None:
                execute(
                    conn,
                    "update part_a_question_set set superseded_at = now(), retired_by = %s "
                    "where question_set_id = %s",
                    (created_by, current.question_set_id),
                )
            row = fetch_one(
                conn,
                f"""
                insert into part_a_question_set
                    (cohort_id, session_id, version, schema_version, content,
                     content_sha256, source, source_ref, based_on_id, created_by)
                values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                returning {_COLUMNS}
                """,
                (
                    cohort_id,
                    session_id,
                    _next_version(conn, cohort_id=cohort_id, session_id=session_id),
                    part_a_questions.SCHEMA_VERSION,
                    json.dumps(clean),
                    digest,
                    source,
                    source_ref,
                    based_on_id,
                    created_by,
                ),
            )
            assert row is not None
            created = _row_to_set(row)
            if current is not None:
                execute(
                    conn,
                    "update part_a_question_set set superseded_by = %s "
                    "where question_set_id = %s",
                    (created.question_set_id, current.question_set_id),
                )
    except psycopg.errors.UniqueViolation as exc:
        # Somebody else saved this scope between our read and our insert.
        raise StaleQuestionSet(
            "Somebody else saved these questions at the same moment. Reload to see "
            "their version, then make your change again."
        ) from exc
    return created


def _require_cohort(conn: psycopg.Connection, cohort_id: str) -> None:
    if fetch_one(conn, "select 1 as ok from cohort where cohort_id = %s", (cohort_id,)) is None:
        raise CufaError(f"No cohort “{cohort_id}”. Load its roster or sessions first.")


def _stale(what: str, current: QuestionSet | None) -> StaleQuestionSet:
    now = f"it is now {current.label}" if current else "it has since been removed"
    return StaleQuestionSet(
        f"The {what} changed while you were editing ({now}). Reload to see the "
        "current version, then make your change again — saving would otherwise "
        "silently discard the other edit."
    )


def save_default(
    conn: psycopg.Connection,
    cohort_id: str,
    content: Any,
    *,
    created_by: str | None,
    source: str = "console",
    source_ref: str | None = None,
    base_id: str | None = None,
) -> QuestionSet:
    """Save a new version of the cohort default. Identical content is a no-op.

    ``base_id`` is the version the editor started from; when given and no longer
    current, ``StaleQuestionSet``. The CLI and the seed pass None, because
    "replace whatever is there with this file" is exactly what they mean.
    """
    clean = _clean_or_raise(content)
    digest = part_a_questions.content_sha256(clean)
    _require_cohort(conn, cohort_id)

    with conn.transaction():
        current = current_default(conn, cohort_id, for_update=True)
        if base_id is not None and (current is None or current.question_set_id != str(base_id)):
            raise _stale("cohort default", current)
        if current is not None and current.content_sha256 == digest:
            return current
        created = _insert_replacing(
            conn,
            current=current,
            cohort_id=cohort_id,
            session_id=None,
            clean=clean,
            digest=digest,
            source=source,
            source_ref=source_ref,
            based_on_id=None,
            created_by=created_by,
        )
    log.info("part A default saved cohort=%s %s source=%s", cohort_id, created.label, source)
    return created


def save_override(
    conn: psycopg.Connection,
    session_id: str,
    content: Any,
    *,
    created_by: str | None,
    source: str = "console",
    base_id: str | None = None,
) -> QuestionSet:
    """Save a new version of one session's override.

    ``base_id`` may name the current override (editing a customised session) or
    the current default (customising for the first time); anything else is
    stale. The new version records, in ``based_on_id``, the default version the
    customisation descends from.
    """
    session = _session(conn, session_id)
    session_id = str(session["session_id"])
    locked, reason = is_locked(conn, session_id)
    if locked:
        raise QuestionsLocked(reason or "This session's Part A questions are locked.")
    clean = _clean_or_raise(content)
    digest = part_a_questions.content_sha256(clean)

    with conn.transaction():
        current = current_override(conn, session_id, for_update=True)
        default = current_default(conn, session["cohort_id"])
        if base_id is not None:
            allowed = {current.question_set_id} if current else (
                {default.question_set_id} if default else set()
            )
            if str(base_id) not in allowed:
                raise _stale("session's questions", current or default)
        if current is not None and current.content_sha256 == digest:
            return current
        # A further edit of a customised session still descends from the default
        # it was first customised from; a first customisation descends from
        # today's default.
        based_on = current.based_on_id if current else (default.question_set_id if default else None)
        created = _insert_replacing(
            conn,
            current=current,
            cohort_id=session["cohort_id"],
            session_id=session_id,
            clean=clean,
            digest=digest,
            source=source,
            source_ref=None,
            based_on_id=based_on,
            created_by=created_by,
        )
    log.info("part A override saved session=%s %s", session_id, created.label)
    return created


def revert_override(conn: psycopg.Connection, session_id: str, *, by: str | None) -> None:
    """Send a session back to the cohort default. No-op if it has no override.

    The override is stamped superseded with no replacement, so it stays in the
    history — "this session was customised, then reverted, by whom" is kept.
    """
    session = _session(conn, session_id)
    session_id = str(session["session_id"])
    locked, reason = is_locked(conn, session_id)
    if locked:
        raise QuestionsLocked(reason or "This session's Part A questions are locked.")
    with conn.transaction():
        current = current_override(conn, session_id, for_update=True)
        if current is None:
            return
        execute(
            conn,
            "update part_a_question_set set superseded_at = now(), retired_by = %s "
            "where question_set_id = %s",
            (by, current.question_set_id),
        )
    log.info("part A override reverted session=%s (%s)", session_id, current.label)


def seed_default_from_file(
    conn: psycopg.Connection,
    cohort_id: str,
    path: str | Path | None = None,
    *,
    created_by: str | None,
) -> QuestionSet:
    """Load the cohort default from a JSON file. Re-running is a no-op.

    The default path is ``config/part_a_default_questions.json``: the team's own
    week-1 exit ticket. If the default was since edited in the console, seeding
    again replaces it with the file (as a new version — the edit stays in the
    history), because that is what an explicit seed asks for.
    """
    file_path = Path(path) if path else DEFAULT_FILE
    clean = part_a_questions.load_file(file_path)
    try:
        ref = str(file_path.resolve().relative_to(ROOT))
    except ValueError:
        ref = str(file_path)
    return save_default(
        conn, cohort_id, clean, created_by=created_by, source="seed_file", source_ref=ref
    )


def import_from_form(
    client: FormsClient,
    conn: psycopg.Connection,
    cohort_id: str,
    form_id: str,
    *,
    created_by: str | None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Read an existing Google Form and make it the cohort default.

    Returns ``(content, warnings)``; with ``dry_run`` nothing is saved. Items the
    editor cannot represent are left out and named in the warnings, never
    dropped silently.

    Which forms can be read is decided by the ``drive.file`` scope (ADR-005):
    forms this app created — including any session form staff have since
    edited in the Forms UI — but not a form somebody made by hand, even one
    they own. That case gets an explanation rather than a bare 404.
    """
    try:
        form_id = part_a_questions.form_id_from_url(form_id)
    except ValueError as exc:
        raise CufaError(str(exc)) from None
    try:
        definition: FormDefinition = client.get_form(form_id)
    except GoogleApiError as exc:
        if exc.status in (403, 404):
            raise CufaError(
                f"Google would not show form {form_id} to this app ({exc.status}).\n\n"
                "The connected account grants the drive.file scope, which covers "
                "forms this app created — a session form edited in the Forms UI "
                "imports fine — but not a form made by hand in Google Forms, even "
                "one you own (docs/decisions.md, ADR-005). For a hand-made form, "
                "write its questions into a JSON file shaped like "
                "config/part_a_default_questions.json and run `cufa questions set "
                f"--cohort {cohort_id} --file PATH`, or type them into the console."
            ) from exc
        raise

    raw = definition.raw or {}
    info = raw.get("info") or {}
    warnings: list[str] = []
    questions: list[dict[str, Any]] = []
    for position, item in enumerate(raw.get("items") or []):
        warnings.extend(part_a_questions.import_notes(item, position))
        question = part_a_questions.from_forms_item(item)
        if question is not None:
            questions.append(question)

    content = {
        "schema_version": part_a_questions.SCHEMA_VERSION,
        "title": info.get("title") or definition.title,
        "description": info.get("description") or "",
        "questions": questions,
    }
    result = part_a_questions.validate(content)
    warnings.extend(result.warnings)
    if result.errors:
        raise InvalidQuestionSet(result.errors)

    text = json.dumps(result.clean)
    if not any(token in text for token in part_a_questions.PLACEHOLDERS):
        warnings.append(
            "The imported wording is literal. If it names a lesson number or a "
            "session title, replace those with {lesson} and {session_title} so "
            "each week's form fills them in."
        )

    if not dry_run:
        save_default(
            conn, cohort_id, result.clean, created_by=created_by,
            source="import_form", source_ref=form_id,
        )
    return result.clean, warnings


# ---------------------------------------------------------------------------
# the per-form map
# ---------------------------------------------------------------------------


def record_form_map(
    conn: psycopg.Connection,
    form_id: str,
    definition: FormDefinition,
    rendered: dict[str, Any],
    question_set_id: str | None,
) -> list[dict[str, Any]]:
    """Record questionId -> question for one Part A form, replacing any old map.

    ``definition`` is what the API says the form contains now; ``rendered`` is
    what this app told it to contain. They are joined on item index, which this
    app controls — never on title. The caller has already asserted the shape
    (see ``provisioning._assert_part_a_shape``); a question with no read-back
    item here is skipped with a log line rather than raised, because this is
    also the repair path for an already-published form, which must never be
    refused over bookkeeping.
    """
    by_index = definition.by_index()
    rows: list[dict[str, Any]] = []
    for index, question in enumerate(rendered.get("questions") or []):
        if question.get("type") not in part_a_questions.ANSWERABLE_TYPES:
            continue
        item = by_index.get(index)
        if item is None or not item.question_id:
            log.warning(
                "form %s has no question at index %d (%s); left out of the map",
                form_id, index, question.get("key"),
            )
            continue
        if item.kind != part_a_questions.forms_kind(question["type"]):
            # Somebody changed the form in the Forms UI. Mapping a scale answer
            # onto a text question's key would be the plausible-looking wrong
            # data this table exists to prevent; an unmapped answer is still
            # stored and still visible, just without a key.
            log.warning(
                "form %s item %d is a %s question, expected %s for %s; left out of the map",
                form_id, index, item.kind, question["type"], question.get("key"),
            )
            continue
        rows.append(
            {
                "question_id": item.question_id,
                "item_id": item.item_id or None,
                "question_key": question["key"],
                "item_index": index,
                "kind": question["type"],
                # What the API reports, not what was sent: if someone edited the
                # wording in the Forms UI, this has to be what fellows saw.
                "question_text": item.title or question.get("title", ""),
                "spec": question,
            }
        )

    execute(conn, "delete from part_a_form_question where form_id = %s", (form_id,))
    for row in rows:
        execute(
            conn,
            """
            insert into part_a_form_question
                (form_id, question_id, item_id, question_key, item_index, kind,
                 question_text, spec, question_set_id)
            values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                form_id,
                row["question_id"],
                row["item_id"],
                row["question_key"],
                row["item_index"],
                row["kind"],
                row["question_text"],
                json.dumps(row["spec"]),
                question_set_id,
            ),
        )
    log.info("part A question map recorded form=%s questions=%d", form_id, len(rows))
    return rows


def form_map(conn: psycopg.Connection, form_id: str) -> list[dict[str, Any]]:
    """The recorded map for one form, in form order."""
    return fetch_all(
        conn,
        """
        select question_id, item_id, question_key, item_index, kind, question_text,
               spec, question_set_id, recorded_at
          from part_a_form_question
         where form_id = %s
         order by item_index
        """,
        (form_id,),
    )


__all__ = [
    "DEFAULT_FILE",
    "QuestionSet",
    "SCOPE_DEFAULT",
    "SCOPE_SESSION",
    "current_default",
    "current_override",
    "form_map",
    "get",
    "history",
    "import_from_form",
    "is_locked",
    "missing_message",
    "provisioned_for_session",
    "record_form_map",
    "resolve_for_session",
    "revert_override",
    "save_default",
    "save_override",
    "seed_default_from_file",
]
