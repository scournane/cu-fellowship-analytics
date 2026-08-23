"""The words on the form, in one place.

Kept out of the provisioning code because these are the strings a CU staff
member is most likely to want to change, and they should not have to read an
API integration to find them.
"""

from __future__ import annotations

TEMPLATE_TITLE = "Civic Innovators check-in (template — do not submit)"

# Shown as the form's header. Plain language, no jargon, and specific about what
# happens to the answer. Research on adolescent survey participation is
# consistent that transparency drives honest responding — a fellow who cannot
# tell what a form is for fills it in defensively or not at all.
#
# TODO(retention): CU has not defined how long check-in records are kept.
# Replace the bracketed sentence below with the real retention period once CU
# decides one. Do not substitute a plausible-sounding number — whatever is
# written here becomes the policy fellows were told, and an assumed retention
# period is the kind of thing nobody revisits.
HEADER_NOTICE = """\
This is the attendance check-in for today's live lesson.

What we collect: your email address (confirmed by Google, so you don't type it),
the time you submit this, and today's passphrase.

Who sees it: Civics Unplugged staff.

What it's used for: recording that you were at today's lesson. Attendance is one
part of fellowship participation.

How long we keep it: [TODO(retention) — CU has not set a retention period yet.]

If something goes wrong — you missed the passphrase, you joined late, your
connection dropped — submit anyway and tell us in the answer. Nothing here is
thrown away, and a person reviews anything the system can't decide.\
"""

QUESTION_HELP = (
    "The word your teacher said out loud and put on screen during today's "
    "lesson. Spelling doesn't have to be perfect."
)


def session_form_title(session_title: str, scheduled_local: str) -> str:
    """The title a fellow sees, and staff see in Drive."""
    return f"Check-in — {session_title} ({scheduled_local})"


def session_form_description(session_title: str) -> str:
    return f"{session_title}\n\n{HEADER_NOTICE}"
