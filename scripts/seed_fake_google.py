#!/usr/bin/env python3
"""Drive the FakeGoogleClient through the parts a human or Google would do.

This is demo scaffolding, not product code. It stands in for exactly three
things that are not `cufa` commands:

  --set-verified    a CU staff member opening the template and setting
                    Settings → Responses → Collect email addresses → Verified.
                    The demo performs it explicitly, so `cufa template verify`
                    is a real gate rather than a formality: run the demo without
                    this step and provisioning is blocked, which is the point.

  --seed-responses  fellows submitting the forms. Loads
                    fixtures/api_responses.json into the fake's provisioned
                    forms, matched by session title.

  --announce        the teacher pressing "Announce now" mid-lesson, stamped at
                    the fixture time so latency is reproducible.

  --seed-responses-b  the same, for the end-of-session form. Answers are keyed
                    by SLOT and resolved to question ids through
                    form_question_map — the same table ingest resolves through —
                    because which id a field ends up with depends on whether the
                    Drive copy preserved them, and a fixture that pinned ids
                    would only load under one of the two possibilities.

  --break-question-map  deletes one row from one form's question map, so the
                    demo can show `cufa pull --part b` REFUSING that form rather
                    than guessing which answer is which. Restored by
                    re-provisioning.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from cufa.config import get_settings
from cufa.db import connection, execute, fetch_all, fetch_one
from cufa.form_content_b import (
    HELP_OPTION,
    SLOT_CONFIDENCE,
    SLOT_HELP,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
    SLOT_TAKEAWAY,
)
from cufa.google.fake import FakeGoogleClient
from cufa.logging_setup import configure_logging, get_logger
from cufa.sessions import announce_now
from cufa.template import get_template
from cufa.timeutil import parse_rfc3339

log = get_logger("seed-fake-google")


def _client() -> FakeGoogleClient:
    settings = get_settings()
    if not settings.fake_google:
        raise SystemExit(
            "Refusing to run: CUFA_FAKE_GOOGLE is not set. This script only "
            "manipulates the fake client and must never touch a real account."
        )
    return FakeGoogleClient.restore(settings.fake_google_state)


def set_verified(part: str = "a") -> int:
    """Simulate the one manual step the API cannot do (trap 2).

    Per part, because email collection is a property of a form and is carried
    only by a Drive copy — Part A being verified says nothing about Part B.
    """
    client = _client()
    with connection() as conn:
        record = get_template(conn, part)
    if record is None:
        raise SystemExit(
            f"No part-{part} template in the database yet. Run "
            f"`cufa template create --part {part}` first."
        )

    client.simulate_human_sets_verified(record.form_id)
    print(
        f"[human step] template {record.form_id} (part {part}): email collection "
        "set to VERIFIED.\n"
        "             `cufa template verify` will now read that back and confirm it."
    )
    return 0


def seed_responses(fixtures_dir: Path) -> int:
    """Load the fixture responses into each session's provisioned form."""
    client = _client()
    payload = json.loads((fixtures_dir / "api_responses.json").read_text(encoding="utf-8"))

    seeded = 0
    with connection() as conn:
        for title, rows in payload.items():
            session = fetch_one(
                conn,
                """
                select s.session_id, sf.form_id
                  from "session" s
                  join session_form sf
                    on sf.session_id = s.session_id and sf.part = 'a'
                 where s.title = %s
                """,
                (title,),
            )
            if session is None:
                log.warning("no provisioned form for session %r; skipping", title)
                continue

            form_id = session["form_id"]
            existing = {r.response_id for r in client.forms[form_id].responses}
            fresh = [
                {"email": row["email"], "submitted_at": row["submitted_at"],
                 "passphrase": row["passphrase"]}
                for row in rows
            ]
            if existing:
                # Re-running the demo must not stack duplicate responses inside
                # the fake — the idempotency being demonstrated is the
                # pipeline's, and pre-duplicated input would hide a real bug.
                log.info("form %s already seeded (%d responses); skipping", form_id, len(existing))
                continue
            client.seed_responses(form_id, fresh)
            seeded += len(fresh)

    print(f"[fellows submit] seeded {seeded} response(s) into the fake forms")
    return 0


def announce(fixtures_dir: Path) -> int:
    """Stamp announced_at_utc for every session that has responses.

    Uses the fixture's announcement instant (18 minutes into the lesson) rather
    than "now", so latency values are reproducible across runs.
    """
    payload = json.loads((fixtures_dir / "api_responses.json").read_text(encoding="utf-8"))
    stamped = 0
    with connection() as conn:
        for title, rows in payload.items():
            if not rows:
                continue
            earliest = min(parse_rfc3339(row["submitted_at"]) for row in rows)
            session = fetch_one(
                conn, 'select session_id from "session" where title = %s', (title,)
            )
            if session is None:
                continue
            # The fixture's first submission is 40 seconds after the announcement.
            announce_now(conn, str(session["session_id"]), earliest - timedelta(seconds=40))
            stamped += 1
    print(f"[teacher announces] stamped announced_at_utc on {stamped} session(s)")
    return 0


