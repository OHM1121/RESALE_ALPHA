"""0.6.4: the stock market score is computed and recorded every 3 minutes between the KRX close and the NXT close (history only; no buy permission changes)."""
import datetime as dt
import json
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path

import buy_record_v0430 as record
import krx_session_v041 as krx
import market_research_v033 as m

KST = dt.timezone(dt.timedelta(hours=9))


def at(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=KST)


class Window(unittest.TestCase):
    def test_the_after_hours_session_is_16_to_20_and_the_pre_session_08_to_0830(self):
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 15, 29))[0])      # still the regular session
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 15, 30))[0])      # nothing trades 15:30-16:00 (the engine's own session table)
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 15, 59))[0])
        self.assertTrue(krx.after_hours_status(at(2026, 11, 27, 16, 0))[0])
        self.assertTrue(krx.after_hours_status(at(2026, 11, 27, 19, 59))[0])
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 20, 0))[0])       # the NXT after-market is closed
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 7, 59))[0])
        self.assertTrue(krx.after_hours_status(at(2026, 11, 27, 8, 0))[0])         # NXT PRE
        self.assertTrue(krx.after_hours_status(at(2026, 11, 27, 8, 29))[0])
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 8, 30))[0])
        self.assertFalse(krx.after_hours_status(at(2026, 11, 27, 9, 0))[0])

    def test_the_window_matches_the_engines_own_session_table(self):
        try:
            import d_v19_portfolio as pf
        except Exception:
            self.skipTest("vendor engine not on the path")
        for hour, minute in ((7, 59), (8, 0), (8, 29), (8, 30), (15, 59), (16, 0), (19, 59), (20, 0)):
            moment = at(2026, 11, 27, hour, minute)
            engine = pf.live_session(moment, "NXT") or (pf.live_session(moment, "KRX") and not krx.is_regular_open(moment))
            self.assertEqual(bool(engine), krx.after_hours_status(moment)[0], (hour, minute))

    def test_weekend_holiday_and_the_nxt_closed_day_have_no_window(self):
        self.assertFalse(krx.after_hours_status(at(2026, 11, 28, 17))[0])           # Saturday
        self.assertFalse(krx.after_hours_status(at(2026, 12, 25, 17))[0])           # exchange holiday
        self.assertFalse(krx.after_hours_status(at(2026, 11, 19, 17))[0])           # college-exam day: KRX until 16:30, NXT closed
        self.assertFalse(krx.after_hours_status(at(2026, 11, 19, 8, 10))[0])
        self.assertFalse(krx.after_hours_status(at(2028, 3, 6, 17))[0])             # calendar of that year unknown: not assumed open

    def test_the_regular_session_status_is_unchanged(self):
        self.assertEqual(krx.status(at(2026, 11, 27, 16))[0], False)
        self.assertEqual(krx.status(at(2026, 11, 27, 10))[0], True)


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = m.ScoreLedger(Path(self.tmp.name) / "market.sqlite3")
        self.addCleanup(self.ledger.close)

    def counts(self):
        with self.ledger.lock:
            return [self.ledger.conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in ("score_events", "cohort_segments", "cohort_positions", "band_time_slices")]

    def test_event_only_writes_history_and_no_band_or_cohort_rows(self):
        self.assertTrue(self.ledger.record_score_event_only("stock", -81.9, 1795000000, {"session": "AFTER_HOURS"}))
        self.assertEqual(self.counts(), [1, 0, 0, 0])
        with self.ledger.lock:
            row = self.ledger.conn.execute("SELECT market,ts,score,band,meta FROM score_events").fetchone()
        self.assertEqual((row["market"], row["ts"], row["score"], row["band"]), ("stock", 1795000000, -81.9, "-100~-81"))
        self.assertEqual(json.loads(row["meta"]), {"session": "AFTER_HOURS"})

    def test_a_score_outside_the_scale_or_an_unknown_market_is_refused(self):
        self.assertFalse(self.ledger.record_score_event_only("stock", 140.0, 1795000000))
        self.assertFalse(self.ledger.record_score_event_only("gold", 1.0, 1795000000))
        self.assertEqual(self.counts()[0], 0)

    def test_the_regular_recorder_still_builds_bands(self):
        self.ledger.record_score("stock", 12.0, 1795000000, {"p": 1.0}, {})
        self.assertEqual(self.counts()[1], 1)


