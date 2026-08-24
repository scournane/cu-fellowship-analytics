"""The contract every Forms/Drive client implements — real or fake.

Seven methods, chosen so that each documented trap is *observable* through the
interface rather than hidden inside one implementation:

  * ``read_settings`` returns both the email-collection type (trap 2) and the
    publish state (trap 1), because both must be read back and asserted.
  * ``copy_form`` exists because settings survive a Drive copy but cannot be set
    reliably through ``batchUpdate`` (trap 2).
  * ``list_responses`` exists because there is no REST way to link a response
    spreadsheet (trap 3).
  * ``get_form`` exists because ``forms.responses.list`` keys answers by
    ``questionId``, and whether a Drive copy preserves those ids across copies is
    **not verified either way** (trap 5). Ids are therefore read back off the
    form after provisioning rather than assumed, hardcoded, or matched by title.

The fake in ``fake.py`` implements the same seven and can be told to reproduce
each failure — including both possible question-id behaviours — so trap handling
is exercised in tests rather than asserted in a comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Trap 4: exactly these two scopes, no more.
#
#   forms.body  — create a form, update its title/description/questions, and
#                 publish it (forms.create, forms.batchUpdate,
#                 forms.setPublishSettings).
#   drive.file  — two jobs, not one. It authorizes the Drive files.copy that
#                 carries the template's Verified email setting onto each
#                 session form, AND it is what authorizes reading responses:
#                 forms.responses.list accepts drive, drive.file or
#                 forms.responses.readonly, and NOT forms.body. Dropping
#                 drive.file to "tighten" the grant would silently break every
#                 pull, so it is load-bearing twice over.
#
# `drive.file` is sufficient — rather than the far broader `drive` — precisely
# because the app creates the template itself, so the template and every copy of
# it are app-created files inside the app's own scope. Asking for full Drive
# would give this tool reach over a staff member's entire Drive to do a job that
# never needs it.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/drive.file",
)

EMAIL_COLLECTION_VERIFIED = "VERIFIED"
EMAIL_COLLECTION_RESPONDER_INPUT = "RESPONDER_INPUT"
EMAIL_COLLECTION_DO_NOT_COLLECT = "DO_NOT_COLLECT"

# The single question on every check-in form. Matched case-insensitively when
# reading responses back, so an edit to the wording in the UI does not orphan
# the answer.
PASSPHRASE_QUESTION_TITLE = "Today's passphrase"


class GoogleApiError(RuntimeError):
    """An error surfaced by the Google API, carrying the HTTP status.

    Status is kept because the handling genuinely differs: 400 on an
    ``emailCollectionType`` update is trap 2 and must abort provisioning, while
    429 is a rate limit and should be retried with backoff.
    """

    def __init__(self, message: str, *, status: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        return f"[{self.status}] {base}" if self.status else base


@dataclass(frozen=True)
class FormRef:
    """A form that exists, and the two URLs a human needs for it."""

    form_id: str
    responder_url: str
    edit_url: str


@dataclass(frozen=True)
class FormState:
    """Everything about a form that has to be verified rather than assumed."""

    form_id: str
    email_collection_type: str
    is_published: bool
    is_accepting_responses: bool
    title: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def collects_verified_email(self) -> bool:
        """True only when Google itself confirms the address.

        RESPONDER_INPUT means the respondent typed it, which is exactly the
        self-reported identity this whole design exists to replace.
        """
        return self.email_collection_type == EMAIL_COLLECTION_VERIFIED

    @property
    def accepts_responses(self) -> bool:
        """A form must be both published and accepting to record anything."""
        return self.is_published and self.is_accepting_responses


@dataclass(frozen=True)
class FormItem:
    """One question on a form, as ``forms.get`` describes it.

    ``question_id`` is the key ``forms.responses.list`` files answers under, and
    ``index`` is the position this application controlled when it created the
    item. Part B resolves answers by index-assigned slot, never by title: the
    rotating slot's title changes every week and a teacher may edit any of the
    others in the Forms UI without telling anyone.
    """

    item_id: str
    question_id: str
    title: str
    index: int
    kind: str = "text"


@dataclass(frozen=True)
class FormDefinition:
    """A form's structure, read back after provisioning to record its ids."""

    form_id: str
    title: str
    items: tuple[FormItem, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def by_index(self) -> dict[int, FormItem]:
        return {item.index: item for item in self.items}


@dataclass(frozen=True)
class FormResponse:
    """One submitted response, as returned by ``forms.responses.list``.

    ``submitted_at`` is RFC3339 UTC straight from the API — the reason this path
    is preferred over a linked spreadsheet, which writes locale-formatted times
    with no offset marker.

    Two views of the same answers. ``answers`` is keyed by question *title* and
    is what Part A's single-question form uses. ``answers_by_id`` is keyed by
    ``questionId``, which is what the API actually returns and what Part B
    resolves through ``form_question_map``. Titles are lossy — two items can
    share one, and an edit in the Forms UI changes one — so the id-keyed view is
    the authoritative one wherever a form has more than one question.
    """

    response_id: str
    respondent_email: str
    submitted_at: str
    answers: dict[str, str] = field(default_factory=dict)
    answers_by_id: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponsePage:
    """One page of responses plus the token for the next, if any."""

    responses: tuple[FormResponse, ...]
    next_page_token: str | None = None


@runtime_checkable
class FormsClient(Protocol):
    """The seven calls this system makes against Google."""

    #: Whether this client simulates Google rather than calling it. Read by
    #: ``cufa.provenance`` to catch a stored form id that belongs to the other
    #: kind of client — the demo's simulated forms sitting in a database a real
    #: account has since been connected to, or the reverse. Both otherwise
    #: surface as a bare 404 that explains nothing.
    is_fake: bool

    def create_template(self, title: str, description: str = "") -> FormRef:
        """Create the one template form all session forms are copied from."""
        ...

    def get_form(self, form_id: str) -> FormDefinition:
        """Read a form's items back, with the question ids the API assigned.

        Called after provisioning a Part B form so the ``questionId`` -> slot
        mapping can be recorded. Never skipped and never cached across a
        ``batchUpdate``: a copy may or may not preserve ids, and assuming either
        way produces answers filed under the wrong field with no error.
        """
        ...

    def read_settings(self, form_id: str) -> FormState:
        """Read a form's settings and publish state back from the API.

        The only source of truth for traps 1 and 2. Never infer either from the
        fact that a previous call returned 200.
        """
        ...

    def copy_form(self, source_form_id: str, new_title: str) -> FormRef:
        """Drive-copy the template. Copying preserves email-collection settings."""
        ...

    def batch_update(self, form_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply batchUpdate requests — title, description, question text only."""
        ...

    def set_publish_settings(
        self, form_id: str, *, is_published: bool = True, is_accepting_responses: bool = True
    ) -> dict[str, Any]:
        """Publish a form. Required since 2026-07-01; see trap 1."""
        ...

    def list_responses(
        self,
        form_id: str,
        *,
        response_filter: str | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
    ) -> ResponsePage:
        """One page of responses, optionally filtered by ``timestamp > ...``."""
        ...
