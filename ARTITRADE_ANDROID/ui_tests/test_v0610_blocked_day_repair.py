"""0.6.1: the daily-bar verification backlog is cleared in the quiet hours with the vendor's own repair function, as-of two past stamps of the target trading day."""
import datetime as dt
import inspect
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import blocked_day_repair_v0610 as rep

KST = dt.timezone(dt.timedelta(hours=9))
FRI, SAT = dt.date(2026, 11, 27), dt.date(2026, 11, 28)


def at(day, h, m=0):
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=KST)


class Store:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()

    def connect(self):
        cx = sqlite3.connect(self.path, timeout=5)
        cx.row_factory = sqlite3.Row
        return cx


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stock").mkdir()
        path = self.root / "stock" / "ARTITRADE_D.sqlite3"
        cx = sqlite3.connect(path)
        cx.executescript("""
            CREATE TABLE v19_orders(signal_id INTEGER PRIMARY KEY,code TEXT,tf TEXT,venue TEXT,status TEXT);
            CREATE TABLE v19_higher_frame_audit14(signal_id INTEGER PRIMARY KEY,code TEXT,tf TEXT,state TEXT);
            CREATE TABLE daily_ohlcv_revision_cases(id INTEGER PRIMARY KEY,code TEXT,frame TEXT,bar_ts TEXT,status TEXT);
        """)
        cx.executemany("INSERT INTO v19_higher_frame_audit14 VALUES(?,?,?,?)", [(1, "000001", "day", "BLOCKED"), (2, "000002", "day", "VERIFIED"), (3, "000003", "30m", "BLOCKED"), (4, "000004", "day", "BLOCKED")])
        cx.executemany("INSERT INTO v19_orders VALUES(?,?,?,?,?)", [(1, "000001", "day", "KRX", "PENDING"), (4, "000004", "day", "KRX", "PENDING"), (3, "000003", "30m", "KRX", "PENDING")])
        cx.executemany("INSERT INTO daily_ohlcv_revision_cases VALUES(?,?,?,?,?)", [(1, "000005", "day", "20261126000000", "PENDING"), (2, "000006", "day", "20261126000000", "REVIEW"), (3, "000007", "day", "20261126000000", "APPLIED"),
                                                                                 (4, "000001", "day", "20261126000000", "PENDING")])
        cx.commit()
        cx.close()
        self.store = Store(str(path))
        self.changed = []
        rep._STATE.update(running=False, failures=0, paused_until=0.0, next_round=0.0)

    def runtime(self, qsize=0):
        runner = types.SimpleNamespace(api=object(), store=self.store, engine=object(), _tick_queue=types.SimpleNamespace(qsize=lambda: qsize), _ledger_changed=lambda: self.changed.append(1))
        return types.SimpleNamespace(stock_runner=runner, root=str(self.root))

    def mark(self, code, status):
        with self.store.connect() as db:
            db.execute("UPDATE daily_ohlcv_revision_cases SET status=? WHERE code=?", (status, code))
            db.execute("UPDATE v19_higher_frame_audit14 SET state=? WHERE code=? AND tf='day'", ("VERIFIED" if status == "APPLIED" else "BLOCKED", code))
            db.commit()


class Selection(Base):
    def test_only_open_day_verification_is_selected(self):
        self.assertEqual(rep.candidates(self.store), {"000001": ["BLOCKED_AUDIT", "PENDING_CASE"], "000004": ["BLOCKED_AUDIT"], "000005": ["PENDING_CASE"]})
        self.assertEqual(rep.candidates(self.store, FRI, align=False), rep.candidates(self.store))
        # a verified day audit, an applied case, a review case (tied to a signal / position) and a blocked 30m audit are not touched

    def test_counts_before_and_after_are_read_only_numbers(self):
        self.assertEqual(rep.counts(self.store), {"blocked_day_audits": 2, "pending_cases": 2, "review_cases": 1, "pending_day_orders": 2})

    def test_missing_tables_give_nothing_and_do_not_raise(self):
        empty = Store(str(self.root / "stock" / "empty.sqlite3"))
        sqlite3.connect(empty.path).close()
        self.assertEqual(rep.candidates(empty), {})
        self.assertEqual(sum(rep.counts(empty).values()), 0)


