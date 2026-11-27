"""0.5.9: the long quiet window (weekend / holiday) runs a deep multi-day audit of the managed stock data (owner 2026-11-27)."""
import datetime as dt
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import offhours_prework_v0530 as base
import offhours_weekend_v0590 as wk

KST = dt.timezone(dt.timedelta(hours=9))
FRI, SAT, MON = dt.date(2026, 11, 27), dt.date(2026, 11, 28), dt.date(2026, 11, 30)
SCHEMA = """CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open INTEGER,high INTEGER,low INTEGER,close INTEGER,volume INTEGER,source TEXT,
            PRIMARY KEY(code,frame,ts));
            CREATE TABLE stocks(code TEXT PRIMARY KEY,market TEXT);
            CREATE TABLE issues(id INTEGER PRIMARY KEY,seen_at TEXT,message TEXT)"""
DAYS = [dt.date(2026, 11, 24), dt.date(2026, 11, 25), dt.date(2026, 11, 26), dt.date(2026, 11, 27)]


def at(day, h, m=0):
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=KST)


class Store:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()

    def connect(self):
        cx = sqlite3.connect(self.path, timeout=5)
        cx.row_factory = sqlite3.Row
        return cx


class Api:
    """Broker stand-in for ka10080: one page holding every finished slot of the four days for the requested tic_scope."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], dict.fromkeys(fail, 0)

    def post(self, api_id, path, body, cont="N", key=""):
        code, scope = body["stk_cd"], int(body["tic_scope"])
        self.calls.append((code, scope))
        if code in self.fail and self.fail[code] < 1:
            self.fail[code] += 1
            raise ConnectionError("no route")
        rows = []
        for day in DAYS:
            for ts in wk.expected_slots(day, {5: "5m", 30: "30m", 60: "60m"}[scope]):
                rows.append({"cntr_tm": ts, "open_pric": "+100", "high_pric": "+110", "low_pric": "-95", "cur_prc": "+105", "trde_qty": "10"})
        return {"stk_min_pole_chart_qry": rows, "_page_cont_yn": "N"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stock").mkdir()
        self.path = str(self.root / "stock" / "db.sqlite3")
        cx = sqlite3.connect(self.path)
        cx.executescript(SCHEMA)
        cx.commit()
        cx.close()
        self.store = Store(self.path)
        wk._STATE.update(running=False, progress=[0, 0], failures=0, paused_until=0.0, next_round=0.0)
        base._STATE.update(failures=0, paused_until=0.0)

    def put(self, code, frame, ts):
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO stocks(code,market) VALUES(?,?)", (code, "KOSPI"))
            db.execute("INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (code, "KOSPI", frame, ts, 100, 110, 95, 105, 10, "LIVE"))

    def count(self, code, frame):
        with self.store.connect() as db:
            return db.execute("SELECT COUNT(*) FROM bars WHERE code=? AND frame=?", (code, frame)).fetchone()[0]

    def runtime(self, api):
        runner = types.SimpleNamespace(api=api, store=self.store, _tick_queue=types.SimpleNamespace(qsize=lambda: 0))
        return types.SimpleNamespace(stock_runner=runner, root=str(self.root))


class Window(unittest.TestCase):
    def test_weekend_belongs_to_the_friday_window_all_day(self):
        for moment in (at(FRI, 20, 5), at(SAT, 3), at(SAT, 12), at(dt.date(2026, 11, 29), 15), at(MON, 7, 44)):
            self.assertEqual(base.target_day(moment), FRI, moment)
        self.assertIsNone(base.target_day(at(MON, 7, 45)))
        self.assertIsNone(base.target_day(at(FRI, 12)))

    def test_long_window_only_over_a_non_trading_day(self):
        self.assertTrue(base.long_window(at(FRI, 21)))
        self.assertTrue(base.long_window(at(SAT, 12)))
        self.assertFalse(base.long_window(at(dt.date(2026, 11, 26), 21)))            # Thursday night -> Friday is a trading day
        self.assertFalse(base.long_window(at(FRI, 12)))

    def test_trading_days_back_skip_the_weekend(self):
        self.assertEqual(wk.trading_days(FRI, 3), [FRI, dt.date(2026, 11, 26), dt.date(2026, 11, 25)])
        self.assertEqual(wk.trading_days(MON, 2), [MON, FRI])


class Audit(Base):
    def test_deep_history_parses_signed_fields_and_drops_unfinished_and_auction_slots(self):
        rows = wk.deep_history(Api(), "000001", "5m", 300, now=at(FRI, 21))
        self.assertTrue(rows)
        self.assertTrue(all(r[3:] == (100, 110, 95, 105, 10) for r in rows))
        stamps = {r[2] for r in rows}
        self.assertNotIn("20261127152000", stamps)           # closing-auction slot, not a regular reference
        self.assertNotIn("20261128090000", stamps)

    def test_audit_code_fills_missing_candles_of_several_days_and_nothing_else(self):
        for frame in ("5m", "30m", "60m"):
            for ts in wk.expected_slots(DAYS[0], frame)[:3]:
                self.put("000001", frame, ts)                  # only 3 candles of the oldest day are stored
        report, requests = wk.audit_code(self.store, Api(), "000001", DAYS, at(FRI, 21), sleep=lambda s: None)
        self.assertGreater(requests, 0)
        for frame in ("5m", "30m", "60m"):
            self.assertEqual(self.count("000001", frame), sum(len(wk.expected_slots(d, frame)) for d in DAYS))
            self.assertEqual(report[frame][0], report[frame][1])
        before = self.count("000001", "5m")
        wk.audit_code(self.store, Api(), "000001", DAYS, at(FRI, 21), sleep=lambda s: None)
        self.assertEqual(self.count("000001", "5m"), before)        # idempotent

    def test_existing_candles_are_never_replaced(self):
        ts = wk.expected_slots(DAYS[1], "5m")[0]
        with self.store.connect() as db:
            db.execute("INSERT INTO stocks VALUES('000001','KOSPI')")
            db.execute("INSERT INTO bars VALUES('000001','KOSPI','5m',?,1,2,1,2,5,'LIVE')", (ts,))
        wk.audit_code(self.store, Api(), "000001", DAYS, at(FRI, 21), sleep=lambda s: None)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT close,source FROM bars WHERE code='000001' AND frame='5m' AND ts=?", (ts,)).fetchone()[:], (2, "LIVE"))

    def test_run_audit_passes_retries_the_failed_stock_and_finishes_with_a_summary(self):
        for code in ("000001", "000002"):
            self.put(code, "5m", wk.expected_slots(FRI, "5m")[0])
        api = Api(fail=("000002",))
        out = wk.run_audit(self.runtime(api), at(SAT, 2), sleep=lambda s: None)
        self.assertTrue(out["ran"])
        self.assertFalse(wk.run_audit(self.runtime(api), at(SAT, 2, 1), sleep=lambda s: None)["ran"])     # the rest between two rounds
        wk._STATE["next_round"] = 0.0                                                                     # ... which has passed
        out2 = wk.run_audit(self.runtime(api), at(SAT, 2, 30), sleep=lambda s: None)
        state = wk.load_state(str(self.root))
        task = state[FRI.isoformat()]
        self.assertTrue(task["finished"], (out, out2, task))
        self.assertEqual(task["unresolved"], [])                       # the second round succeeded
        self.assertGreater(task["filled"], 0)
        for code in ("000001", "000002"):
            self.assertEqual(self.count(code, "30m"), sum(len(wk.expected_slots(d, "30m")) for d in DAYS))
        log = (self.root / "stock" / wk.LOG_FILE).read_text("utf8")
        self.assertIn('"event": "START"', log)
        self.assertIn('"event": "DONE"', log)

    def test_a_round_takes_at_most_batch_stocks_then_rests(self):
        for i in range(5):
            self.put("00000%d" % (i + 1), "5m", wk.expected_slots(FRI, "5m")[0])
        old = wk.BATCH
        wk.BATCH = 2
        try:
            out = wk.run_audit(self.runtime(Api()), at(SAT, 2), sleep=lambda s: None)
        finally:
            wk.BATCH = old
        self.assertEqual((out["todo"], out["finished"]), (2, False))
        self.assertGreater(wk._STATE["next_round"], 0.0)
        self.assertEqual(len(wk.load_state(str(self.root))[FRI.isoformat()]["done_codes"]), 2)

    def test_pace_is_gentle(self):
        self.assertGreaterEqual(wk.PAUSE, 1.0)
        self.assertGreaterEqual(wk.ROUND_REST, 600.0)
        self.assertLessEqual(wk.BATCH, 40)

    def test_not_in_a_normal_night(self):
        self.put("000001", "5m", wk.expected_slots(DAYS[2], "5m")[0])
        out = wk.run_audit(self.runtime(Api()), at(dt.date(2026, 11, 26), 21), sleep=lambda s: None)
        self.assertFalse(out["ran"])

    def test_yields_to_a_busy_live_feed(self):
        self.put("000001", "5m", wk.expected_slots(FRI, "5m")[0])
        rt = self.runtime(Api())
        rt.stock_runner._tick_queue = types.SimpleNamespace(qsize=lambda: 99)
        out = wk.run_audit(rt, at(SAT, 2), sleep=lambda s: None)
        self.assertEqual(rt.stock_runner.api.calls, [])
        self.assertTrue(out["ran"])

    def test_request_budget_is_respected(self):
        self.put("000001", "5m", wk.expected_slots(FRI, "5m")[0])
        old = wk.MAX_REQUESTS
        wk.MAX_REQUESTS = 0
        try:
            wk.run_audit(self.runtime(Api()), at(SAT, 2), sleep=lambda s: None)
        finally:
            wk.MAX_REQUESTS = old
        self.assertEqual(self.count("000001", "30m"), 0)


class Inventory(Base):
    def test_backlog_inventory_counts_only_the_unverified_and_delayed_diagnostics_of_the_last_week(self):
        with self.store.connect() as db:
            for stamp, msg in ((at(FRI, 10).isoformat(), "일봉 원본 공식 완료봉 검증 미완료 · 재시도 예약"), (at(FRI, 11).isoformat(), "일봉 원본 공식 완료봉 검증 미완료 · 재시도 예약"),
                               (at(FRI, 12).isoformat(), "실시간 매매 판단 지연 - 오래된 틱으로 가상체결하지 않음"), (at(FRI, 13).isoformat(), "FIX3 거래소별 구독 변경"),
                               ((at(FRI, 10) - dt.timedelta(days=20)).isoformat(), "일봉 원본 공식 완료봉 검증 미완료 · 재시도 예약")):
                db.execute("INSERT INTO issues(seen_at,message) VALUES(?,?)", (stamp, msg))
        inv = wk.backlog_inventory(self.store, at(FRI, 21))
        self.assertEqual(inv, {"일봉 원본 공식 완료봉 검증 미완료 · 재시도 예약": 2, "실시간 매매 판단 지연 - 오래된 틱으로 가상체결하지 않음": 1})


class Status(Base):
    def test_status_lines(self):
        self.assertEqual(wk.status_line(str(self.root), FRI, at(dt.date(2026, 11, 26), 21)), {})
        wk._STATE.update(running=True, progress=[40, 400])
        self.assertEqual(wk.status_line(str(self.root), FRI, at(SAT, 3))["label"], "주말 데이터 점검·보충 중 40/400")
        wk._STATE["running"] = False
        wk.save_state(str(self.root), {FRI.isoformat(): {"finished": True, "filled": 123}})
        s = wk.status_line(str(self.root), FRI, at(SAT, 3))
        self.assertEqual((s["phase"], s["label"]), ("done", "주말 데이터 점검·보충 완료 · 123개 보충"))

    def test_base_status_prefers_the_audit_line_while_it_runs(self):
        wk._STATE.update(running=True, progress=[1, 2])
        self.assertEqual(base.status(str(self.root), at(SAT, 3))["phase"], "running")
        self.assertIn("주말", base.status(str(self.root), at(SAT, 3))["label"])


if __name__ == "__main__":
    unittest.main()
