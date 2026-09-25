#!/usr/bin/env python3
"""Generate the synthetic fixture set the demo and the tests run against.

Deterministic: a fixed seed, no clock reads, no randomness that varies between
machines. Re-running produces byte-identical files, which is what lets
``make demo`` be re-run and compared.

Nothing here resembles a real fellow. Names are obviously invented and every
address is ``@example.invalid``, a reserved TLD that cannot be registered or
routed — so a fixture can never accidentally email a person.

The response set is built to cover every case the pipeline is supposed to
survive, including the ones a naive parser would drop.

Part A, the exit ticket. Answers are keyed by question KEY, read from
config/part_a_default_questions.json and from the Session 3 override this
script also writes, so the fixture never pins a question id:

  every question answered · required questions only · answers padded with
  whitespace, and a whitespace-only optional answer · emoji-only answers ·
  one-word answers · a very long answer · an answer in Spanish · the rating
  at both ends of its scale · several boxes ticked, and "Other" typed in (the
  added Session 3 question) · a submission at each inclusive edge of the
  window · one two days early · one the next morning · an address not on the
  roster · an exact duplicate submission · CSV rows outside every window,
  inside two overlapping windows, inside exactly one, and either side of a
  DST boundary · an unexpected extra column

Part B:

  confidence across the whole 1-7 range · confidence of 0, of 8, and of "four" ·
  blank confidence · a substantive takeaway · a one-word one · a whitespace-only
  one · an emoji-only one · a very long one · rotating answers for all three
  kinds across ten weeks · a single-name shoutout · a comma-separated list ·
  an "X and Y" · a name matching nobody · a first name matching two fellows ·
  a blank shoutout · the help box ticked on a small number of submissions ·
  one fellow giving an identical confidence value five sessions running ·
  a fellow who answered Part B and not Part A, and the reverse
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SEED = 20260901
COHORT_ID = "demo"
TIMEZONE = "America/New_York"
ZONE = ZoneInfo(TIMEZONE)

# Obviously fictional. Two-part invented names, no real people.
FIRST_NAMES = [
    "Ardith", "Bexley", "Corvin", "Delphine", "Ellery", "Faro", "Glenna",
    "Halcyon", "Ingram", "Jessamy", "Kestrel", "Lorne", "Marisol", "Nevin",
    "Oriel", "Peregrine", "Quill", "Rosalind", "Sabra", "Tobias",
]
LAST_NAMES = [
    "Aldergrove", "Brambleton", "Cinderwick", "Dunmore", "Everstead",
    "Fallowmere", "Gladstone", "Havershill", "Ironwood", "Junipero",
    "Kelloway", "Larkspur", "Mossgate", "Northcote", "Oakhaven",
    "Pennyfeather", "Quarrington", "Ravensmoor", "Stonebrook", "Thornbury",
]

# Index 19 is deliberately given index 12's first name, so "Marisol" in a
# shoutout matches two fellows. That has to resolve to `unresolved` and a review
# entry, never to a coin flip: attributing someone's praise to the wrong person
# is invisible once it has happened.
AMBIGUOUS_FIRST_NAME_AT = 19
AMBIGUOUS_TWIN_OF = 12

# Ten lessons, weekly — ten because that is one full turn of the Part B rotation,
# so every rotating kind appears and the wrap is reachable.
LESSONS = [
    "Session 1 — What a civic problem is",
    "Session 2 — Finding the people affected",
    "Session 3 — Reading a budget",
    "Session 4 — Interview practice",
    "Session 5 — Building a coalition",
    "Session 6 — Presenting to power",
    "Session 7 — Where the money actually goes",
    "Session 8 — Writing the ask",
    "Session 9 — Testing it with someone",
    "Session 10 — What happens next",
]

# The teacher's own question, for the weeks the rotation assigns to it. Weeks
# without one here would BLOCK provisioning rather than fall back to something
# generic — which is the behaviour the demo demonstrates, so week 10 is left
# unset on purpose and filled in by the demo before it provisions.
TEACHER_QUESTIONS = {
    1: "What surprised you about the problem we picked apart today?",
    4: "Which question from the practice interviews would you ask differently?",
    7: "Where did the money go that you did not expect?",
}

FIRST_SUNDAY = datetime(2026, 9, 27, 19, 0)  # 7pm local, weekly
DURATION_MINUTES = 90
GRACE_MINUTES = 15

# A deliberately misconfigured extra session whose window overlaps Session 5's.
# It exists to produce the `ambiguous` case, which must be recorded rather than
# guessed at — an overlapping schedule is a bug worth surfacing.
OVERLAP_SESSION_TITLE = "Session 5 — makeup (overlaps deliberately)"

# Part A's questions. The fixtures read the same file `cufa questions
# seed-default` loads, rather than a copy of it, so an edit to the default set
# reaches the fixtures on the next `make fixtures` without anyone remembering.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = REPO_ROOT / "config" / "part_a_default_questions.json"

# The week whose exit ticket the demo customises before it is published, and
# the two files it uses: the override itself, and a later edit that must be
# refused because by then the form is live.
OVERRIDE_WEEK = 3
OVERRIDE_FILE = "part_a_session3_override.json"
LATE_EDIT_FILE = "part_a_session3_late_edit.json"

# Question types a fellow can answer. `section` and `text` are layout.
ANSWERABLE = {
    "short_answer", "paragraph", "multiple_choice", "checkboxes", "dropdown",
    "linear_scale",
}


def _rfc3339(local: datetime) -> str:
    """Local wall clock in TIMEZONE -> RFC3339 UTC, the way the API returns it."""
    return (
        local.replace(tzinfo=ZONE)
        .astimezone(ZoneInfo("UTC"))
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_fellows() -> list[dict[str, str]]:
    def first_name(index: int) -> str:
        if index == AMBIGUOUS_FIRST_NAME_AT:
            return FIRST_NAMES[AMBIGUOUS_TWIN_OF]
        return FIRST_NAMES[index]

    return [
        {
            "fellow_id": f"CU-{2600 + index:04d}",
            "full_name": f"{first_name(index)} {LAST_NAMES[index]}",
            # Still unique: the surnames differ, so only the FIRST name collides.
            "primary_email": (
                f"{first_name(index).lower()}.{LAST_NAMES[index].lower()}@example.invalid"
            ),
            "status": "active",
        }
        for index in range(20)
    ]


def build_sessions() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, title in enumerate(LESSONS):
        scheduled = FIRST_SUNDAY + timedelta(weeks=index)
        week = index + 1
        rows.append(
            {
                "cohort_id": COHORT_ID,
                "title": title,
                "scheduled_at_local": scheduled.strftime("%Y-%m-%d %H:%M"),
                "timezone": TIMEZONE,
                "duration_minutes": str(DURATION_MINUTES),
                "grace_minutes": str(GRACE_MINUTES),
                # The week is data, not a derivation of the date. Rescheduling
                # any of these must not change which question its Part B form
                # asks, or which lesson number its exit ticket names.
                "week_index": str(week),
                "teacher_question": TEACHER_QUESTIONS.get(week, ""),
            }
        )

    # Overlaps Session 5 (week index 4) by starting 30 minutes into it.
    overlap_start = FIRST_SUNDAY + timedelta(weeks=4, minutes=30)
    rows.append(
        {
            "cohort_id": COHORT_ID,
            "title": OVERLAP_SESSION_TITLE,
            "scheduled_at_local": overlap_start.strftime("%Y-%m-%d %H:%M"),
            "timezone": TIMEZONE,
            "duration_minutes": str(DURATION_MINUTES),
            "grace_minutes": str(GRACE_MINUTES),
            # No week: a makeup session is not a week of the rotation, and Part B
            # is simply not run for it. An unnumbered session is a legal state.
            "week_index": "",
            "teacher_question": "",
        }
    )
    return rows


# ---------------------------------------------------------------------------
# Part A — the exit ticket
# ---------------------------------------------------------------------------


def load_default_questions() -> dict[str, object]:
    """The cohort default, minus the file-only keys `load_file` also drops."""
    raw = json.loads(DEFAULT_QUESTIONS.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if k not in ("status", "_comment")}


def build_session_override(default: dict[str, object]) -> dict[str, object]:
    """Session 3's exit ticket: the default with one question added, one
    removed and one moved.

    A full snapshot rather than a diff, because that is what an override is: the
    form a session gets is exactly this, and a later edit to the default does not
    reach it. The three changes are the three a teacher actually makes.
    """
    by_key = {q["key"]: q for q in default["questions"]}  # type: ignore[index]
    added = {
        "key": "q_budget_parts",
        "type": "checkboxes",
        "title": "Which parts of the budget did you look at closely today?",
        "description": "Tick as many as apply.",
        "required": False,
        "options": ["Revenue", "Operating budget", "Capital budget", "Reserves"],
        "allow_other": True,
        "shuffle": False,
    }
    order = [
        "q_first_name",
        "q_last_name",
        # Moved: the takeaway before the rating, so the rating is given after
        # the fellow has thought about what they learned.
        "q_biggest_takeaway",
        "q_session_rating",
        added["key"],
        "q_more_of",
        # Removed: q_less_of. One fewer free-text box in a week that adds one.
        "q_open_questions",
        "q_other_feedback",
    ]
    questions = [added if key == added["key"] else by_key[key] for key in order]
    return {
        "_comment": [
            "Session 3's exit ticket, customised by the demo before its form is "
            "published (`cufa questions set --session <id> --file <this>`).",
            "Against the default: q_budget_parts added (checkboxes, with Other), "
            "q_less_of removed, q_biggest_takeaway moved above q_session_rating.",
        ],
        "schema_version": default.get("schema_version", 1),
        "title": default["title"],
        "description": default["description"],
        "questions": questions,
    }


def build_late_edit(override: dict[str, object]) -> dict[str, object]:
    """A second edit to Session 3, attempted after its form is published.

    It must be refused. Changing a live form's questions would split one
    session's answers across two question sets, with nothing in either to say
    which fellow saw which. The content differs from the override on purpose,
    so the refusal cannot be mistaken for "nothing changed".
    """
    questions = [dict(q) for q in override["questions"]]  # type: ignore[union-attr]
    for question in questions:
        if question["key"] == "q_budget_parts":
            question["required"] = True
    return {
        "_comment": [
            "A late edit to Session 3, made after its form was published. "
            "`cufa questions set` must refuse it: the questions lock at publish."
        ],
        "schema_version": override.get("schema_version", 1),
        "title": override["title"],
        "description": override["description"],
        "questions": questions,
    }


def questions_for_week(week: int, default: dict, override: dict) -> list[dict]:
    """The answerable questions a week's form asks, in form order."""
    source = override if week == OVERRIDE_WEEK else default
    return [q for q in source["questions"] if q["type"] in ANSWERABLE]