class Alignment(Base):
    def add_day_bars(self, rows):
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)")
            db.executemany("INSERT INTO bars VALUES(?,?,?,?,1,1,1,1,1,'X')", rows)
            db.commit()

    def test_every_managed_stock_with_recent_day_bars_is_aligned_after_the_open_cases(self):
        self.add_day_bars([("000009", "KOSPI", "day", "20261127000000"), ("000008", "KOSPI", "day", "20261126000000"), ("000001", "KOSPI", "day", "20261125000000"),
                           ("000007", "KOSPI", "day", "20261120000000"), ("000006", "KOSPI", "5m", "20261127100000")])
        got = rep.candidates(self.store, FRI)
        self.assertEqual(list(got), ["000001", "000004", "000005", "000008", "000009"])        # open cases first, then the aligned stocks; the 3-trading-day window (11-25..11-27) only
        self.assertEqual(got["000001"], ["ALIGN_FINAL_CANDLE", "BLOCKED_AUDIT", "PENDING_CASE"])
        self.assertEqual(got["000009"], ["ALIGN_FINAL_CANDLE"])

    def test_a_round_aligns_them_through_the_same_wrapper_path(self):
        self.add_day_bars([("000009", "KOSPI", "day", "20261127000000")])
        seen = []

        def recover(store, engine, api, code, at_):
            seen.append(code)
            if code in ("000001", "000004", "000005"):
                self.mark(code, "APPLIED")
            return {"state": "VERIFIED_RESTORED", "new_pending": 0}
        summary = rep.run(self.runtime(), now=at(SAT, 12), sleep=lambda s: None, recover=recover)
        self.assertIn("000009", seen)
        self.assertTrue(summary["finished"], summary)


class Stamps(Base):
    def test_stamps_are_past_moments_of_the_target_trading_day_and_differ(self):
        first, second = rep.stamps(FRI)
        self.assertEqual((first, second), ("20261127200500", "20261127200510"))
        self.assertGreaterEqual(int(second[-2:]) - int(first[-2:]), 5)   # the vendor needs two requests at least 5 s apart

    def test_a_stamp_is_never_earlier_than_what_the_vendor_already_stored(self):
        self.assertEqual(rep.stamps(FRI, "20261127113948"), ("20261127200500", "20261127200510"))      # earlier record: keep 20:05
        self.assertEqual(rep.stamps(FRI, "20261127200503"), ("20261127200504", "20261127200514"))      # a later record: go after it
        with self.store.connect() as db:
            db.execute("CREATE TABLE v19_frame_revision_confirm(code TEXT,tf TEXT,ts TEXT,proposal TEXT,first_seen TEXT,last_seen TEXT,observations INTEGER)")
            db.execute("INSERT INTO v19_frame_revision_confirm VALUES('000005','day','20261126000000','x','20261127113948','20261127200700',2)")
            db.execute("INSERT INTO v19_frame_revision_confirm VALUES('000005','30m','20261126000000','x','20261127230000','20261127230000',2)")
            db.commit()
        self.assertEqual(rep.latest_stamp(self.store, "000005"), "20261127200700")                      # only the day rows count
        seen = []
        rep.repair_code(self.store, object(), object(), "000005", FRI, sleep=lambda s: None, recover=lambda *a: seen.append(a[-1]) or {"state": "OFFICIAL_REVISION_PENDING_CONFIRMATION"})
        self.assertEqual(seen, ["20261127200701", "20261127200711"])

    def test_on_a_saturday_the_stamps_are_still_friday(self):
        calls = []
        rep.run(self.runtime(), now=at(SAT, 12), sleep=lambda s: None, recover=lambda *a: calls.append(a[-1]) or {"state": "VERIFIED_RESTORED", "new_pending": 0})
        self.assertTrue(calls)
        self.assertTrue(all(c.startswith("20261127") for c in calls))        # never the real Saturday, whose daily candle does not exist

    def test_the_vendor_function_accepts_the_as_of_argument(self):
        try:
            import d_multiframe_recovery_fix14 as fix
        except Exception:
            self.skipTest("vendor module not on the path")
        params = inspect.signature(getattr(fix.recover_frame, "__wrapped__", fix.recover_frame)).parameters
        self.assertIn("at", params)
        self.assertIn("fetch", params)


