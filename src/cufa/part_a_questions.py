"""Part A's questions as data: the shape, the rules, and the Forms mapping.

Part A used to be one fixed question — a spoken passphrase — and the code that
built it was a constant. It is now the **exit ticket**: several questions that
CU staff write and edit themselves, a cohort default plus an optional full
override per session. This module is the pure half of that: no database, no
network, just "is this a question set Google will accept, and what does it look
like as Forms API items?". Storage and versioning live in ``question_sets.py``.

Four decisions shape it:

* **Validation says everything at once.** ``validate`` returns every error and
  every warning in one pass, so a staff member fixing an eight-question form
  sees all of it on the first save rather than one complaint per round trip.
* **Strict about what Google would reject, lenient about leftovers.** An empty
  option, a dropdown with no options, a scale of 3 to 12 — each would be a 400
  from ``batchUpdate`` in the middle of provisioning, so each is an error here,
  before anything is saved. A ``scale`` block left on a question whose type was
  switched to paragraph is just debris from the editor and is dropped quietly.
* **Keys are identity, titles are wording.** Every question carries a
  ``q_[a-z0-9_]+`` key that survives rewording, reordering and new versions, so
  "how did answers to *this* question change across ten weeks" stays answerable
  after somebody fixes a typo. Questions without one get a key minted from their
  type and wording — deterministically, so saving the same key-less file twice
  is recognised as the same content rather than as a new version.
* **Answers are counted, never graded.** Nothing here scores a response. The
  question type decides how answers are *displayed* (a distribution for a
  choice, a list for free text); no type turns an answer into a number about the
  fellow. See the README's design invariants.

Placeholders ``{lesson}`` and ``{session_title}`` are filled per session by
``render`` at provisioning time, from the session's ``week_index`` and title —
so one default serves every week and the stored set never goes stale.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import InvalidQuestionSet

SCHEMA_VERSION = 1

#: Every question type staff can choose, with what the editor needs to know to
#: draw it. Order is the order the console offers them in.
QUESTION_TYPES: list[dict[str, Any]] = [
    {"type": "short_answer", "label": "Short answer", "has_options": False,
     "has_scale": False, "answerable": True, "allows_other": False},
    {"type": "paragraph", "label": "Paragraph", "has_options": False,
     "has_scale": False, "answerable": True, "allows_other": False},
    {"type": "multiple_choice", "label": "Multiple choice", "has_options": True,
     "has_scale": False, "answerable": True, "allows_other": True},
    {"type": "checkboxes", "label": "Checkboxes", "has_options": True,
     "has_scale": False, "answerable": True, "allows_other": True},
    {"type": "dropdown", "label": "Dropdown", "has_options": True,
     "has_scale": False, "answerable": True, "allows_other": False},
    {"type": "linear_scale", "label": "Linear scale", "has_options": False,
     "has_scale": True, "answerable": True, "allows_other": False},
    {"type": "section", "label": "Section break", "has_options": False,
     "has_scale": False, "answerable": False, "allows_other": False},
    {"type": "text", "label": "Title and description", "has_options": False,
     "has_scale": False, "answerable": False, "allows_other": False},
]

_TYPES: dict[str, dict[str, Any]] = {t["type"]: t for t in QUESTION_TYPES}
CHOICE_TYPES = frozenset(t["type"] for t in QUESTION_TYPES if t["has_options"])
ANSWERABLE_TYPES = frozenset(t["type"] for t in QUESTION_TYPES if t["answerable"])

#: The Forms API's ``choiceQuestion.type`` for each choice type.
_CHOICE_API_TYPE = {"multiple_choice": "RADIO", "checkboxes": "CHECKBOX", "dropdown": "DROP_DOWN"}
_CHOICE_FROM_API = {value: key for key, value in _CHOICE_API_TYPE.items()}

KEY_PATTERN = re.compile(r"^q_[a-z0-9_]{1,62}$")
PLACEHOLDERS = ("{lesson}", "{session_title}")
_PLACEHOLDER_SCAN = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")

# Limits. Generous enough that nothing a person would write trips them, tight
# enough that a paste of the wrong document does.
MAX_FORM_TITLE = 300
MAX_FORM_DESCRIPTION = 10_000
MAX_ITEMS = 50
MAX_QUESTION_TITLE = 1_000
MAX_QUESTION_DESCRIPTION = 4_000
MAX_OPTIONS = 50
MAX_OPTION_LENGTH = 500
MAX_SCALE_LABEL = 100

#: Above this many answerable questions, ``validate`` warns (never refuses).
#: Going from three questions to four drops completion by about 18%, and
#: response rates fall steeply past a few minutes — see
#: ``form_content_b.SURVEY_LENGTH_RATIONALE``. The cohort default is the team's
#: own week-1 form and is longer than this on purpose, so it is a note to the
#: person editing, not a rule.
SOFT_QUESTION_LIMIT = 6

_QUESTION_FIELDS = frozenset(
    {"key", "type", "title", "description", "required", "options", "allow_other",
     "shuffle", "scale"}
)
_CONTENT_FIELDS = frozenset({"schema_version", "title", "description", "questions"})
#: Carried by the config file for humans and dropped on load.
_FILE_ONLY_FIELDS = frozenset({"status", "_comment"})


@dataclass
class ValidationResult:
    """The cleaned set plus everything found wrong with the input.

    ``clean`` is always returned, even alongside errors, so an editor can show
    the normalised form next to the complaints. It must not be *saved* unless
    ``errors`` is empty — ``question_sets`` enforces that.
    """

    clean: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """A trimmed string with Windows newlines normalised. None becomes ''."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _label(index: int, title: str) -> str:
    short = title if len(title) <= 50 else title[:47] + "…"
    return f"Question {index + 1}" + (f" (“{short}”)" if short else "")