# Invented answers, in the register fellows actually write in. Every one of them
# is legal input and every one is stored verbatim: free text is COUNTED, never
# graded, so "ok" and the 600-character answer are worth exactly the same.
PARAGRAPH_ANSWERS = {
    "q_biggest_takeaway": [
        "A civic problem is only a problem once you can say who it affects.",
        "The people affected are findable if you look at who shows up to complain.",
        "A budget is a list of priorities written in numbers.",
        "Interviewing is mostly listening and not filling the silence.",
        "Coalitions hold when everyone gets something they already wanted.",
    ],
    "q_more_of": [
        "More time in breakout rooms.",
        "More real examples from other cities.",
        "More time for questions at the end.",
        "Case studies from places like where I live.",
    ],
    "q_less_of": [
        "Less time on the slides at the start.",
        "Fewer readings before the session.",
        "Nothing, the pace was fine.",
        "Less switching between tabs.",
    ],
    "q_open_questions": [
        "Who actually decides what goes in the budget?",
        "How do we find the decision-maker for our own project?",
        "What happens if the council just says no?",
        "Can we use this for the Solvathon?",
    ],
    "q_other_feedback": [
        "Thanks, this was a good one.",
        "The audio cut out for a few minutes in the middle.",
        "Could the slides be shared before the session?",
    ],
}
GENERIC_PARAGRAPHS = ["It was useful.", "Nothing to add.", "Good session."]
SPANISH = {
    "q_biggest_takeaway": "El presupuesto muestra lo que la ciudad considera importante.",
    "q_more_of": "Más tiempo en grupos pequeños, por favor.",
    "q_less_of": "Menos diapositivas al principio.",
    "q_open_questions": "¿Quién decide al final?",
    "q_other_feedback": "Gracias por la sesión.",
}
EMOJI = ["🙂", "👍", "🤔", "🔥🔥", "🙏"]
ONE_WORD = ["ok", "Budgets.", "idk", "More.", "none"]
VERY_LONG = (
    "I think the thing that actually landed for me is that a budget is a "
    "document about priorities rather than about money, and that once you can "
    "read one you can see which things somebody decided mattered without ever "
    "having to ask them, which is a bit unsettling but also means the "
    "information is just sitting there in public the whole time and nobody "
    "reads it, and I want to go and read our district's one now and then maybe "
    "bring it to the next session so we can pull it apart together."
)

