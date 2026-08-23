"""Provision one Google Form per session — safely, and safe to retry.

Trap 1 lives here. Since 2026-07-01 a form created through the API is
**unpublished** and refuses every submission. The form exists, the link
resolves, the teacher shares it, and nothing arrives. So publishing is not
optional, and calling ``setPublishSettings`` is not proof it worked: the state
is read back and asserted before anything is reported as ready.

Retry safety is structural rather than careful. The ``session_form`` row is
written **before** publishing, with ``publish_verified_at`` NULL. "Ready" means
that column is non-NULL. A run that copies a form and then fails to verify the
publish leaves a row that a later run *resumes* — it publishes the form that
already exists instead of copying a second one, and it is never reported ready
in the meantime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from .db import execute, fetch_one
from .errors import PublishVerificationFailed
from .form_content import (
    QUESTION_HELP,
    session_form_description,
    session_form_title,
)
from .google.base import PASSPHRASE_QUESTION_TITLE, FormsClient
from .logging_setup import get_logger
from .template import require_verified_template

log = get_logger(__name__)


@dataclass(frozen=True)
class ProvisionResult:
    """What happened, in terms the console and the CLI both report."""

    session_id: str
    form_id: str | None
    form_url: str | None
    edit_url: str | None
    created: bool
    resumed: bool
    already_ready: bool
    dry_run: bool = False

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


def get_session_form(conn: psycopg.Connection, session_id: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        select session_form_id, session_id, template_id, form_id, form_url, edit_url,
               provisioned_at, published_at, publish_verified_at,
               response_watermark, last_polled_at
          from session_form
         where session_id = %s
        """,
        (session_id,),
    )


def _load_session(conn: psycopg.Connection, session_id: str) -> dict[str, Any]:
    row = fetch_one(
        conn,
        """
        select session_id, cohort_id, title, scheduled_at_local, timezone,
               scheduled_at_utc, duration_minutes, grace_minutes, passphrase
          from "session"
         where session_id = %s
        """,
        (session_id,),
    )
    if row is None:
        raise LookupError(f"No session with id {session_id}")
    return row


