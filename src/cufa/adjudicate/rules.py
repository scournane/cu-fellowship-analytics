"""The rule table: which address, and when.

Attendance is two facts and nothing else: the response came from a
**Google-verified address**, and it was submitted **inside the session window**
``[start - grace, end + grace]``. The exit-ticket answers are collected and
counted; they are never read here, and no model ever judges them. A wrong,
short or blank answer from someone who was in the room is still someone who was
in the room.

Every outcome is reproducible from the row alone — no model, no clock, no
network. That matters more than it sounds: re-running adjudication over history
after changing the definition of "attended" produces the same answers for the
same inputs, so a changed definition is a real experiment rather than a new
roll of the dice.

``in_window`` is supplied by the caller rather than recomputed here, and the
engine computes it with :func:`cufa.timeutil.session_window` — the same
definition ingest matches with and ``v_checkin_resolved.in_session_window``
reports. Three definitions of the edge would disagree at exactly the minute
someone asks about.

Two outcomes are deliberately *not* decided:

* **A CSV row inside a window** is ``needs_review``. The sheet export carries a
  typed address, not a verified one, so the thing that makes a timestamp
  evidence of *this fellow* is missing. A person looks.
* **A timestamp inside two overlapping windows** cannot be attributed to
  either. That is a scheduling bug, and answering it either way would hide it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleOutcome:
    """What the rules concluded, and under which name."""

    status: str  # 'attended' | 'not_attended' | 'needs_review'
    rule_name: str
    confidence: float | None


# A verified address, inside the window. 0.7 rather than 1.0 because the
# timestamp says the form was submitted while the lesson ran, not that the
# person was watching it — which is the most a form can say.
VERIFIED_EMAIL_IN_WINDOW = RuleOutcome("attended", "verified_email_in_window", 0.7)

# Matched to a session (the form it came in on, or its one window at ingest)
# but outside that session's window as it stands now. 0.6 rather than 1.0: the
# commonest cause is a session time entered wrongly, or a form opened late from
# the Zoom chat, not a fellow who was absent. The review tab exists for this.
OUTSIDE_SESSION_WINDOW = RuleOutcome("not_attended", "outside_session_window", 0.6)

# A sheet export: in exactly one window, but from an address Google did not
# verify. A person decides.
UNVERIFIED_EMAIL_IN_WINDOW = RuleOutcome("needs_review", "unverified_email_in_window", None)

# A submission that matched no session window is evidence of a submission, not
# of attendance at any particular lesson. 0.6 for the same reason as above.
OUTSIDE_ALL_WINDOWS = RuleOutcome("not_attended", "outside_all_windows", 0.6)

# A timestamp inside two overlapping windows.
AMBIGUOUS_SESSION = RuleOutcome("needs_review", "ambiguous_session", None)

#: Every outcome the rules can produce, by name, in precedence order.
RULES: dict[str, RuleOutcome] = {
    outcome.rule_name: outcome
    for outcome in (
        AMBIGUOUS_SESSION,
        OUTSIDE_ALL_WINDOWS,
        OUTSIDE_SESSION_WINDOW,
        VERIFIED_EMAIL_IN_WINDOW,
        UNVERIFIED_EMAIL_IN_WINDOW,
    )
}

#: The rules that put a check-in on the "Outside the window" review tab.
OUTSIDE_WINDOW_RULES: tuple[str, ...] = (
    OUTSIDE_SESSION_WINDOW.rule_name,
    OUTSIDE_ALL_WINDOWS.rule_name,
)


def apply_rules(source: str, session_match: str, in_window: bool | None) -> RuleOutcome:
    """Map one observation to its outcome.

    ``session_match`` is what ingest recorded (``matched`` | ``none`` |
    ``ambiguous``). ``in_window`` is whether the submission time falls inside
    the matched session's window *as the session is scheduled now*, so a
    reschedule re-judges; it is ``None`` when there is no matched session.
    """
    if session_match == "ambiguous":
        return AMBIGUOUS_SESSION
    if session_match == "none":
        return OUTSIDE_ALL_WINDOWS
    if session_match == "matched" and in_window is not None:
        if not in_window:
            return OUTSIDE_SESSION_WINDOW
        if source == "forms_api":
            return VERIFIED_EMAIL_IN_WINDOW
        if source == "csv":
            return UNVERIFIED_EMAIL_IN_WINDOW

    # Unreachable for the observations ingest writes today. A new source or
    # match value added later should surface for a human rather than default
    # to a guess.
    return RuleOutcome("needs_review", "unhandled_observation", None)