class Recorder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = m.ScoreLedger(Path(self.tmp.name) / "market.sqlite3")
        self.addCleanup(self.ledger.close)
        self.worker = types.SimpleNamespace(AFTER_HOURS_INTERVAL=180)

    def run_once(self, fetch, now):
        return m._MarketWorker._record_after_hours(self.worker, self.ledger, fetch, now)

    def events(self):
        with self.ledger.lock:
            return [dict(r) for r in self.ledger.conn.execute("SELECT ts,score,meta FROM score_events ORDER BY ts")]

    def test_records_the_computed_score_with_the_session_marked(self):
        now = at(2026, 11, 27, 16, 5)
        self.assertTrue(self.run_once(lambda: {"score": -81.5, "status": "정상", "coverage": 0.95}, now))
        rows = self.events()
        self.assertEqual((rows[0]["ts"], rows[0]["score"]), (int(now.timestamp()), -81.5))
        meta = json.loads(rows[0]["meta"])
        self.assertEqual((meta["session"], meta["coverage"]), ("AFTER_HOURS", 0.95))
        self.assertEqual(self.worker._after_hours_status, "시간외 점수 기록 -81.5")

    def test_every_three_minutes_not_more_often(self):
        calls = []
        fetch = lambda: calls.append(1) or {"score": 1.0}
        self.run_once(fetch, at(2026, 11, 27, 16, 5))
        self.run_once(fetch, at(2026, 11, 27, 16, 6))
        self.assertEqual(len(calls), 1)
        self.worker._after_hours_next = 0.0
        self.run_once(fetch, at(2026, 11, 27, 16, 9))
        self.assertEqual(len(calls), 2)

    def test_outside_the_window_nothing_is_fetched(self):
        calls = []
        for moment in (at(2026, 11, 27, 12), at(2026, 11, 27, 15, 45), at(2026, 11, 27, 20, 1), at(2026, 11, 28, 17)):
            self.assertFalse(self.run_once(lambda: calls.append(1) or {"score": 1.0}, moment))
        self.assertEqual((calls, self.events()), ([], []))

    def test_a_missing_score_or_an_error_records_nothing_and_never_raises(self):
        self.assertFalse(self.run_once(lambda: {"score": None, "status": "KOSPI/KOSDAQ 미수신"}, at(2026, 11, 27, 16)))
        self.assertIn("미수신", self.worker._after_hours_status)
        self.worker._after_hours_next = 0.0

        def boom():
            raise ConnectionError("down")
        self.assertFalse(self.run_once(boom, at(2026, 11, 27, 16, 5)))
        self.assertIn("ConnectionError", self.worker._after_hours_status)
        self.assertEqual(self.events(), [])

    def test_the_fetch_loop_calls_it_when_the_regular_session_is_closed(self):
        import inspect
        source = inspect.getsource(m._MarketWorker._fetch_loop)
        self.assertIn("_record_after_hours", source)
        self.assertLess(source.index("close_market"), source.index("_record_after_hours"))


