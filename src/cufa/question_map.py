"""questionId → semantic slot, recorded per form and never guessed.

**The Part B trap.** Part A had one question, so "the answer" was unambiguous.
Part B has five, and ``forms.responses.list`` returns answers keyed by
``questionId`` — not by title, not by position. Whether Drive's ``files.copy``
preserves question ids across copies **could not be verified**, so this module
assumes neither: after every Part B form is provisioned, the form is read back
with ``forms.get`` and the mapping recorded.

Two rules follow, and both are the difference between correct data and
plausible-looking wrong data:

* **Slots are matched by item index, never by title.** The application controls
  the index at creation time. The rotating slot's title changes every week by
  design, and a teacher can retitle any of the others in the Forms UI without
  telling anyone — so matching on text would silently re-file answers the first
  time someone fixed a typo.
* **An incomplete map refuses to ingest.** A missing entry does not mean "skip
  that field"; it means the form is not the shape this code thinks it is.
  Guessing would attribute a confidence score to a takeaway column and corrupt
  every downstream number in a way that reads as ordinary data.

The exact question text is snapshot here at provisioning time and never
reconstructed later from config. "What was actually asked in week 3" has to be
answerable from the database alone, because the config may well have changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import psycopg

from .db import execute, fetch_all
from .errors import CufaError
from .form_content_b import ItemSpec, SLOT_ROTATING
from .google.base import FormDefinition
from .logging_setup import get_logger

log = get_logger(__name__)


class QuestionMapIncomplete(CufaError):
    """A form's question map is missing or does not cover every slot.

    Fatal at ingest. See the module docstring — this is the failure that would
    otherwise be invisible.
    """


@dataclass(frozen=True)
class MappedQuestion:
    """One recorded mapping row."""

    question_id: str
    slot: str
    rotating_kind: str | None
    question_text: str
    item_index: int


def record_map(
    conn: psycopg.Connection,
    form_id: str,
    definition: FormDefinition,
    specs: Iterable[ItemSpec],
    *,
    rotating_kind: str | None = None,
) -> list[MappedQuestion]:
    """Record ``questionId`` → slot for one provisioned form.

    ``definition`` is what the API says the form contains *now*; ``specs`` is
    what this application asked it to contain. The two are joined on item index,
    and a spec whose index is not present in the read-back is an error rather
    than a row quietly left out — a form missing a question it was told to
    create is not a form worth collecting on.

    Idempotent: re-provisioning the same form re-reads the ids and updates the
    rows in place, which is what makes a resumed provision safe.
    """
    by_index = definition.by_index()
    rows: list[MappedQuestion] = []
    missing: list[str] = []

    for spec in specs:
        item = by_index.get(spec.index)
        if item is None or not item.question_id:
            missing.append(f"{spec.slot} (expected at index {spec.index})")
            continue
        rows.append(
            MappedQuestion(
                question_id=item.question_id,
                slot=spec.slot,
                rotating_kind=rotating_kind if spec.slot == SLOT_ROTATING else None,
                # The text the API reports, not the text we asked for. If a
                # teacher edited the wording between creation and this read, the
                # snapshot has to be what fellows will actually see.
                question_text=item.title or spec.title,
                item_index=spec.index,
            )
        )

    if missing:
        raise QuestionMapIncomplete(
            f"Form {form_id} was read back and does not contain the question(s) it "
            f"was told to create: {', '.join(missing)}.\n\n"
            "The form is not the shape this code expects, so its answers cannot be "
            "resolved and it has not been recorded as ready. Check the form in the "
            "Forms editor — most likely someone deleted or reordered a question."
        )

    # Replace rather than merge. A form whose questions were recreated has new
    # ids, and leaving the old rows behind would violate the (form_id, slot)
    # uniqueness while looking like a harmless duplicate.
    execute(conn, "delete from form_question_map where form_id = %s", (form_id,))
    for row in rows:
        execute(
            conn,
            """
            insert into form_question_map
                (form_id, question_id, slot, rotating_kind, question_text, item_index)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                form_id,
                row.question_id,
                row.slot,
                row.rotating_kind,
                row.question_text,
                row.item_index,
            ),
        )

    log.info("question map recorded form=%s slots=%d", form_id, len(rows))
    return rows


