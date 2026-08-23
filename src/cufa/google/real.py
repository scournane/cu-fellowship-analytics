"""The real Forms + Drive client.

Thin on purpose. Every decision that matters — publish and then verify, refuse
an unverified template, poll responses instead of a linked sheet — lives in
``template.py`` and ``provisioning.py`` so that it is tested against the fake
and exercised identically here.

Two details worth knowing before editing this file:

* ``forms.responses.list`` keys answers by ``questionId``, not by question text.
  The mapping is read from the form once and cached, so the passphrase answer
  can be looked up by the title a human recognises.
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
        return self._ref(form_id, created)

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
    """Flatten the answers payload to ``{question title: text}``."""
    flattened: dict[str, str] = {}
    for question_id, answer in answers.items():
        values = ((answer or {}).get("textAnswers") or {}).get("answers") or []
        text = " ".join(str(v.get("value", "")) for v in values).strip()
        flattened[titles.get(question_id, question_id)] = text
    return flattened


__all__ = ["RealGoogleClient"]
