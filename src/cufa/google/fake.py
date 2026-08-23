"""An in-memory stand-in for Forms + Drive that can reproduce each trap.

The point of this class is not to let tests run offline — that is a side
effect. The point is that trap handling is only trustworthy if the failures are
*exercised*. A comment saying "we check the publish state" proves nothing; a
fake that hands back an unpublished form and a test that asserts provisioning
refuses it proves something.

Defaults reproduce Google's real behaviour as of August 2026:

  * a newly created form is **unpublished** and accepts no responses,
  * ``batchUpdate`` → ``updateSettings`` → ``emailCollectionType`` is
    **rejected with 400**,
  * a Drive copy **preserves** the source form's settings.

So the happy path through this fake is only reachable by code that handles the
traps correctly.
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..timeutil import parse_rfc3339
from .base import (
    EMAIL_COLLECTION_DO_NOT_COLLECT,
    EMAIL_COLLECTION_RESPONDER_INPUT,
    EMAIL_COLLECTION_VERIFIED,
    PASSPHRASE_QUESTION_TITLE,
    FormRef,
    FormResponse,
    FormState,
    GoogleApiError,
    ResponsePage,
)


@dataclass
class _FakeForm:
    form_id: str
    title: str
    description: str = ""
    email_collection_type: str = EMAIL_COLLECTION_DO_NOT_COLLECT
    is_published: bool = False
    is_accepting_responses: bool = False
    question_title: str = PASSPHRASE_QUESTION_TITLE
    responses: list[FormResponse] = field(default_factory=list)


class FakeGoogleClient:
    """Implements ``FormsClient`` against a dictionary.

    Every knob below turns on one specific real failure. They are constructor
    arguments rather than monkeypatches so a test reads as a description of the
    scenario it covers.
    """

    def __init__(
        self,
        *,
        # Trap 1: publishing silently does not take effect.
        publish_readback_fails: bool = False,
        # Trap 2: what a freshly copied/created form reports for email collection.
        # Google's real default for an API-created form is DO_NOT_COLLECT; the
        # human flips the template to VERIFIED by hand.
        default_email_collection: str = EMAIL_COLLECTION_DO_NOT_COLLECT,
        # Trap 2: batchUpdate rejects emailCollectionType. True mirrors reality.
        reject_email_collection: bool = True,
        # Pagination granularity for list_responses.
        page_size: int = 2,
        # Raise 429 on this many list_responses calls before succeeding.
        rate_limit_calls: int = 0,
        # Raise on the Nth list_responses call (1-based) to test watermark safety.
        fail_on_response_page: int | None = None,
        # When set, state is written here after every mutating call so the demo
        # can drive the fake across separate `cufa` processes the same way it
        # would drive the real API.
        state_path: str | Path | None = None,
    ) -> None:
        self.forms: dict[str, _FakeForm] = {}
        self.call_log: list[tuple[str, dict[str, Any]]] = []
        self.state_path = Path(state_path) if state_path else None

        self.publish_readback_fails = publish_readback_fails
        self.default_email_collection = default_email_collection
        self.reject_email_collection = reject_email_collection
        self.page_size = max(1, page_size)
        self.rate_limit_calls = rate_limit_calls
        self.fail_on_response_page = fail_on_response_page

        self._next_id = 1
        self._list_calls = 0

    # -- helpers used by tests and the demo ---------------------------------

    def _record(self, action: str, **details: Any) -> None:
        self.call_log.append((action, details))
        if self.state_path is not None and action != "read_settings":
            self.save()

    def calls(self, action: str) -> list[dict[str, Any]]:
        """Every recorded call of one kind, for assertions like 'publish was called'."""
        return [details for name, details in self.call_log if name == action]

    def _new_id(self) -> str:
        form_id = f"fake-form-{self._next_id:04d}"
        self._next_id += 1
        return form_id

    def _get(self, form_id: str) -> _FakeForm:
        try:
            return self.forms[form_id]
        except KeyError:
            raise GoogleApiError(f"form {form_id} not found", status=404) from None

    def simulate_human_sets_verified(self, form_id: str) -> None:
        """Stand in for the one manual step: a human flips email collection.

        This is the step the API cannot do reliably (trap 2), so the fake cannot
        do it either as a side effect of any API call — only explicitly, the way
        a person would.
        """
        self._get(form_id).email_collection_type = EMAIL_COLLECTION_VERIFIED
        self.save()

    def simulate_human_breaks_verified(self, form_id: str) -> None:
        """Someone edits the template and turns email collection back down."""
        self._get(form_id).email_collection_type = EMAIL_COLLECTION_RESPONDER_INPUT
        self.save()

    def seed_responses(
        self,
        form_id: str,
        rows: list[tuple[str, str, str]] | list[dict[str, Any]],
    ) -> None:
        """Load responses as ``(email, rfc3339_timestamp, passphrase)`` triples."""
        form = self._get(form_id)
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                email = row["email"]
                submitted_at = row["submitted_at"]
                answers = dict(row.get("answers") or {})
                if "passphrase" in row:
                    answers.setdefault(form.question_title, row["passphrase"])
            else:
                email, submitted_at, passphrase = row
                answers = {form.question_title: passphrase}
            form.responses.append(
                FormResponse(
                    response_id=f"{form_id}-resp-{len(form.responses) + index:04d}",
                    respondent_email=email,
                    submitted_at=submitted_at,
                    answers=answers,
                )
            )
        # The API returns responses oldest-first; keeping that order here means
        # watermark logic is exercised the same way it will be in production.
        form.responses.sort(key=lambda r: r.submitted_at)
        self.save()

    # -- persistence --------------------------------------------------------
    #
    # A fake that forgets everything when the process exits could not stand in
    # for Google across a multi-command demo. State lives in one JSON file so
    # `cufa provision`, `cufa pull` and the console — three separate processes —
    # see the same forms, exactly as they would see the same real forms.

    def to_dict(self) -> dict[str, Any]:
        return {
            "next_id": self._next_id,
            "forms": {
                form_id: {
                    "form_id": form.form_id,
                    "title": form.title,
                    "description": form.description,
                    "email_collection_type": form.email_collection_type,
                    "is_published": form.is_published,
                    "is_accepting_responses": form.is_accepting_responses,
                    "question_title": form.question_title,
                    "responses": [
                        {
                            "response_id": r.response_id,
                            "respondent_email": r.respondent_email,
                            "submitted_at": r.submitted_at,
                            "answers": r.answers,
                        }
                        for r in form.responses
                    ],
                }
                for form_id, form in self.forms.items()
            },
        }

    def load_dict(self, payload: dict[str, Any]) -> None:
        self.forms = {}
        for form_id, data in (payload.get("forms") or {}).items():
            self.forms[form_id] = _FakeForm(
                form_id=data["form_id"],
                title=data.get("title", ""),
                description=data.get("description", ""),
                email_collection_type=data.get(
                    "email_collection_type", EMAIL_COLLECTION_DO_NOT_COLLECT
                ),
                is_published=bool(data.get("is_published", False)),
                is_accepting_responses=bool(data.get("is_accepting_responses", False)),
                question_title=data.get("question_title", PASSPHRASE_QUESTION_TITLE),
                responses=[
                    FormResponse(
                        response_id=r["response_id"],
                        respondent_email=r["respondent_email"],
                        submitted_at=r["submitted_at"],
                        answers=dict(r.get("answers") or {}),
                    )
                    for r in (data.get("responses") or [])
                ],
            )
        self._next_id = int(payload.get("next_id", 1))

    def save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a crash mid-write must not leave a truncated file
        # that the next command reads as "no forms exist".
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(self.state_path)

    @classmethod
    def restore(cls, state_path: str | Path, **kwargs: Any) -> "FakeGoogleClient":
        """Load persisted state, or start fresh if there is none yet."""
        path = Path(state_path)
        client = cls(state_path=path, **kwargs)
        if path.exists():
            client.load_dict(json.loads(path.read_text(encoding="utf-8")))
        return client

    # -- FormsClient --------------------------------------------------------

    def create_template(self, title: str, description: str = "") -> FormRef:
        form_id = self._new_id()
        self.forms[form_id] = _FakeForm(
            form_id=form_id,
            title=title,
            description=description,
            email_collection_type=self.default_email_collection,
            # Trap 1 in its default form: created unpublished.
            is_published=False,
            is_accepting_responses=False,
        )
        self._record("create_template", form_id=form_id, title=title)
        return FormRef(
            form_id=form_id,
            responder_url=f"https://forms.example.invalid/d/e/{form_id}/viewform",
            edit_url=f"https://forms.example.invalid/d/{form_id}/edit",
        )

    def read_settings(self, form_id: str) -> FormState:
        form = self._get(form_id)
        self._record("read_settings", form_id=form_id)

        published = form.is_published
        accepting = form.is_accepting_responses
        if self.publish_readback_fails:
            # The call returned 200, the state did not change. This is exactly
            # what "fails silently" means, and why the read-back exists.
            published = False
            accepting = False

        return FormState(
            form_id=form_id,
            email_collection_type=form.email_collection_type,
            is_published=published,
            is_accepting_responses=accepting,
            title=form.title,
            raw={
                "formId": form_id,
                "info": {"title": form.title, "description": form.description},
                "settings": {"emailCollectionType": form.email_collection_type},
                "publishSettings": {
                    "publishState": {
                        "isPublished": published,
                        "isAcceptingResponses": accepting,
                    }
                },
            },
        )

    def copy_form(self, source_form_id: str, new_title: str) -> FormRef:
        source = self._get(source_form_id)
        form_id = self._new_id()
        self.forms[form_id] = _FakeForm(
            form_id=form_id,
            title=new_title,
            description=source.description,
            # The property that makes template-and-copy work at all.
            email_collection_type=source.email_collection_type,
            is_published=False,
            is_accepting_responses=False,
            question_title=source.question_title,
        )
        self._record("copy_form", source_form_id=source_form_id, form_id=form_id, title=new_title)
        return FormRef(
            form_id=form_id,
            responder_url=f"https://forms.example.invalid/d/e/{form_id}/viewform",
            edit_url=f"https://forms.example.invalid/d/{form_id}/edit",
        )

    def batch_update(self, form_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        form = self._get(form_id)
        self._record("batch_update", form_id=form_id, requests=json.loads(json.dumps(requests)))

        for index, request in enumerate(requests):
            if "updateFormInfo" in request:
                info = request["updateFormInfo"].get("info", {})
                if "title" in info:
                    form.title = info["title"]
                if "description" in info:
                    form.description = info["description"]
            elif "updateSettings" in request:
                settings = request["updateSettings"].get("settings", {})
                if "emailCollectionType" in settings:
                    if self.reject_email_collection:
                        # Trap 2, verbatim in shape: a 400 naming the field path.
                        raise GoogleApiError(
                            "Invalid JSON payload received. Unknown value at "
                            f"requests[{index}].update_settings.settings.email_collection_type",
                            status=400,
                            reason="INVALID_ARGUMENT",
                        )
                    form.email_collection_type = settings["emailCollectionType"]
            elif "updateItem" in request:
                item = request["updateItem"].get("item", {})
                if "title" in item:
                    form.question_title = item["title"]
            elif "createItem" in request:
                item = request["createItem"].get("item", {})
                if "title" in item:
                    form.question_title = item["title"]

        return {"form": {"formId": form_id}}

    def set_publish_settings(
        self, form_id: str, *, is_published: bool = True, is_accepting_responses: bool = True
    ) -> dict[str, Any]:
        form = self._get(form_id)
        self._record(
            "set_publish_settings",
            form_id=form_id,
            is_published=is_published,
            is_accepting_responses=is_accepting_responses,
        )
        form.is_published = is_published
        form.is_accepting_responses = is_accepting_responses
        return {
            "publishState": {
                "isPublished": is_published,
                "isAcceptingResponses": is_accepting_responses,
            }
        }

    def list_responses(
        self,
        form_id: str,
        *,
        response_filter: str | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
    ) -> ResponsePage:
        form = self._get(form_id)
        self._list_calls += 1
        self._record(
            "list_responses",
            form_id=form_id,
            response_filter=response_filter,
            page_token=page_token,
        )

        if self.rate_limit_calls > 0:
            self.rate_limit_calls -= 1
            raise GoogleApiError("Quota exceeded", status=429, reason="RESOURCE_EXHAUSTED")

        if self.fail_on_response_page is not None and self._list_calls == self.fail_on_response_page:
            raise GoogleApiError("Backend error", status=503, reason="UNAVAILABLE")

        rows = list(form.responses)
        if response_filter:
            cutoff = _parse_timestamp_filter(response_filter)
            if cutoff is not None:
                rows = [r for r in rows if parse_rfc3339(r.submitted_at) > cutoff]

        offset = int(page_token) if page_token else 0
        limit = page_size or self.page_size
        page = rows[offset : offset + limit]
        next_token = str(offset + limit) if offset + limit < len(rows) else None
        return ResponsePage(responses=tuple(page), next_page_token=next_token)


def _parse_timestamp_filter(expression: str) -> datetime | None:
    """Parse ``timestamp > 2026-09-15T17:00:00Z`` the way the real API does."""
    text = expression.strip()
    if not text.lower().startswith("timestamp"):
        return None
    _, _, remainder = text.partition(">")
    remainder = remainder.strip().strip("\"'")
    if not remainder:
        return None
    try:
        return parse_rfc3339(remainder)
    except ValueError:
        return None


def demo_client(state_path: str | Path | None = None, **kwargs: Any) -> FakeGoogleClient:
    """A fake pre-walked through the one-time setup, for `make demo`.

    Creates the template and performs the human's manual Verified step, so the
    demo starts where a real CU install starts on day two.
    """
    client = FakeGoogleClient(state_path=state_path, **kwargs)
    ref = client.create_template("CU Check-in Template", "Template — do not submit")
    client.simulate_human_sets_verified(ref.form_id)
    return client


__all__ = ["FakeGoogleClient", "demo_client"]