class Gate(unittest.TestCase):
    """0.6.5 (owner: "똑같이 써"): the after-hours score is the published score, so the buy permission works as in the regular session."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = m.ScoreLedger(Path(self.tmp.name) / "market.sqlite3")
        self.addCleanup(self.ledger.close)
        self.worker = types.SimpleNamespace(AFTER_HOURS_MAX_AGE=600)

    def shown(self, moment, record_at=None, score=None):
        if score is not None:
            self.ledger.record_score_event_only("stock", score, int((record_at or moment).timestamp()), {"session": "AFTER_HOURS"})
        stamp = moment.timestamp()
        closed = m.display_snapshot("stock", self.ledger.snapshot("stock"), now=stamp)
        return m._MarketWorker._after_hours_display(self.worker, self.ledger, closed, moment)

    def test_a_fresh_after_hours_score_is_published_as_the_current_score(self):
        moment = at(2026, 11, 27, 16, 5)
        out = self.shown(moment, moment - dt.timedelta(minutes=2), 23.5)
        self.assertEqual((out["score"], out["stale"], out["session"]), (23.5, False, "AFTER_HOURS"))

    def test_the_buy_permission_reads_it_like_a_regular_score(self):
        import at_market_score_gate_v107 as gate
        moment = at(2026, 11, 27, 16, 5)
        out = self.shown(moment, moment - dt.timedelta(minutes=2), 23.5)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = str(gate._score_worker_root(root, "stock"))
            m._WORKERS[key] = types.SimpleNamespace(snapshot=lambda: {"stock": out})
            try:
                ev = gate.score_evidence(root, "stock")
                self.assertEqual((ev["positive"], ev["negative"], ev["score"], ev["basis"]), (True, False, 23.5, "SIGNED_MARKET_SCORE_V033"))
                m._WORKERS[key] = types.SimpleNamespace(snapshot=lambda: {"stock": dict(out, score=-12.0)})
                ev = gate.score_evidence(root, "stock")
                self.assertEqual((ev["positive"], ev["negative"]), (False, True))
            finally:
                m._WORKERS.pop(key, None)

    def test_no_fresh_score_keeps_the_closed_display(self):
        moment = at(2026, 11, 27, 16, 30)
        out = self.shown(moment, moment - dt.timedelta(minutes=30), 23.5)       # recorded 30 minutes ago: not current
        self.assertIsNone(out["score"])
        self.assertFalse(out["market_open"])

    def test_outside_the_after_hours_sessions_nothing_changes(self):
        for moment in (at(2026, 11, 27, 15, 45), at(2026, 11, 27, 20, 5), at(2026, 11, 28, 17)):
            out = self.shown(moment, moment - dt.timedelta(minutes=1), 23.5)
            self.assertIsNone(out["score"], moment)

    def test_the_regular_session_is_unchanged(self):
        stamp = at(2026, 11, 27, 10, 0).timestamp()
        out = m.display_snapshot("stock", {"score": 5.0, "asof": stamp - 30}, now=stamp)
        self.assertEqual(out["score"], 5.0)

    def test_both_loops_publish_through_the_same_helper(self):
        import inspect
        self.assertIn("_after_hours_display", inspect.getsource(m._MarketWorker._fetch_loop))
        self.assertIn("_after_hours_display", inspect.getsource(m._MarketWorker._loop))


class BuyRecord(unittest.TestCase):
    def snapshot_with(self, history_age):
        import at_market_score_gate_v107 as gate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = str(gate._score_worker_root(root, "stock"))
            now = time.time()
            fake = types.SimpleNamespace(snapshot=lambda: {"stock": {"score": None, "asof": now - history_age, "recent_scores": [{"ts": now - history_age, "score": -12.5, "band": "x"}]}})
            old = gate.score_evidence
            gate.score_evidence = lambda r, mk: {"score": None, "basis": "STALE_SCORE_NO_DECISION"}
            m._WORKERS[key] = fake
            try:
                return record.score_snapshot("stock", root)
            finally:
                gate.score_evidence = old
                m._WORKERS.pop(key, None)

    def test_a_score_recorded_within_ten_minutes_is_the_score_of_that_moment(self):
        got = self.snapshot_with(120)
        self.assertEqual((got["score"], got["stale"], got["basis"]), (-12.5, False, "AFTER_HOURS_SCORE_RECORDED_WITHIN_10_MIN"))

    def test_an_older_last_known_score_stays_marked_stale(self):
        got = self.snapshot_with(4 * 3600)
        self.assertEqual((got["score"], got["stale"], got["basis"]), (-12.5, True, "LAST_KNOWN_SCORE_STALE_AT_BUY"))


if __name__ == "__main__":
    unittest.main()
