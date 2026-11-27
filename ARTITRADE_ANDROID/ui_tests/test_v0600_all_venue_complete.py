"""0.6.0: the quiet hours complete the combined (KRX + NXT) 5m / 30m candles of the managed stocks in the separate stage-1 store (owner 2026-11-27)."""
import datetime as dt
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import all_venue_complete_v0600 as al
import stock_all_venue_bars_v0440 as stage1

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


def combined_rows(frame, days, hours=(8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19)):
    """ka10080 `_AL` rows of the given days: every candle start from 08:00 to 19:55 (5m) / 19:30 (30m), newest first like the broker."""
    step = 5 if frame == "5m" else 30
    rows = []
    for day in days:
        for h in hours:
            for m in range(0, 60, step):
                rows.append({"cntr_tm": "%s%02d%02d00" % (day.strftime("%Y%m%d"), h, m), "open_pric": "+100", "high_pric": "+110", "low_pric": "-95", "cur_prc": "+105", "trde_qty": "10"})
    return sorted(rows, key=lambda r: r["cntr_tm"], reverse=True)


class Api:
    def __init__(self, pages_by_frame=None, fail=(), nxt=True):
        self.calls, self.fail, self.nxt = [], dict.fromkeys(fail, 0), nxt
        self.pages = pages_by_frame or {}

    def post(self, api_id, path, body, cont="N", key=""):
        code, scope = body["stk_cd"], int(body["tic_scope"])
        self.calls.append((code, scope, cont, key))
        base_code = code[:-3]
        if base_code in self.fail and self.fail[base_code] < 1:
            self.fail[base_code] += 1
            raise ConnectionError("no route")
        frame = "5m" if scope == 5 else "30m"
        page = int(key or 0)
        days = [dt.date(2026, 11, 27), dt.date(2026, 11, 26)] if page == 0 else [dt.date(2026, 11, 25), dt.date(2026, 11, 24)]
        hours = (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19) if self.nxt else (9, 10, 11, 12, 13, 14, 15)
        return {"stk_min_pole_chart_qry": combined_rows(frame, days, hours), "_page_cont_yn": "Y" if page == 0 else "N", "_page_next_key": "1"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stock").mkdir()
        main = self.root / "stock" / "ARTITRADE_D.sqlite3"
        cx = sqlite3.connect(main)
        cx.executescript("CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)")
        for code in ("000001", "000002"):
            cx.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (code, "KOSPI", "5m", "20261127100000", 1, 1, 1, 1, 1, "LIVE"))
        cx.commit()
        cx.close()
        self.store = Store(str(main))
        al._STATE.update(running=False, progress=[0, 0], failures=0, paused_until=0.0, next_round=0.0)

    def runtime(self, api):
        runner = types.SimpleNamespace(api=api, store=self.store, _tick_queue=types.SimpleNamespace(qsize=lambda: 0))
        return types.SimpleNamespace(stock_runner=runner, root=str(self.root))

    def al_count(self, code, frame):
        path = self.root / "stock" / stage1.FILE
        if not path.exists():
            return 0
        with sqlite3.connect(path) as db:
            return db.execute("SELECT COUNT(*) FROM bars_all WHERE code=? AND frame=?", (code, frame)).fetchone()[0]


class Fetch(Base):
    def test_all_sessions_are_kept_including_nxt_hours_and_the_forming_candle_is_not(self):
        rows = al.fetch_pages(Api(), "000001", "5m", at(SAT, 12), 1, "20200101", sleep=lambda s: None)
        hours = {ts[8:10] for ts in rows}
        self.assertTrue({"08", "16", "17", "18", "19"} <= hours)
        self.assertTrue(al.has_nxt_hours(rows))
        self.assertFalse(al.has_nxt_hours({"20261127100000": (1, 1, 1, 1, 1)}))
        early = al.fetch_pages(Api(), "000001", "5m", at(FRI, 8, 7), 1, "20200101", sleep=lambda s: None)
        self.assertNotIn("20261127080500", early)                 # its end 08:10 is after 08:07: still forming

    def test_pages_stop_at_the_horizon_or_when_there_are_no_more(self):
        api = Api()
        rows = al.fetch_pages(api, "000001", "5m", at(SAT, 12), 6, "20200101", sleep=lambda s: None)
        self.assertEqual(len(api.calls), 2)                        # page 0 says "more", page 1 says "no more"
        self.assertEqual(min(rows)[:8], "20261124")
        api2 = Api()
        al.fetch_pages(api2, "000001", "5m", at(SAT, 12), 6, "20261126", sleep=lambda s: None)
        self.assertEqual(len(api2.calls), 1)                       # the horizon is reached on page 0

    def test_pace_between_pages(self):
        slept = []
        al.fetch_pages(Api(), "000001", "5m", at(SAT, 12), 6, "20200101", sleep=slept.append)
        self.assertEqual(slept, [al.PAUSE])

    def test_first_page_error_raises_but_an_older_page_error_is_tolerated(self):
        with self.assertRaises(ConnectionError):
            al.fetch_pages(Api(fail=("000001",)), "000001", "5m", at(SAT, 12), 2, "20200101", sleep=lambda s: None)

        class Late(Api):
            def post(self, *a, **k):
                if len(self.calls) >= 1:
                    raise ConnectionError("late")
                return super().post(*a, **k)
        rows = al.fetch_pages(Late(), "000001", "5m", at(SAT, 12), 3, "20200101", sleep=lambda s: None)
        self.assertTrue(rows)