class TwoSnapshots(Base):
    def test_two_independent_requests_with_a_real_pause_between(self):
        events = []
        states = iter(["OFFICIAL_REVISION_PENDING_CONFIRMATION", "VERIFIED_RESTORED"])

        def recover(store, engine, api, code, at_):
            events.append(("call", code, at_))
            return {"state": next(states), "new_pending": 0}
        final, new_pending, seen = rep.repair_code(self.store, object(), object(), "000005", FRI, sleep=lambda s: events.append(("sleep", s)), recover=recover)
        self.assertEqual(final, "VERIFIED_RESTORED")
        self.assertEqual([e[0] for e in events], ["call", "sleep", "call"])
        self.assertEqual(events[1][1], rep.BETWEEN_SNAPSHOTS)
        self.assertNotEqual(events[0][2], events[2][2])

    def test_verified_on_the_first_request_needs_no_second(self):
        calls = []
        final, _, seen = rep.repair_code(self.store, object(), object(), "000005", FRI, sleep=lambda s: None, recover=lambda *a: calls.append(a[-1]) or {"state": "VERIFIED_RESTORED", "new_pending": 2})
        self.assertEqual(len(calls), 1)
        self.assertEqual(seen, ["VERIFIED_RESTORED"])

    def test_a_bar_tied_to_a_signal_or_position_is_protected_and_not_retried(self):
        calls = []
        final, _, _ = rep.repair_code(self.store, object(), object(), "000006", FRI, sleep=lambda s: None, recover=lambda *a: calls.append(1) or {"state": "OFFICIAL_REVISION_REQUIRES_REVIEW"})
        self.assertEqual((final, len(calls)), ("OFFICIAL_REVISION_REQUIRES_REVIEW", 1))


class NoOrders(Base):
    class Engine:
        def __init__(self):
            self.store, self.flag, self.enqueued = "S", 1, 0

        def _enqueue_signals(self, db, now):
            self.enqueued += 1

        def classify(self):
            return "real"

    def test_reads_and_other_methods_go_to_the_real_engine_but_order_creation_does_nothing(self):
        real = self.Engine()
        proxy = rep.NoOrderEngine(real)
        self.assertEqual((proxy.store, proxy.classify()), ("S", "real"))
        proxy._enqueue_signals(None, "20261127200500")
        proxy._enqueue_signals(None, "20261127200510")
        self.assertEqual((real.enqueued, proxy.blocked_enqueues), (0, 2))
        real._enqueue_signals(None, "x")
        self.assertEqual(real.enqueued, 1)                       # nothing is patched on the real engine: the live threads still create orders

    def test_attribute_writes_reach_the_real_engine(self):
        real = self.Engine()
        proxy = rep.NoOrderEngine(real)
        proxy.flag = 7
        proxy.new_attr = "x"
        self.assertEqual((real.flag, real.new_attr), (7, "x"))

    def test_the_vendor_function_receives_the_wrapper_and_the_day_frame_and_the_stamp(self):
        import sys
        got = {}
        fake = types.ModuleType("d_multiframe_recovery_fix14")
        fake.recover_frame = lambda store, engine, api, code, tf, at=None, fetch=True: got.update(engine=engine, tf=tf, at=at) or {"state": "VERIFIED_RESTORED"}
        old = sys.modules.get("d_multiframe_recovery_fix14")
        sys.modules["d_multiframe_recovery_fix14"] = fake
        try:
            real = self.Engine()
            rep._recover(self.store, real, object(), "000005", "20261127200500")
        finally:
            if old is None:
                sys.modules.pop("d_multiframe_recovery_fix14", None)
            else:
                sys.modules["d_multiframe_recovery_fix14"] = old
        self.assertIsInstance(got["engine"], rep.NoOrderEngine)
        self.assertEqual((got["tf"], got["at"]), ("day", "20261127200500"))


