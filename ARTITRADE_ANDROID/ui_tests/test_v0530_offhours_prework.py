"""0.5.3: the off-hours worker completes the finished candles of the day the live feed missed (no signals, no orders, no ledger)."""
import datetime as dt
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import offhours_prework_v0530 as off

KST = dt.timezone(dt.timedelta(hours=9))
DAY = dt.date(2026, 11, 26)
SCHEMA = """CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open INTEGER,high INTEGER,low INTEGER,close INTEGER,volume INTEGER,source TEXT,
            PRIMARY KEY(code,frame,ts));
            CREATE TABLE stocks(code TEXT PRIMARY KEY,market TEXT)"""


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def connect(self):
        cx = sqlite3.connect(self.path, timeout=5)
        cx.row_factory = sqlite3.Row
        return cx


def slots(frame):
    step = {"5m": 5, "30m": 30, "60m": 60}[frame]
    out, minute = [], 540
    while minute + step <= 930:
        out.append("20261126%02d%02d00" % (minute // 60, minute % 60))
        minute += step
    return out


def accepted(frame):
    """The slots the vendor accepts as regular-session references (the closing-auction slots 15:20 / 15:25 of 5m are excluded by design)."""
    from d_engine import is_krx_regular_reference
    return [ts for ts in slots(frame) if is_krx_regular_reference(ts, frame)]


class Api:
    """Broker stand-in: returns the full finished day for every code and frame."""

    def __init__(self, drop=()):
        self.calls = []
        self.drop = set(drop)

    def history_bars(self, code, frame):
        self.calls.append((code, frame))
        if frame == "day":
            return [(code, "day", "20261126000000", 100, 110, 95, 105, 1000)]
        return [(code, frame, ts, 100, 110, 95, 105, 10) for ts in slots(frame) if (code, frame, ts) not in self.drop]


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
        off._STATE.update(failures=0, paused_until=0.0)

    def put(self, code, frame, ts, close=105, market="KOSPI"):
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO stocks(code,market) VALUES(?,?)", (code, market))
            db.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (code, market, frame, ts, 100, 110, 95, close, 10, "LIVE"))

    def count(self, code, frame):
        with self.store.connect() as db:
            return db.execute("SELECT COUNT(*) FROM bars WHERE code=? AND frame=?", (code, frame)).fetchone()[0]

    def runtime(self, api):
        runner = types.SimpleNamespace(api=api, store=self.store, _tick_queue=types.SimpleNamespace(qsize=lambda: 0))
        return types.SimpleNamespace(stock_runner=runner, root=str(self.root))

    def at(self, h, m=0, day=DAY):
        return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=KST)


class Window(unittest.TestCase):
    def test_the_work_day_and_the_window(self):
        self.assertEqual(off.target_day(dt.datetime(2026, 11, 26, 20, 5, tzinfo=KST)), DAY)
        self.assertEqual(off.target_day(dt.datetime(2026, 11, 27, 3, 0, tzinfo=KST)), DAY)        # the next morning, phone was off
        self.assertEqual(off.target_day(dt.datetime(2026, 11, 30, 3, 0, tzinfo=KST)), dt.date(2026, 11, 27))   # Monday morning -> Friday
        self.assertIsNone(off.target_day(dt.datetime(2026, 11, 26, 12, 0, tzinfo=KST)))           # never during the market
        self.assertIsNone(off.target_day(dt.datetime(2026, 11, 27, 7, 45, tzinfo=KST)))
        self.assertEqual(off.target_day(dt.datetime(2026, 11, 28, 22, 0, tzinfo=KST)), dt.date(2026, 11, 27))   # 0.5.9: Saturday belongs to the Friday window