class Store1(Base):
    def test_store_rows_inserts_new_candles_only_and_never_replaces(self):
        rows = al.fetch_pages(Api(), "000001", "5m", at(SAT, 12), 1, "20200101", sleep=lambda s: None)
        path = self.root / "stock" / stage1.FILE
        first = al.store_rows(path, "000001", "5m", rows, at(SAT, 12))
        self.assertEqual(first, len(rows))
        key = sorted(rows)[0]
        with sqlite3.connect(path) as db:
            db.execute("UPDATE bars_all SET close=999 WHERE code='000001' AND frame='5m' AND ts=?", (key,))
        self.assertEqual(al.store_rows(path, "000001", "5m", rows, at(SAT, 12, 5)), 0)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT close FROM bars_all WHERE ts=?", (key,)).fetchone()[0], 999)

    def test_the_shared_bars_table_is_never_touched(self):
        with self.store.connect() as db:
            before = db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        al.run(self.runtime(Api()), at(SAT, 2), sleep=lambda s: None)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bars").fetchone()[0], before)


class Round(Base):
    def test_a_round_completes_every_managed_stock_in_both_frames_and_records_nxt_hours(self):
        out = al.run(self.runtime(Api()), at(SAT, 2), sleep=lambda s: None)
        self.assertTrue(out["finished"], out)
        for code in ("000001", "000002"):
            self.assertGreater(self.al_count(code, "5m"), 0)
            self.assertGreater(self.al_count(code, "30m"), 0)
        task = al.load_state(str(self.root))[FRI.isoformat()]
        self.assertEqual(sorted(task["nxt_hours"]), ["000001", "000002"])
        self.assertEqual(task["unresolved"], [])
        log = (self.root / "stock" / al.LOG_FILE).read_text("utf8")
        self.assertIn('"event": "START"', log)
        self.assertIn('"event": "DONE"', log)

    def test_a_stock_without_nxt_hours_is_recorded_as_such(self):
        al.run(self.runtime(Api(nxt=False)), at(SAT, 2), sleep=lambda s: None)
        self.assertEqual(al.load_state(str(self.root))[FRI.isoformat()]["nxt_hours"], [])

    def test_failed_stock_is_retried_in_a_later_round_up_to_three_times(self):
        api = Api(fail=("000002",))
        out = al.run(self.runtime(api), at(SAT, 2), sleep=lambda s: None)
        self.assertFalse(out["finished"])
        self.assertGreater(al._STATE["next_round"], 0.0)                  # rest between rounds
        self.assertFalse(al.run(self.runtime(api), at(SAT, 2, 1), sleep=lambda s: None)["ran"])
        al._STATE["next_round"] = 0.0
        out2 = al.run(self.runtime(api), at(SAT, 2, 30), sleep=lambda s: None)
        self.assertTrue(out2["finished"])
        self.assertEqual(al.load_state(str(self.root))[FRI.isoformat()]["unresolved"], [])

    def test_batch_limit_and_gentle_pace(self):
        self.assertLessEqual(al.BATCH, 60)                    # 0.6.7: 60 stocks a round, 120 s rest (was 40 / 600 s); the pace per request is unchanged: one request per second
        self.assertGreaterEqual(al.ROUND_REST, 120.0)
        self.assertGreaterEqual(al.PAUSE, 1.0)

    def test_yields_to_a_busy_feed_and_runs_only_in_the_quiet_window(self):
        rt = self.runtime(Api())
        rt.stock_runner._tick_queue = types.SimpleNamespace(qsize=lambda: 99)
        al.run(rt, at(SAT, 2), sleep=lambda s: None)
        self.assertEqual(rt.stock_runner.api.calls, [])
        self.assertFalse(al.run(self.runtime(Api()), at(FRI, 12), sleep=lambda s: None)["ran"])

    def test_normal_night_uses_the_shallow_page_count_and_a_weekend_the_deep_one(self):
        self.assertEqual(al.run(self.runtime(Api()), at(dt.date(2026, 11, 26), 21), sleep=lambda s: None)["pages"], al.PAGES)
        al._STATE["next_round"] = 0.0
        self.assertEqual(al.run(self.runtime(Api()), at(SAT, 2), sleep=lambda s: None)["pages"], al.WEEKEND_PAGES)


class Status(Base):
    def test_card_line_while_the_combined_work_runs(self):
        import offhours_prework_v0530 as base
        al._STATE.update(running=True, progress=[7, 100])
        s = base.status(str(self.root), at(SAT, 3))
        self.assertEqual((s["phase"], s["label"]), ("running", "장마감 후 통합봉 수집·저장 중 7/100"))


if __name__ == "__main__":
    unittest.main()
