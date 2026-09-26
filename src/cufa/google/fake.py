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
  * a Drive copy **preserves** the source form's settings,
  * ``batchUpdate`` is **atomic** — one rejected request and none apply,
  * ``createItem`` **validates** the item the way the live API does: a choice
    question with no options, an empty option, a dropdown with "Other", a
    scale outside 0/1..2-10, a file-upload question — each is a 400. Part A's
    questions are staff-written, so a fake that accepted anything would let a
    form that Google refuses sail through every test,
  * items are stored as **full bodies**, and section breaks and text blocks
    are items *without* a question id that still occupy an index — exactly the
    shape that makes "match by title" or "count the questions" wrong.

So the happy path through this fake is only reachable by code that handles the
traps correctly.

One knob is different in kind from the others. ``question_id_scheme`` decides
whether a Drive copy preserves question ids or mints new ones, because **which
of those Google actually does is not verified**. It is not a failure mode to be
switched on for one test — both settings are equally plausible descriptions of
reality, and the mapping logic has to be correct under either, so the suite runs
the same assertions twice.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..timeutil import parse_rfc3339
from .base import (
    EMAIL_COLLECTION_DO_NOT_COLLECT,
    EMAIL_COLLECTION_RESPONDER_INPUT,
    EMAIL_COLLECTION_VERIFIED,
    FormDefinition,
    FormItem,
    FormRef,
    FormResponse,
    FormState,
    GoogleApiError,
    ResponsePage,
)

#: How a Drive copy treats question ids.
#:
#: ``preserve``    — the copy keeps the source form's question ids.
#: ``regenerate``  — the copy mints new ones.
#:
#: Measured against a live account in August 2026: Google **preserves** them, so
#: ``preserve`` is the default here. Both are still implemented and both are
#: still tested — one measurement is evidence about current behaviour rather
#: than a guarantee, and code that reads the ids back off each form is correct
#: under either. Code that assumes one is silently wrong under the other, and
#: the wrongness looks like plausible data.
#:
#: Note which way round the risk runs. Under ``preserve`` every form copied from
#: one template answers under the SAME ids, so a map keyed on question id alone
#: would merge every week's rotating question into one entry — and look right
#: doing it. That is why the map is keyed per form.
QUESTION_IDS_PRESERVED = "preserve"
QUESTION_IDS_REGENERATED = "regenerate"

#: The item kinds a Forms ``Item`` can be. Exactly one per item.
_ITEM_KINDS = (
    "questionItem", "questionGroupItem", "pageBreakItem", "textItem", "imageItem", "videoItem",
)
#: The question kinds a ``Question`` can be. Exactly one per question.
_QUESTION_KINDS = (
    "textQuestion", "choiceQuestion", "scaleQuestion", "dateQuestion", "timeQuestion",
    "fileUploadQuestion", "ratingQuestion", "rowQuestion",
)
_CHOICE_TYPES = ("RADIO", "CHECKBOX", "DROP_DOWN")


def _bad_request(message: str) -> GoogleApiError:
    return GoogleApiError(message, status=400, reason="INVALID_ARGUMENT")


@dataclass
class _FakeItem:
    """One item on a fake form: an id plus the full Forms ``Item`` body.

    The body is stored whole — ``questionItem`` with its options and scale,
    ``pageBreakItem``, ``textItem`` — because the real API returns it whole and
    provisioning reads it back to check what it created.
    """

    item_id: str
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.body.get("title", "")

    @title.setter
    def title(self, value: str) -> None:
        self.body["title"] = value

    @property
    def description(self) -> str:
        return self.body.get("description", "")

    @property
    def question(self) -> dict[str, Any] | None:
        question = (self.body.get("questionItem") or {}).get("question")
        return question if isinstance(question, dict) else None

    @property
    def question_id(self) -> str | None:
        question = self.question
        return question.get("questionId") if question else None

    def all_question_ids(self) -> list[str]:
        """Question ids answerable on this item: one, or one per grid row."""
        if self.question_id:
            return [self.question_id]
        group = self.body.get("questionGroupItem") or {}
        return [q["questionId"] for q in group.get("questions") or [] if q.get("questionId")]

    @property
    def kind(self) -> str:
        question = self.question or {}
        if "scaleQuestion" in question:
            return "scale"
        if "choiceQuestion" in question:
            return "choice"
        return "text"

    def as_api(self) -> dict[str, Any]:
        return {"itemId": self.item_id, **copy.deepcopy(self.body)}