# How each response is filled in. Every week has every shape; Session 3 adds the
# two only its added checkbox question can produce.
ANSWER_SHAPES = [
    "all_answered",
    "required_only",
    "whitespace",
    "emoji",
    "one_word",
    "very_long",
    "non_english",
    "scale_low",
    "scale_high",
]
OVERRIDE_SHAPES = ["multi_checkbox", "other_option"]

# Shapes that leave optional questions unanswered.
SKIPS_OPTIONAL = {"required_only", "scale_low", "scale_high"}


def _answer(
    question: dict, shape: str, fellow: dict[str, str], week: int, slot: int
) -> list[str] | None:
    """One question's answer as the API returns it: a list of strings, or None
    when the fellow left it blank (the API omits unanswered questions)."""
    kind = question["type"]
    key = question["key"]
    required = bool(question.get("required"))
    if not required and shape in SKIPS_OPTIONAL:
        return None

    if kind == "short_answer":
        first, _, last = fellow["full_name"].partition(" ")
        value = {"q_first_name": first, "q_last_name": last}.get(key, "yes")
        if shape == "whitespace":
            # Stored exactly as typed. Trimming is a read-time choice.
            value = f"  {value} "
        return [value]

    if kind == "linear_scale":
        scale = question.get("scale") or {"low": 1, "high": 5}
        low, high = int(scale["low"]), int(scale["high"])
        if shape == "scale_low":
            return [str(low)]
        if shape == "scale_high":
            return [str(high)]
        # Somewhere in the upper-middle, varying by week and slot.
        span = max(1, high - low)
        return [str(low + 1 + (week + slot) % span)]

    if kind in ("checkboxes", "multiple_choice", "dropdown"):
        options = list(question.get("options") or [])
        if not options:
            return None
        if kind == "checkboxes" and shape == "multi_checkbox":
            return options[:3]
        if kind == "checkboxes" and shape == "other_option" and question.get("allow_other"):
            # A Forms "Other" comes back as the typed text itself, beside any
            # boxes that were ticked. It is not one of the options.
            return [options[2], "the school district's share"]
        return [options[(week + slot) % len(options)]]

    # paragraph
    pool = PARAGRAPH_ANSWERS.get(key, GENERIC_PARAGRAPHS)
    if shape == "whitespace":
        if not required:
            return ["   "]
        return [f"  {pool[(week + slot) % len(pool)]}\n"]
    if shape == "emoji":
        return [EMOJI[(week + slot + len(key)) % len(EMOJI)]]
    if shape == "one_word":
        return [ONE_WORD[(week + slot + len(key)) % len(ONE_WORD)]]
    if shape == "non_english":
        return [SPANISH.get(key, "Todo bien.")]
    if shape == "very_long" and key == "q_biggest_takeaway":
        return [VERY_LONG]
    return [pool[(week + slot) % len(pool)]]


