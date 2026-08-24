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

Part A:

  exact match · case variant · surrounding whitespace · trailing punctuation ·
  edit-distance-1 typo · conversational answer · a plainly wrong answer ·
  a blank answer · a submission outside every session window · a timestamp
  inside two overlapping windows · an address not on the roster · an exact
  duplicate submission · a submission either side of a DST boundary ·
  an unexpected extra column

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
import random
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
# so every rotating kind appears and the wrap is reachable. Session 4 has no
# passphrase set: that is legal and must adjudicate as `not_set`, not as a
# failure.
LESSONS = [
    ("Session 1 — What a civic problem is", "lantern"),
    ("Session 2 — Finding the people affected", "harbor"),
    ("Session 3 — Reading a budget", "quorum"),
    ("Session 4 — Interview practice", None),
    ("Session 5 — Building a coalition", "trellis"),
    ("Session 6 — Presenting to power", "sequoia"),
    ("Session 7 — Where the money actually goes", "meridian"),
    ("Session 8 — Writing the ask", "cobblestone"),
    ("Session 9 — Testing it with someone", "wayfarer"),
    ("Session 10 — What happens next", "compass"),
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
OVERLAP_SESSION = ("Session 5 — makeup (overlaps deliberately)", "trellis")


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
    for index, (title, passphrase) in enumerate(LESSONS):
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
                "passphrase": passphrase or "",
                # The week is data, not a derivation of the date. Rescheduling
                # any of these must not change which question its Part B form
                # asks.
                "week_index": str(week),
                "teacher_question": TEACHER_QUESTIONS.get(week, ""),
            }
        )

    # Overlaps Session 5 (week index 4) by starting 30 minutes into it.
    overlap_start = FIRST_SUNDAY + timedelta(weeks=4, minutes=30)
    rows.append(
        {
            "cohort_id": COHORT_ID,
            "title": OVERLAP_SESSION[0],
            "scheduled_at_local": overlap_start.strftime("%Y-%m-%d %H:%M"),
            "timezone": TIMEZONE,
            "duration_minutes": str(DURATION_MINUTES),
            "grace_minutes": str(GRACE_MINUTES),
            "passphrase": OVERLAP_SESSION[1] or "",
            # No week: a makeup session is not a week of the rotation, and Part B
            # is simply not run for it. An unnumbered session is a legal state.
            "week_index": "",
            "teacher_question": "",
        }
    )
    return rows


def _answer_variants(passphrase: str | None, rng: random.Random) -> list[tuple[str, str]]:
    """(kind, typed answer) pairs covering every comparison outcome."""
    if not passphrase:
        # No passphrase set: whatever people type is not a comparison at all.
        return [
            ("not_set_blank", ""),
            ("not_set_text", "n/a"),
            ("not_set_word", "here"),
        ]

    typo = passphrase[:2] + ("z" if passphrase[2] != "z" else "x") + passphrase[3:]
    return [
        ("exact", passphrase),
        ("case_variant", passphrase.upper()),
        ("whitespace_variant", f"  {passphrase} "),
        ("punctuation_variant", f"{passphrase}."),
        ("typo_distance_1", typo),
        ("conversational", f"the word was {passphrase}"),
        ("conversational_hedged", f"{passphrase} i think?"),
        ("wrong_answer", "committee"),
        ("blank_answer", ""),
        ("admits_missing", "sorry I missed it"),
    ]


def build_api_responses() -> dict[str, list[dict[str, str]]]:
    """Responses per session, as the Forms API would return them.

    Every one lands inside its own session's window, because on the API path the
    form itself implies the session.
    """
    rng = random.Random(SEED)
    fellows = build_fellows()
    per_session: dict[str, list[dict[str, str]]] = {}

    for index, (title, passphrase) in enumerate(LESSONS):
        scheduled = FIRST_SUNDAY + timedelta(weeks=index)
        variants = _answer_variants(passphrase, rng)
        rows: list[dict[str, str]] = []

        # Announcement is ~18 minutes in — mid-session, at a moment nobody could
        # have predicted, which is the entire point of the design.
        announce = scheduled + timedelta(minutes=18)

        for slot, (kind, answer) in enumerate(variants):
            fellow_index = (index * 3 + slot) % len(fellows)
            if fellow_index == PART_B_ONLY_INDEX:
                # This fellow answers Part B and never Part A. Both halves of
                # that case have to exist for the independence test to mean
                # anything.
                fellow_index = (fellow_index + 1) % len(fellows)
            fellow = fellows[fellow_index]
            submitted = announce + timedelta(seconds=40 + slot * 37)
            rows.append(
                {
                    "kind": kind,
                    "email": fellow["primary_email"],
                    "submitted_at": _rfc3339(submitted),
                    "passphrase": answer,
                }
            )

        # An address that is not on the roster. Must still produce a check-in,
        # and must land in the identity review queue.
        if index == 1:
            rows.append(
                {
                    "kind": "unknown_email",
                    "email": "someone.else@example.invalid",
                    "submitted_at": _rfc3339(announce + timedelta(minutes=4)),
                    "passphrase": passphrase or "",
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

    for index, (title, _passphrase) in enumerate(LESSONS):
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
    --sheet-timezone has no default), rows outside every window, rows inside two
    overlapping windows, and rows either side of a DST boundary.
    """
    fellows = build_fellows()
    rows: list[dict[str, str]] = []

    def row(kind: str, fellow_index: int, local: datetime, answer: str, device: str) -> None:
        rows.append(
            {
                "kind": kind,
                # Column order here is intentionally not the order the parser
                # expects; header matching must be by name, not position.
                "Today's passphrase": answer,
                "Device": device,
                "Timestamp": local.strftime("%Y-%m-%d %H:%M:%S"),
                "Email Address": fellows[fellow_index]["primary_email"],
            }
        )

    session5 = FIRST_SUNDAY + timedelta(weeks=4)

    # Inside BOTH Session 5 and the overlapping makeup session.
    row("ambiguous_window", 12, session5 + timedelta(minutes=45), "trellis", "iPhone")
    row("ambiguous_window", 13, session5 + timedelta(minutes=50), "Trellis", "Android")

    # Outside every window — a fellow who filled the form in the following
    # afternoon. Still an observation.
    row("outside_all_windows", 14, session5 + timedelta(days=1, hours=6), "trellis", "laptop")

    # Either side of the US fall-back boundary, 2026-11-01. 01:30 is EDT
    # (UTC-4) and 03:30 is EST (UTC-5); a parser that ignores the zone gets both
    # wrong by an hour or four and never says so.
    row("dst_before_fallback", 15, datetime(2026, 11, 1, 1, 30, 0), "lantern", "iPad")
    row("dst_after_fallback", 16, datetime(2026, 11, 1, 3, 30, 0), "lantern", "iPad")

    return rows


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
                "duration_minutes", "grace_minutes", "passphrase",
                "week_index", "teacher_question",
            ],
        )
        writer.writeheader()
        writer.writerows(sessions)

    api_responses = build_api_responses()
    (out_dir / "api_responses.json").write_text(
        json.dumps(api_responses, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manual = build_manual_export()
    with (out_dir / "manual_form_export.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["Today's passphrase", "Device", "Timestamp", "Email Address"]
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
            {row["kind"] for rows in api_responses.values() for row in rows}
            | {row["kind"] for row in manual}
        ),
        "part_b_kinds": sorted(
            {str(row["kind"]) for rows in part_b_responses.values() for row in rows}
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
