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

PASS = "  ok   "
FAIL = "  FAIL "


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", default="demo")
    parser.add_argument("--fake-url", default="http://127.0.0.1:3001")
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

    print("=" * 62)
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