def _answers(
    questions: list[dict], shape: str, fellow: dict[str, str], week: int, slot: int
) -> dict[str, list[str]]:
    answers: dict[str, list[str]] = {}
    for question in questions:
        value = _answer(question, shape, fellow, week, slot)
        if value is not None:
            answers[question["key"]] = value
    return answers


# Submissions whose timing is the point. On the Forms API path the form implies
# the session, so none of these can land in the wrong one; what they test is
# whether the submit time is inside that session's window, inclusive at both
# ends. The window is [start − grace, start + duration + grace].
TIMING_CASES = {
    # week index (0-based) -> [(timing, local offset from scheduled start)]
    0: [
        ("window_start_edge", timedelta(minutes=-GRACE_MINUTES)),
        ("window_end_edge", timedelta(minutes=DURATION_MINUTES + GRACE_MINUTES)),
    ],
    # The link reached somebody two days before the lesson.
    3: [("two_days_early", timedelta(days=-2))],
    # Somebody filled it in over breakfast the next day.
    6: [("next_morning", timedelta(hours=14))],
}
IN_WINDOW_TIMINGS = {"in_window", "window_start_edge", "window_end_edge"}

# When the exit ticket goes out: ten minutes before the scheduled end. The
# week-1 form said it stayed open for ten minutes after the session, which the
# 15-minute grace covers.
EXIT_TICKET_RELEASE = timedelta(minutes=DURATION_MINUTES - 10)


def _pick_fellows(start: int, count: int, total: int) -> list[int]:
    """`count` distinct roster indexes from `start`, skipping the fellow who
    answers only Part B. Distinct, so no fellow submits twice to one form by
    accident — the duplicate case is added on purpose, below."""
    picked: list[int] = []
    index = start
    while len(picked) < count:
        candidate = index % total
        if candidate != PART_B_ONLY_INDEX and candidate not in picked:
            picked.append(candidate)
        index += 1
    return picked


