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
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from cufa.config import get_settings
from cufa.db import connection, fetch_all, fetch_one
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


def set_verified() -> int:
    """Simulate the one manual step the API cannot do (trap 2)."""
    client = _client()
    with connection() as conn:
        record = get_template(conn)
    if record is None:
        raise SystemExit("No template in the database yet. Run `cufa template create` first.")

    client.simulate_human_sets_verified(record.form_id)
    print(
        f"[human step] template {record.form_id}: email collection set to VERIFIED.\n"
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
                  join session_form sf on sf.session_id = s.session_id
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set-verified", action="store_true")
    parser.add_argument("--seed-responses", action="store_true")
    parser.add_argument("--announce", action="store_true")
    parser.add_argument("--fixtures", default="fixtures")
    args = parser.parse_args()
    configure_logging()

    fixtures_dir = Path(args.fixtures)
    if args.set_verified:
        return set_verified()
    if args.seed_responses:
        return seed_responses(fixtures_dir)
    if args.announce:
        return announce(fixtures_dir)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
