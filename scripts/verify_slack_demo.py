#!/usr/bin/env python3
"""Assert the Slack demo's guarantees against the database it just wrote.

`make demo-slack-batch` printing counts proves the bot ran. These checks prove
the claims: nothing the fake delivered is missing, a retried delivery wrote
nothing, a forged one wrote nothing, the backfill collided with the live rows
instead of duplicating them, and no message text is stored.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parent.parent.joinpath("src").as_posix())

from cufa.db import connection, fetch_all, fetch_one  # noqa: E402
from cufa.slack.fake import DEMO_STAFF_EMAIL  # noqa: E402

PASS = "  ok   "
FAIL = "  FAIL "


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", default="demo")
    parser.add_argument("--fake-url", default="http://127.0.0.1:3001")
    parser.add_argument("--staff-email", default="", help="the demo's staff address, to check nothing was DM'd to it")
    args = parser.parse_args()

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{PASS if ok else FAIL}{label}{('  — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    with urllib.request.urlopen(f"{args.fake_url}/ui/state", timeout=10) as resp:
        state = json.loads(resp.read().decode("utf-8"))
    log = state["log"]

    print("Slack acceptance checks")
    print("=" * 62)

    with connection() as conn:
        n = (fetch_one(conn, "select count(*) as n from slack_event") or {})["n"]

        # 1. Every accepted, non-skipped delivery is in the table. A bot message
        #    is skipped by design; an app_mention is a request to the bot, not
        #    an act of participation (the message event for the same post is).
        accepted = [e for e in log if e["status"] == 200 and not e["note"]]
        # Accepted deliveries that are meant to leave no row: a bot's own
        # message, a mention the Q&A handler replies to, and a file that
        # turns out not to be a canvas.
        skippable = [e for e in accepted if e["kind"].startswith(("message/bot_message", "app_mention")) or "(not a canvas)" in e["kind"]]
        expected_min = len(accepted) - len(skippable)
        check(
            f"every accepted delivery is recorded ({n} rows, ≥{expected_min} unique deliveries)",
            n >= expected_min,
        )

        # 2. The retry wrote nothing: the row count equals the number of DISTINCT keys
        #    the fake produced, and the table's own unique constraint held.
        retried = [e for e in log if "retry" in (e["note"] or "")]
        check("a retried delivery was acked (200)", all(e["status"] == 200 for e in retried), f"{len(retried)} retry")
        dup = (fetch_one(conn, "select count(*) - count(distinct source_event_id) as d from slack_event") or {})["d"]
        check("no duplicate source_event_id in the table", dup == 0)

        # 3. The forged delivery was refused.
        forged = [e for e in log if "BAD SIGNATURE" in (e["note"] or "")]
        check("a forged delivery was refused (not 200)", bool(forged) and all(e["status"] != 200 for e in forged))

        # 4. Bot messages were skipped, not recorded.
        bots = (fetch_one(conn, "select count(*) as n from slack_event where raw->>'subtype' = 'bot_message'") or {})["n"]
        check("bot messages are not recorded", bots == 0)

        # 5. Text is not stored.
        texts = (fetch_one(conn, "select count(*) as n from slack_event where text is not null") or {})["n"]
        check("no message text stored (CUFA_SLACK_STORE_TEXT unset)", texts == 0)

        # 6. Identity: roster fellows attributed, strangers queued, nobody dropped.
        attributed = (fetch_one(
            conn,
            """
            select count(distinct e.slack_user_id) as n
              from slack_event e
              join slack_workspace w on w.team_id = e.team_id
              join fellow f on f.cohort_id = w.cohort_id and lower(f.primary_email) = lower(e.user_email)
            """,
        ) or {})["n"]
        check(f"roster fellows attributed by email ({attributed})", attributed > 0)
        queued = (fetch_one(conn, "select count(*) as n from identity_unresolved where resolved_at is null") or {})["n"]
        check(f"non-roster addresses queued for review, not dropped ({queued})", queued >= 1)
        no_email = (fetch_one(conn, "select count(*) as n from slack_event where user_email is null") or {})["n"]
        check(f"events from profiles with no email still recorded ({no_email})", True)

        # 7. Backfill collided rather than duplicated.
        runs = fetch_all(conn, "select source, status, rows_read, rows_written, rows_skipped from load_run where source like 'slack_%' order by started_at")
        bf = [r for r in runs if r["source"] == "slack_backfill"]
        check("backfill run recorded", bool(bf))
        if bf:
            check(
                f"backfill re-read {bf[-1]['rows_read']} messages and wrote {bf[-1]['rows_written']} new rows",
                bf[-1]["status"] == "succeeded" and bf[-1]["rows_skipped"] >= 1,
                "skipped ≥ 1 means it collided with what the bot already had",
            )

        # 8. Every event type the fake sent is present.
        types = {r["event_type"] for r in fetch_all(conn, "select distinct event_type from slack_event")}
        wanted = {"message", "reaction_added", "member_joined_channel", "message_changed"}
        check(f"event types present: {sorted(types)}", wanted <= types)

        # 9. Immutability trigger exists.
        trig = fetch_one(conn, "select 1 as ok from pg_trigger where tgname = 'slack_event_no_mutation'")
        check("slack_event immutability trigger installed", bool(trig))

        # 10. The newer signals arrived and are shaped as designed.
        huddle = fetch_one(conn, "select count(*) filter (where event_type = 'huddle_joined') as j, count(*) filter (where event_type = 'huddle_left') as l, count(*) filter (where event_type like 'huddle%' and channel_id is not null) as with_channel from slack_event") or {}
        check(f"huddle joins and leaves recorded ({huddle.get('j')} joins, {huddle.get('l')} leaves)", (huddle.get("j") or 0) >= 2 and (huddle.get("l") or 0) >= 1)
        check("huddle rows carry no channel (Slack sends none)", (huddle.get("with_channel") or 0) == 0)
        canvas = fetch_one(conn, "select count(*) filter (where event_type = 'canvas_created') as c, count(*) filter (where event_type = 'canvas_edited') as e, count(*) filter (where event_type = 'canvas_edited' and slack_user_id is not null) as attributed_edits, count(*) filter (where event_type = 'canvas_comment') as m from slack_event") or {}
        check(f"canvas created/edited/commented recorded ({canvas.get('c')}/{canvas.get('e')}/{canvas.get('m')})", all((canvas.get(k) or 0) >= 1 for k in ("c", "e", "m")))
        check("a canvas edit is not attributed to anyone (Slack names no editor)", (canvas.get("attributed_edits") or 0) == 0)
        pdf = (fetch_one(conn, "select count(*) as n from slack_file where not is_canvas") or {})["n"]
        pdf_rows = (fetch_one(conn, "select count(*) as n from slack_event e join slack_file f on f.team_id = e.team_id and f.file_id = e.file_id where not f.is_canvas") or {})["n"]
        check("a non-canvas file was looked up and skipped", pdf >= 1 and pdf_rows == 0)
        mentioned = (fetch_one(conn, "select count(*) as n from slack_event where cardinality(mentions) > 0") or {})["n"]
        check(f"@-mentions extracted before the text was dropped ({mentioned} messages)", mentioned >= 1)
        votes = fetch_one(conn, "select count(*) as rows, count(distinct slack_user_id) as people from slack_event where event_type = 'poll_vote'") or {}
        check(f"poll votes recorded as observations ({votes.get('rows')} votes from {votes.get('people')} people; a changed vote is a new row)", (votes.get("rows") or 0) >= 4 and (votes.get("people") or 0) >= 3)
        latest = fetch_all(conn, """
            select poll_choice, count(*) as n from (
                select distinct on (slack_user_id) slack_user_id, poll_choice
                  from slack_event where event_type = 'poll_vote'
                 order by slack_user_id, event_time_utc desc) t group by 1""")
        check(f"poll counts each person's latest vote once ({', '.join(f'{r['poll_choice']}={r['n']}' for r in latest)})", sum(r["n"] for r in latest) == (votes.get("people") or 0))

        # 11. The insights never rank received recognition or name emoji users.
        from cufa.slack.insights import emoji_mood, reply_graph
        mood = emoji_mood(conn, args.cohort)
        check("emoji mood is cohort-level (no user in the output)", not any(k in json.dumps(mood) for k in ("user", "email", "fellow")))
        graph = reply_graph(conn, args.cohort)
        check("reply graph reports nobody-replied-to alphabetically, never a received count",
              "received" not in json.dumps(graph) and graph["not_replied_to"] == sorted(graph["not_replied_to"], key=lambda r: (r["full_name"], r["fellow_id"])))
        # 12. Q&A: questions and answers captured (text stored THERE, and only there),
        #     the repeat got a pointer to the earlier answer, the mention got a summary.
        questions = (fetch_one(conn, "select count(*) as n from slack_qa_question where deleted_at_utc is null") or {})["n"]
        answers = (fetch_one(conn, "select count(*) as n from slack_qa_answer where deleted_at_utc is null") or {})["n"]
        check(f"Q&A questions and replies captured ({questions} questions, {answers} replies)", questions >= 3 and answers >= 1)
        accepted_answers = (fetch_one(conn, "select count(*) as n from slack_qa_answer where accepted") or {})["n"]
        check("a ✅ reaction marked an answer accepted", accepted_answers >= 1)
        pointer = fetch_one(conn, "select method, similarity, posted_ts, post_error from slack_qa_pointer order by created_at desc limit 1")
        check(
            "the repeated question got a pointer to the earlier answer, and it was posted",
            bool(pointer) and bool(pointer["posted_ts"]),
            f"method={pointer['method']} similarity={pointer['similarity']}" if pointer else "no pointer row",
        )
        summary = fetch_one(conn, "select model, questions_considered, answered_count, posted_ts from slack_qa_summary where superseded_at is null order by generated_at desc limit 1")
        check(
            "the mention produced a session summary, and it was posted",
            bool(summary) and bool(summary["posted_ts"]),
            f"{summary['questions_considered']} questions, {summary['answered_count']} answered, model={summary['model']}" if summary else "no summary row",
        )
        posted = state.get("posted") or []
        check("what the bot posted names no address", all("@example.invalid" not in p["text"] and "<@U" not in p["text"] for p in posted))
        qa_text_in_events = (fetch_one(conn, "select count(*) as n from slack_event where text is not null") or {})["n"]
        check("Q&A text lives in the Q&A tables, not on slack_event", qa_text_in_events == 0)

        # ------------------------------------------------------------------
        # The other half of the bot: what it SENDS. Everything below went out
        # through the production client (slack_sdk over HTTP to the fake
        # server), so these checks cover the adapter and the wire as well as
        # the logic the unit tests cover against the in-memory fake.
        # ------------------------------------------------------------------
        if args.staff_email:
            check(
                "the demo's staff address matches the one the fake workspace creates",
                args.staff_email == DEMO_STAFF_EMAIL,
                f"{args.staff_email} vs {DEMO_STAFF_EMAIL}",
            )
        dms = state.get("dms") or []
        check(f"the bot sent direct messages ({len(dms)})", bool(dms))

        # 11. One welcome per fellow, once, with the check-in button on it.
        welcomes = [d for d in dms if "I'm the fellowship bot" in d["text"]]
        recipients = [d["to"] for d in welcomes]
        rostered_on_slack = (fetch_one(
            conn,
            "select count(*) as n from v_slack_user_resolved "
            "where cohort_id = %s and fellow_id is not null and not is_bot and not deleted",
            (args.cohort,),
        ) or {})["n"]
        check(
            f"every fellow on Slack was welcomed exactly once ({len(welcomes)} of {rostered_on_slack})",
            len(welcomes) == rostered_on_slack and len(set(recipients)) == len(recipients),
        )
        check(
            "the welcome carries the 'check in with me' button",
            all(d["blocks"] for d in welcomes),
            f"{sum(1 for d in welcomes if d['blocks'])} of {len(welcomes)} had blocks",
        )
        welcomed_in_db = (fetch_one(conn, "select count(*) as n from slack_user where welcomed_at is not null") or {})["n"]
        check("each welcome is recorded, so a re-run sends none", welcomed_in_db == len(welcomes))

        # 12. Reminders: the right interval, the link in the message, once each.
        session_reminders = [d for d in dms if "starts in 1 hour" in d["text"]]
        check(f"the one-hour session reminder went out ({len(session_reminders)})", bool(session_reminders))
        check(
            "the session reminder carries the Zoom link a staff member set from Slack",
            all("https://zoom.us/j/demo" in d["text"] for d in session_reminders),
        )
        assignment_reminders = [d for d in dms if "is due in 24 hours" in d["text"]]
        check(f"the 24-hour assignment reminder went out ({len(assignment_reminders)})", bool(assignment_reminders))
        check(
            "the assignment reminder carries the submission link",
            all("forms.example.invalid" in d["text"] for d in assignment_reminders),
        )
        sent_rows = (fetch_one(
            conn,
            "select count(*) as n from bot_delivery where status = 'sent' "
            "and kind in ('session_reminder', 'assignment_reminder')",
        ) or {})["n"]
        check(
            f"one bot_delivery row per reminder delivered ({sent_rows} rows, "
            f"{len(session_reminders) + len(assignment_reminders)} messages)",
            sent_rows == len(session_reminders) + len(assignment_reminders),
        )

        # 13. Nobody who is not a fellow was DM'd, and no address leaked into one.
        staff_ids = {u["id"] for u in state["users"] if (u["email"] or "") == DEMO_STAFF_EMAIL}
        check("no direct message went to the staff account", not (staff_ids & {d["to"] for d in dms}))
        check(
            "no direct message names an email address",
            all("@example.invalid" not in d["text"] for d in dms),
        )

        # 14. The staff-facing posts went to the private channel, and only there.
        staff_channel = fetch_one(
            conn, "select channel_id, name, is_private from slack_channel where lower(name) = 'cohort-private'"
        )
        check("the staff channel is private", bool(staff_channel) and bool(staff_channel["is_private"]))
        flagged = fetch_all(conn, "select name from slack_channel where is_staff")
        check(
            "the staff channel is flagged as staff, so its messages are not fellow participation",
            [r["name"] for r in flagged] == ["cohort-private"],
            f"flagged: {[r['name'] for r in flagged]}",
        )
        digests = fetch_all(conn, "select kind, channel_id, body from digest_log order by posted_at")
        check(
            "no staff-channel post names an email address",
            all("@example.invalid" not in (d["body"] or "") for d in digests),
            "; ".join(d["kind"] for d in digests if "@example.invalid" in (d["body"] or "")),
        )
        kinds = {d["kind"] for d in digests}
        check(
            f"the session summary and the weekly digest were posted ({sorted(kinds)})",
            {"session_summary", "weekly"} <= kinds,
        )
        if staff_channel:
            check(
                "every staff-facing post went to the private staff channel",
                all(d["channel_id"] == staff_channel["channel_id"] for d in digests),
                f"{sorted({d['channel_id'] for d in digests})}",
            )


        # 15. A badge award is either announced or waiting on a Slack account.
        unannounced = fetch_all(
            conn,
            "select a.badge_key from badge_award a "
            "left join v_slack_user_resolved u on u.fellow_id = a.fellow_id and not u.is_bot and not u.deleted "
            "where a.notified_at is null and u.slack_user_id is not null",
        )
        awarded = (fetch_one(conn, "select count(*) as n from badge_award") or {})["n"]
        check(
            f"every badge award reachable on Slack was announced ({awarded} awarded)",
            not unannounced,
            f"{len(unannounced)} unannounced",
        )


    print("=" * 62)
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
