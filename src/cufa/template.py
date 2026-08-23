"""The one template form, and the manual step the API cannot replace.

Trap 2 in full: setting ``emailCollectionType`` through ``forms.batchUpdate``
has been observed returning 400 INVALID_ARGUMENT with no working enum value.
Verified email collection is the entire premise of this design — an address the
respondent types is the self-reported identity we are replacing — so it cannot
depend on a call that may reject.

What this module does instead:

1. Create one template form through the API.
2. *Attempt* the settings update anyway, because if Google fixes it the human
   step disappears for free. Catch the 400 and carry on.
3. Ask a human to flip Settings → Responses → Collect email addresses →
   Verified, by hand, once.
4. Read ``form.settings`` back and refuse to proceed until the API itself says
   VERIFIED. The human's word is not evidence.

Step 4 is re-run before every provisioning batch, so a template someone edited
back to responder-input fails loudly instead of quietly producing forms that
collect nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from .db import execute, fetch_one
from .errors import EmailCollectionRejected, TemplateNotVerified
from .form_content import HEADER_NOTICE, QUESTION_HELP, TEMPLATE_TITLE
from .google.base import (
    EMAIL_COLLECTION_VERIFIED,
    PASSPHRASE_QUESTION_TITLE,
    FormsClient,
    FormState,
    GoogleApiError,
)
from .logging_setup import get_logger

log = get_logger(__name__)

MANUAL_STEP = (
    "Open the template form, then set:\n"
    "    Settings → Responses → Collect email addresses → Verified\n"
    "This is the one thing the Forms API cannot do reliably (see "
    "docs/google-api-traps.md, trap 2). It takes about 30 seconds and is "
    "needed once, not per session."
)


@dataclass(frozen=True)
class TemplateRecord:
    """The stored template row, plus whether it is currently usable."""

    template_id: str
    form_id: str
    form_url: str | None
    edit_url: str | None
    verified_email_confirmed_at: Any
    last_verified_at: Any
    settings_snapshot: dict[str, Any]

    @property
    def is_verified(self) -> bool:
        return self.verified_email_confirmed_at is not None


def _row_to_record(row: dict[str, Any]) -> TemplateRecord:
    return TemplateRecord(
        template_id=str(row["template_id"]),
        form_id=row["form_id"],
        form_url=row.get("form_url"),
        edit_url=row.get("edit_url"),
        verified_email_confirmed_at=row.get("verified_email_confirmed_at"),
        last_verified_at=row.get("last_verified_at"),
        settings_snapshot=row.get("settings_snapshot") or {},
    )


def get_template(conn: psycopg.Connection) -> TemplateRecord | None:
    """The active template, or None if `cufa template create` has not been run."""
    row = fetch_one(
        conn,
        """
        select template_id, form_id, form_url, edit_url,
               verified_email_confirmed_at, last_verified_at, settings_snapshot
          from form_template
         where is_active
         order by created_at desc
         limit 1
        """,
    )
    return _row_to_record(row) if row else None


def create_template(conn: psycopg.Connection, client: FormsClient) -> TemplateRecord:
    """Create the template form and record it as *not yet verified*.

    Idempotent: an existing active template is returned rather than a second one
    created. Two templates would mean two sets of settings to keep correct, and
    only one of them would be checked.
    """
    existing = get_template(conn)
    if existing is not None:
        log.info("template already exists form_id=%s", existing.form_id)
        return existing

    ref = client.create_template(TEMPLATE_TITLE, HEADER_NOTICE)

    client.batch_update(
        ref.form_id,
        [
            {
                "updateFormInfo": {
                    "info": {"title": TEMPLATE_TITLE, "description": HEADER_NOTICE},
                    "updateMask": "title,description",
                }
            },
            {
                "createItem": {
                    "item": {
                        "title": PASSPHRASE_QUESTION_TITLE,
                        "description": QUESTION_HELP,
                        "questionItem": {
                            "question": {
                                "required": True,
                                "textQuestion": {"paragraph": False},
                            }
                        },
                    },
                    "location": {"index": 0},
                }
            },
        ],
    )

    attempted = try_set_verified_email(client, ref.form_id)

    execute(
        conn,
        """
        insert into form_template (form_id, form_url, edit_url, settings_snapshot)
        values (%s, %s, %s, '{}'::jsonb)
        """,
        (ref.form_id, ref.responder_url, ref.edit_url),
    )
    log.info(
        "template created form_id=%s email_collection_set_by_api=%s",
        ref.form_id,
        attempted,
    )

    record = get_template(conn)
    assert record is not None
    return record


def try_set_verified_email(client: FormsClient, form_id: str) -> bool:
    """Best-effort attempt at setting Verified collection through the API.

    Returns True if the call was accepted. A 400 is the *expected* outcome
    (trap 2) and is not an error here — it is the reason the manual step exists.
    Any other failure is re-raised, because "the API is broken in a new way" and
    "the API is broken in the documented way" call for different responses.

    Note this returning True still proves nothing: the caller must read the
    settings back regardless. A 200 is not a state.
    """
    try:
        client.batch_update(
            form_id,
            [
                {
                    "updateSettings": {
                        "settings": {"emailCollectionType": EMAIL_COLLECTION_VERIFIED},
                        "updateMask": "emailCollectionType",
                    }
                }
            ],
        )
        return True
    except GoogleApiError as exc:
        if exc.status == 400:
            log.info(
                "emailCollectionType update rejected by the API as expected "
                "(trap 2); falling back to the one-time manual step"
            )
            return False
        raise EmailCollectionRejected(
            f"Unexpected failure setting email collection on {form_id}: {exc}"
        ) from exc


def verify_template(conn: psycopg.Connection, client: FormsClient) -> FormState:
    """Read the template's settings back and record the verdict.

    Raises ``TemplateNotVerified`` when the API does not say VERIFIED. On
    failure the stored confirmation is *cleared*, not left stale: a template
    that was verified in September and edited in October must stop being usable
    the moment we notice, not keep its old green tick.
    """
    record = get_template(conn)
    if record is None:
        raise TemplateNotVerified(
            "No template form exists yet. Run `cufa template create` (or use the "
            "console's Template setup screen) first."
        )

    state = client.read_settings(record.form_id)
    snapshot = state.raw.get("settings") if state.raw else None

    if state.collects_verified_email:
        execute(
            conn,
            """
            update form_template
               set verified_email_confirmed_at = coalesce(verified_email_confirmed_at, now()),
                   last_verified_at = now(),
                   settings_snapshot = %s::jsonb
             where template_id = %s
            """,
            (_as_jsonb(snapshot or {"emailCollectionType": state.email_collection_type}), record.template_id),
        )
        log.info("template verified form_id=%s email_collection=VERIFIED", record.form_id)
        return state

    execute(
        conn,
        """
        update form_template
           set verified_email_confirmed_at = null,
               last_verified_at = now(),
               settings_snapshot = %s::jsonb
         where template_id = %s
        """,
        (_as_jsonb(snapshot or {"emailCollectionType": state.email_collection_type}), record.template_id),
    )
    raise TemplateNotVerified(
        f"Template form {record.form_id} reports emailCollectionType="
        f"{state.email_collection_type!r}, not {EMAIL_COLLECTION_VERIFIED!r}.\n\n"
        f"{MANUAL_STEP}\n\n"
        f"Template link: {record.edit_url or record.form_url or '(unknown)'}\n\n"
        "Provisioning is blocked until this reads back as VERIFIED. Forms copied "
        "from an unverified template collect a typed address instead of a "
        "Google-confirmed one, which looks identical until you try to trust it."
    )


def require_verified_template(conn: psycopg.Connection, client: FormsClient) -> TemplateRecord:
    """Re-verify before use and return the template. Never trusts the stored flag."""
    verify_template(conn, client)
    record = get_template(conn)
    assert record is not None
    return record


def _as_jsonb(value: Any) -> str:
    import json

    return json.dumps(value)
