"""The template forms, and the manual step the API cannot replace.

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

**There is one template per part, and each needs its own step 3.** Part A is the
mid-session passphrase check-in; Part B is the end-of-session check-in. Email
collection is a property of a *form*, and it is carried by a Drive copy, not by
having been set on some other form in the same account — so verifying Part A's
template says nothing at all about Part B's. Two templates means the manual step
happens twice, once, which is the honest cost of the trap rather than a
shortcut around it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from . import form_content_b
from .db import execute, fetch_all, fetch_one
from .errors import EmailCollectionRejected, FormUnreachable, TemplateNotVerified
from .form_content import HEADER_NOTICE, QUESTION_HELP, TEMPLATE_TITLE
from .google.base import (
    EMAIL_COLLECTION_VERIFIED,
    PASSPHRASE_QUESTION_TITLE,
    FormsClient,
    FormState,
    GoogleApiError,
)  # noqa: F401  (GoogleApiError is caught below)
from .logging_setup import get_logger
from .provenance import explain_google_404, require_usable_form

log = get_logger(__name__)

PART_A = "a"
PART_B = "b"
PARTS: tuple[str, ...] = (PART_A, PART_B)

PART_LABELS = {
    PART_A: "Part A — mid-session passphrase check-in",
    PART_B: "Part B — end-of-session check-in",
}

MANUAL_STEP = (
    "Open the template form, then set:\n"
    "    Settings → Responses → Collect email addresses → Verified\n"
    "This is the one thing the Forms API cannot do reliably (see "
    "docs/google-api-traps.md, trap 2). It takes about 30 seconds and is "
    "needed once per part, not per session."
)


def validate_part(part: str) -> str:
    """Reject a part value before it reaches SQL or a form title."""
    normalized = (part or "").strip().lower()
    if normalized not in PARTS:
        raise ValueError(f"part must be one of {PARTS}, got {part!r}")
    return normalized


def _template_content(part: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Title, description and the ``createItem`` requests for one part's template.

    Part B's template carries the four fields that are always present, and
    **not** the help checkbox. The checkbox's presence is decided per form at
    provisioning time from ``config/help_routing.json``, because a recipient can
    be named — or un-named — long after the template was made, and a form
    provisioned today must reflect today's routing rather than the routing that
    happened to be configured when the template was created.

    The rotating slot is on the template with placeholder wording; provisioning
    retitles it in place, which changes the text without changing the question
    id.
    """
    if part == PART_A:
        return (
            TEMPLATE_TITLE,
            HEADER_NOTICE,
            [
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
                }
            ],
        )

    return (
        form_content_b.TEMPLATE_TITLE,
        form_content_b.HEADER_NOTICE,
        form_content_b.item_requests(
            form_content_b.ROTATING_PLACEHOLDER_TITLE, include_help=False
        ),
    )


@dataclass(frozen=True)
class TemplateRecord:
    """The stored template row, plus whether it is currently usable."""

    template_id: str
    form_id: str
    part: str
    form_url: str | None
    edit_url: str | None
    verified_email_confirmed_at: Any
    last_verified_at: Any
    settings_snapshot: dict[str, Any]

    @property
    def is_verified(self) -> bool:
        return self.verified_email_confirmed_at is not None

    @property
    def label(self) -> str:
        return PART_LABELS.get(self.part, self.part)


def _row_to_record(row: dict[str, Any]) -> TemplateRecord:
    return TemplateRecord(
        template_id=str(row["template_id"]),
        form_id=row["form_id"],
        part=row.get("part") or PART_A,
        form_url=row.get("form_url"),
        edit_url=row.get("edit_url"),
        verified_email_confirmed_at=row.get("verified_email_confirmed_at"),
        last_verified_at=row.get("last_verified_at"),
        settings_snapshot=row.get("settings_snapshot") or {},
    )


def connected_account(conn: psycopg.Connection) -> str | None:
    """The Google address currently connected, for error messages only.

    Naming it turns "Requested entity was not found" into "the account you are
    signed in as cannot see this form", which is the difference between a dead
    end and a next step.
    """
    row = fetch_one(
        conn,
        "select account_email from google_credential where revoked_at is null "
        "order by connected_at desc limit 1",
    )
    return row["account_email"] if row else None


def get_template(conn: psycopg.Connection, part: str = PART_A) -> TemplateRecord | None:
    """The active template for one part, or None if it has not been created."""
    part = validate_part(part)
    row = fetch_one(
        conn,
        """
        select template_id, form_id, part, form_url, edit_url,
               verified_email_confirmed_at, last_verified_at, settings_snapshot
          from form_template
         where is_active and part = %s
         order by created_at desc
         limit 1
        """,
        (part,),
    )
    return _row_to_record(row) if row else None


def all_templates(conn: psycopg.Connection) -> list[TemplateRecord]:
    """Every active template, one per part, in part order.

    Used by the console's setup screen, which shows both parts side by side —
    including the one that has not been created yet, because "Part B does not
    exist" is the state a person most needs to see there.
    """
    rows = fetch_all(
        conn,
        """
        select template_id, form_id, part, form_url, edit_url,
               verified_email_confirmed_at, last_verified_at, settings_snapshot
          from form_template
         where is_active
         order by part, created_at desc
        """,
    )
    return [_row_to_record(row) for row in rows]


