"""0.6.6 (owner 2026-11-27): MA, supply-demand and MarketTrend may also trade after hours (16:00-20:00); the bar series is continuous across the sessions."""
import datetime as dt
import inspect
import json
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import after_hours_bars_v0660 as ext
import krx_session_v041 as krx

KST = dt.timezone(dt.timedelta(hours=9))


def at(h, mi=0, s=0, day=(2026, 11, 27)):
    return dt.datetime(*day, h, mi, s, tzinfo=KST)


class Clocks(unittest.TestCase):
    def test_the_tradable_time_is_regular_continuous_or_the_after_hours_session(self):
        for moment, expected in ((at(9, 0), True), (at(15, 19, 59), True), (at(15, 20), False), (at(15, 45), False), (at(16, 0), True), (at(19, 59, 59), True), (at(20, 0), False), (at(8, 10), False)):
            self.assertEqual(krx.in_tradable_clock(moment, "KRX"), expected, moment)
            self.assertEqual(krx.is_tradable_session(moment), expected, moment)

    def test_the_special_days_and_closed_days(self):
        self.assertFalse(krx.in_post_clock(at(16, 30, day=(2026, 11, 19))))           # college-exam day: the engine's after-hours session starts at 17:00
        self.assertTrue(krx.in_post_clock(at(17, 0, day=(2026, 11, 19))))
        self.assertFalse(krx.in_post_clock(at(17, 0, day=(2026, 11, 28))))            # Saturday
        self.assertFalse(krx.in_post_clock(at(17, 0, day=(2026, 12, 25))))            # exchange holiday
        self.assertFalse(krx.in_post_clock(at(17, 0, day=(2026, 9, 11))))             # before the after-hours session existed (2026-09-14)

    def test_it_equals_the_engines_own_clock(self):
        try:
            import d_v19_portfolio as pf
        except Exception:
            self.skipTest("vendor engine not on the path")
        for hh, mm in ((9, 0), (15, 19), (15, 20), (15, 29), (15, 30), (15, 59), (16, 0), (19, 59), (20, 0)):
            moment = at(hh, mm)
            self.assertEqual(krx.in_tradable_clock(moment, "KRX"), bool(pf.live_session(moment, "KRX")), (hh, mm))


class MarketTrendGrid(unittest.TestCase):
    def setUp(self):
        try:
            import market_trend_runtime_v043 as trend
        except Exception as exc:
            self.skipTest("market trend runtime not importable: %r" % (exc,))
        self.trend = trend

    def test_after_hours_bars_end_on_the_continued_grid_and_the_day_has_none(self):
        end = self.trend._end_clock
        self.assertEqual(end(at(16, 10), "stock", "30m"), at(16, 30))
        self.assertEqual(end(at(16, 10), "stock", "60m"), at(17, 0))
        self.assertEqual(end(at(19, 40), "stock", "60m"), at(20, 0))
        self.assertEqual(end(at(19, 40), "stock", "30m"), at(20, 0))
        self.assertIsNone(end(at(17, 0), "stock", "day"))

    def test_the_regular_session_is_unchanged(self):
        end = self.trend._end_clock
        self.assertEqual(end(at(10, 10), "stock", "30m"), at(10, 30))
        self.assertEqual(end(at(10, 10), "stock", "day"), at(15, 30))
        self.assertEqual(end(at(15, 10), "stock", "60m"), at(15, 30))
        self.assertIsNone(end(at(15, 45), "stock", "30m"))                          # the gap 15:30-16:00
        self.assertIsNone(end(at(8, 10), "stock", "30m"))

    def test_every_session_check_of_the_strategy_uses_the_tradable_session(self):
        for name in ("market_trend_runtime_v043", "market_trend_1118_runtime", "market_trend_preplan_v099"):
            module = __import__(name)
            source = inspect.getsource(module)
            self.assertIn("is_tradable_session", source, name)
        source = inspect.getsource(self.trend)
        self.assertEqual(source.count("is_regular_open"), 2)                         # only the history refresh of an old local cache still asks the regular session


class Engine(unittest.TestCase):
    def setUp(self):
        try:
            import d_v19_portfolio as pf  # noqa: F401
        except Exception:
            self.skipTest("vendor engine not on the path")

    def source(self, name):
        import importlib
        return Path(importlib.import_module(name).__file__).read_text("utf8")

    def test_supply_demand_checks_the_tradable_clock_at_all_six_places(self):
        import projectgmo.engine as gmo
        text = Path(gmo.__file__).read_text("utf8")
        self.assertEqual(text.count("in_tradable_clock("), 6)
        self.assertEqual(text.count("in_regular_clock("), 0)

    def test_ma_paper_engine_may_enter_in_the_after_hours_session(self):
        text = self.source("stock_ma_engine")
        self.assertIn("or (after and '160000'<=now.strftime('%H%M%S')<'195915')", text)
        self.assertIn("'090000'<=now.strftime('%H%M%S')<'151915' and not after", text)            # the regular entry window is unchanged