def _bool(value: Any, default: bool = False) -> bool | None:
    """A strict-ish boolean: real bools, 0/1 and None. Anything else is None."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _mint_key(question_type: str, title: str, taken: set[str]) -> str:
    """``q_`` + 10 hex, derived from the question's type and wording.

    Deterministic on purpose. A random key would make saving the same key-less
    file twice look like two different sets, and "re-seeding creates no new
    versions" would stop being true. Two questions with identical type and
    wording in one set get different keys through the collision counter.
    """
    basis = f"{question_type}\x1f{title.casefold()}"
    counter = 0
    while True:
        digest = hashlib.sha256(f"{basis}\x1f{counter}".encode("utf-8")).hexdigest()[:10]
        key = f"q_{digest}"
        if key not in taken:
            return key
        counter += 1


def _scan_placeholders(text: str, where: str, warnings: list[str]) -> None:
    for token in _PLACEHOLDER_SCAN.findall(text or ""):
        if token not in PLACEHOLDERS:
            warnings.append(
                f"{where}: {token} is not a placeholder this tool fills in "
                f"(only {' and '.join(PLACEHOLDERS)} are), so fellows will see it "
                "exactly as typed."
            )


def _clean_options(
    raw: Any, where: str, errors: list[str]
) -> list[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        errors.append(f"{where}: options must be a list.")
        return []
    options: list[str] = []
    seen: set[str] = set()
    before = len(errors)
    for position, option in enumerate(raw, start=1):
        if isinstance(option, dict):  # tolerate {"value": "..."} from the API shape
            option = option.get("value")
        text = _text(option)
        if not text:
            errors.append(f"{where}: option {position} is blank. Google rejects empty options.")
            continue
        if len(text) > MAX_OPTION_LENGTH:
            errors.append(
                f"{where}: option {position} is {len(text)} characters "
                f"(limit {MAX_OPTION_LENGTH})."
            )
        if text in seen:
            errors.append(f"{where}: option “{text}” appears twice. Google rejects duplicates.")
            continue
        seen.add(text)
        options.append(text)
    if not options and len(errors) == before:
        errors.append(f"{where}: a choice question needs at least one option.")
    if len(options) > MAX_OPTIONS:
        errors.append(f"{where}: {len(options)} options (limit {MAX_OPTIONS}).")
    return options


def _clean_scale(raw: Any, where: str, errors: list[str]) -> dict[str, Any]:
    if raw is None:
        # Google's own default for a new scale question.
        raw = {"low": 1, "high": 5}
    if not isinstance(raw, dict):
        errors.append(f"{where}: scale must be an object with low and high.")
        return {"low": 1, "high": 5, "low_label": "", "high_label": ""}
    low, high = raw.get("low", 1), raw.get("high", 5)
    if isinstance(low, bool) or not isinstance(low, int) or low not in (0, 1):
        errors.append(f"{where}: the scale must start at 0 or 1 (got {low!r}).")
        low = 1 if not isinstance(low, int) or isinstance(low, bool) else low
    if isinstance(high, bool) or not isinstance(high, int) or not 2 <= high <= 10:
        errors.append(f"{where}: the scale must end between 2 and 10 (got {high!r}).")
        high = 5 if not isinstance(high, int) or isinstance(high, bool) else high
    labels = {}
    for name in ("low_label", "high_label"):
        text = _text(raw.get(name))
        if len(text) > MAX_SCALE_LABEL:
            errors.append(f"{where}: {name} is {len(text)} characters (limit {MAX_SCALE_LABEL}).")
        labels[name] = text
    return {"low": low, "high": high, **labels}


def _clean_question(
    raw: Any, index: int, taken: set[str], errors: list[str], warnings: list[str]
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        errors.append(f"Question {index + 1}: must be an object, got {type(raw).__name__}.")
        return None

    title = _text(raw.get("title"))
    where = _label(index, title)
    qtype = _text(raw.get("type"))
    spec = _TYPES.get(qtype)
    if spec is None:
        errors.append(
            f"{where}: type {qtype or '(missing)'!r} is not one of "
            f"{', '.join(_TYPES)}."
        )
        return None

    unknown = sorted(set(raw) - _QUESTION_FIELDS)
    if unknown:
        warnings.append(f"{where}: ignored unknown field(s) {', '.join(unknown)}.")

    key = raw.get("key")
    if key in (None, ""):
        key = _mint_key(qtype, title, taken)
    elif not isinstance(key, str) or not KEY_PATTERN.match(key):
        errors.append(
            f"{where}: key {key!r} must look like q_first_name — q_ then lower-case "
            "letters, digits and underscores."
        )
    taken.add(str(key))

    description = _text(raw.get("description"))
    if spec["answerable"] and not title:
        errors.append(f"{where}: a question needs a title — it is what fellows read.")
    if len(title) > MAX_QUESTION_TITLE:
        errors.append(f"{where}: title is {len(title)} characters (limit {MAX_QUESTION_TITLE}).")
    if len(description) > MAX_QUESTION_DESCRIPTION:
        errors.append(
            f"{where}: description is {len(description)} characters "
            f"(limit {MAX_QUESTION_DESCRIPTION})."
        )
    _scan_placeholders(title, where, warnings)
    _scan_placeholders(description, where, warnings)

    clean: dict[str, Any] = {"key": key, "type": qtype, "title": title, "description": description}
    if not spec["answerable"]:
        # A section break or a block of text has nothing to require.
        if qtype == "text" and not title and not description:
            warnings.append(f"{where}: this text block is empty, so fellows will see nothing.")
        return clean

    required = _bool(raw.get("required"), False)
    if required is None:
        errors.append(f"{where}: required must be true or false.")
        required = False
    clean["required"] = required

    if spec["has_options"]:
        clean["options"] = _clean_options(raw.get("options"), where, errors)
        for option in clean["options"]:
            _scan_placeholders(option, where, warnings)
        allow_other = _bool(raw.get("allow_other"), False)
        if allow_other is None:
            errors.append(f"{where}: allow_other must be true or false.")
            allow_other = False
        if spec["allows_other"]:
            clean["allow_other"] = allow_other
        elif allow_other:
            # Google has no "Other" on a dropdown and rejects the request. The
            # likeliest cause is switching a multiple-choice question to a
            # dropdown in the editor, so this is a note rather than a refusal.
            warnings.append(
                f"{where}: dropdowns cannot offer “Other” in Google Forms, so it was removed."
            )
        shuffle = _bool(raw.get("shuffle"), False)
        if shuffle is None:
            errors.append(f"{where}: shuffle must be true or false.")
            shuffle = False
        clean["shuffle"] = shuffle

    if spec["has_scale"]:
        clean["scale"] = _clean_scale(raw.get("scale"), where, errors)

    return clean


def validate(content: Any) -> ValidationResult:
    """Clean a question set and report everything wrong with it.

    Mints keys for questions without one, trims every string, drops leftover
    fields a type does not use, and enforces the limits Google would otherwise
    enforce with a 400 halfway through provisioning. Returns warnings for things
    that are allowed but probably unintended.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(content, dict):
        return ValidationResult(
            clean={}, errors=["A question set must be a JSON object."], warnings=[]
        )

    schema_version = content.get("schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version {schema_version!r} is not supported (this version reads "
            f"{SCHEMA_VERSION})."
        )

    unknown = sorted(set(content) - _CONTENT_FIELDS - _FILE_ONLY_FIELDS)
    if unknown:
        warnings.append(f"Ignored unknown top-level field(s): {', '.join(unknown)}.")

    title = _text(content.get("title"))
    description = _text(content.get("description"))
    if not title:
        errors.append("The form needs a title.")
    elif len(title) > MAX_FORM_TITLE:
        errors.append(f"The form title is {len(title)} characters (limit {MAX_FORM_TITLE}).")
    if len(description) > MAX_FORM_DESCRIPTION:
        errors.append(
            f"The form description is {len(description)} characters "
            f"(limit {MAX_FORM_DESCRIPTION})."
        )
    _scan_placeholders(title, "Form title", warnings)
    _scan_placeholders(description, "Form description", warnings)

    raw_questions = content.get("questions")
    questions: list[dict[str, Any]] = []
    if not isinstance(raw_questions, list):
        errors.append("questions must be a list.")
        raw_questions = []
    elif len(raw_questions) > MAX_ITEMS:
        errors.append(f"{len(raw_questions)} items is too many (limit {MAX_ITEMS}).")

    # Explicit keys are claimed first, so a minted key can never collide with
    # one somebody chose — whatever order the questions arrive in.
    taken: set[str] = set()
    seen_keys: dict[str, int] = {}
    for position, raw in enumerate(raw_questions):
        key = raw.get("key") if isinstance(raw, dict) else None
        if isinstance(key, str) and key:
            if key in seen_keys:
                errors.append(
                    f"Questions {seen_keys[key] + 1} and {position + 1} share the key "
                    f"{key!r}. Keys identify a question across versions, so each must "
                    "be unique."
                )
            seen_keys.setdefault(key, position)
            taken.add(key)

    for position, raw in enumerate(raw_questions):
        cleaned = _clean_question(raw, position, taken, errors, warnings)
        if cleaned is not None:
            questions.append(cleaned)

    answerable = [q for q in questions if q["type"] in ANSWERABLE_TYPES]
    if isinstance(content.get("questions"), list) and not answerable:
        errors.append(
            "There is no question to answer. A section break or a block of text on "
            "its own gives fellows nothing to submit."
        )
    if len(answerable) > SOFT_QUESTION_LIMIT:
        warnings.append(
            f"{len(answerable)} questions to answer. Every extra question costs "
            "completions — going from three to four drops completion by about 18% — "
            f"so more than {SOFT_QUESTION_LIMIT} is worth a second look. It is allowed."
        )
    if questions and questions[0]["type"] == "section":
        warnings.append(
            "The form starts with a section break, so its first page shows only the "
            "form's own title and description."
        )
    if questions and questions[-1]["type"] == "section":
        warnings.append("The form ends with a section break, which leaves an empty last page.")

    clean = {
        "schema_version": SCHEMA_VERSION,
        "title": title,
        "description": description,
        "questions": questions,
    }
    return ValidationResult(clean=clean, errors=errors, warnings=warnings)


