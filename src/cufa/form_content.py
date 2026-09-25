"""The fixed words around Part A, the exit ticket, in one place.

Part A's *questions* are no longer here: they are data, written and versioned
by staff (``part_a_questions``, ``question_sets``), with the team's own week-1
exit ticket as the cohort default in ``config/part_a_default_questions.json``.
What stays in code is the handful of strings that are not a question — the
template form's own title and notice, the Drive file name, and the reminder
staff see before they share the link.

Kept out of the provisioning code because these are the strings a CU staff
member is most likely to want to change, and they should not have to read an
API integration to find them.
"""

from __future__ import annotations

TEMPLATE_TITLE = "Civic Innovators exit ticket (template — do not submit)"

# The template form's header. Fellows never see the template — every session
# gets its own copy, retitled and re-described from its question set — so this
# is written for the staff member who opens it in Drive and wonders what it is.
#
# TODO(retention): CU has not defined how long check-in records are kept.
# Replace the bracketed sentence below with the real retention period once CU
# decides one. Do not substitute a plausible-sounding number — whatever is
# written here becomes the policy fellows were told, and an assumed retention
# period is the kind of thing nobody revisits.
HEADER_NOTICE = """\
This is the template every session's exit ticket is copied from. Do not share
it and do not submit it.

Each session's form is a copy of this one: the copy keeps this form's
"Collect email addresses → Verified" setting, and gets its title, description
and questions from the exit-ticket questions set in the console (Templates →
Default exit ticket questions, or the session's own page).

What the session forms collect: the fellow's email address (confirmed by
Google, so nobody types it), the time they submit, and their answers.
Attendance is the verified address plus a submit time inside the session's
window. Answers are read by staff, counted — never graded — and no AI reads
them.

How long we keep it: [TODO(retention) — CU has not set a retention period yet.]\
"""

#: Shown to staff wherever the Part A link is about to be shared — the console
#: puts it next to the link and the QR code. Both, not either: a QR code alone fails the fellow on a
#: phone who cannot scan their own screen, a link alone fails the one who joined
#: from a laptop with Zoom chat closed — and the window is minutes long.
LINK_REMINDER = (
    "Put the QR code on screen AND paste the link in the Zoom chat. Some fellows "
    "join on the phone they would scan with; others have chat hidden. Attendance "
    "counts only submissions inside the session window, so share both, and early."
)


def session_form_title(session_title: str, scheduled_local: str) -> str:
    """The Drive file name staff see. Fellows see the question set's own title."""
    return f"Exit ticket — {session_title} ({scheduled_local})"


__all__ = ["HEADER_NOTICE", "LINK_REMINDER", "TEMPLATE_TITLE", "session_form_title"]
