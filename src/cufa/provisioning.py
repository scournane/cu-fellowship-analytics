"""Provision Google Forms per session — safely, and safe to retry.

A session has **two** forms. Part A goes out mid-lesson and proves presence;
Part B goes out at the end and measures what landed. They are released at
different moments, so one form cannot be both. Everything below is
part-parameterised rather than duplicated: one provisioning path, one publish
verification, one idempotency story.

Trap 1 lives here. Since 2026-07-01 a form created through the API is
**unpublished** and refuses every submission. The form exists, the link
resolves, the teacher shares it, and nothing arrives. So publishing is not
optional, and calling ``setPublishSettings`` is not proof it worked: the state
is read back and asserted before anything is reported as ready.

Trap 5 lives here too, for Part B. ``forms.responses.list`` keys answers by
``questionId``, and whether a Drive copy preserves those ids is not verified —
so after the questions are in place the form is read back with ``forms.get`` and
the id-to-slot mapping recorded. Nothing is ever matched by title.

Retry safety is structural rather than careful. The ``session_form`` row is
written **before** publishing, with ``publish_verified_at`` NULL. "Ready" means
that column is non-NULL. A run that copies a form and then fails to verify the
publish leaves a row that a later run *resumes* — it publishes the form that
already exists instead of copying a second one, and it is never reported ready
in the meantime. Part B's content step is written as a reconciliation against
what the form currently contains, so resuming re-applies it without creating a
second copy of every question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from . import form_content_b
from .db import connection, execute, fetch_all, fetch_one
from .errors import FormUnreachable, PublishVerificationFailed
from .form_content import (
    QUESTION_HELP,
    session_form_description,
    session_form_title,
)
from .google.base import (
    PASSPHRASE_QUESTION_TITLE,
    FormDefinition,
    FormsClient,
    GoogleApiError,
)
from .help_routing import HelpRouting, get_help_routing
from .logging_setup import get_logger
from .provenance import explain_google_404, is_simulated_form_id, client_is_fake
from .question_map import record_map
from .rotation import RotationConfig, get_rotation
from .template import (
    PART_A,
    PART_B,
    connected_account,
    require_verified_template,
    validate_part,
)

log = get_logger(__name__)


@dataclass(frozen=True)
class ProvisionResult:
    """What happened, in terms the console and the CLI both report."""

    session_id: str
    part: str
    form_id: str | None
    form_url: str | None
    edit_url: str | None
    created: bool
    resumed: bool
    already_ready: bool
    dry_run: bool = False
    #: Part B only: the exact wording the rotating slot was given, and its kind.
    rotating_kind: str | None = None
    rotating_text: str | None = None
    #: Part B only. None means the field is on the form; a string is the reason
    #: it was left off, and is shown rather than swallowed.
    help_field_omitted_reason: str | None = None

    @property
    def outcome(self) -> str:
        if self.dry_run:
            return "dry_run"
        if self.already_ready:
            return "skipped"
        return "success"

    @property
    def summary(self) -> str:
        if self.dry_run:
            return "dry run — no Google calls were made"
        if self.already_ready:
            return "already provisioned; showing the existing form"
        if self.resumed:
            return "resumed a partially provisioned form and verified publish"
        return "form copied, published and publish state verified"


def _record_orphan(
    session_id: str,
    part: str,
    template_id: str,
    form_id: str,
    form_url: str,
    edit_url: str | None,
    error: str,
) -> None:
    """Record a copied-but-unfinished form on its own connection.

    The copy already exists in Drive. Writing the row on the caller's connection
    looks right and is not: every caller wraps provisioning in a transaction and
    rolls it back when this raises, so the row — and the provisioning_log entry
    that would have made the orphan findable — disappear with it. The next run
    then copies a *second* form, and the first is left untracked in Drive with
    nothing pointing at it.

    That is not theoretical. It happened on the first real install, and the
    stray form was only found by listing Drive.

    A separate autocommit connection survives the rollback, which is the whole
    point. It is deliberately best-effort: if this fails too, the original
    provisioning error is the one worth showing, not a bookkeeping failure on
    top of it.
    """
    try:
        with connection(autocommit=True) as side:
            execute(
                side,
                """
                insert into session_form
                    (session_id, part, template_id, form_id, form_url, edit_url)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (session_id, part) do nothing
                """,
                (session_id, part, template_id, form_id, form_url, edit_url),
            )
            execute(
                side,
                """
                insert into provisioning_log
                    (session_id, action, request_summary, outcome, error)
                values (%s, %s, %s::jsonb, %s, %s)
                """,
                (
                    session_id,
                    "batch_update",
                    json.dumps({"form_id": form_id, "part": part}),
                    "failure",
                    error,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - never mask the real failure
        log.warning(
            "could not record the orphaned form %s for session %s: %s. It exists in "
            "Drive and is not tracked here — provisioning again will copy another.",
            form_id,
            session_id,
            type(exc).__name__,
        )


def _log_attempt(
    conn: psycopg.Connection,
    session_id: str,
    action: str,
    outcome: str,
    *,
    request: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Every provisioning attempt is recorded, successful or not."""
    execute(
        conn,
        """
        insert into provisioning_log (session_id, action, request_summary, outcome, error)
        values (%s, %s, %s::jsonb, %s, %s)
        """,
        (session_id, action, json.dumps(request or {}), outcome, error),
    )