class Completion(Base):
    def test_missing_candles_are_inserted_and_existing_ones_are_never_replaced(self):
        for ts in slots("5m")[:10]:
            self.put("005930", "5m", ts, close=999)                       # the live feed delivered only the first 10 (own values)
        summary = off.run(self.runtime(Api()), now=self.at(20, 10), sleep=lambda s: None)
        self.assertTrue(summary["done"])
        self.assertEqual(self.count("005930", "5m"), len(accepted("5m")))
        with self.store.connect() as db:
            kept = db.execute("SELECT close FROM bars WHERE code='005930' AND frame='5m' AND ts=?", (slots("5m")[0],)).fetchone()[0]
            added = db.execute("SELECT close,source FROM bars WHERE code='005930' AND frame='5m' AND ts=?", (slots("5m")[20],)).fetchone()
        self.assertEqual(kept, 999)                                        # not overwritten
        self.assertEqual((added[0], added[1]), (105, off.SOURCE))
        self.assertEqual(self.count("005930", "30m"), len(accepted("30m")))
        self.assertEqual(self.count("005930", "day"), 1)

    def test_a_complete_day_is_left_alone(self):
        for frame in ("5m", "30m", "60m"):
            for ts in accepted(frame):
                self.put("000660", frame, ts)
        self.put("000660", "day", "20261126000000")
        summary = off.run(self.runtime(Api()), now=self.at(20, 10), sleep=lambda s: None)
        self.assertEqual((summary["missing_found"], summary["inserted"]), (0, 0))

    def test_invalid_rows_are_never_stored(self):
        class Dirty(Api):
            def history_bars(self, code, frame):
                good = super().history_bars(code, frame)
                bad = [(code, frame, "20261125090000", 100, 110, 95, 105, 10),          # another day
                       (code, frame, "20261126084000", 100, 110, 95, 105, 10),          # outside the regular session
                       ("000001", frame, "20261126100000", 100, 110, 95, 105, 10),      # another code
                       (code, frame, "20261126100500", 100, 90, 95, 105, 10),           # high below open
                       (code, frame, "20261126101000", 100, 110, 95, 105, 0),           # zero volume
                       (code, frame, "20261126101500", 100, 110, 95, 105, 10)]
                return good + bad if frame == "5m" else good
        self.put("005930", "5m", "20261126090000")
        off.run(self.runtime(Dirty()), now=self.at(20, 10), sleep=lambda s: None)
        with self.store.connect() as db:
            stamps = {r[0] for r in db.execute("SELECT ts FROM bars WHERE code='005930' AND frame='5m'")}
        self.assertTrue(stamps <= set(accepted("5m")))                      # nothing outside the regular slots of the day

    def test_it_yields_to_a_running_feed_and_stops_outside_the_window(self):
        self.put("005930", "5m", slots("5m")[0])
        busy = self.runtime(Api())
        busy.stock_runner._tick_queue = types.SimpleNamespace(qsize=lambda: 50)
        self.assertFalse(off.run(busy, now=self.at(20, 10), sleep=lambda s: None).get("done"))
        self.assertEqual(self.count("005930", "5m"), 1)
        self.assertFalse(off.run(self.runtime(Api()), now=self.at(12, 0), sleep=lambda s: None)["ran"])

    def test_the_night_is_done_once_and_resumes_after_an_interruption(self):
        for code in ("005930", "000660", "035420"):
            self.put(code, "5m", slots("5m")[0])
        api = Api()
        original = off.MAX_REQUESTS
        try:
            off.MAX_REQUESTS = 5                                           # budget ends the first pass after one code (4 requests) + 1
            first = off.run(self.runtime(api), now=self.at(20, 10), sleep=lambda s: None)
            self.assertFalse(first["done"])
        finally:
            off.MAX_REQUESTS = original
        done_before = len(off.load_state(str(self.root))["2026-11-26"]["bars_completion"]["codes_done"])
        second = off.run(self.runtime(api), now=self.at(20, 40), sleep=lambda s: None)
        self.assertTrue(second["done"])
        calls_after = len(api.calls)
        third = off.run(self.runtime(api), now=self.at(21, 0), sleep=lambda s: None)
        self.assertFalse(third["ran"] and third.get("todo"))
        self.assertEqual(len(api.calls), calls_after)                      # done: no more requests
        self.assertGreaterEqual(done_before, 1)
        lines = (self.root / "stock" / off.LOG_FILE).read_text("utf8").splitlines()
        self.assertTrue(any('"event": "DONE"' in x for x in lines))

    def test_three_failures_in_a_row_pause_the_task(self):
        class Broken(Api):
            def history_bars(self, code, frame):
                raise OSError("network down")
        for code in ("005930", "000660", "035420", "051910"):
            self.put(code, "5m", slots("5m")[0])
        summary = off.run(self.runtime(Broken()), now=self.at(20, 10), sleep=lambda s: None)
        self.assertFalse(summary.get("done"))
        self.assertGreater(off._STATE["paused_until"], 0)
        off._STATE.update(failures=0, paused_until=0.0)


if __name__ == "__main__":
    unittest.main()