SLOT_BY_FIXTURE_KEY = {
    "confidence": SLOT_CONFIDENCE,
    "takeaway": SLOT_TAKEAWAY,
    "rotating": SLOT_ROTATING,
    "shoutout": SLOT_SHOUTOUT,
}


def seed_responses_b(fixtures_dir: Path) -> int:
    """Load the Part B fixture responses into each session's Part B form.

    Answers go in keyed by question id, exactly as the API returns them. The ids
    come from ``form_question_map`` rather than from the fixture, because a Drive
    copy may or may not preserve them and the fixture must load either way.
    """
    client = _client()
    payload = json.loads(
        (fixtures_dir / "api_responses_b.json").read_text(encoding="utf-8")
    )

    seeded = skipped = 0
    with connection() as conn:
        for title, rows in payload.items():
            session = fetch_one(
                conn,
                """
                select s.session_id, sf.form_id
                  from "session" s
                  join session_form sf
                    on sf.session_id = s.session_id and sf.part = 'b'
                 where s.title = %s
                """,
                (title,),
            )
            if session is None:
                log.warning("no provisioned Part B form for session %r; skipping", title)
                continue

            form_id = session["form_id"]
            question_by_slot = {
                row["slot"]: row["question_id"]
                for row in fetch_all(
                    conn,
                    "select slot, question_id from form_question_map where form_id = %s",
                    (form_id,),
                )
            }
            if not question_by_slot:
                log.warning("form %s has no question map yet; skipping", form_id)
                continue

            if client.forms[form_id].responses:
                # Re-running the demo must not stack duplicate responses inside
                # the fake — the idempotency being demonstrated is the
                # pipeline's, and pre-duplicated input would hide a real bug.
                skipped += 1
                continue

            fresh = []
            for row in rows:
                answers = {}
                for key, slot in SLOT_BY_FIXTURE_KEY.items():
                    question_id = question_by_slot.get(slot)
                    if question_id is not None:
                        answers[question_id] = str(row.get(key, ""))
                if row.get("help") and SLOT_HELP in question_by_slot:
                    # A Forms checkbox answers as the option's own text.
                    answers[question_by_slot[SLOT_HELP]] = HELP_OPTION
                fresh.append(
                    {
                        "email": row["email"],
                        "submitted_at": row["submitted_at"],
                        "answers_by_id": answers,
                    }
                )

            client.seed_responses(form_id, fresh)
            seeded += len(fresh)

    print(
        f"[fellows submit] seeded {seeded} Part B response(s) into the fake forms"
        + (f"; {skipped} form(s) already had some" if skipped else "")
    )
    return 0


def break_question_map(title: str) -> int:
    """Delete one slot from one form's map, to show ingest refusing it.

    This is the failure the map exists to prevent, staged deliberately: without
    the mapping there is no way to tell a confidence rating from a takeaway, and
    guessing would produce numbers that look entirely plausible and are wrong.
    """
    with connection() as conn:
        session = fetch_one(
            conn,
            """
            select sf.form_id
              from "session" s
              join session_form sf
                on sf.session_id = s.session_id and sf.part = 'b'
             where s.title = %s
            """,
            (title,),
        )
        if session is None:
            raise SystemExit(f"No provisioned Part B form for session {title!r}")
        removed = execute(
            conn,
            "delete from form_question_map where form_id = %s and slot = %s",
            (session["form_id"], SLOT_TAKEAWAY),
        )
    print(
        f"[sabotage] removed the {SLOT_TAKEAWAY!r} row from form "
        f"{session['form_id']}'s question map ({removed} row). "
        "`cufa pull --part b` must now refuse this form."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set-verified", action="store_true")
    parser.add_argument("--part", default="a", choices=["a", "b"])
    parser.add_argument("--seed-responses", action="store_true")
    parser.add_argument("--seed-responses-b", action="store_true")
    parser.add_argument("--announce", action="store_true")
    parser.add_argument("--break-question-map", default=None, metavar="SESSION_TITLE")
    parser.add_argument("--fixtures", default="fixtures")
    args = parser.parse_args()
    configure_logging()

    fixtures_dir = Path(args.fixtures)
    if args.set_verified:
        return set_verified(args.part)
    if args.seed_responses:
        return seed_responses(fixtures_dir)
    if args.seed_responses_b:
        return seed_responses_b(fixtures_dir)
    if args.announce:
        return announce(fixtures_dir)
    if args.break_question_map:
        return break_question_map(args.break_question_map)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