def create_template(
    conn: psycopg.Connection, client: FormsClient, part: str = PART_A
) -> TemplateRecord:
    """Create one part's template form and record it as *not yet verified*.

    Idempotent: an existing active template for that part is returned rather
    than a second one created. Two templates for one part would mean two sets of
    settings to keep correct, and only one of them would ever be checked.
    """
    part = validate_part(part)
    existing = get_template(conn, part)
    if existing is not None:
        log.info("template already exists part=%s form_id=%s", part, existing.form_id)
        return existing

    title, description, item_requests = _template_content(part)

    ref = client.create_template(title, description)

    client.batch_update(
        ref.form_id,
        [
            {
                "updateFormInfo": {
                    "info": {"title": title, "description": description},
                    "updateMask": "title,description",
                }
            },
            *item_requests,
        ],
    )

    attempted = try_set_verified_email(client, ref.form_id)

    execute(
        conn,
        """
        insert into form_template (form_id, part, form_url, edit_url, settings_snapshot)
        values (%s, %s, %s, %s, '{}'::jsonb)
        """,
        (ref.form_id, part, ref.responder_url, ref.edit_url),
    )
    log.info(
        "template created part=%s form_id=%s email_collection_set_by_api=%s",
        part,
        ref.form_id,
        attempted,
    )

    record = get_template(conn, part)
    assert record is not None
    return record


def replace_template(
    conn: psycopg.Connection, client: FormsClient, part: str = PART_A
) -> TemplateRecord:
    """Retire the current template for a part and create a fresh one.

    The recovery path for a template that cannot be reached — deleted from
    Drive, owned by another account, or left behind by the demo. The old row is
    deactivated rather than deleted: session forms copied from it still point at
    it through ``session_form.template_id``, and losing that link would make
    "which template did this form come from?" unanswerable.

    Deliberately explicit rather than automatic. A template carries a one-time
    human Verified step, so silently making a new one would quietly un-verify
    email collection — the exact thing trap 2 exists to prevent — while the
    screen still looked fine.
    """
    part = validate_part(part)
    existing = get_template(conn, part)
    if existing is not None:
        execute(
            conn,
            "update form_template set is_active = false where template_id = %s",
            (existing.template_id,),
        )
        log.warning(
            "template retired part=%s form_id=%s; a replacement is being created",
            part,
            existing.form_id,
        )

    record = create_template(conn, client, part)
    log.info("template replaced part=%s form_id=%s", part, record.form_id)
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


def verify_template(
    conn: psycopg.Connection, client: FormsClient, part: str = PART_A
) -> FormState:
    """Read one template's settings back and record the verdict.

    Raises ``TemplateNotVerified`` when the API does not say VERIFIED. On
    failure the stored confirmation is *cleared*, not left stale: a template
    that was verified in September and edited in October must stop being usable
    the moment we notice, not keep its old green tick.
    """
    part = validate_part(part)
    record = get_template(conn, part)
    if record is None:
        raise TemplateNotVerified(
            f"No template form exists for {PART_LABELS[part]} yet. Run "
            f"`cufa template create --part {part}` (or use the console's Template "
            "setup screen) first."
        )

    # Cheap, offline, and catches the common case before Google is asked: a
    # database that still holds the demo's simulated forms, with a real account
    # now connected. Google's answer to that is a bare 404.
    require_usable_form(
        record.form_id,
        client,
        what=f"The stored {PART_LABELS[part]} template form",
        account=connected_account(conn),
    )

    try:
        state = client.read_settings(record.form_id)
    except GoogleApiError as exc:
        if exc.status in (403, 404):
            raise FormUnreachable(
                explain_google_404(
                    record.form_id,
                    client,
                    what=f"The {PART_LABELS[part]} template",
                    account=connected_account(conn),
                )
            ) from exc
        raise

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
            (
                _as_jsonb(snapshot or {"emailCollectionType": state.email_collection_type}),
                record.template_id,
            ),
        )
        log.info(
            "template verified part=%s form_id=%s email_collection=VERIFIED",
            part,
            record.form_id,
        )
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
        (
            _as_jsonb(snapshot or {"emailCollectionType": state.email_collection_type}),
            record.template_id,
        ),
    )
    raise TemplateNotVerified(
        f"{PART_LABELS[part]}: template form {record.form_id} reports "
        f"emailCollectionType={state.email_collection_type!r}, not "
        f"{EMAIL_COLLECTION_VERIFIED!r}.\n\n"
        f"{MANUAL_STEP}\n\n"
        f"Template link: {record.edit_url or record.form_url or '(unknown)'}\n\n"
        "Provisioning is blocked until this reads back as VERIFIED. Forms copied "
        "from an unverified template collect a typed address instead of a "
        "Google-confirmed one, which looks identical until you try to trust it."
    )


def require_verified_template(
    conn: psycopg.Connection, client: FormsClient, part: str = PART_A
) -> TemplateRecord:
    """Re-verify before use and return the template. Never trusts the stored flag."""
    part = validate_part(part)
    verify_template(conn, client, part)
    record = get_template(conn, part)
    assert record is not None
    return record


def _as_jsonb(value: Any) -> str:
    return json.dumps(value)
