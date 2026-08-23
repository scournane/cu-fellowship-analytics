#!/usr/bin/env python3
"""Generate the synthetic fixture set the demo and the tests run against.

Deterministic: a fixed seed, no clock reads, no randomness that varies between
machines. Re-running produces byte-identical files, which is what lets
``make demo`` be re-run and compared.

Nothing here resembles a real fellow. Names are obviously invented and every
address is ``@example.invalid``, a reserved TLD that cannot be registered or
routed — so a fixture can never accidentally email a person.

The response set is built to cover every case the pipeline is supposed to
survive, including the ones a naive parser would drop:

  exact match · case variant · surrounding whitespace · trailing punctuation ·
  edit-distance-1 typo · conversational answer · a plainly wrong answer ·
  a blank answer · a submission outside every session window · a timestamp
  inside two overlapping windows · an address not on the roster · an exact
  duplicate submission · a submission either side of a DST boundary ·
  an unexpected extra column
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

# Six lessons, weekly. Session 4 has no passphrase set — that is legal and must
# adjudicate as `not_set`, not as a failure.
LESSONS = [
    ("Session 1 — What a civic problem is", "lantern"),
    ("Session 2 — Finding the people affected", "harbor"),
    ("Session 3 — Reading a budget", "quorum"),
    ("Session 4 — Interview practice", None),
    ("Session 5 — Building a coalition", "trellis"),
    ("Session 6 — Presenting to power", "sequoia"),
]

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
    return [
        {
            "fellow_id": f"CU-{2600 + index:04d}",
            "full_name": f"{FIRST_NAMES[index]} {LAST_NAMES[index]}",
            "primary_email": (
                f"{FIRST_NAMES[index].lower()}.{LAST_NAMES[index].lower()}@example.invalid"
            ),
            "status": "active",
        }
        for index in range(20)
    ]


def build_sessions() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, (title, passphrase) in enumerate(LESSONS):
        scheduled = FIRST_SUNDAY + timedelta(weeks=index)
        rows.append(
            {
                "cohort_id": COHORT_ID,
                "title": title,
                "scheduled_at_local": scheduled.strftime("%Y-%m-%d %H:%M"),
                "timezone": TIMEZONE,
                "duration_minutes": str(DURATION_MINUTES),
                "grace_minutes": str(GRACE_MINUTES),
                "passphrase": passphrase or "",
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
            fellow = fellows[(index * 3 + slot) % len(fellows)]
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

    api_total = sum(len(rows) for rows in api_responses.values())
    duplicates = sum(
        1 for rows in api_responses.values() for row in rows if row["kind"] == "exact_duplicate"
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
        "kinds": sorted(
            {row["kind"] for rows in api_responses.values() for row in rows}
            | {row["kind"] for row in manual}
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="fixtures", help="output directory")
    args = parser.parse_args()

    manifest = write_fixtures(Path(args.out))
    print(f"fixtures written to {args.out}/")
    for key in (
        "fellows", "sessions", "api_responses", "manual_rows",
        "responses_total", "intentional_duplicates", "expected_checkin_rows",
    ):
        print(f"  {key:<24}{manifest[key]}")
    print(f"  edge cases covered      {len(manifest['kinds'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
