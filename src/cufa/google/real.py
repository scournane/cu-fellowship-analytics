"""The real Forms + Drive client.

Thin on purpose. Every decision that matters — publish and then verify, refuse
an unverified template, poll responses instead of a linked sheet — lives in
``template.py`` and ``provisioning.py`` so that it is tested against the fake
and exercised identically here.

Two details worth knowing before editing this file:

* ``forms.responses.list`` keys answers by ``questionId``, not by question text.
  Both views are returned: ``answers`` keyed by title for Part A's single
  question, and ``answers_by_id`` keyed by ``questionId``, which is what Part B
  resolves through ``form_question_map``. Titles are a convenience; ids are the
  contract.
* ``get_form`` is a separate call from ``read_settings`` even though both hit
  ``forms.get``. They answer different questions — publish state versus question
  ids — and the id read must never be served from a cache that a ``batchUpdate``
  could have invalidated.
* ``setPublishSettings`` is newer than some builds of the discovery document.
  If the generated client does not expose it, the request is issued directly
  against the REST endpoint with the same credentials rather than being skipped —
  skipping it would produce a form that silently accepts nothing.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from ..logging_setup import get_logger
from .base import (
    PASSPHRASE_QUESTION_TITLE,
    FormDefinition,
    FormItem,
    FormRef,
    FormResponse,
    FormState,
    GoogleApiError,
    ResponsePage,
)

log = get_logger(__name__)

FORMS_API_ROOT = "https://forms.googleapis.com/v1"
_MAX_ATTEMPTS = 5


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with jitter, so parallel runs do not resonate."""
    delay = min(2**attempt, 32) * (0.5 + random.random() / 2)
    time.sleep(delay)


