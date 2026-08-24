#!/usr/bin/env python3
"""Assert the acceptance criteria against the database the demo just built.

`make demo` printing a report proves it ran. These checks prove it ran
*correctly* — chiefly that nothing was dropped, that the decision table has
exactly one live row per observation, and that the help-request table is
reachable from nothing.
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


        # ------------------------------------------------------------------
        # Part B
        # ------------------------------------------------------------------
        print("-" * 62)

        # 11. Every Part B fixture response is in checkin_b, whatever the
        #     validity of any individual field. Invariant 1 again: a dropped
        #     observation is unrecoverable.
        actual_b = (fetch_one(conn, "select count(*) as n from checkin_b") or {})["n"]
        expected_b = manifest["expected_checkin_b_rows"]
        check(
            f"every Part B fixture response is in checkin_b ({actual_b} rows)",
            actual_b == expected_b,
            f"expected {expected_b} "
            f"({manifest['part_b_responses']} responses − "
            f"{manifest['part_b_intentional_duplicates']} intentional duplicate)",
        )

        # 12. Confidence out of range: NULL, raw kept, never clamped.
        clamped = (
            fetch_one(
                conn,
                """
                select count(*) as n from checkin_b
                 where extra_fields ? '_confidence_rejected_raw'
                   and confidence_raw is not null
                """,
            )
            or {}
        )["n"]
        rejected = (
            fetch_one(
                conn,
                "select count(*) as n from checkin_b "
                "where extra_fields ? '_confidence_rejected_raw'",
            )
            or {}
        )["n"]
        check(
            "out-of-range confidence was rejected, not clamped",
            rejected > 0 and clamped == 0,
            f"{rejected} rejected, {clamped} wrongly kept a value",
        )
        in_range = (
            fetch_one(
                conn,
                "select count(*) as n from checkin_b where confidence_raw is not null "
                "and confidence_raw not between 1 and 7",
            )
            or {}
        )["n"]
        check("no stored confidence is outside 1-7", in_range == 0)

        # 13. Free text is preserved verbatim, whitespace included.
        whitespace = (
            fetch_one(
                conn,
                "select count(*) as n from checkin_b "
                "where takeaway_text ~ '^[[:space:]]+$'",
            )
            or {}
        )["n"]
        check(
            "a whitespace-only takeaway survived verbatim",
            whitespace > 0,
            f"{whitespace} row(s)",
        )

        # 14. Every answer was resolved through a complete question map.
        unmapped = (
            fetch_one(
                conn,
                """
                select count(*) as n
                  from session_form sf
                 where sf.part = 'b'
                   and (select count(*) from form_question_map m
                         where m.form_id = sf.form_id) < 4
                """,
            )
            or {}
        )["n"]
        check("every provisioned Part B form has a complete question map", unmapped == 0)

        # 15. The rotating question text is a SNAPSHOT, not a reconstruction.
        rotating = fetch_all(
            conn,
            """
            select distinct rotating_kind, question_text
              from form_question_map
             where slot = 'rotating'
             order by rotating_kind
            """,
        )
        kinds = {row["rotating_kind"] for row in rotating}
        check(
            "all three rotating kinds were asked across the ten weeks",
            kinds == {"teacher_question", "muddiest_point", "application"},
            ", ".join(sorted(k for k in kinds if k)),
        )
        blank_text = [r for r in rotating if not (r["question_text"] or "").strip()]
        check("every rotating slot snapshotted the text it showed", not blank_text)

        # 16. Shoutouts: split, resolved conservatively, never auto-guessed.
        shoutouts = {
            row["match_method"]: row["n"]
            for row in fetch_all(
                conn,
                "select match_method, count(*) as n from peer_shoutout "
                "group by match_method",
            )
        }
        check(
            "shoutout names were extracted",
            sum(shoutouts.values()) > 0,
            f"{sum(shoutouts.values())} name(s)",
        )
        check(
            "some resolved to exactly one fellow",
            shoutouts.get("exact_name", 0) > 0,
            f"{shoutouts.get('exact_name', 0)}",
        )
        check(
            "ambiguous and non-roster names are unresolved, not guessed",
            shoutouts.get("unresolved", 0) > 0,
            f"{shoutouts.get('unresolved', 0)} in the review queue",
        )
        guessed = (
            fetch_one(
                conn,
                "select count(*) as n from peer_shoutout "
                "where match_method = 'unresolved' and named_fellow_id is not null",
            )
            or {}
        )["n"]
        check("nothing unresolved was silently linked anyway", guessed == 0)

        # 17. Help requests: recorded, and NOWHERE else.
        help_rows = (fetch_one(conn, "select count(*) as n from help_request") or {})["n"]
        check(
            "help requests were recorded",
            help_rows == manifest["expected_help_requests"],
            f"{help_rows}, expected {manifest['expected_help_requests']}",
        )
        help_column = (
            fetch_one(
                conn,
                """
                select count(*) as n from information_schema.columns
                 where table_schema = 'public' and table_name = 'checkin_b'
                   and column_name ilike '%help%'
                """,
            )
            or {}
        )["n"]
        check("the help checkbox is not a column on checkin_b", help_column == 0)
        help_in_views = (
            fetch_one(
                conn,
                """
                select count(*) as n from information_schema.view_column_usage
                 where table_schema = 'public' and table_name = 'help_request'
                """,
            )
            or {}
        )["n"]
        check("no view reads help_request", help_in_views == 0)
        help_policies = (
            fetch_one(
                conn,
                "select count(*) as n from pg_policies "
                "where tablename = 'help_request'",
            )
            or {}
        )["n"]
        rls_on = fetch_one(
            conn,
            "select relrowsecurity from pg_class where relname = 'help_request'",
        )
        check(
            "help_request has RLS on and no permissive policy",
            bool(rls_on and rls_on["relrowsecurity"]) and help_policies == 0,
        )

        # 18. Straight-lining is detected, and is only a data-quality flag.
        runs = fetch_all(
            conn,
            "select fellow_id, confidence_raw, run_length "
            "from v_confidence_straightline",
        )
        check(
            "a fellow answering identically 4+ sessions running is flagged",
            any(r["run_length"] >= 4 for r in runs),
            f"{len(runs)} run(s)",
        )

        # 19. Both parts are independent: each has someone the other does not.
        b_only = (
            fetch_one(
                conn,
                """
                select count(*) as n from (
                    select distinct v.fellow_id from v_checkin_b_resolved v
                     where v.fellow_id is not null
                       and not exists (
                           select 1 from v_checkin_resolved a
                            where a.fellow_id = v.fellow_id
                       )
                ) t
                """,
            )
            or {}
        )["n"]
        a_only = (
            fetch_one(
                conn,
                """
                select count(*) as n from (
                    select distinct a.fellow_id from v_checkin_resolved a
                     where a.fellow_id is not null
                       and not exists (
                           select 1 from v_checkin_b_resolved v
                            where v.fellow_id = a.fellow_id
                       )
                ) t
                """,
            )
            or {}
        )["n"]
        check(
            "a fellow submitted Part B and not Part A",
            b_only > 0,
            f"{b_only} fellow(s)",
        )
        check(
            "a fellow submitted Part A and not Part B",
            a_only > 0,
            f"{a_only} fellow(s)",
        )

        # 20. Latency is derived per part, and NULL where nothing matched.
        leaked_b = (
            fetch_one(
                conn,
                "select count(*) as n from checkin_b "
                "where session_id is null and latency_seconds is not null",
            )
            or {}
        )["n"]
        check("Part B latency is NULL when no session matched", leaked_b == 0)

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