def require_valid(content: Any) -> ValidationResult:
    """``validate``, raising ``InvalidQuestionSet`` when there are errors."""
    result = validate(content)
    if result.errors:
        raise InvalidQuestionSet(result.errors)
    return result


def content_sha256(clean: dict[str, Any]) -> str:
    """A stable hash of cleaned content: key order and whitespace never matter."""
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def answerable(content: dict[str, Any]) -> list[dict[str, Any]]:
    """The questions a fellow can answer, in form order."""
    return [q for q in content.get("questions") or [] if q.get("type") in ANSWERABLE_TYPES]


# ---------------------------------------------------------------------------
# placeholders
# ---------------------------------------------------------------------------


def _fill(text: str, lesson: str | None, session_title: str) -> str:
    if not text:
        return text
    if lesson:
        text = text.replace("{lesson}", lesson)
    else:
        # A makeup session has no week number. "Lesson  “Budgets”" with a
        # double space — or "Lesson None" — is worse than no number at all.
        text = text.replace(" {lesson}", "").replace("{lesson}", "")
    return text.replace("{session_title}", session_title)


def render(content: dict[str, Any], *, week_index: int | None, session_title: str) -> dict[str, Any]:
    """The set with ``{lesson}`` and ``{session_title}`` filled in for one session.

    Plain string replacement rather than ``str.format``: a question that
    legitimately contains braces must not raise, and an unknown placeholder must
    appear as typed (``validate`` warns about it) rather than crash provisioning.
    Keys are never touched, so answers to "What was your biggest takeaway from
    Lesson {lesson}?" line up across every week.
    """
    lesson = str(week_index) if week_index is not None else None
    title = session_title or ""
    rendered = copy.deepcopy(content)
    rendered["title"] = _fill(rendered.get("title", ""), lesson, title)
    rendered["description"] = _fill(rendered.get("description", ""), lesson, title)
    for question in rendered.get("questions") or []:
        question["title"] = _fill(question.get("title", ""), lesson, title)
        question["description"] = _fill(question.get("description", ""), lesson, title)
        if "options" in question:
            question["options"] = [_fill(o, lesson, title) for o in question["options"]]
        scale = question.get("scale")
        if isinstance(scale, dict):
            for name in ("low_label", "high_label"):
                scale[name] = _fill(scale.get(name, ""), lesson, title)
    return rendered


