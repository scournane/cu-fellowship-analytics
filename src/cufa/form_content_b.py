"""The six fields of the end-of-session form, and the order they appear in.

**The order is load-bearing, not cosmetic.** Do not reorder these for visual
balance, or because a different order looks tidier in the Forms editor:

* Field 1 is a **click, not a text box.** Surveys that open with a simple
  multiple-choice complete at 89% against 83% for open-ended. The cheapest
  possible first action is what gets someone into the form at all.
* Field 5, the help checkbox, is **last**, after rapport is built. Sensitive
  items placed early measurably raise abandonment — not just of that item, of
  the whole form.
* Only fields 1-3 are **required**. Forcing responses measurably hurts
  completion, and forcing either the shoutout or the help box would be actively
  wrong: an optional field that must be answered is not optional, and a
  compulsory "do you need help?" is a different question from a voluntary one.

Six fields is the design, not a starting point. Going from three questions to
four drops completion by 18%; response rates fall roughly 60% past eight
minutes; fatigued respondents straight-line about a third more often. The
rotating slot exists precisely so that a seventh field is never needed — three
dimensions get collected across ten weeks while nobody ever faces more than four
questions at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Semantic slots, in the order they appear on the form. Index position is the
#: contract: ``form_question_map`` matches by item index, which this application
#: controls at creation time, and never by title text.
SLOT_CONFIDENCE = "confidence"
SLOT_TAKEAWAY = "takeaway"
SLOT_ROTATING = "rotating"
SLOT_SHOUTOUT = "shoutout"
SLOT_HELP = "help"

#: Every slot, in form order. The help slot is conditional — see
#: ``item_requests``.
SLOT_ORDER: tuple[str, ...] = (
    SLOT_CONFIDENCE,
    SLOT_TAKEAWAY,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
    SLOT_HELP,
)

#: The slots that must be present on every Part B form, help or no help.
REQUIRED_SLOTS: tuple[str, ...] = (
    SLOT_CONFIDENCE,
    SLOT_TAKEAWAY,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
)

TEMPLATE_TITLE = "Civic Innovators end-of-session check-in (template — do not submit)"

CONFIDENCE_TITLE = "I could explain today's topic to someone"
CONFIDENCE_LOW_LABEL = "Not at all"
CONFIDENCE_HIGH_LABEL = "Easily"
CONFIDENCE_LOW = 1
CONFIDENCE_HIGH = 7

TAKEAWAY_TITLE = "In one sentence, what are you taking away from today?"
TAKEAWAY_HELP = (
    "One sentence is genuinely enough. Nobody is marking the writing — we are "
    "reading what landed, not how it is phrased."
)

SHOUTOUT_TITLE = "Who helped you today?"
SHOUTOUT_HELP = (
    "Optional. A name, or a few names. This is not a vote and there is no "
    "leaderboard — it is so the people doing quiet work get noticed."
)

HELP_TITLE = "Before you go"
HELP_OPTION = "I'd like someone to check in with me"
HELP_DESCRIPTION = (
    "Optional, and it counts for nothing either way — ticking this never affects "
    "your attendance, your participation, or anything else recorded about you. It "
    "goes to one person, who will get in touch."
)

#: Placeholder wording on the template form itself. Every session form overwrites
#: it with the week's actual question before anyone sees it.
ROTATING_PLACEHOLDER_TITLE = "(this week's question — set when the form is created)"

# The header a fellow reads before answering. Plain language, specific about what
# happens to the answer, and specific about the help box costing nothing —
# because the box only works if that is believed, and it is only believable if it
# is said out loud and is true.
#
# TODO(retention): CU has not defined how long these records are kept. Replace
# the bracketed sentence with the real period once CU decides one. Do not
# substitute a plausible-sounding number: whatever is written here is what
# fellows were told, and an assumed retention period is the kind of thing nobody
# revisits. The help checkbox has its own, separate retention question — see
# docs/safeguarding.md.
HEADER_NOTICE = """\
Two minutes at the end of today's lesson.