def build_api_responses(
    default: dict | None = None, override: dict | None = None
) -> dict[str, list[dict[str, object]]]:
    """Responses per session, as the Forms API would return them, keyed by
    question KEY.

    The seeding script resolves each key to the question id the provisioned
    form actually uses, through `part_a_form_question` — the same table ingest
    resolves through — so the fixture loads whether or not a Drive copy
    preserved ids.
    """
    default = default or load_default_questions()
    override = override or build_session_override(default)
    fellows = build_fellows()
    per_session: dict[str, list[dict[str, object]]] = {}

    for index, title in enumerate(LESSONS):
        week = index + 1
        scheduled = FIRST_SUNDAY + timedelta(weeks=index)
        questions = questions_for_week(week, default, override)
        shapes = ANSWER_SHAPES + (OVERRIDE_SHAPES if week == OVERRIDE_WEEK else [])
        timing_cases = TIMING_CASES.get(index, [])
        released = scheduled + EXIT_TICKET_RELEASE

        slots = [(shape, "in_window", None) for shape in shapes] + [
            ("all_answered", timing, offset) for timing, offset in timing_cases
        ]
        chosen = _pick_fellows(index * 3, len(slots), len(fellows))
        rows: list[dict[str, object]] = []

        for slot, ((shape, timing, offset), fellow_index) in enumerate(zip(slots, chosen)):
            fellow = fellows[fellow_index]
            if offset is None:
                submitted = released + timedelta(seconds=40 + slot * 37)
            else:
                submitted = scheduled + offset
            rows.append(
                {
                    "kind": shape,
                    "timing": timing,
                    "email": fellow["primary_email"],
                    "submitted_at": _rfc3339(submitted),
                    "answers": _answers(questions, shape, fellow, week, slot),
                }
            )

        # An address that is not on the roster. Must still produce a check-in,
        # and must land in the identity review queue.
        if index == 1:
            rows.append(
                {
                    "kind": "unknown_email",
                    "timing": "in_window",
                    "email": "someone.else@example.invalid",
                    "submitted_at": _rfc3339(released + timedelta(minutes=4)),
                    "answers": _answers(
                        questions,
                        "required_only",
                        {"full_name": "Someone Else"},
                        week,
                        0,
                    ),
                }
            )

        # An exact duplicate: same address, same second. Two responses in, one
        # row out — this is the idempotency key doing its job.
        if index == 2:
            rows.append(dict(rows[0], kind="exact_duplicate"))

        per_session[title] = rows

    return per_session


# ---------------------------------------------------------------------------
# Part B — the end-of-session check-in
# ---------------------------------------------------------------------------

# The rotation, mirrored from config/rotation.json. Duplicated here on purpose:
# the fixture set has to be generatable without importing the application, and a
# test asserts the two agree — which is a stronger guarantee than sharing the
# constant would be, because it would catch the config being edited too.
ROTATION_BY_WEEK = {
    1: "teacher_question", 2: "muddiest_point", 3: "application",
    4: "teacher_question", 5: "muddiest_point", 6: "application",
    7: "teacher_question", 8: "muddiest_point", 9: "application",
    10: "teacher_question",
}

MUDDIEST_ANSWERS = [
    "I still don't get where the surplus actually goes.",
    "How do you find out who the decision-maker even is?",
    "The difference between the operating and capital budget.",
    "Nothing really, it was clear.",
    "Who do you talk to first if the office won't answer?",
    "I lost the thread when we got to the line items.",
    "Why some line items are locked and some aren't.",
    "How long any of this normally takes.",
]

APPLICATION_ANSWERS = [
    "I'd map who the bus route change actually affects before I write anything.",
    "Use the budget trick on our school's activity fund.",
    "Ask the three questions on my project's first interview.",
    "Try the one-page ask with the neighbourhood association.",
    "Find out who signs off on the park proposal.",
]

TEACHER_ANSWERS = [
    "That the money was already allocated before anyone was asked.",
    "How much of it was decided in one meeting.",
    "That I could just look the whole thing up.",
    "Nobody had asked the people it affects.",
]

# Takeaways, chosen to cover what free text actually arrives as. Every one of
# these is legal input and every one produces a row — they are COUNTED, never
# graded, so "ok" and the 600-character one are worth exactly the same here.
TAKEAWAYS = [
    ("substantive", "Budgets have line items you can question, and most people never do."),
    ("substantive", "The affected people are findable if you look at who shows up to complain."),
    ("one_word", "Budgets."),
    ("one_word", "ok"),
    ("whitespace_only", "   "),
    ("emoji_only", "🙂"),
    ("blank", ""),
    (
        "very_long",
        "I think the thing that actually landed for me is that a budget is a "
        "document about priorities rather than about money, and that once you can "
        "read one you can see which things somebody decided mattered without ever "
        "having to ask them, which is a bit unsettling but also means the "
        "information is just sitting there in public the whole time and nobody "
        "reads it, and I want to go and read our district's one now.",
    ),
]

CONFIDENCE_VALUES = ["1", "2", "3", "4", "5", "6", "7"]
# Out of range and unparseable. These must land as NULL with the raw value kept,
# and must NEVER be clamped to 1 or 7 — a clamped 8 is a plausible number
# invented from a broken form.
CONFIDENCE_BAD = ["0", "8", "four"]

SHOUTOUTS = [
    ("single_name", "Kestrel"),
    ("comma_separated", "Kestrel, Lorne, Nevin"),
    ("and_separated", "Ingram and Jessamy"),
    ("ampersand", "Halcyon & Glenna"),
    ("full_name", "Delphine Dunmore"),
    ("non_roster_name", "Ms Aldergrove from the district office"),
    # Matches two fellows, because two of them are called this. Must resolve to
    # unresolved and a review entry.
    ("ambiguous_first_name", "Marisol"),
    ("blank", ""),
]