# ---------------------------------------------------------------------------
# Forms API mapping
# ---------------------------------------------------------------------------


def forms_kind(question_type: str) -> str | None:
    """The coarse kind ``FormItem.kind`` reports for a question type.

    ``text`` / ``choice`` / ``scale`` for answerable types, None for section
    breaks and text blocks (which the Forms API reads back without a question
    id). Used to assert a read-back form is the shape it was told to be.
    """
    if question_type in ("short_answer", "paragraph"):
        return "text"
    if question_type in CHOICE_TYPES:
        return "choice"
    if question_type == "linear_scale":
        return "scale"
    return None


def to_item_body(question: dict[str, Any]) -> dict[str, Any]:
    """The full Forms API ``Item`` for one cleaned question.

    Full bodies always — never a title-only fragment. The live API reads an item
    without a ``questionItem`` as a request to turn the question into a text
    block (see ``form_content_b.ItemSpec.update_request``), and a create with a
    partial body is at best a different question from the one staff wrote.
    """
    qtype = question["type"]
    item: dict[str, Any] = {"title": question.get("title", "")}
    if question.get("description"):
        item["description"] = question["description"]

    if qtype == "section":
        item["pageBreakItem"] = {}
        return item
    if qtype == "text":
        item["textItem"] = {}
        return item

    body: dict[str, Any] = {"required": bool(question.get("required", False))}
    if qtype in ("short_answer", "paragraph"):
        body["textQuestion"] = {"paragraph": qtype == "paragraph"}
    elif qtype in CHOICE_TYPES:
        options: list[dict[str, Any]] = [{"value": value} for value in question.get("options") or []]
        if question.get("allow_other") and qtype != "dropdown":
            options.append({"isOther": True})
        choice: dict[str, Any] = {"type": _CHOICE_API_TYPE[qtype], "options": options}
        if question.get("shuffle"):
            choice["shuffle"] = True
        body["choiceQuestion"] = choice
    elif qtype == "linear_scale":
        scale = question.get("scale") or {}
        scale_body: dict[str, Any] = {"low": int(scale.get("low", 1)), "high": int(scale.get("high", 5))}
        if scale.get("low_label"):
            scale_body["lowLabel"] = scale["low_label"]
        if scale.get("high_label"):
            scale_body["highLabel"] = scale["high_label"]
        body["scaleQuestion"] = scale_body
    else:  # pragma: no cover - validate() refuses unknown types
        raise ValueError(f"unknown question type {qtype!r}")

    item["questionItem"] = {"question": body}
    return item