def load_map(conn: psycopg.Connection, form_id: str) -> dict[str, MappedQuestion]:
    """``questionId`` -> mapping, for one form."""
    return {
        row["question_id"]: MappedQuestion(
            question_id=row["question_id"],
            slot=row["slot"],
            rotating_kind=row["rotating_kind"],
            question_text=row["question_text"],
            item_index=row["item_index"],
        )
        for row in fetch_all(
            conn,
            """
            select question_id, slot, rotating_kind, question_text, item_index
              from form_question_map
             where form_id = %s
             order by item_index
            """,
            (form_id,),
        )
    }


def require_map(
    conn: psycopg.Connection, form_id: str, expected_slots: Iterable[str]
) -> dict[str, MappedQuestion]:
    """Load the map and refuse to continue unless it covers every expected slot.

    Called at the top of every Part B pull. Failing here costs one re-provision;
    not failing here costs a term of numbers that look right.
    """
    mapping = load_map(conn, form_id)
    present = {entry.slot for entry in mapping.values()}
    wanted = set(expected_slots)
    absent = sorted(wanted - present)

    if not mapping:
        raise QuestionMapIncomplete(
            f"Form {form_id} has no question map, so there is no way to tell which "
            "answer is the confidence rating and which is the takeaway.\n\n"
            "Responses have NOT been ingested. Re-provision this session's Part B "
            "form — provisioning reads the form back and records the map. Ingesting "
            "on a guess would file answers under the wrong fields and every number "
            "downstream would look entirely plausible."
        )

    if absent:
        raise QuestionMapIncomplete(
            f"Form {form_id}'s question map is missing: {', '.join(absent)}.\n\n"
            f"It maps {len(mapping)} question(s) but the form is expected to have "
            f"{len(wanted)}. Responses have NOT been ingested. Re-provision this "
            "session's Part B form to record the map again."
        )

    return mapping


def resolve_answers(
    mapping: Mapping[str, MappedQuestion], answers_by_id: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Split a response's answers into ``{slot: value}`` and everything else.

    The leftovers are returned rather than dropped: a teacher who added a
    question in the Forms UI produces answers this code has no slot for, and
    those still belong in ``extra_fields`` where somebody can find them.
    """
    slots: dict[str, str] = {}
    extras: dict[str, str] = {}
    for question_id, value in (answers_by_id or {}).items():
        entry = mapping.get(question_id)
        if entry is None:
            extras[question_id] = value
        else:
            slots[entry.slot] = value
    return slots, extras


def rotating_kind_for(mapping: Mapping[str, MappedQuestion]) -> str | None:
    """The rotating kind recorded for this form, if any."""
    for entry in mapping.values():
        if entry.slot == SLOT_ROTATING:
            return entry.rotating_kind
    return None


def rotating_text_for(mapping: Mapping[str, MappedQuestion]) -> str | None:
    """The exact rotating question text this form showed."""
    for entry in mapping.values():
        if entry.slot == SLOT_ROTATING:
            return entry.question_text
    return None


def map_rows(conn: psycopg.Connection, form_id: str) -> list[dict[str, Any]]:
    """The raw rows, for the console and for `cufa` output."""
    return fetch_all(
        conn,
        """
        select question_id, slot, rotating_kind, question_text, item_index, recorded_at
          from form_question_map
         where form_id = %s
         order by item_index
        """,
        (form_id,),
    )


__all__ = [
    "MappedQuestion",
    "QuestionMapIncomplete",
    "load_map",
    "map_rows",
    "record_map",
    "require_map",
    "resolve_answers",
    "rotating_kind_for",
    "rotating_text_for",
]