class Rounds(Base):
    def ok(self, code_status=None):
        def recover(store, engine, api, code, at_):
            self.mark(code, "APPLIED")
            return {"state": "VERIFIED_RESTORED", "new_pending": 1 if code == "000001" else 0}
        return recover

    def test_a_full_run_clears_the_backlog_records_before_after_and_wakes_the_ledger(self):
        summary = rep.run(self.runtime(), now=at(SAT, 12), sleep=lambda s: None, recover=self.ok())
        self.assertTrue(summary["finished"], summary)
        state = rep.load_state(str(self.root))["2026-11-27"]
        self.assertTrue(state["finished"])
        self.assertEqual(state["before"], {"blocked_day_audits": 2, "pending_cases": 2, "review_cases": 1, "pending_day_orders": 2})
        self.assertEqual(state["after"]["blocked_day_audits"], 0)
        self.assertEqual(state["after"]["pending_cases"], 0)
        self.assertEqual(state["after"]["review_cases"], 1)                 # untouched
        self.assertEqual(state["new_pending"], 1)
        self.assertEqual(sorted(state["done"]), ["000001", "000004", "000005"])
        self.assertTrue(self.changed)
        lines = (self.root / "stock" / rep.LOG_FILE).read_text("utf8").splitlines()
        self.assertTrue(lines[0].count("START") and lines[-1].count("DONE"))
        self.assertEqual(rep.run(self.runtime(), now=at(SAT, 12, 30), sleep=lambda s: None, recover=self.ok()), {"ran": False})

    def test_outside_the_quiet_window_nothing_runs(self):
        calls = []
        out = rep.run(self.runtime(), now=at(FRI, 10), sleep=lambda s: None, recover=lambda *a: calls.append(1) or {})
        self.assertEqual((out, calls), ({"ran": False}, []))
        rep.run(self.runtime(), now=at(FRI, 20, 1), sleep=lambda s: None, recover=lambda *a: calls.append(1) or {})
        self.assertEqual(calls, [])                                          # the candle is not final before 20:05

    def test_a_busy_tick_queue_has_priority(self):
        calls = []
        rep.run(self.runtime(qsize=30), now=at(SAT, 12), sleep=lambda s: None, recover=lambda *a: calls.append(1) or {})
        self.assertEqual(calls, [])

    def test_batch_and_rest_between_rounds(self):
        old = rep.BATCH
        rep.BATCH = 1
        try:
            first = rep.run(self.runtime(), now=at(SAT, 12), sleep=lambda s: None, recover=self.ok())
            self.assertEqual((first["todo"], first["finished"]), (1, False))
            self.assertEqual(rep.run(self.runtime(), now=at(SAT, 12, 1), sleep=lambda s: None, recover=self.ok()), {"ran": False})   # resting
            rep._STATE["next_round"] = 0.0
            second = rep.run(self.runtime(), now=at(SAT, 12, 11), sleep=lambda s: None, recover=self.ok())
            self.assertEqual(second["todo"], 1)
        finally:
            rep.BATCH = old

    def test_an_unresolved_stock_is_tried_at_most_attempts_times_and_listed(self):
        def never(store, engine, api, code, at_):
            return {"state": "OFFICIAL_REVISION_PENDING_CONFIRMATION", "new_pending": 0}
        for round_no in range(rep.ATTEMPTS + 1):
            rep._STATE["next_round"] = 0.0
            rep.run(self.runtime(), now=at(SAT, 12, round_no), sleep=lambda s: None, recover=never)
        state = rep.load_state(str(self.root))["2026-11-27"]
        self.assertTrue(state["finished"])
        self.assertEqual(set(state["unresolved"]), {"000001", "000004", "000005"})
        self.assertTrue(all(v == rep.ATTEMPTS for v in state["attempts"].values()))

    def test_errors_are_counted_logged_and_three_in_a_row_pause_the_work(self):
        def boom(*a):
            raise ConnectionError("no route")
        self.mark("000004", "BLOCKED")
        rep.run(self.runtime(), now=at(SAT, 12), sleep=lambda s: None, recover=boom)
        self.assertGreater(rep._STATE["paused_until"], 0.0)
        log = (self.root / "stock" / rep.LOG_FILE).read_text("utf8")
        self.assertIn("ERROR", log)

    def test_installed_in_the_quiet_hours_loop(self):
        import offhours_prework_v0530 as off
        self.assertIn("blocked_day_repair_v0610", inspect.getsource(off._loop))


if __name__ == "__main__":
    unittest.main()