def _wrap(exc: Exception) -> GoogleApiError:
    """Translate a googleapiclient error into ours, keeping the status."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    reason = None
    detail = str(exc)
    content = getattr(exc, "content", None)
    if content:
        try:
            payload = json.loads(content)
            error = payload.get("error", {})
            detail = error.get("message", detail)
            reason = error.get("status")
            status = status or error.get("code")
        except (ValueError, AttributeError):
            pass
    return GoogleApiError(detail, status=status, reason=reason)


def _execute(request: Any) -> Any:
    """Run a discovery request, retrying only what is worth retrying."""
    from googleapiclient.errors import HttpError  # imported lazily: heavy module

    last: GoogleApiError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return request.execute()
        except HttpError as exc:
            err = _wrap(exc)
            # 4xx other than 429 will not become true by being asked again.
            if err.status not in (429, 500, 502, 503, 504):
                raise err from exc
            last = err
            if attempt < _MAX_ATTEMPTS - 1:
                _sleep_backoff(attempt)
    assert last is not None
    raise last


class RealGoogleClient:
    """``FormsClient`` backed by the live Forms and Drive APIs."""

    is_fake = False

    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._credentials = credentials
        self._forms = build("forms", "v1", credentials=credentials, cache_discovery=False)
        self._drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._question_titles: dict[str, dict[str, str]] = {}

    # -- FormsClient --------------------------------------------------------

    def create_template(self, title: str, description: str = "") -> FormRef:
        # forms.create accepts only info.title; description and items must follow
        # in a batchUpdate. Fighting that ordering is not worth it.
        created = _execute(self._forms.forms().create(body={"info": {"title": title}}))
        form_id = created["formId"]
        if description:
            self.batch_update(
                form_id,
                [
                    {
                        "updateFormInfo": {
                            "info": {"description": description},
                            "updateMask": "description",
                        }
                    }
                ],
            )
        self._rename_in_drive(form_id, title)
        return self._ref(form_id, created)

    def _rename_in_drive(self, form_id: str, name: str) -> None:
        """Give the Drive file the same name as the form.

        A form's document title and its Drive filename are separate, and
        ``forms.create`` sets only the first. Without this the templates sit in
        a staff member's Drive as two files called "Untitled form", which is
        exactly the pair of files somebody needs to find later to do the manual
        Verified step on.

        Session forms do not need it — ``files.copy`` takes a ``name`` — so this
        is only for the two created from scratch.

        Best-effort: a template with the wrong filename is untidy, and failing
        the creation over it would be worse than untidy.
        """
        try:
            _execute(
                self._drive.files().update(
                    fileId=form_id, body={"name": name}, fields="id", supportsAllDrives=True
                )
            )
        except GoogleApiError as exc:  # pragma: no cover - cosmetic only
            log.warning(
                "could not rename form %s in Drive (%s); it will show as "
                "'Untitled form' there",
                form_id,
                exc,
            )

    def get_form(self, form_id: str) -> FormDefinition:
        """Read the form's items back, with the ids responses will be keyed by.

        Not cached. This is called once per provisioned form, immediately after
        its questions are created, to record the ``questionId`` -> slot mapping —
        and a cached answer there would be the one bug the mapping table exists
        to prevent.
        """
        form = _execute(self._forms.forms().get(formId=form_id))
        items: list[FormItem] = []
        for index, item in enumerate(form.get("items", [])):
            question = (item.get("questionItem") or {}).get("question") or {}
            question_id = question.get("questionId")
            if not question_id:
                # Page breaks, images and section headers are items too and have
                # no question id. They still occupy an index, so they are skipped
                # rather than counted — which is why the index recorded on
                # FormItem is the enumerate position of the item, and slot
                # assignment is done against the items this app created.
                continue
            if "scaleQuestion" in question:
                kind = "scale"
            elif "choiceQuestion" in question:
                kind = "choice"
            else:
                kind = "text"
            items.append(
                FormItem(
                    item_id=item.get("itemId", ""),
                    question_id=question_id,
                    title=item.get("title") or "",
                    index=index,
                    kind=kind,
                )
            )
        return FormDefinition(
            form_id=form_id,
            title=(form.get("info") or {}).get("title", ""),
            items=tuple(items),
            raw=form,
        )

    def read_settings(self, form_id: str) -> FormState:
        form = _execute(self._forms.forms().get(formId=form_id))
        settings = form.get("settings") or {}
        publish_state = (form.get("publishSettings") or {}).get("publishState") or {}
        return FormState(
            form_id=form_id,
            email_collection_type=settings.get("emailCollectionType", "UNSPECIFIED"),
            is_published=bool(publish_state.get("isPublished", False)),
            is_accepting_responses=bool(publish_state.get("isAcceptingResponses", False)),
            title=(form.get("info") or {}).get("title", ""),
            raw=form,
        )

    def copy_form(self, source_form_id: str, new_title: str) -> FormRef:
        # A Google Form is a Drive file, so the copy goes through Drive — and
        # copying is what carries the Verified email setting across (trap 2).
        copied = _execute(
            self._drive.files().copy(
                fileId=source_form_id,
                body={"name": new_title},
                fields="id,name",
                supportsAllDrives=True,
            )
        )
        form_id = copied["id"]
        form = _execute(self._forms.forms().get(formId=form_id))
        return self._ref(form_id, form)

    def batch_update(self, form_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        self._question_titles.pop(form_id, None)
        return _execute(
            self._forms.forms().batchUpdate(formId=form_id, body={"requests": requests})
        )

    def set_publish_settings(
        self, form_id: str, *, is_published: bool = True, is_accepting_responses: bool = True
    ) -> dict[str, Any]:
        body = {
            "publishSettings": {
                "publishState": {
                    "isPublished": is_published,
                    "isAcceptingResponses": is_accepting_responses,
                }
            },
            "updateMask": "publishState.isPublished,publishState.isAcceptingResponses",
        }
        forms_resource = self._forms.forms()
        if hasattr(forms_resource, "setPublishSettings"):
            return _execute(forms_resource.setPublishSettings(formId=form_id, body=body))
        return self._raw_post(f"{FORMS_API_ROOT}/forms/{form_id}:setPublishSettings", body)

    def list_responses(
        self,
        form_id: str,
        *,
        response_filter: str | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
    ) -> ResponsePage:
        kwargs: dict[str, Any] = {"formId": form_id}
        if response_filter:
            kwargs["filter"] = response_filter
        if page_token:
            kwargs["pageToken"] = page_token
        if page_size:
            kwargs["pageSize"] = page_size

        payload = _execute(self._forms.forms().responses().list(**kwargs))
        titles = self._titles_for(form_id)

        responses = tuple(
            FormResponse(
                response_id=item.get("responseId", ""),
                respondent_email=item.get("respondentEmail", ""),
                # lastSubmittedTime is the moment that matters. createTime is
                # when the response was started, which for a form left open is
                # not when the fellow answered.
                submitted_at=item.get("lastSubmittedTime") or item.get("createTime", ""),
                answers=_extract_answers(item.get("answers") or {}, titles),
                answers_by_id=_extract_answers(item.get("answers") or {}, {}),
                raw=item,
            )
            for item in payload.get("responses", [])
        )
        return ResponsePage(responses=responses, next_page_token=payload.get("nextPageToken"))

    # -- internals ----------------------------------------------------------

    def _titles_for(self, form_id: str) -> dict[str, str]:
        """questionId -> question title, cached per form."""
        cached = self._question_titles.get(form_id)
        if cached is not None:
            return cached

        form = _execute(self._forms.forms().get(formId=form_id))
        mapping: dict[str, str] = {}
        for item in form.get("items", []):
            title = item.get("title") or PASSPHRASE_QUESTION_TITLE
            question = (item.get("questionItem") or {}).get("question") or {}
            question_id = question.get("questionId")
            if question_id:
                mapping[question_id] = title
        self._question_titles[form_id] = mapping
        return mapping

    def _ref(self, form_id: str, form: dict[str, Any]) -> FormRef:
        return FormRef(
            form_id=form_id,
            responder_url=form.get("responderUri") or f"https://docs.google.com/forms/d/{form_id}/viewform",
            edit_url=f"https://docs.google.com/forms/d/{form_id}/edit",
        )

    def _raw_post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """Issue an authorized POST for a method missing from the discovery doc."""
        import google.auth.transport.requests as gart

        session = gart.AuthorizedSession(self._credentials)
        for attempt in range(_MAX_ATTEMPTS):
            response = session.post(url, json=body, timeout=30)
            if response.status_code < 400:
                return response.json() if response.content else {}
            if response.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_ATTEMPTS - 1:
                _sleep_backoff(attempt)
                continue
            raise GoogleApiError(
                response.text[:500] or "setPublishSettings failed",
                status=response.status_code,
            )
        raise GoogleApiError("setPublishSettings exhausted retries", status=429)


def _extract_answers(answers: dict[str, Any], titles: dict[str, str]) -> dict[str, str]:
    """Flatten the answers payload, keyed by title or — with no titles — by id.

    A checkbox with one option comes back the same way a text answer does: a
    ``textAnswers.answers`` list whose single value is the option's text. That is
    why the help field needs no special case here and is instead recognised by
    the slot its question id maps to.
    """
    flattened: dict[str, str] = {}
    for question_id, answer in answers.items():
        values = ((answer or {}).get("textAnswers") or {}).get("answers") or []
        text = " ".join(str(v.get("value", "")) for v in values).strip()
        flattened[titles.get(question_id, question_id)] = text
    return flattened


__all__ = ["RealGoogleClient"]