@dataclass
class _FakeResponse:
    """A submission, stored the way the API returns it: keyed by question id,
    every value kept separately."""

    response_id: str
    respondent_email: str
    submitted_at: str
    answers: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _FakeForm:
    form_id: str
    title: str
    description: str = ""
    email_collection_type: str = EMAIL_COLLECTION_DO_NOT_COLLECT
    is_published: bool = False
    is_accepting_responses: bool = False
    items: list[_FakeItem] = field(default_factory=list)
    responses: list[_FakeResponse] = field(default_factory=list)

    def item_at(self, index: int) -> _FakeItem | None:
        return self.items[index] if 0 <= index < len(self.items) else None

    def question_items(self) -> list[_FakeItem]:
        return [item for item in self.items if item.question_id]

    def titles(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for item in self.items:
            if item.question_id:
                mapping[item.question_id] = item.title
            for row in (item.body.get("questionGroupItem") or {}).get("questions") or []:
                if row.get("questionId"):
                    row_title = (row.get("rowQuestion") or {}).get("title", "")
                    mapping[row["questionId"]] = f"{item.title} [{row_title}]"
        return mapping


def _as_values(value: Any) -> list[str]:
    """A seeded answer as the list of values the API would carry."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


class FakeGoogleClient:
    """Implements ``FormsClient`` against a dictionary.

    Every knob below turns on one specific real behaviour. They are constructor
    arguments rather than monkeypatches so a test reads as a description of the
    scenario it covers.
    """

    #: Read by ``cufa.provenance``: every form this client mints carries a
    #: ``fake-form-`` id, and asking Google for one of those returns a 404 that
    #: says nothing about where it came from.
    is_fake = True

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
        # Trap 5: whether files.copy preserves question ids. Unverified in
        # reality, so tests run both.
        question_id_scheme: str = QUESTION_IDS_PRESERVED,
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
        if question_id_scheme not in (QUESTION_IDS_PRESERVED, QUESTION_IDS_REGENERATED):
            raise ValueError(
                f"question_id_scheme must be {QUESTION_IDS_PRESERVED!r} or "
                f"{QUESTION_IDS_REGENERATED!r}, got {question_id_scheme!r}"
            )
        self.question_id_scheme = question_id_scheme
        self.page_size = max(1, page_size)
        self.rate_limit_calls = rate_limit_calls
        self.fail_on_response_page = fail_on_response_page

        self._next_id = 1
        self._next_question = 1
        self._list_calls = 0

    # -- helpers used by tests and the demo ---------------------------------

    def _record(self, action: str, **details: Any) -> None:
        self.call_log.append((action, details))
        if self.state_path is not None and action not in ("read_settings", "get_form"):
            self.save()

    def calls(self, action: str) -> list[dict[str, Any]]:
        """Every recorded call of one kind, for assertions like 'publish was called'."""
        return [details for name, details in self.call_log if name == action]

    def _new_id(self) -> str:
        form_id = f"fake-form-{self._next_id:04d}"
        self._next_id += 1
        return form_id

    def _new_question_id(self) -> str:
        question_id = f"q{self._next_question:05x}"
        self._next_question += 1
        return question_id

    def _get(self, form_id: str) -> _FakeForm:
        try:
            return self.forms[form_id]
        except KeyError:
            raise GoogleApiError(f"form {form_id} not found", status=404) from None

    def _stamp_ids(self, body: dict[str, Any]) -> dict[str, Any]:
        """Give every question in a new item body a fresh question id."""
        body = copy.deepcopy(body)
        body.pop("itemId", None)
        question = (body.get("questionItem") or {}).get("question")
        if isinstance(question, dict):
            question["questionId"] = self._new_question_id()
        for row in (body.get("questionGroupItem") or {}).get("questions") or []:
            row["questionId"] = self._new_question_id()
        return body

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

    def simulate_teacher_retitles(self, form_id: str, index: int, title: str) -> None:
        """A teacher edits a question's wording in the Forms UI.

        Question ids do not change when the text does — which is exactly why
        answers are resolved by id and slots are assigned by index, never by
        matching the title back.
        """
        item = self._get(form_id).item_at(index)
        if item is None:
            raise GoogleApiError(f"no item at index {index}", status=400)
        item.title = title
        self.save()

    def simulate_form_created_by_hand(
        self,
        items: list[dict[str, Any]],
        *,
        title: str = "Exit Ticket",
        description: str = "",
    ) -> str:
        """A form somebody built in the Forms UI, for the importer's tests.

        Deliberately NOT validated the way ``createItem`` is: the Forms UI can
        make things the API cannot (a file-upload question, a grid, an image),
        and those are precisely the items an importer has to cope with. Returns
        the new form's id.
        """
        form_id = self._new_id()
        self.forms[form_id] = _FakeForm(
            form_id=form_id,
            title=title,
            description=description,
            email_collection_type=self.default_email_collection,
            items=[
                _FakeItem(item_id=self._new_question_id(), body=self._stamp_ids(item))
                for item in items
            ],
        )
        self._record("simulate_form_created_by_hand", form_id=form_id, items=len(items))
        return form_id

    def _append_response(
        self, form: _FakeForm, email: str, submitted_at: str, answers: dict[str, list[str]]
    ) -> None:
        form.responses.append(
            _FakeResponse(
                response_id=f"{form.form_id}-resp-{len(form.responses):04d}",
                respondent_email=email,
                submitted_at=submitted_at,
                answers=answers,
            )
        )

    def _sort_responses(self, form: _FakeForm) -> None:
        # The API returns responses oldest-first; keeping that order here means
        # watermark logic is exercised the same way it will be in production.
        form.responses.sort(key=lambda r: parse_rfc3339(r.submitted_at))

    def seed_responses(
        self,
        form_id: str,
        rows: list[tuple[str, str, str]] | list[dict[str, Any]],
    ) -> None:
        """Load responses into a form (the older, convenience shape).

        Accepts, in decreasing order of convenience:

        * ``(email, rfc3339_timestamp, answer)`` triples — ``answer`` is the
          reply to the form's first question.
        * ``{"email", "submitted_at", "answers_by_index": {0: "5", 1: "..."}}`` —
          indexed rather than keyed by question id so a fixture does not have to
          know which id scheme the copy used.
        * ``{"email", "submitted_at", "answers": {title: value}}`` or
          ``"answers_by_id"`` — when a test wants to be explicit.

        A value may be a string or a list of strings. New Part A fixtures should
        prefer ``seed_answers``, which is keyed by question id and never guesses.
        """
        form = self._get(form_id)
        by_title = {item.title: item.question_id for item in form.question_items()}
        first = form.question_items()[0].question_id if form.question_items() else None

        for row in rows:
            answers: dict[str, list[str]] = {}
            if isinstance(row, dict):
                email = row["email"]
                submitted_at = row["submitted_at"]
                for question_id, value in (row.get("answers_by_id") or {}).items():
                    answers[question_id] = _as_values(value)
                for slot_index, value in (row.get("answers_by_index") or {}).items():
                    item = form.item_at(int(slot_index))
                    if item is not None and item.question_id:
                        answers[item.question_id] = _as_values(value)
                for title, value in (row.get("answers") or {}).items():
                    question_id = by_title.get(title)
                    if question_id is not None:
                        answers[question_id] = _as_values(value)
            else:
                email, submitted_at, answer = row
                if first is not None:
                    answers[first] = _as_values(answer)
            self._append_response(form, email, submitted_at, answers)

        self._sort_responses(form)
        self.save()

    def seed_answers(self, form_id: str, rows: list[dict[str, Any]]) -> None:
        """Load responses keyed by question id, the way the API stores them.

        ``rows`` is ``[{"respondent_email": str, "create_time": RFC3339,
        "answers": {question_id: [str, ...]}}]``. Resolve question ids through
        ``part_a_form_question`` first — which id a question has depends on the
        copy, and no fixture may know that in advance.

        Stricter than ``seed_responses`` on purpose: an id that is not on the
        form raises, because the real API would never return one and a fixture
        that invents one is testing nothing. An empty list is left out, the way
        the API leaves out a question somebody skipped.
        """
        form = self._get(form_id)
        known = {qid for item in form.items for qid in item.all_question_ids()}
        for row in rows:
            answers: dict[str, list[str]] = {}
            for question_id, values in (row.get("answers") or {}).items():
                if question_id not in known:
                    raise ValueError(
                        f"question id {question_id!r} is not on form {form_id}; "
                        "resolve question keys through part_a_form_question"
                    )
                listed = _as_values(values)
                if listed:
                    answers[question_id] = listed
            self._append_response(form, row["respondent_email"], row["create_time"], answers)
        self._sort_responses(form)
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
            "next_question": self._next_question,
            "question_id_scheme": self.question_id_scheme,
            "forms": {
                form_id: {
                    "form_id": form.form_id,
                    "title": form.title,
                    "description": form.description,
                    "email_collection_type": form.email_collection_type,
                    "is_published": form.is_published,
                    "is_accepting_responses": form.is_accepting_responses,
                    "items": [
                        {"item_id": item.item_id, "body": item.body} for item in form.items
                    ],
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
            items = [_load_item(item) for item in (data.get("items") or [])]
            responses = [
                _FakeResponse(
                    response_id=r["response_id"],
                    respondent_email=r["respondent_email"],
                    submitted_at=r["submitted_at"],
                    answers=(
                        {k: _as_values(v) for k, v in (r.get("answers") or {}).items()}
                        if "answers" in r
                        # State written before answers kept every value.
                        else {k: _as_values(v) for k, v in (r.get("answers_by_id") or {}).items()}
                    ),
                )
                for r in (data.get("responses") or [])
            ]
            self.forms[form_id] = _FakeForm(
                form_id=data["form_id"],
                title=data.get("title", ""),
                description=data.get("description", ""),
                email_collection_type=data.get(
                    "email_collection_type", EMAIL_COLLECTION_DO_NOT_COLLECT
                ),
                is_published=bool(data.get("is_published", False)),
                is_accepting_responses=bool(data.get("is_accepting_responses", False)),
                items=items,
                responses=responses,
            )
        self._next_id = int(payload.get("next_id", 1))
        self._next_question = int(payload.get("next_question", 1))

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

    def get_form(self, form_id: str) -> FormDefinition:
        """The form's items, with the ids ``list_responses`` will use.

        Mirrors ``RealGoogleClient.get_form``: items without a question id
        (section breaks, text blocks, grids) are skipped, but the index recorded
        on each ``FormItem`` is its position among *all* items.
        """
        form = self._get(form_id)
        self._record("get_form", form_id=form_id)
        items = tuple(
            FormItem(
                item_id=item.item_id,
                question_id=item.question_id,
                title=item.title,
                index=index,
                kind=item.kind,
                description=item.description,
                required=bool((item.question or {}).get("required", False)),
                question=copy.deepcopy(item.question or {}),
            )
            for index, item in enumerate(form.items)
            if item.question_id
        )
        return FormDefinition(
            form_id=form_id,
            title=form.title,
            items=items,
            raw={
                "formId": form_id,
                "info": {"title": form.title, "description": form.description},
                "items": [item.as_api() for item in form.items],
            },
        )

    def copy_form(self, source_form_id: str, new_title: str) -> FormRef:
        source = self._get(source_form_id)
        form_id = self._new_id()

        # The whole reason ``question_id_scheme`` exists. Under `preserve`, the
        # copy answers under the same ids as the template; under `regenerate` it
        # answers under new ones. Anything that hardcoded a template's ids, or
        # cached them across a copy, is wrong under exactly one of these and
        # produces answers filed against the wrong field with no error.
        preserve = self.question_id_scheme == QUESTION_IDS_PRESERVED
        items = [
            _FakeItem(item_id=item.item_id, body=copy.deepcopy(item.body))
            if preserve
            else _FakeItem(item_id=self._new_question_id(), body=self._stamp_ids(item.body))
            for item in source.items
        ]

        self.forms[form_id] = _FakeForm(
            form_id=form_id,
            title=new_title,
            description=source.description,
            # The property that makes template-and-copy work at all.
            email_collection_type=source.email_collection_type,
            is_published=False,
            is_accepting_responses=False,
            items=items,
        )
        self._record(
            "copy_form",
            source_form_id=source_form_id,
            form_id=form_id,
            title=new_title,
            question_id_scheme=self.question_id_scheme,
        )
        return FormRef(
            form_id=form_id,
            responder_url=f"https://forms.example.invalid/d/e/{form_id}/viewform",
            edit_url=f"https://forms.example.invalid/d/{form_id}/edit",
        )

    def batch_update(self, form_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply a batch the way Google does: validated, and all-or-nothing.

        Every request is applied to a staged copy of the form; the copy replaces
        the form only if every request succeeded. A caller that assumed the
        first half of a failed batch had landed would be wrong against the real
        API, and would be wrong here too.
        """
        form = self._get(form_id)
        self._record("batch_update", form_id=form_id, requests=json.loads(json.dumps(requests)))

        title, description = form.title, form.description
        email_collection = form.email_collection_type
        items = [copy.deepcopy(item) for item in form.items]
        replies: list[dict[str, Any]] = []

        for index, request in enumerate(requests):
            where = f"requests[{index}]"
            if "updateFormInfo" in request:
                info = request["updateFormInfo"].get("info", {})
                mask = _mask(request["updateFormInfo"].get("updateMask"), info)
                if "title" in mask or "*" in mask:
                    title = info.get("title", "")
                if "description" in mask or "*" in mask:
                    description = info.get("description", "")
                replies.append({})
            elif "updateSettings" in request:
                settings = request["updateSettings"].get("settings", {})
                if "emailCollectionType" in settings:
                    if self.reject_email_collection:
                        # Trap 2, verbatim in shape: a 400 naming the field path.
                        raise _bad_request(
                            "Invalid JSON payload received. Unknown value at "
                            f"{where}.update_settings.settings.email_collection_type"
                        )
                    email_collection = settings["emailCollectionType"]
                replies.append({})
            elif "updateItem" in request:
                item = request["updateItem"].get("item", {})
                at = int((request["updateItem"].get("location") or {}).get("index", 0))
                target = items[at] if 0 <= at < len(items) else None
                if target is None:
                    # An update against an index that does not exist is a 400 in
                    # the real API, not a silent create.
                    raise _bad_request(f"no item at index {at} on form {form_id}")
                if target.question is not None and "questionItem" not in item:
                    # Verbatim in shape from the live API. An item body without
                    # a questionItem reads as "turn this question into a text
                    # block", whatever the updateMask says — so sending only the
                    # field being changed is rejected. This cost a real Part B
                    # provisioning run, because the offline fake used to accept
                    # it happily.
                    raise _bad_request(
                        f"Invalid {where}: A QuestionItem or QuestionGroupItem cannot "
                        "be changed into a non question Item type by an Update operation."
                    )
                question_id = target.question_id
                for path in _mask(request["updateItem"].get("updateMask"), item):
                    if path == "*":
                        target.body = copy.deepcopy({k: v for k, v in item.items() if k != "itemId"})
                    else:
                        _apply_path(target.body, item, path)
                if question_id and target.question is not None:
                    # An update never changes a question's id.
                    target.question["questionId"] = question_id
                replies.append({})
            elif "deleteItem" in request:
                at = int((request["deleteItem"].get("location") or {}).get("index", -1))
                if not 0 <= at < len(items):
                    raise _bad_request(
                        f"Invalid {where}.deleteItem: no item at index {at} "
                        f"(the form has {len(items)})."
                    )
                items.pop(at)
                replies.append({})
            elif "createItem" in request:
                item = request["createItem"].get("item", {})
                at = (request["createItem"].get("location") or {}).get("index")
                position = len(items) if at is None else int(at)
                if not 0 <= position <= len(items):
                    raise _bad_request(
                        f"Invalid {where}.createItem: index {position} is out of range "
                        f"(the form has {len(items)} items)."
                    )
                _validate_new_item(item, f"{where}.createItem")
                created = _FakeItem(item_id=self._new_question_id(), body=self._stamp_ids(item))
                items.insert(position, created)
                replies.append(
                    {"createItem": {"itemId": created.item_id,
                                    "questionId": created.all_question_ids()}}
                )
            else:
                raise _bad_request(f"Invalid {where}: unsupported request {sorted(request)}.")

        form.title, form.description = title, description
        form.email_collection_type = email_collection
        form.items = items
        self.save()
        return {"form": {"formId": form_id}, "replies": replies}

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
        self.save()
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

        titles = form.titles()
        responses = tuple(
            FormResponse(
                response_id=r.response_id,
                respondent_email=r.respondent_email,
                submitted_at=r.submitted_at,
                # Joined but not stripped: a single value comes back verbatim,
                # whitespace included, which is what Part B's "answered with a
                # space" versus "did not answer" test depends on.
                answers={titles.get(qid, qid): " ".join(values) for qid, values in r.answers.items()},
                answers_by_id={qid: " ".join(values) for qid, values in r.answers.items()},
                answer_values_by_id={qid: tuple(values) for qid, values in r.answers.items()},
                raw={
                    "responseId": r.response_id,
                    "respondentEmail": r.respondent_email,
                    "createTime": r.submitted_at,
                    "lastSubmittedTime": r.submitted_at,
                    "answers": {
                        qid: {
                            "questionId": qid,
                            "textAnswers": {"answers": [{"value": v} for v in values]},
                        }
                        for qid, values in r.answers.items()
                    },
                },
            )
            for r in page
        )
        return ResponsePage(responses=responses, next_page_token=next_token, titles_by_id=titles)


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _mask(update_mask: str | None, body: dict[str, Any]) -> list[str]:
    """The field paths a request applies. No mask means "the fields present"."""
    if update_mask:
        return [part.strip() for part in update_mask.split(",") if part.strip()]
    return list(body)


def _apply_path(target: dict[str, Any], source: dict[str, Any], path: str) -> None:
    """Copy one dotted field path from ``source`` into ``target``.

    A path absent from ``source`` clears it in ``target``, which is what a
    field mask means: "make this field what the request says, including
    nothing".
    """
    keys = path.split(".")
    src: Any = source
    for key in keys[:-1]:
        src = src.get(key) if isinstance(src, dict) else None
    dst = target
    for key in keys[:-1]:
        dst = dst.setdefault(key, {})
    last = keys[-1]
    if isinstance(src, dict) and last in src:
        dst[last] = copy.deepcopy(src[last])
    else:
        dst.pop(last, None)


def _validate_new_item(item: dict[str, Any], where: str) -> None:
    """Reject what the live API rejects on ``createItem``, with the same 400."""
    kinds = [kind for kind in _ITEM_KINDS if kind in item]
    if len(kinds) != 1:
        raise _bad_request(f"Invalid {where}: an item must be exactly one kind, got {kinds or 'none'}.")
    if kinds[0] != "questionItem":
        return
    question = (item.get("questionItem") or {}).get("question")
    if not isinstance(question, dict):
        raise _bad_request(f"Invalid {where}: questionItem.question is required.")
    qkinds = [kind for kind in _QUESTION_KINDS if kind in question]
    if len(qkinds) != 1:
        raise _bad_request(f"Invalid {where}: a question must be exactly one kind, got {qkinds or 'none'}.")
    qkind = qkinds[0]

    if qkind == "fileUploadQuestion":
        raise _bad_request(f"Invalid {where}: file upload questions cannot be created through the API.")
    if qkind == "choiceQuestion":
        choice = question["choiceQuestion"] or {}
        if choice.get("type") not in _CHOICE_TYPES:
            raise _bad_request(f"Invalid {where}: choiceQuestion.type must be one of {_CHOICE_TYPES}.")
        options = choice.get("options") or []
        if not options:
            raise _bad_request(f"Invalid {where}: a choice question needs at least one option.")
        values: list[str] = []
        others = 0
        for option in options:
            if option.get("isOther"):
                others += 1
                continue
            value = option.get("value")
            if not isinstance(value, str) or not value.strip():
                raise _bad_request(f"Invalid {where}: choice option values cannot be empty.")
            values.append(value)
        if others and choice["type"] == "DROP_DOWN":
            raise _bad_request(f"Invalid {where}: a DROP_DOWN question cannot have an 'Other' option.")
        if others > 1:
            raise _bad_request(f"Invalid {where}: at most one 'Other' option.")
        if not values:
            raise _bad_request(f"Invalid {where}: a choice question needs at least one option.")
        if len(set(values)) != len(values):
            raise _bad_request(f"Invalid {where}: duplicate choice option values.")
    elif qkind == "scaleQuestion":
        scale = question["scaleQuestion"] or {}
        low, high = scale.get("low"), scale.get("high")
        if low not in (0, 1) or isinstance(low, bool):
            raise _bad_request(f"Invalid {where}: scaleQuestion.low must be 0 or 1.")
        if not isinstance(high, int) or isinstance(high, bool) or not 2 <= high <= 10:
            raise _bad_request(f"Invalid {where}: scaleQuestion.high must be between 2 and 10.")


def _load_item(data: dict[str, Any]) -> _FakeItem:
    """One persisted item; also reads state written before bodies were stored."""
    if "body" in data:
        return _FakeItem(item_id=data["item_id"], body=dict(data["body"]))
    kind = data.get("kind", "text")
    if kind == "scale":
        question: dict[str, Any] = {"scaleQuestion": {"low": 1, "high": 7}}
    elif kind == "choice":
        question = {"choiceQuestion": {"type": "CHECKBOX",
                                       "options": [{"value": data.get("title") or "Yes"}]}}
    else:
        question = {"textQuestion": {"paragraph": False}}
    question["questionId"] = data.get("question_id")
    body: dict[str, Any] = {"title": data.get("title", ""), "questionItem": {"question": question}}
    if data.get("description"):
        body["description"] = data["description"]
    return _FakeItem(item_id=data["item_id"], body=body)


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
    ref = client.create_template("CU Exit Ticket Template", "Template — do not submit")
    client.simulate_human_sets_verified(ref.form_id)
    return client


__all__ = [
    "QUESTION_IDS_PRESERVED",
    "QUESTION_IDS_REGENERATED",
    "FakeGoogleClient",
    "demo_client",
]