# Ticked on a small number of submissions, in one place in the file, so the
# fixture is easy to find and easy to reason about.
HELP_AT = {("Session 3 — Reading a budget", 2), ("Session 8 — Writing the ask", 1)}

# One fellow answers the same value every time for five sessions running. This
# has to be flagged as a DATA QUALITY issue on the responses and must not appear
# in any participation number.
STRAIGHTLINER_INDEX = 7
STRAIGHTLINE_VALUE = "4"
STRAIGHTLINE_SESSIONS = 5

# Independence: one fellow answers Part B and never Part A, another answers
# Part A and never Part B. Both are valid and neither backfills the other.
PART_B_ONLY_INDEX = 17
PART_A_ONLY_INDEX = 18


def rotating_answer(kind: str, week: int, slot: int) -> str:
    """A plausible answer for the kind of question this week asked."""
    if kind == "muddiest_point":
        return MUDDIEST_ANSWERS[(week * 3 + slot) % len(MUDDIEST_ANSWERS)]
    if kind == "application":
        return APPLICATION_ANSWERS[(week * 2 + slot) % len(APPLICATION_ANSWERS)]
    return TEACHER_ANSWERS[(week + slot) % len(TEACHER_ANSWERS)]


def build_part_b_responses() -> dict[str, list[dict[str, object]]]:
    """Responses per session, as the Part B Forms API would return them.

    Keyed by SLOT rather than by question id: which id a field ends up with
    depends on whether the Drive copy preserved them, and a fixture that pinned
    ids would only be loadable under one of the two possibilities. The seeding
    script resolves slot -> question id through ``form_question_map``, which is
    the same table ingest resolves through.
    """
    fellows = build_fellows()
    per_session: dict[str, list[dict[str, object]]] = {}

    for index, title in enumerate(LESSONS):
        week = index + 1
        kind = ROTATION_BY_WEEK[week]
        scheduled = FIRST_SUNDAY + timedelta(weeks=index)
        # Released at the END of the lesson: 5 minutes before the scheduled
        # finish, which is inside the window rather than after it.
        released = scheduled + timedelta(minutes=DURATION_MINUTES - 5)
        rows: list[dict[str, object]] = []

        for slot in range(8):
            fellow_index = (index * 2 + slot) % len(fellows)
            if fellow_index == PART_A_ONLY_INDEX:
                # This fellow never answers Part B.
                fellow_index = (fellow_index + 1) % len(fellows)
            fellow = fellows[fellow_index]

            takeaway_kind, takeaway = TAKEAWAYS[slot % len(TAKEAWAYS)]
            shoutout_kind, shoutout = SHOUTOUTS[(index + slot) % len(SHOUTOUTS)]

            if fellow_index == STRAIGHTLINER_INDEX and week <= STRAIGHTLINE_SESSIONS:
                confidence, confidence_kind = STRAIGHTLINE_VALUE, "straightlining"
            elif slot == 6 and index < len(CONFIDENCE_BAD):
                confidence, confidence_kind = CONFIDENCE_BAD[index], "out_of_range"
            elif slot == 7:
                confidence, confidence_kind = "", "blank"
            else:
                confidence = CONFIDENCE_VALUES[(index + slot) % len(CONFIDENCE_VALUES)]
                confidence_kind = "in_range"

            rows.append(
                {
                    "kind": f"{confidence_kind}/{takeaway_kind}/{shoutout_kind}",
                    "email": fellow["primary_email"],
                    "submitted_at": _rfc3339(released + timedelta(seconds=30 + slot * 41)),
                    "confidence": confidence,
                    "takeaway": takeaway,
                    "rotating_kind": kind,
                    "rotating": rotating_answer(kind, week, slot),
                    "shoutout": shoutout,
                    "help": (title, slot) in HELP_AT,
                }
            )

        # A fellow who answers Part B and never Part A. Part B is not evidence
        # for Part A and must never be used to backfill it.
        if index == 1:
            rows.append(
                {
                    "kind": "part_b_without_part_a",
                    "email": fellows[PART_B_ONLY_INDEX]["primary_email"],
                    "submitted_at": _rfc3339(released + timedelta(minutes=6)),
                    "confidence": "5",
                    "takeaway": "I only made the second half but the mapping bit stuck.",
                    "rotating_kind": kind,
                    "rotating": rotating_answer(kind, week, 1),
                    "shoutout": "",
                    "help": False,
                }
            )

        # An address that is not on the roster: still a row, still in the
        # identity review queue, never a dropped observation.
        if index == 2:
            rows.append(
                {
                    "kind": "unknown_email",
                    "email": "someone.else@example.invalid",
                    "submitted_at": _rfc3339(released + timedelta(minutes=7)),
                    "confidence": "6",
                    "takeaway": "Sat in on this one.",
                    "rotating_kind": kind,
                    "rotating": rotating_answer(kind, week, 2),
                    "shoutout": "Kestrel",
                    "help": False,
                }
            )

        # An exact duplicate: same address, same second. Two responses in, one
        # row out.
        if index == 3:
            rows.append(dict(rows[0], kind="exact_duplicate"))

        per_session[title] = rows

    return per_session