def from_forms_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One Forms API ``Item`` as an (unvalidated, key-less) question, or None.

    None means "this app cannot represent it": grids, dates, times, file
    uploads, ratings, images and videos. ``import_notes`` explains each one, so
    the importer can say what it left behind rather than silently shortening
    the form.

    Branching (``goToAction`` / ``goToSectionId`` on an option) is dropped: the
    editor has no way to show it, and a branch that pointed at a section this
    app then renumbered would send fellows somewhere nobody chose.
    """
    title = raw.get("title") or ""
    description = raw.get("description") or ""
    if "pageBreakItem" in raw:
        return {"type": "section", "title": title, "description": description}
    if "textItem" in raw:
        return {"type": "text", "title": title, "description": description}

    question = (raw.get("questionItem") or {}).get("question")
    if not isinstance(question, dict):
        return None
    base = {"title": title, "description": description, "required": bool(question.get("required"))}

    if "textQuestion" in question:
        paragraph = bool((question.get("textQuestion") or {}).get("paragraph"))
        return {"type": "paragraph" if paragraph else "short_answer", **base}
    if "choiceQuestion" in question:
        choice = question.get("choiceQuestion") or {}
        qtype = _CHOICE_FROM_API.get(choice.get("type") or "")
        if qtype is None:
            return None
        options = [o.get("value", "") for o in choice.get("options") or [] if not o.get("isOther")]
        allow_other = any(o.get("isOther") for o in choice.get("options") or [])
        result: dict[str, Any] = {"type": qtype, **base, "options": options,
                                  "shuffle": bool(choice.get("shuffle"))}
        if qtype != "dropdown":
            result["allow_other"] = allow_other
        return result
    if "scaleQuestion" in question:
        scale = question.get("scaleQuestion") or {}
        return {
            "type": "linear_scale",
            **base,
            "scale": {
                "low": scale.get("low", 1),
                "high": scale.get("high", 5),
                "low_label": scale.get("lowLabel", ""),
                "high_label": scale.get("highLabel", ""),
            },
        }
    return None


_UNSUPPORTED = (
    ("questionGroupItem", "a grid question"),
    ("imageItem", "an image"),
    ("videoItem", "a video"),
)
_UNSUPPORTED_QUESTIONS = (
    ("dateQuestion", "a date question"),
    ("timeQuestion", "a time question"),
    ("fileUploadQuestion", "a file-upload question"),
    ("ratingQuestion", "a rating question"),
    ("rowQuestion", "a grid row"),
)


def import_notes(raw: dict[str, Any], position: int) -> list[str]:
    """Why an imported item was changed or left out, in words for staff."""
    title = raw.get("title") or ""
    where = f"Item {position + 1}" + (f" (“{title[:50]}”)" if title else "")
    notes: list[str] = []
    for key, what in _UNSUPPORTED:
        if key in raw:
            notes.append(f"{where} is {what}, which this editor cannot represent; it was left out.")
    question = (raw.get("questionItem") or {}).get("question") or {}
    for key, what in _UNSUPPORTED_QUESTIONS:
        if key in question:
            notes.append(f"{where} is {what}, which this editor cannot represent; it was left out.")
    options = ((question.get("choiceQuestion") or {}).get("options")) or []
    if any(o.get("goToAction") or o.get("goToSectionId") for o in options):
        notes.append(
            f"{where} sends fellows to different sections depending on the answer. "
            "Branching is not supported, so every fellow will now see every section."
        )
    if any(o.get("image") for o in options):
        notes.append(f"{where} has option images; only the option text was imported.")
    if (raw.get("questionItem") or {}).get("image"):
        notes.append(f"{where} has an image attached; only the text was imported.")
    return notes


# ---------------------------------------------------------------------------
# files and links
# ---------------------------------------------------------------------------


def load_file(path: str | Path) -> dict[str, Any]:
    """Read a question-set JSON file, drop its human-only fields, validate it.

    Returns the cleaned content. Raises ``InvalidQuestionSet`` listing every
    problem, and ``FileNotFoundError`` / ``json.JSONDecodeError`` as themselves:
    a missing or malformed file is not a question-set problem and should not be
    reported as one.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in _FILE_ONLY_FIELDS}
    return require_valid(data).clean


