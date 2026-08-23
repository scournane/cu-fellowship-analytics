#!/usr/bin/env python3
"""Assert the acceptance criteria against the database the demo just built.

`make demo` printing a report proves it ran. These checks prove it ran
*correctly* — chiefly that nothing was dropped and that the decision table has
exactly one live row per observation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cufa.db import connection, fetch_all, fetch_one

PASS = "  ok   "
FAIL = "  FAIL "


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", default="demo")
    parser.add_argument("--fixtures", default="fixtures")
    args = parser.parse_args()

    manifest = json.loads(
        (Path(args.fixtures) / "manifest.json").read_text(encoding="utf-8")
    )
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{PASS if ok else FAIL}{label}{('  — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    print("Acceptance checks")
    print("=" * 62)

    with connection() as conn:
        # 1. Never drop a submission. Every fixture response is in `checkin`,
        #    whatever its passphrase or session outcome — minus the one
        #    deliberate duplicate, which is there to prove idempotency.
        actual = (fetch_one(conn, "select count(*) as n from checkin") or {})["n"]
        expected = manifest["expected_checkin_rows"]
        check(
            f"every fixture response is in checkin ({actual} rows)",
            actual == expected,
            f"expected {expected} "
            f"({manifest['responses_total']} responses − "
            f"{manifest['intentional_duplicates']} intentional duplicate)",
        )

        # 2. Exactly one current decision per check-in, enforced by the index.
        orphans = (
            fetch_one(
                conn,
                """
                select count(*) as n from checkin c
                 where not exists (
                     select 1 from attendance_decision d
                      where d.checkin_id = c.checkin_id and d.superseded_at is null
                 )
                """,
            )
            or {}
        )["n"]
        check("every check-in has a current decision", orphans == 0, f"{orphans} without one")

        duplicates = fetch_all(
            conn,
            """
            select checkin_id, count(*) as n
              from attendance_decision
             where superseded_at is null
             group by checkin_id having count(*) > 1
            """,
        )
        check("exactly one current decision per check-in", not duplicates)

        # 3. The index that enforces it actually exists.
        index = fetch_one(
            conn,
            "select indexdef from pg_indexes where indexname = "
            "'attendance_decision_one_current'",
        )
        check(
            "partial unique index is present",
            bool(index and "superseded_at IS NULL" in index["indexdef"]),
        )

        # 4. All three session-match outcomes were exercised and all wrote rows.
        matches = {
            row["session_match"]: row["n"]
            for row in fetch_all(
                conn,
                "select session_match, count(*) as n from checkin group by session_match",
            )
        }
        for kind in ("matched", "none", "ambiguous"):
            check(
                f"session_match '{kind}' present and written",
                matches.get(kind, 0) > 0,
                f"{matches.get(kind, 0)} rows",
            )

        # 5. Every passphrase comparison outcome appears.
        outcomes = {
            row["passphrase_match"]: row["n"]
            for row in fetch_all(
                conn,
                "select passphrase_match, count(*) as n from checkin group by passphrase_match",
            )
        }
        for kind in ("exact", "fuzzy", "mismatch", "not_set", "no_session"):
            check(
                f"passphrase_match '{kind}' present",
                outcomes.get(kind, 0) > 0,
                f"{outcomes.get(kind, 0)} rows",
            )

        # 6. An unknown address did not block ingest and did reach the queue.
        unresolved = (
            fetch_one(
                conn,
                "select count(*) as n from identity_unresolved where cohort_id = %s",
                (args.cohort,),
            )
            or {}
        )["n"]
        check("unknown address queued for review", unresolved > 0, f"{unresolved} address(es)")

        # 7. needs_review was never collapsed into not_attended.
        bad = (
            fetch_one(
                conn,
                """
                select count(*) as n from attendance_decision
                 where status = 'needs_review' and attended is not null
                """,
            )
            or {}
        )["n"]
        check("needs_review never carries an attended verdict", bad == 0)

        # 8. Provisioning verified the publish state for every form.
        unverified = (
            fetch_one(
                conn,
                "select count(*) as n from session_form where publish_verified_at is null",
            )
            or {}
        )["n"]
        check("every provisioned form had its publish state verified", unverified == 0)

        # 9. Latency is populated where a session matched, NULL where none did.
        leaked = (
            fetch_one(
                conn,
                "select count(*) as n from checkin "
                "where session_id is null and latency_seconds is not null",
            )
            or {}
        )["n"]
        check("latency is NULL when no session matched", leaked == 0)

        # 10. The extra CSV column survived into extra_fields.
        preserved = (
            fetch_one(
                conn,
                "select count(*) as n from checkin where extra_fields ? 'Device'",
            )
            or {}
        )["n"]
        check("unexpected CSV column preserved in extra_fields", preserved > 0,
              f"{preserved} rows")

    print("=" * 62)
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all acceptance checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