def build_manual_export() -> list[dict[str, str]]:
    """A CSV as exported from a manually created form.

    Deliberately awkward: columns in an unexpected order, an unrecognised extra
    column, timestamps with no offset marker (which is exactly why
    --sheet-timezone has no default), a row inside exactly one session's
    window, rows outside every window, rows inside two overlapping windows, and
    rows either side of a DST boundary.

    A hand-made form's email column is whatever its owner configured, so none
    of these rows can show Google verified the address. That is why the one
    inside a single window goes to a human rather than counting as attended.
    """
    fellows = build_fellows()
    rows: list[dict[str, str]] = []

    def row(kind: str, fellow_index: int, local: datetime, rating: str, device: str) -> None:
        rows.append(
            {
                "kind": kind,
                # Column order here is intentionally not the order the parser
                # expects; header matching must be by name, not position.
                "How would you rate today's session?": rating,
                "Device": device,
                "Timestamp": local.strftime("%Y-%m-%d %H:%M:%S"),
                "Email Address": fellows[fellow_index]["primary_email"],
            }
        )

    session3 = FIRST_SUNDAY + timedelta(weeks=2)
    session5 = FIRST_SUNDAY + timedelta(weeks=4)

    # Inside Session 3's window and no other. On this path the address is
    # unverified, so the row is recorded and waits for a human.
    row("single_window", 11, session3 + timedelta(minutes=83), "4", "Chromebook")

    # Inside BOTH Session 5 and the overlapping makeup session.
    row("ambiguous_window", 12, session5 + timedelta(minutes=45), "5", "iPhone")
    row("ambiguous_window", 13, session5 + timedelta(minutes=50), "4", "Android")

    # Outside every window — a fellow who filled the form in the following
    # afternoon. Still an observation.
    row("outside_all_windows", 14, session5 + timedelta(days=1, hours=6), "3", "laptop")

    # Either side of the US fall-back boundary, 2026-11-01. 01:30 is EDT
    # (UTC-4) and 03:30 is EST (UTC-5); a parser that ignores the zone gets both
    # wrong by an hour or four and never says so.
    row("dst_before_fallback", 15, datetime(2026, 11, 1, 1, 30, 0), "5", "iPad")
    row("dst_after_fallback", 16, datetime(2026, 11, 1, 3, 30, 0), "5", "iPad")

    return rows


# What adjudication must conclude for each case, by rule name. Recorded in the
# manifest so the acceptance checks compare against the fixture rather than
# against a number somebody copied into them.
MANUAL_RULES = {
    "single_window": "unverified_email_in_window",
    "ambiguous_window": "ambiguous_session",
    "outside_all_windows": "outside_all_windows",
    "dst_before_fallback": "outside_all_windows",
    "dst_after_fallback": "outside_all_windows",
}


def expected_rules(
    api_responses: dict[str, list[dict[str, object]]], manual: list[dict[str, str]]
) -> dict[str, int]:
    counts = {
        "verified_email_in_window": 0,
        "outside_session_window": 0,
        "unverified_email_in_window": 0,
        "outside_all_windows": 0,
        "ambiguous_session": 0,
    }
    for rows in api_responses.values():
        for row in rows:
            if row["kind"] == "exact_duplicate":
                continue  # one check-in, already counted
            if row["timing"] in IN_WINDOW_TIMINGS:
                counts["verified_email_in_window"] += 1
            else:
                counts["outside_session_window"] += 1
    for entry in manual:
        counts[MANUAL_RULES[entry["kind"]]] += 1
    return counts