What we collect: your email address (confirmed by Google, so you don't type it),
the time you submit this, and your answers below.

What it's used for: seeing what landed and what didn't, so the next lesson can
change. The "what's still unclear" answers get grouped into themes and shown to
your teacher — as themes, not as individual answers with names on them.

What is NOT done with it: nothing here is graded, scored, or used to rate you.
No AI reads your answers to judge you. Writing more does not score higher than
writing less.

Who sees it: Civics Unplugged staff.

How long we keep it: [TODO(retention) — CU has not set a retention period yet.]

The last box asks whether you'd like someone to check in with you. It is
optional, it affects nothing that is counted about you, and it goes to one named
person who will get in touch.\
"""


# Shown in the console wherever a staffer might be tempted to add a field.
# Somebody will want "just one more question", and the honest answer to that is
# a number, not a preference.
SURVEY_LENGTH_RATIONALE = """\
This form is six fields by design, not by accident, and the rotating slot exists
so that a seventh is never needed.

Opening with a click rather than a text box: 89% completion versus 83%. Going
from three questions to four: completion drops 18%. Past eight minutes: response
rates fall roughly 60%, and fatigued respondents repeat the same answer about a
third more often — which corrupts the confidence trend rather than merely
shortening it.

The field order is load-bearing too. The help checkbox is last because sensitive
items placed early measurably raise abandonment of the whole form, not just of
that item.

If a new question genuinely has to be asked, put it in the rotating slot for one
week rather than adding a field to every week.\
"""


def session_form_title(session_title: str, scheduled_local: str) -> str:
    """The title a fellow sees, and staff see in Drive."""
    return f"End-of-session — {session_title} ({scheduled_local})"


def session_form_description(session_title: str) -> str:
    return f"{session_title}\n\n{HEADER_NOTICE}"


@dataclass(frozen=True)
class ItemSpec:
    """One field: which slot it is, where it sits, and what it says.

    ``index`` is the authoritative link between a form item and its meaning.
    ``title`` is recorded alongside it as the exact text shown to fellows, but is
    never used to identify the item afterwards.

    ``item`` is the full Forms item body, and both the create and the update
    request are derived from it. That is not tidiness — see ``update_request``.
    """

    slot: str
    index: int
    title: str
    item: dict[str, Any]

    @property
    def request(self) -> dict[str, Any]:
        """The ``createItem`` request for this field."""
        return {"createItem": {"item": self.item, "location": {"index": self.index}}}

    @property
    def update_request(self) -> dict[str, Any]:
        """The ``updateItem`` request that retitles this field in place.

        **The whole item body goes in, not just the field being changed.**
        Sending ``{"item": {"title": ...}}`` with ``updateMask: "title"`` looks
        correct, passes every offline check, and is rejected by the live API
        with:

            400 Invalid requests[N]: A QuestionItem or QuestionGroupItem cannot
                be changed into a non question Item type by an Update operation.

        An item body with no ``questionItem`` reads to Google as a request to
        turn the question into a text block. The mask still decides what is
        actually applied; the body has to describe the item it remains.

        This is the call that retitles the rotating slot every week, so getting
        it wrong breaks Part B provisioning outright while Part A carries on
        working — Part A has always sent its full item body.
        """
        mask = "title,description" if "description" in self.item else "title"
        return {
            "updateItem": {
                "item": self.item,
                "location": {"index": self.index},
                "updateMask": mask,
            }
        }


def _text_item(title: str, description: str, *, required: bool) -> dict[str, Any]:
    """The item body for a short-text question."""
    item: dict[str, Any] = {
        "title": title,
        "questionItem": {
            "question": {"required": required, "textQuestion": {"paragraph": False}}
        },
    }
    if description:
        item["description"] = description
    return item


def item_specs(rotating_title: str, *, include_help: bool) -> list[ItemSpec]:
    """The fields to create, in order, for one Part B form.

    ``include_help`` is decided by ``config/help_routing.json`` having a named
    recipient, and by nothing else. When it is false the form ends at the
    shoutout — see ``cufa.help_routing`` for why a help box with nowhere to route
    is worse than no help box.
    """
    specs = [
        ItemSpec(
            slot=SLOT_CONFIDENCE,
            index=0,
            title=CONFIDENCE_TITLE,
            item={
                "title": CONFIDENCE_TITLE,
                "questionItem": {
                    "question": {
                        "required": True,
                        # Seven points, not five. Scales under 5 points lose
                        # reliability (Preston & Colman); 5-point scales induce
                        # interpolation — respondents try to answer *between* two
                        # values — and this field gets graphed, so the extra
                        # resolution is the difference between a readable trend
                        # and a stepped one. Labels on the endpoints only:
                        # labelling every point invites people to read the words
                        # instead of the position.
                        "scaleQuestion": {
                            "low": CONFIDENCE_LOW,
                            "high": CONFIDENCE_HIGH,
                            "lowLabel": CONFIDENCE_LOW_LABEL,
                            "highLabel": CONFIDENCE_HIGH_LABEL,
                        },
                    }
                },
            },
        ),
        ItemSpec(
            slot=SLOT_TAKEAWAY,
            index=1,
            title=TAKEAWAY_TITLE,
            item=_text_item(TAKEAWAY_TITLE, TAKEAWAY_HELP, required=True),
        ),
        ItemSpec(
            slot=SLOT_ROTATING,
            index=2,
            title=rotating_title,
            item=_text_item(rotating_title, "", required=True),
        ),
        ItemSpec(
            slot=SLOT_SHOUTOUT,
            index=3,
            title=SHOUTOUT_TITLE,
            # Deliberately NOT required. A compulsory "who helped you?" produces
            # a name from someone who did not have one to give.
            item=_text_item(SHOUTOUT_TITLE, SHOUTOUT_HELP, required=False),
        ),
    ]

    if include_help:
        specs.append(
            ItemSpec(
                slot=SLOT_HELP,
                index=4,
                title=HELP_TITLE,
                item={
                    "title": HELP_TITLE,
                    "description": HELP_DESCRIPTION,
                    "questionItem": {
                        "question": {
                            "required": False,
                            "choiceQuestion": {
                                "type": "CHECKBOX",
                                "options": [{"value": HELP_OPTION}],
                            },
                        }
                    },
                },
            )
        )

    return specs


def item_requests(rotating_title: str, *, include_help: bool) -> list[dict[str, Any]]:
    """Just the ``batchUpdate`` requests, in creation order."""
    return [spec.request for spec in item_specs(rotating_title, include_help=include_help)]


def expected_slots(*, include_help: bool) -> tuple[str, ...]:
    """Which slots a correctly provisioned form must have mapped."""
    return SLOT_ORDER if include_help else REQUIRED_SLOTS


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_HIGH_LABEL",
    "CONFIDENCE_LOW",
    "CONFIDENCE_LOW_LABEL",
    "CONFIDENCE_TITLE",
    "HEADER_NOTICE",
    "HELP_DESCRIPTION",
    "HELP_OPTION",
    "HELP_TITLE",
    "ItemSpec",
    "REQUIRED_SLOTS",
    "ROTATING_PLACEHOLDER_TITLE",
    "SHOUTOUT_HELP",
    "SHOUTOUT_TITLE",
    "SLOT_CONFIDENCE",
    "SLOT_HELP",
    "SLOT_ORDER",
    "SLOT_ROTATING",
    "SLOT_SHOUTOUT",
    "SLOT_TAKEAWAY",
    "SURVEY_LENGTH_RATIONALE",
    "TAKEAWAY_HELP",
    "TAKEAWAY_TITLE",
    "TEMPLATE_TITLE",
    "expected_slots",
    "item_requests",
    "item_specs",
    "session_form_description",
    "session_form_title",
]