SCHEMA = """CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,PRIMARY KEY(code,frame,ts));
 CREATE TABLE stocks(code TEXT PRIMARY KEY,market TEXT,name TEXT,source TEXT);
 CREATE TABLE v19_venue_frames(code TEXT,venue TEXT,segment TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL)"""


class Store:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()

    def connect(self):
        cx = sqlite3.connect(self.path, timeout=5)
        cx.row_factory = sqlite3.Row
        return cx


class Backfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stock").mkdir()
        path = self.root / "stock" / "ARTITRADE_D.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO stocks VALUES('000001','KOSPI','A','X')")
            db.execute("INSERT INTO bars VALUES('000001','KOSPI','5m','20261126100000',1,1,1,1,5,'LIVE_WS_0B')")
            db.executemany("INSERT INTO v19_venue_frames VALUES(?,?,?,?,?,?,?,?,?,?)", [("000001", "KRX", "POST", "5m", "20261126160000", 10, 11, 9, 10.5, 7), ("000001", "NXT", "PRE", "5m", "20261127080000", 10, 11, 9, 10.5, 3), ("000009", "KRX", "POST", "5m", "20261126160000", 1, 1, 1, 1, 1)])
        with sqlite3.connect(self.root / "stock" / "all_venue_bars_v0440.sqlite3") as db:
            db.execute("CREATE TABLE bars_all(code TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,fetched_at TEXT,PRIMARY KEY(code,frame,ts))")
            db.executemany("INSERT INTO bars_all VALUES(?,?,?,?,?,?,?,?,?)", [("000001", "5m", "20261126100000", 1, 1, 1, 1, 9, "x"), ("000001", "5m", "20261126160000", 99, 99, 99, 99, 99, "x"),
                                                                              ("000001", "5m", "20261126163000", 12, 13, 11, 12, 20, "x"), ("000001", "30m", "20261126170000", 12, 13, 11, 12, 40, "x"),
                                                                              ("000001", "5m", "20261126154000", 8, 8, 8, 8, 8, "x")])
        self.runtime = types.SimpleNamespace(root=str(self.root), stock_runner=types.SimpleNamespace(store=Store(str(path))))

    def rows(self):
        with self.runtime.stock_runner.store.connect() as db:
            return {(r["frame"], r["ts"]): (r["source"], r["volume"]) for r in db.execute("SELECT * FROM bars WHERE code='000001' ORDER BY ts").fetchall()}

    def test_frames_and_official_extended_candles_join_the_series_and_nothing_else_changes(self):
        out = ext.run(self.runtime, now=dt.datetime(2026, 11, 28, 12, tzinfo=KST))
        self.assertTrue(out["ran"], out)
        rows = self.rows()
        self.assertEqual(rows[("5m", "20261126160000")], ("LIVE_EXT_KRX_POST", 7))        # the live frame wins over the official candle of the same start
        self.assertEqual(rows[("5m", "20261127080000")], ("LIVE_EXT_NXT_PRE", 3))
        self.assertEqual(rows[("5m", "20261126163000")], ("AL_EXT_OFFICIAL", 20))
        self.assertEqual(rows[("30m", "20261126170000")], ("AL_EXT_OFFICIAL", 40))
        self.assertEqual(rows[("5m", "20261126100000")], ("LIVE_WS_0B", 5))               # a regular bar is never replaced
        self.assertNotIn(("5m", "20261126154000"), rows)                                  # 15:40 is neither a regular nor an extended session
        self.assertEqual(out["frames_copied"], 2)                                          # 000009 has no market: skipped

    def test_once_per_trading_day_and_idempotent(self):
        now = dt.datetime(2026, 11, 28, 12, tzinfo=KST)
        self.assertTrue(ext.run(self.runtime, now=now)["ran"])
        self.assertFalse(ext.run(self.runtime, now=now + dt.timedelta(minutes=5))["ran"])
        with self.runtime.stock_runner.store.connect() as db:
            self.assertEqual(ext.copy_frames(db), 0)
            self.assertEqual(ext.copy_official(db, self.root / "stock" / "all_venue_bars_v0440.sqlite3"), 0)
        self.assertEqual(list(json.loads((self.root / "stock" / ext.STATE_FILE).read_text("utf8"))), ["2026-11-27"])

    def test_outside_the_quiet_window_nothing_runs_and_installed_in_the_loop(self):
        self.assertFalse(ext.run(self.runtime, now=dt.datetime(2026, 11, 27, 10, tzinfo=KST))["ran"])
        import offhours_prework_v0530 as off
        self.assertIn("after_hours_bars_v0660", inspect.getsource(off._loop))


if __name__ == "__main__":
    unittest.main()