def _content_requests(session: dict[str, Any]) -> list[dict[str, Any]]:
    """batchUpdate requests for the things batchUpdate reliably does.

    Title, description and the question text — deliberately nothing about
    settings. Email collection travels by Drive copy (trap 2), and publish state
    has its own endpoint (trap 1).
    """
    local = session["scheduled_at_local"]
    local_text = local.strftime("%Y-%m-%d %H:%M") if hasattr(local, "strftime") else str(local)
    return [
        {
            "updateFormInfo": {
                "info": {
                    "title": session_form_title(session["title"], local_text),
                    "description": session_form_description(session["title"]),
                },
                "updateMask": "title,description",
            }
        },
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


def _publish_and_verify(
    conn: psycopg.Connection, client: FormsClient, session_id: str, form_id: str
) -> None:
    """Publish, then read the state back and refuse to accept a 200 as proof."""
    client.set_publish_settings(form_id, is_published=True, is_accepting_responses=True)
    execute(
        conn,
        "update session_form set published_at = now() where session_id = %s",
        (session_id,),
    )

    state = client.read_settings(form_id)
    if not state.accepts_responses:
        _log_attempt(
            conn,
            session_id,
            "publish",
            "failure",
            request={"form_id": form_id},
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
        "update session_form set publish_verified_at = now() where session_id = %s",
        (session_id,),
    )
    _log_attempt(conn, session_id, "publish", "success", request={"form_id": form_id})


def provision_session(
    conn: psycopg.Connection,
    client: FormsClient,
    session_id: str,
    *,
    dry_run: bool = False,
) -> ProvisionResult:
    """Provision (or resume, or skip) the form for one session."""
    session = _load_session(conn, session_id)
    session_id = str(session["session_id"])
    existing = get_session_form(conn, session_id)

    if dry_run:
        # A dry run still verifies the template, and still fails if it is not
        # Verified. "What would happen" is "this would be blocked", and a dry
        # run that reported a clean plan would be lying about the outcome.
        template = require_verified_template(conn, client)
        planned = {
            "template_form_id": template.form_id,
            "would_copy": existing is None,
            "would_publish": True,
            "would_verify_publish": True,
            "title": session_form_title(
                session["title"], str(session["scheduled_at_local"])
            ),
        }
        _log_attempt(conn, session_id, "provision", "dry_run", request=planned)
        log.info("dry run session=%s plan=%s", session_id, planned)
        return ProvisionResult(
            session_id=session_id,
            form_id=existing["form_id"] if existing else None,
            form_url=existing["form_url"] if existing else None,
            edit_url=existing["edit_url"] if existing else None,
            created=False,
            resumed=False,
            already_ready=False,
            dry_run=True,
        )

    # Idempotency, case 1: already finished. Show it, do not create a second.
    if existing and existing["publish_verified_at"] is not None:
        _log_attempt(
            conn, session_id, "provision", "skipped", request={"form_id": existing["form_id"]}
        )
        log.info("session=%s already provisioned form_id=%s", session_id, existing["form_id"])
        return ProvisionResult(
            session_id=session_id,
            form_id=existing["form_id"],
            form_url=existing["form_url"],
            edit_url=existing["edit_url"],
            created=False,
            resumed=False,
            already_ready=True,
        )

    # Trap 2 gate: re-verified on every run, not read from a stored flag.
    template = require_verified_template(conn, client)

    # Idempotency, case 2: a previous run copied a form but did not finish.
    # Resume that form rather than orphaning it and copying another.
    if existing:
        log.info(
            "session=%s resuming partially provisioned form_id=%s",
            session_id,
            existing["form_id"],
        )
        try:
            _publish_and_verify(conn, client, session_id, existing["form_id"])
        except Exception as exc:
            _log_attempt(
                conn, session_id, "provision", "failure",
                request={"form_id": existing["form_id"], "resumed": True},
                error=str(exc),
            )
            raise
        _log_attempt(
            conn, session_id, "provision", "success",
            request={"form_id": existing["form_id"], "resumed": True},
        )
        return ProvisionResult(
            session_id=session_id,
            form_id=existing["form_id"],
            form_url=existing["form_url"],
            edit_url=existing["edit_url"],
            created=False,
            resumed=True,
            already_ready=False,
        )

    local = session["scheduled_at_local"]
    local_text = local.strftime("%Y-%m-%d %H:%M") if hasattr(local, "strftime") else str(local)
    title = session_form_title(session["title"], local_text)

    ref = client.copy_form(template.form_id, title)
    _log_attempt(
        conn, session_id, "copy_form", "success",
        request={"template_form_id": template.form_id, "form_id": ref.form_id},
    )

    try:
        client.batch_update(ref.form_id, _content_requests(session))
    except Exception as exc:
        # The copy exists in Drive. Record it so a retry resumes rather than
        # copying again, and so the orphan is findable.
        execute(
            conn,
            """
            insert into session_form (session_id, template_id, form_id, form_url, edit_url)
            values (%s, %s, %s, %s, %s)
            on conflict (session_id) do nothing
            """,
            (session_id, template.template_id, ref.form_id, ref.responder_url, ref.edit_url),
        )
        _log_attempt(
            conn, session_id, "batch_update", "failure",
            request={"form_id": ref.form_id}, error=str(exc),
        )
        raise

    execute(
        conn,
        """
        insert into session_form (session_id, template_id, form_id, form_url, edit_url)
        values (%s, %s, %s, %s, %s)
        on conflict (session_id) do update
           set form_id  = excluded.form_id,
               form_url = excluded.form_url,
               edit_url = excluded.edit_url
        """,
        (session_id, template.template_id, ref.form_id, ref.responder_url, ref.edit_url),
    )

    try:
        _publish_and_verify(conn, client, session_id, ref.form_id)
    except Exception as exc:
        _log_attempt(
            conn, session_id, "provision", "failure",
            request={"form_id": ref.form_id}, error=str(exc),
        )
        raise

    _log_attempt(conn, session_id, "provision", "success", request={"form_id": ref.form_id})
    log.info("session=%s provisioned form_id=%s", session_id, ref.form_id)
    return ProvisionResult(
        session_id=session_id,
        form_id=ref.form_id,
        form_url=ref.responder_url,
        edit_url=ref.edit_url,
        created=True,
        resumed=False,
        already_ready=False,
    )


def is_ready(conn: psycopg.Connection, session_id: str) -> bool:
    """True only when the publish state has been read back and confirmed."""
    row = get_session_form(conn, session_id)
    return bool(row and row["publish_verified_at"] is not None)