_FORM_URL = re.compile(r"/forms/d/(e/)?([A-Za-z0-9_-]{10,})")


def form_id_from_url(value: str) -> str:
    """Accept a form id or an editor URL; return the id.

    A *responder* link (``/forms/d/e/1FAIpQL…/viewform``) carries a different
    id that the API does not accept, and guessing from it would 404 with no
    explanation — so it is refused with the reason.
    """
    text = (value or "").strip()
    match = _FORM_URL.search(text)
    if match is None:
        if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text):
            return text
        raise ValueError(f"{value!r} is neither a Google Form id nor a form URL.")
    if match.group(1):
        raise ValueError(
            "That is the link fellows answer on (…/forms/d/e/…/viewform). The API "
            "needs the editor's id: open the form in edit mode and copy that URL "
            "(…/forms/d/<id>/edit)."
        )
    return match.group(2)


__all__ = [
    "ANSWERABLE_TYPES",
    "CHOICE_TYPES",
    "KEY_PATTERN",
    "PLACEHOLDERS",
    "QUESTION_TYPES",
    "SCHEMA_VERSION",
    "SOFT_QUESTION_LIMIT",
    "ValidationResult",
    "answerable",
    "content_sha256",
    "form_id_from_url",
    "forms_kind",
    "from_forms_item",
    "import_notes",
    "load_file",
    "render",
    "require_valid",
    "to_item_body",
    "validate",
]