def _write_json(path: Path, payload: object, *, sort_keys: bool = True) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=sort_keys, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_fixtures(out_dir: Path) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)

    fellows = build_fellows()
    with (out_dir / "roster.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["fellow_id", "full_name", "primary_email", "status"]
        )
        writer.writeheader()
        writer.writerows(fellows)

    sessions = build_sessions()
    with (out_dir / "sessions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cohort_id", "title", "scheduled_at_local", "timezone",
                "duration_minutes", "grace_minutes",
                "week_index", "teacher_question",
            ],
        )
        writer.writeheader()
        writer.writerows(sessions)

    default = load_default_questions()
    override = build_session_override(default)
    # Unsorted, so each question reads key-first the way the default file does:
    # these two are meant to be opened by a person following the demo.
    _write_json(out_dir / OVERRIDE_FILE, override, sort_keys=False)
    _write_json(out_dir / LATE_EDIT_FILE, build_late_edit(override), sort_keys=False)

    api_responses = build_api_responses(default, override)
    (out_dir / "api_responses.json").write_text(
        json.dumps(api_responses, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manual = build_manual_export()
    with (out_dir / "manual_form_export.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "How would you rate today's session?", "Device", "Timestamp",
                "Email Address",
            ],
        )
        writer.writeheader()
        for entry in manual:
            writer.writerow({k: v for k, v in entry.items() if k != "kind"})

    part_b_responses = build_part_b_responses()
    (out_dir / "api_responses_b.json").write_text(
        json.dumps(part_b_responses, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    api_total = sum(len(rows) for rows in api_responses.values())
    duplicates = sum(
        1 for rows in api_responses.values() for row in rows if row["kind"] == "exact_duplicate"
    )
    b_total = sum(len(rows) for rows in part_b_responses.values())
    b_duplicates = sum(
        1
        for rows in part_b_responses.values()
        for row in rows
        if row["kind"] == "exact_duplicate"
    )
    b_help = sum(
        1 for rows in part_b_responses.values() for row in rows if row["help"]
    )
    b_shoutout_names = sum(
        len([f for f in _rough_split(str(row["shoutout"])) if f])
        for rows in part_b_responses.values()
        for row in rows
    )

    manifest = {
        "seed": SEED,
        "cohort_id": COHORT_ID,
        "timezone": TIMEZONE,
        "fellows": len(fellows),
        "sessions": len(sessions),
        "api_responses": api_total,
        "manual_rows": len(manual),
        "responses_total": api_total + len(manual),
        "intentional_duplicates": duplicates,
        # The duplicate is in the fixture set on purpose, so the number of rows
        # that should exist in `checkin` is the total minus the duplicates.
        "expected_checkin_rows": api_total + len(manual) - duplicates,
        # One current decision per check-in, by the rule that made it.
        "expected_rules": expected_rules(api_responses, manual),
        "part_a_questions": {
            "default_file": str(DEFAULT_QUESTIONS.relative_to(REPO_ROOT)),
            "default_question_keys": [q["key"] for q in default["questions"]],
            "override_week": OVERRIDE_WEEK,
            "override_file": OVERRIDE_FILE,
            "override_question_keys": [q["key"] for q in override["questions"]],
            "late_edit_file": LATE_EDIT_FILE,
        },
        "part_b_responses": b_total,
        "part_b_intentional_duplicates": b_duplicates,
        "expected_checkin_b_rows": b_total - b_duplicates,
        "expected_help_requests": b_help,
        # Approximate: the real splitter is cufa.shoutouts.split_names, and this
        # deliberately does not import it — the fixture set must be generatable
        # without the application installed. A test compares the two.
        "approx_shoutout_names": b_shoutout_names,
        "straightliner_fellow": (
            f"CU-{2600 + STRAIGHTLINER_INDEX:04d}"
        ),
        "part_b_only_fellow": f"CU-{2600 + PART_B_ONLY_INDEX:04d}",
        "part_a_only_fellow": f"CU-{2600 + PART_A_ONLY_INDEX:04d}",
        "kinds": sorted(
            {str(row["kind"]) for rows in api_responses.values() for row in rows}
            | {str(row["timing"]) for rows in api_responses.values() for row in rows}
            | {row["kind"] for row in manual}
        ),
        "part_b_kinds": sorted(
            {str(row["kind"]) for rows in part_b_responses.values() for row in rows}
        ),
    }
    _write_json(out_dir / "manifest.json", manifest)
    return manifest


def _rough_split(value: str) -> list[str]:
    """A crude stand-in for cufa.shoutouts.split_names, for the manifest only.

    Deliberately its own implementation rather than an import: these fixtures
    have to be generatable before the package is installed. A test asserts the
    real splitter agrees with the number recorded here, which catches the two
    drifting apart — a shared import would not, because it cannot disagree with
    itself.
    """
    import re as _re

    return [
        piece.strip()
        for piece in _re.split(r"[,;/&\n]+|\band\b|\+", value or "")
        if piece.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="fixtures", help="output directory")
    args = parser.parse_args()

    manifest = write_fixtures(Path(args.out))
    print(f"fixtures written to {args.out}/")
    for key in (
        "fellows", "sessions", "api_responses", "manual_rows",
        "responses_total", "intentional_duplicates", "expected_checkin_rows",
        "part_b_responses", "expected_checkin_b_rows", "expected_help_requests",
    ):
        print(f"  {key:<28}{manifest[key]}")
    print(f"  part A edge cases covered   {len(manifest['kinds'])}")
    print(f"  part B combinations covered {len(manifest['part_b_kinds'])}")
    print("  part A decisions expected, by rule:")
    for rule, count in manifest["expected_rules"].items():
        print(f"    {rule:<30}{count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