def get_session_form(
    conn: psycopg.Connection, session_id: str, part: str = PART_A
) -> dict[str, Any] | None:
    """The provisioned form row for one session and part, if there is one."""
    part = validate_part(part)
    return fetch_one(
        conn,
        """
        select session_form_id, session_id, part, template_id, form_id, form_url,
               edit_url, provisioned_at, published_at, publish_verified_at,
               response_watermark, last_polled_at
          from session_form
         where session_id = %s and part = %s
        """,
        (session_id, part),
    )


def session_forms(conn: psycopg.Connection, session_id: str) -> dict[str, dict[str, Any]]:
    """Both parts' forms for one session, keyed by part."""
    rows = fetch_all(
        conn,
        """
        select session_form_id, session_id, part, template_id, form_id, form_url,
               edit_url, provisioned_at, published_at, publish_verified_at,
               response_watermark, last_polled_at
          from session_form
         where session_id = %s
        """,
        (session_id,),
    )
    return {row["part"]: row for row in rows}


def _discard_unusable_form(
    conn: psycopg.Connection,
    client: FormsClient,
    session_id: str,
    part: str,
    existing: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Drop a ``session_form`` row that provably points at nothing.

    One case only, and it is unambiguous: the stored id was minted by the fake
    client and the client now in use is the real one (or the exact reverse).
    That form has never existed for this client, so there is nothing to resume
    and nothing to lose — no response can be attached to a form Google has never
    heard of.

    Deliberately narrower than "delete anything that 404s". A real form that
    Google cannot find might be in Drive's bin, and restoring it keeps every
    response already collected on it; throwing the row away would strand them.
    That case raises instead, in ``_publish_and_verify`` and the copy path.
    """
    if existing is None:
        return None
    if is_simulated_form_id(existing["form_id"]) == client_is_fake(client):
        return existing

    log.warning(
        "discarding session_form session=%s part=%s form_id=%s: it was created by "
        "the other kind of client and cannot exist here",
        session_id,
        part,
        existing["form_id"],
    )
    execute(
        conn,
        "delete from form_question_map where form_id = %s",
        (existing["form_id"],),
    )
    execute(
        conn,
        "delete from session_form where session_id = %s and part = %s",
        (session_id, part),
    )
    _log_attempt(
        conn,
        session_id,
        "discard_stale_form",
        "success",
        request={"form_id": existing["form_id"], "part": part},
        error=(
            "the stored form was created by the other kind of Google client "
            "(simulated vs real), so it could never be opened; a fresh form is "
            "being provisioned in its place"
        ),
    )
    return None


def _load_session(conn: psycopg.Connection, session_id: str) -> dict[str, Any]:
    row = fetch_one(
        conn,
        """
        select session_id, cohort_id, title, scheduled_at_local, timezone,
               scheduled_at_utc, duration_minutes, grace_minutes, passphrase,
               week_index, teacher_question
          from "session"
         where session_id = %s
        """,
        (session_id,),
    )
    if row is None:
        raise LookupError(f"No session with id {session_id}")
    return row


def _local_text(session: dict[str, Any]) -> str:
    local = session["scheduled_at_local"]
    return local.strftime("%Y-%m-%d %H:%M") if hasattr(local, "strftime") else str(local)


def form_title_for(session: dict[str, Any], part: str) -> str:
    if part == PART_B:
        return form_content_b.session_form_title(session["title"], _local_text(session))
    return session_form_title(session["title"], _local_text(session))


def _form_info_request(session: dict[str, Any], part: str) -> dict[str, Any]:
    """Title and description — the things ``batchUpdate`` reliably does.

    Deliberately nothing about settings. Email collection travels by Drive copy
    (trap 2), and publish state has its own endpoint (trap 1).
    """
    if part == PART_B:
        description = form_content_b.session_form_description(session["title"])
    else:
        description = session_form_description(session["title"])
    return {
        "updateFormInfo": {
            "info": {"title": form_title_for(session, part), "description": description},
            "updateMask": "title,description",
        }
    }


def resolve_rotating_slot(
    session: dict[str, Any], rotation: RotationConfig | None = None
) -> Any:
    """Which question this session's rotating slot asks.

    Raises ``TeacherQuestionMissing`` when the week calls for the teacher's own
    question and none is set — which blocks provisioning, on purpose.
    """
    rotation = rotation or get_rotation()
    week = session.get("week_index")
    if week is None:
        from .rotation import RotationConfigError

        raise RotationConfigError(
            f"Session “{session['title']}” has no week number, so the rotating "
            "question cannot be resolved.\n\n"
            "The week drives the rotation and is typed in rather than derived from "
            "the date: sessions get rescheduled, skipped and doubled up, and a "
            "calendar-derived week would desynchronise the whole rotation "
            "silently. Set “Week of the fellowship” on the session and try again."
        )
    return rotation.resolve(
        int(week),
        teacher_question=session.get("teacher_question"),
        session_label=f"session “{session['title']}”",
    )


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------


def _part_a_content(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Part A's one question, retitled in place on the copied form."""
    return [
        _form_info_request(session, PART_A),
        {
            "updateItem": {
                "item": {
                    "title": PASSPHRASE_QUESTION_TITLE,
                    "description": QUESTION_HELP,
                    "questionItem": {
                        "question": {"required": True, "textQuestion": {"paragraph": False}}
                    },
                },
                "location": {"index": 0},
                "updateMask": "title,description",
            }
        },
    ]


def _reconcile_requests(
    definition: FormDefinition, specs: list[form_content_b.ItemSpec]
) -> list[dict[str, Any]]:
    """The minimal batch that makes ``definition`` match ``specs``.

    Written as a reconciliation rather than "create five questions" for one
    reason: provisioning has to be resumable. A run that copies the template and
    then fails before publishing leaves a form that already has four questions;
    a second run that blindly created five more would produce a nine-question
    form and no error. Comparing against what is actually there makes the second
    run a no-op on the items and a retry on the publish.
    """
    existing = definition.by_index()
    requests: list[dict[str, Any]] = []

    for spec in specs:
        item = existing.get(spec.index)
        if item is None:
            requests.append(spec.request)
        elif item.title != spec.title:
            # Retitle in place. A question id survives a title change, which is
            # exactly why the rotating slot can be reworded weekly without the
            # previous weeks' answers becoming unresolvable.
            #
            # The request carries the FULL item body — see ItemSpec.update_request
            # for the 400 a title-only body earns from the live API.
            requests.append(spec.update_request)
    return requests


def _help_removal_requests(
    conn: psycopg.Connection, definition: FormDefinition, form_id: str, want_help: bool
) -> list[dict[str, Any]]:
    """Delete a help field that is no longer routed anywhere.

    Only ever deletes an item this application recorded as the help slot. A
    question a teacher added by hand is left alone — its answers land in
    ``extra_fields`` rather than being thrown away with the form item.
    """
    if want_help:
        return []
    recorded = fetch_all(
        conn,
        "select question_id from form_question_map where form_id = %s and slot = 'help'",
        (form_id,),
    )
    help_ids = {row["question_id"] for row in recorded}
    if not help_ids:
        return []

    requests = []
    for item in sorted(definition.items, key=lambda i: i.index, reverse=True):
        if item.question_id in help_ids:
            log.info(
                "removing the help field from form %s: no recipient is configured",
                form_id,
            )
            requests.append({"deleteItem": {"location": {"index": item.index}}})
    return requests


def _apply_part_b_content(
    conn: psycopg.Connection,
    client: FormsClient,
    session: dict[str, Any],
    form_id: str,
    *,
    routing: HelpRouting,
    rotating_title: str,
) -> tuple[FormDefinition, list[form_content_b.ItemSpec]]:
    """Put the six fields on a Part B form and return what is actually there."""
    specs = form_content_b.item_specs(rotating_title, include_help=routing.has_recipient)

    before = client.get_form(form_id)
    requests = [_form_info_request(session, PART_B)]
    requests += _help_removal_requests(conn, before, form_id, routing.has_recipient)
    requests += _reconcile_requests(before, specs)
    client.batch_update(form_id, requests)

    # Read back rather than assume. This is the only source of question ids, and
    # a 200 from batchUpdate is not a form.
    return client.get_form(form_id), specs


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------


def _publish_and_verify(
    conn: psycopg.Connection,
    client: FormsClient,
    session_id: str,
    form_id: str,
    part: str,
) -> None:
    """Publish, then read the state back and refuse to accept a 200 as proof."""
    client.set_publish_settings(form_id, is_published=True, is_accepting_responses=True)
    execute(
        conn,
        "update session_form set published_at = now() where session_id = %s and part = %s",
        (session_id, part),
    )

    state = client.read_settings(form_id)
    if not state.accepts_responses:
        _log_attempt(
            conn,
            session_id,
            "publish",
            "failure",
            request={"form_id": form_id, "part": part},
            error=(
                f"publish read-back: isPublished={state.is_published} "
                f"isAcceptingResponses={state.is_accepting_responses}"
            ),
        )
        raise PublishVerificationFailed(
            f"Form {form_id} was published, but reading the state back says "
            f"isPublished={state.is_published} and "
            f"isAcceptingResponses={state.is_accepting_responses}.\n\n"
            "An unpublished form accepts no responses while its link still "
            "resolves, so this would look fine and collect nothing (trap 1). "
            "The form has NOT been reported as ready; re-run provisioning for "
            "this session to retry — it will reuse this form rather than "
            "creating another."
        )

    execute(
        conn,
        "update session_form set publish_verified_at = now() where session_id = %s and part = %s",
        (session_id, part),
    )
    _log_attempt(conn, session_id, "publish", "success", request={"form_id": form_id, "part": part})


def _upsert_session_form(
    conn: psycopg.Connection,
    session_id: str,
    part: str,
    template_id: str,
    form_id: str,
    form_url: str,
    edit_url: str | None,
) -> None:
    execute(
        conn,
        """
        insert into session_form (session_id, part, template_id, form_id, form_url, edit_url)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (session_id, part) do update
           set form_id  = excluded.form_id,
               form_url = excluded.form_url,
               edit_url = excluded.edit_url
        """,
        (session_id, part, template_id, form_id, form_url, edit_url),
    )


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------


def provision_session(
    conn: psycopg.Connection,
    client: FormsClient,
    session_id: str,
    *,
    part: str = PART_A,
    dry_run: bool = False,
) -> ProvisionResult:
    """Provision (or resume, or skip) one part's form for one session."""
    part = validate_part(part)
    session = _load_session(conn, session_id)
    session_id = str(session["session_id"])
    existing = get_session_form(conn, session_id, part)
    if not dry_run:
        # Leftovers from `make demo` are cleared here rather than surfacing as a
        # 404 three calls later. See _discard_unusable_form.
        existing = _discard_unusable_form(conn, client, session_id, part, existing)

    routing = get_help_routing() if part == PART_B else HelpRouting()
    slot = resolve_rotating_slot(session) if part == PART_B else None

    if dry_run:
        # A dry run still verifies the template, still resolves the rotation and
        # still fails if either is not ready. "What would happen" is "this would
        # be blocked", and a dry run reporting a clean plan would be lying about
        # the outcome.
        template = require_verified_template(conn, client, part)
        planned: dict[str, Any] = {
            "part": part,
            "template_form_id": template.form_id,
            "would_copy": existing is None,
            "would_publish": True,
            "would_verify_publish": True,
            "title": form_title_for(session, part),
        }
        if part == PART_B:
            planned["rotating_kind"] = slot.kind
            planned["rotating_text"] = slot.text
            planned["help_field"] = routing.has_recipient
            planned["would_record_question_map"] = True
        _log_attempt(conn, session_id, "provision", "dry_run", request=planned)
        log.info("dry run session=%s part=%s plan=%s", session_id, part, planned)
        return ProvisionResult(
            session_id=session_id,
            part=part,
            form_id=existing["form_id"] if existing else None,
            form_url=existing["form_url"] if existing else None,
            edit_url=existing["edit_url"] if existing else None,
            created=False,
            resumed=False,
            already_ready=False,
            dry_run=True,
            rotating_kind=slot.kind if slot else None,
            rotating_text=slot.text if slot else None,
            help_field_omitted_reason=routing.reason_omitted if part == PART_B else None,
        )

    # Idempotency, case 1: already finished. Show it, do not create a second.
    if existing and existing["publish_verified_at"] is not None:
        if part == PART_B:
            # Re-read the form and refresh the question map, without touching a
            # single question. This is what makes "provision again" the repair
            # for a map that is missing or incomplete — the state ingest refuses
            # on — and it is safe to press on a form that is already collecting,
            # because it reads and records rather than writing to the form.
            assert slot is not None
            definition = client.get_form(existing["form_id"])
            specs = form_content_b.item_specs(
                slot.text, include_help=routing.has_recipient
            )
            _record_question_map(
                conn, session_id, existing["form_id"], definition, specs,
                slot.kind, routing,
            )
        _log_attempt(
            conn,
            session_id,
            "provision",
            "skipped",
            request={"form_id": existing["form_id"], "part": part},
        )
        log.info(
            "session=%s part=%s already provisioned form_id=%s",
            session_id,
            part,
            existing["form_id"],
        )
        return ProvisionResult(
            session_id=session_id,
            part=part,
            form_id=existing["form_id"],
            form_url=existing["form_url"],
            edit_url=existing["edit_url"],
            created=False,
            resumed=False,
            already_ready=True,
            rotating_kind=slot.kind if slot else None,
            rotating_text=slot.text if slot else None,
            help_field_omitted_reason=routing.reason_omitted if part == PART_B else None,
        )

    # Trap 2 gate: re-verified on every run, not read from a stored flag.
    template = require_verified_template(conn, client, part)

    # Idempotency, case 2: a previous run copied a form but did not finish.
    # Resume that form rather than orphaning it and copying another.
    if existing:
        log.info(
            "session=%s part=%s resuming partially provisioned form_id=%s",
            session_id,
            part,
            existing["form_id"],
        )
        try:
            if part == PART_B:
                assert slot is not None
                definition, specs = _apply_part_b_content(
                    conn, client, session, existing["form_id"],
                    routing=routing, rotating_title=slot.text,
                )
                _record_question_map(
                    conn, session_id, existing["form_id"], definition, specs, slot.kind, routing
                )
            _publish_and_verify(conn, client, session_id, existing["form_id"], part)
        except GoogleApiError as exc:
            if exc.status in (403, 404):
                _log_attempt(
                    conn, session_id, "provision", "failure",
                    request={"form_id": existing["form_id"], "part": part, "resumed": True},
                    error=str(exc),
                )
                raise FormUnreachable(
                    explain_google_404(
                        existing["form_id"],
                        client,
                        what=f"This session's part-{part} form",
                        account=connected_account(conn),
                    )
                ) from exc
            raise
        except Exception as exc:
            _log_attempt(
                conn, session_id, "provision", "failure",
                request={"form_id": existing["form_id"], "part": part, "resumed": True},
                error=str(exc),
            )
            raise
        _log_attempt(
            conn, session_id, "provision", "success",
            request={"form_id": existing["form_id"], "part": part, "resumed": True},
        )
        return ProvisionResult(
            session_id=session_id,
            part=part,
            form_id=existing["form_id"],
            form_url=existing["form_url"],
            edit_url=existing["edit_url"],
            created=False,
            resumed=True,
            already_ready=False,
            rotating_kind=slot.kind if slot else None,
            rotating_text=slot.text if slot else None,
            help_field_omitted_reason=routing.reason_omitted if part == PART_B else None,
        )

    try:
        ref = client.copy_form(template.form_id, form_title_for(session, part))
    except GoogleApiError as exc:
        if exc.status in (403, 404):
            _log_attempt(
                conn, session_id, "copy_form", "failure",
                request={"template_form_id": template.form_id, "part": part},
                error=str(exc),
            )
            raise FormUnreachable(
                explain_google_404(
                    template.form_id,
                    client,
                    what=f"The part-{part} template",
                    account=connected_account(conn),
                )
            ) from exc
        raise
    _log_attempt(
        conn, session_id, "copy_form", "success",
        request={"template_form_id": template.form_id, "form_id": ref.form_id, "part": part},
    )

    try:
        if part == PART_B:
            assert slot is not None
            definition, specs = _apply_part_b_content(
                conn, client, session, ref.form_id,
                routing=routing, rotating_title=slot.text,
            )
        else:
            client.batch_update(ref.form_id, _part_a_content(session))
            definition, specs = None, None
    except Exception as exc:
        # The copy exists in Drive. Record it so a retry resumes rather than
        # copying again, and so the orphan is findable — out of band, because the
        # caller is about to roll this transaction back. See _record_orphan.
        _record_orphan(
            session_id,
            part,
            template.template_id,
            ref.form_id,
            ref.responder_url,
            ref.edit_url,
            str(exc),
        )
        raise

    _upsert_session_form(
        conn, session_id, part, template.template_id,
        ref.form_id, ref.responder_url, ref.edit_url,
    )

    try:
        if part == PART_B:
            assert slot is not None and definition is not None and specs is not None
            _record_question_map(
                conn, session_id, ref.form_id, definition, specs, slot.kind, routing
            )
        _publish_and_verify(conn, client, session_id, ref.form_id, part)
    except Exception as exc:
        _log_attempt(
            conn, session_id, "provision", "failure",
            request={"form_id": ref.form_id, "part": part}, error=str(exc),
        )
        raise

    _log_attempt(
        conn, session_id, "provision", "success",
        request={"form_id": ref.form_id, "part": part},
    )
    log.info("session=%s part=%s provisioned form_id=%s", session_id, part, ref.form_id)
    return ProvisionResult(
        session_id=session_id,
        part=part,
        form_id=ref.form_id,
        form_url=ref.responder_url,
        edit_url=ref.edit_url,
        created=True,
        resumed=False,
        already_ready=False,
        rotating_kind=slot.kind if slot else None,
        rotating_text=slot.text if slot else None,
        help_field_omitted_reason=routing.reason_omitted if part == PART_B else None,
    )


def _record_question_map(
    conn: psycopg.Connection,
    session_id: str,
    form_id: str,
    definition: FormDefinition,
    specs: list[form_content_b.ItemSpec],
    rotating_kind: str,
    routing: HelpRouting,
) -> None:
    """Record the id map and log whether the help field made it onto the form."""
    rows = record_map(conn, form_id, definition, specs, rotating_kind=rotating_kind)
    _log_attempt(
        conn,
        session_id,
        "question_map",
        "success",
        request={
            "form_id": form_id,
            "slots": {row.slot: row.item_index for row in rows},
            "rotating_kind": rotating_kind,
        },
    )
    if not routing.has_recipient:
        # Design invariant 2. Logged as its own provisioning event so a staffer
        # looking at the session's history sees the field was left off and why,
        # rather than wondering where it went.
        log.warning(
            "help checkbox omitted from form %s: no recipient configured", form_id
        )
        _log_attempt(
            conn,
            session_id,
            "help_field_omitted",
            "skipped",
            request={"form_id": form_id},
            error=routing.reason_omitted,
        )


def is_ready(conn: psycopg.Connection, session_id: str, part: str = PART_A) -> bool:
    """True only when the publish state has been read back and confirmed."""
    row = get_session_form(conn, session_id, part)
    return bool(row and row["publish_verified_at"] is not None)


__all__ = [
    "PART_A",
    "PART_B",
    "ProvisionResult",
    "form_title_for",
    "get_session_form",
    "is_ready",
    "provision_session",
    "resolve_rotating_slot",
    "session_forms",
]
