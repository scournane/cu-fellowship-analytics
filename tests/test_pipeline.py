"""Tests for the CIF analytics pipeline.

The gate tests matter most: they are what stops an undefined term from
quietly acquiring a default.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cufa import config, metrics, roster
from cufa.model import Event, Fellow, Identity, RawEvent, week_id, week_start
from cufa.sources import resolve, sheets, slack, zoom
from cufa.store import Store

UTC = dt.timezone.utc


class TestModel(unittest.TestCase):
    def test_week_id_and_start_roundtrip(self):
        wid = week_id(dt.datetime(2026, 8, 24, 12, tzinfo=UTC))   # a Monday
        self.assertEqual(wid, "2026-W35")
        self.assertEqual(week_start(wid), dt.date(2026, 8, 24))

    def test_sunday_belongs_to_the_week_that_started_monday(self):
        self.assertEqual(week_id(dt.datetime(2026, 8, 30, 23, tzinfo=UTC)), "2026-W35")

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            RawEvent("slack", "U1", "slack_message", dt.datetime(2026, 9, 1, 12))

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            RawEvent("slack", "U1", "vibes", dt.datetime(2026, 9, 1, 12, tzinfo=UTC))

    def test_event_id_is_deterministic(self):
        raw = RawEvent("zoom", "a@b.org", "live_lesson_attended",
                       dt.datetime(2026, 9, 3, 18, tzinfo=UTC), meta={"session": "s1"})
        self.assertEqual(Event.make("f1", raw).event_id, Event.make("f1", raw).event_id)

    def test_event_id_differs_per_fellow(self):
        raw = RawEvent("zoom", "a@b.org", "live_lesson_attended",
                       dt.datetime(2026, 9, 3, 18, tzinfo=UTC))
        self.assertNotEqual(Event.make("f1", raw).event_id, Event.make("f2", raw).event_id)


class TestGate(unittest.TestCase):
    """The core discipline: undefined terms withhold metrics."""

    def setUp(self):
        self.defs = config.load(ROOT / "config" / "definitions.toml")

    def test_confirmed_term_permits_metric(self):
        self.assertEqual(self.defs.check("weekly_participation",
                                         ["participation", "cohort"]), [])

    def test_undefined_term_withholds_metric(self):
        reasons = self.defs.check("at_risk_flag", ["falling_behind"])
        self.assertEqual(len(reasons), 1)
        self.assertEqual(reasons[0].term, "falling_behind")
        self.assertTrue(reasons[0].needed_from)
        self.assertTrue(reasons[0].question)

    def test_all_unmet_requirements_reported_not_just_first(self):
        reasons = self.defs.check("intervention_queue",
                                  ["falling_behind", "fellow_support_responder"])
        self.assertEqual({r.term for r in reasons},
                         {"falling_behind", "fellow_support_responder"})

    def test_unknown_term_is_withheld_not_ignored(self):
        reasons = self.defs.check("m", ["term_nobody_wrote_down"])
        self.assertEqual(len(reasons), 1)

    def test_every_blocked_metric_is_a_real_metric(self):
        # definitions.toml must not name metrics that metrics.py does not have.
        for metric in self.defs.blocked_metrics():
            self.assertIn(metric, metrics.REQUIREMENTS, f"{metric} not in REQUIREMENTS")

    def test_every_requirement_is_a_declared_term(self):
        for metric, requires in metrics.REQUIREMENTS.items():
            for term in requires:
                self.assertIn(term, self.defs.terms, f"{metric} needs undeclared {term}")


class TestSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_zoom_sums_rejoins_and_keeps_earliest_join(self):
        p = self.dir / "lesson.csv"
        p.write_text(
            "Name (Original Name),User Email,Join Time,Leave Time,Duration (Minutes),Guest\n"
            "A,a@b.org,2026-09-03 18:00:00,2026-09-03 18:20:00,20,No\n"
            "A,a@b.org,2026-09-03 18:25:00,2026-09-03 18:50:00,25,No\n")
        events = zoom.read(p)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].meta["minutes"], 45.0)
        self.assertEqual(events[0].occurred_at.hour, 18)

    def test_zoom_ignores_brief_dropins(self):
        p = self.dir / "lesson.csv"
        p.write_text(
            "Name (Original Name),User Email,Join Time,Leave Time,Duration (Minutes),Guest\n"
            "A,a@b.org,2026-09-03 18:00:00,2026-09-03 18:03:00,3,No\n")
        self.assertEqual(zoom.read(p), [])

    def test_zoom_records_absence_for_expected_fellow(self):
        p = self.dir / "lesson.csv"
        p.write_text(
            "Name (Original Name),User Email,Join Time,Leave Time,Duration (Minutes),Guest\n"
            "A,a@b.org,2026-09-03 18:00:00,2026-09-03 19:00:00,60,No\n")
        events = zoom.read(p, expected={"a@b.org": dt.date(2026, 9, 1),
                                        "b@b.org": dt.date(2026, 9, 1)})
        kinds = {e.source_user_id: e.kind for e in events}
        self.assertEqual(kinds["b@b.org"], "live_lesson_absent")

    def test_zoom_no_absence_before_a_fellow_joined(self):
        p = self.dir / "lesson.csv"
        p.write_text(
            "Name (Original Name),User Email,Join Time,Leave Time,Duration (Minutes),Guest\n"
            "A,a@b.org,2026-09-03 18:00:00,2026-09-03 19:00:00,60,No\n")
        events = zoom.read(p, expected={"a@b.org": dt.date(2026, 9, 1),
                                        "late@b.org": dt.date(2026, 9, 20)})
        self.assertNotIn("late@b.org", {e.source_user_id for e in events})

    def test_slack_skips_joins_and_bots_and_credits_reactors(self):
        ch = self.dir / "general"
        ch.mkdir()
        ts = dt.datetime(2026, 9, 3, 15, tzinfo=UTC).timestamp()
        ch.joinpath("2026-09-03.json").write_text(json.dumps([
            {"type": "message", "subtype": "channel_join", "user": "U1", "ts": f"{ts:.6f}"},
            {"type": "message", "subtype": "bot_message", "bot_id": "B1", "ts": f"{ts:.6f}"},
            {"type": "message", "user": "U1", "ts": f"{ts:.6f}", "text": "hello",
             "reactions": [{"name": "tada", "users": ["U2", "U3"], "count": 2}]},
        ]))
        events = slack.read(self.dir)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds.count("slack_message"), 1)
        self.assertEqual(kinds.count("slack_reaction"), 2)
        self.assertEqual({e.source_user_id for e in events if e.kind == "slack_reaction"},
                         {"U2", "U3"})

    def test_sheets_submitted_missed_and_pending(self):
        p = self.dir / "a.csv"
        p.write_text(
            "email,assignment,due_on,submitted_at\n"
            "a@b.org,M1,2026-09-10,2026-09-09 10:00:00\n"   # submitted on time
            "b@b.org,M1,2026-09-10,\n"                       # overdue -> missed
            "c@b.org,M2,2026-12-01,\n")                      # not due yet -> silent
        events = sheets.read(p, as_of=dt.datetime(2026, 9, 20, tzinfo=UTC))
        got = {(e.source_user_id, e.kind) for e in events}
        self.assertIn(("a@b.org", "assignment_submitted"), got)
        self.assertIn(("b@b.org", "assignment_missed"), got)
        self.assertNotIn("c@b.org", {e.source_user_id for e in events})

    def test_sheets_flags_late_submission(self):
        p = self.dir / "a.csv"
        p.write_text("email,assignment,due_on,submitted_at\n"
                     "a@b.org,M1,2026-09-10,2026-09-12 10:00:00\n")
        self.assertTrue(sheets.read(p)[0].meta["late"])


class TestResolveAndStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.store.upsert_fellows([
            Fellow("f1", "Ada Lovelace", "2026-27", dt.date(2026, 9, 7)),
            Fellow("f2", "Alan Turing", "2026-27", dt.date(2026, 9, 14)),
        ])
        self.store.upsert_identities([Identity("slack", "U1", "f1")])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_unmapped_account_is_quarantined_not_dropped(self):
        raws = [
            RawEvent("slack", "U1", "slack_message", dt.datetime(2026, 9, 8, 12, tzinfo=UTC)),
            RawEvent("slack", "U_STRANGER", "slack_message", dt.datetime(2026, 9, 8, 13, tzinfo=UTC)),
        ]
        events, orphans = resolve(raws, self.store.identity_map())
        self.assertEqual(len(events), 1)
        self.assertEqual(len(orphans), 1)
        self.store.add_unresolved(orphans)
        self.assertEqual(self.store.unresolved_summary()[0]["source_user_id"], "U_STRANGER")

    def test_reingest_does_not_double_count(self):
        raw = RawEvent("slack", "U1", "slack_message", dt.datetime(2026, 9, 8, 12, tzinfo=UTC))
        events, _ = resolve([raw], self.store.identity_map())
        self.assertEqual(self.store.add_events(events), 1)
        self.assertEqual(self.store.add_events(events), 0)
        self.assertEqual(self.store.weekly_counts()[("f1", "2026-W37")]["slack_message"], 1.0)

    def test_identity_remap_moves_future_events(self):
        self.store.upsert_identities([Identity("slack", "U1", "f2")])
        self.assertEqual(self.store.identity_map()[("slack", "U1")], "f2")


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.defs = config.load(ROOT / "config" / "definitions.toml")
        self.store.upsert_fellows([
            Fellow("f1", "Ada Lovelace", "2026-27", dt.date(2026, 9, 7)),
            Fellow("f2", "Alan Turing", "2026-27", dt.date(2026, 9, 14)),
        ])
        self.store.upsert_identities([
            Identity("slack", "U1", "f1"), Identity("zoom", "a@b.org", "f1")])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _ingest(self, raws):
        events, _ = resolve(raws, self.store.identity_map())
        self.store.add_events(events)

    def test_weights_applied(self):
        self._ingest([
            RawEvent("zoom", "a@b.org", "live_lesson_attended",
                     dt.datetime(2026, 9, 10, 18, tzinfo=UTC)),
            RawEvent("slack", "U1", "slack_message",
                     dt.datetime(2026, 9, 8, 12, tzinfo=UTC)),
        ])
        r = metrics.compute(self.store, self.defs)
        row = r.row("f1", "2026-W37")
        self.assertEqual(row.total, 4.0)     # 3.0 lesson + 1.0 message

    def test_slack_cap_limits_a_single_noisy_channel(self):
        base = dt.datetime(2026, 9, 8, 12, tzinfo=UTC)
        self._ingest([
            RawEvent("slack", "U1", "slack_message", base + dt.timedelta(minutes=i),
                     meta={"i": i})
            for i in range(40)
        ])
        r = metrics.compute(self.store, self.defs)
        row = r.row("f1", "2026-W37")
        self.assertEqual(row.components["slack_message"], 40.0)   # raw count preserved
        self.assertEqual(row.points["slack_message"], 10.0)       # capped for scoring

    def test_fellow_not_enrolled_before_join_week(self):
        self._ingest([RawEvent("slack", "U1", "slack_message",
                               dt.datetime(2026, 9, 8, 12, tzinfo=UTC))])
        r = metrics.compute(self.store, self.defs)
        self.assertFalse(r.row("f2", "2026-W37").enrolled)

    def test_silent_means_enrolled_with_no_signal(self):
        self._ingest([RawEvent("slack", "U1", "slack_message",
                               dt.datetime(2026, 9, 15, 12, tzinfo=UTC))])
        r = metrics.compute(self.store, self.defs)
        f2 = r.row("f2", "2026-W38")
        self.assertTrue(f2.enrolled)
        self.assertTrue(f2.silent)

    def test_at_risk_metrics_are_withheld(self):
        self._ingest([RawEvent("slack", "U1", "slack_message",
                               dt.datetime(2026, 9, 8, 12, tzinfo=UTC))])
        r = metrics.compute(self.store, self.defs)
        withheld_terms = {w.term for w in r.withheld}
        self.assertIn("falling_behind", withheld_terms)
        self.assertIn("fellow_support_responder", withheld_terms)
        self.assertIn("performance", withheld_terms)

    def test_attendance_rate_uses_absences(self):
        self._ingest([
            RawEvent("zoom", "a@b.org", "live_lesson_attended",
                     dt.datetime(2026, 9, 10, 18, tzinfo=UTC), meta={"s": 1}),
            RawEvent("zoom", "a@b.org", "live_lesson_absent",
                     dt.datetime(2026, 9, 11, 18, tzinfo=UTC), meta={"s": 2}),
        ])
        r = metrics.compute(self.store, self.defs)
        self.assertAlmostEqual(r.cohort[0].attendance_rate, 0.5)


class TestRosterAndReport(unittest.TestCase):
    def test_roster_builds_one_identity_per_system(self):
        fellows, ids = roster.read(ROOT / "fixtures" / "roster.csv")
        self.assertEqual(len(ids), len(fellows) * 3)
        self.assertEqual({i.source for i in ids}, {"zoom", "slack", "sheets"})

    def test_roster_lowercases_emails_but_not_slack_ids(self):
        fellows, ids = roster.read(ROOT / "fixtures" / "roster.csv")
        for i in ids:
            if i.source in ("zoom", "sheets"):
                self.assertEqual(i.source_user_id, i.source_user_id.lower())
            else:
                self.assertTrue(i.source_user_id.startswith("U"))

    def test_report_escapes_hostile_display_names(self):
        from cufa import report
        tmp = tempfile.TemporaryDirectory()
        store = Store(Path(tmp.name) / "t.db")
        store.upsert_fellows([
            Fellow("f1", "<script>alert(1)</script>", "2026-27", dt.date(2026, 9, 7))])
        store.upsert_identities([Identity("slack", "U1", "f1")])
        events, _ = resolve([RawEvent("slack", "U1", "slack_message",
                                      dt.datetime(2026, 9, 8, 12, tzinfo=UTC))],
                            store.identity_map())
        store.add_events(events)
        defs = config.load(ROOT / "config" / "definitions.toml")
        html = report.render(metrics.compute(store, defs), defs)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        store.close(); tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
