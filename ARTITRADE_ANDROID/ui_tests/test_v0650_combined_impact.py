"""0.6.5: report of how much the decisions would change on the combined (KRX + NXT) candles (local reads only)."""
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import combined_impact_v0650 as ci

KST = dt.timezone(dt.timedelta(hours=9))
FRI, SAT = dt.date(2026, 11, 27), dt.datetime(2026, 11, 28, 12, 0, tzinfo=KST)


class Store:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()

    def connect(self):
        cx = sqlite3.connect(self.path, timeout=5)
        cx.row_factory = sqlite3.Row
        return cx


def stamps(count, start=dt.datetime(2026, 11, 2, 9, 0), step=30):
    out, t = [], start
    while len(out) < count:
        if t.weekday() < 5 and 9 * 60 <= t.hour * 60 + t.minute < 15 * 60 + 30:
            out.append(t.strftime("%Y%m%d%H%M%S"))
        t += dt.timedelta(minutes=step)
        if t.hour * 60 + t.minute >= 15 * 60 + 30:
            t = (t + dt.timedelta(days=1)).replace(hour=9, minute=0)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stock").mkdir()
        main = self.root / "stock" / "ARTITRADE_D.sqlite3"
        with sqlite3.connect(main) as db:
            db.execute("CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)")
            for code, drift in (("000001", 1.0), ("000002", 1.0)):
                for i, ts in enumerate(stamps(130)):
                    price = 1000 + i * drift
                    db.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (code, "KOSPI", "30m", ts, price, price, price, price, 100, "LIVE"))
        self.store = Store(str(main))
        with sqlite3.connect(self.root / "stock" / "all_venue_bars_v0440.sqlite3") as db:
            db.execute("CREATE TABLE bars_all(code TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,fetched_at TEXT, PRIMARY KEY(code,frame,ts))")
            for code in ("000001", "000002"):
                for i, ts in enumerate(stamps(130)):
                    price = 1000 + i
                    db.execute("INSERT INTO bars_all VALUES(?,?,?,?,?,?,?,?,?)", (code, "30m", ts, price, price, price, price, 100, "x"))
            # 000002: a falling NXT after-market run after the last regular bar changes the combined trend
            last = stamps(130)[-1]
            for k in range(1, 40):
                ts = (dt.datetime.strptime(last, "%Y%m%d%H%M%S").replace(hour=16, minute=0) + dt.timedelta(minutes=30 * (k - 1))).strftime("%Y%m%d%H%M%S")
                db.execute("INSERT OR IGNORE INTO bars_all VALUES(?,?,?,?,?,?,?,?,?)", ("000002", "30m", ts, 900 - k, 900 - k, 900 - k, 900 - k, 50, "x"))
        self.runtime = types.SimpleNamespace(root=str(self.root), stock_runner=types.SimpleNamespace(store=self.store))


class Report(Base):
    def test_the_label_difference_the_volume_ratio_and_the_share_outside_regular_hours(self):
        report = ci.run(self.runtime, now=SAT)
        self.assertTrue(report["ran"])
        frame = report["frames"]["30m"]
        self.assertEqual(frame["codes"], 2)
        self.assertEqual((frame["trend_same"], frame["trend_diff"]), (1, 1))
        self.assertEqual(frame["examples"][0]["code"], "000002")
        self.assertGreater(frame["outside_regular"], 0)
        self.assertGreater(frame["outside_regular_share"], 0.0)
        self.assertEqual(frame["volume_ratio_median"], 1.0)
        self.assertEqual(report["readiness"]["managed"], 0)                   # no 5m bars in this fixture: nothing is "managed" for the readiness share

    def test_once_per_trading_day_and_outside_the_quiet_window_nothing(self):
        self.assertTrue(ci.run(self.runtime, now=SAT)["ran"])
        self.assertFalse(ci.run(self.runtime, now=SAT + dt.timedelta(minutes=5))["ran"])
        state = json.loads((self.root / "stock" / ci.STATE_FILE).read_text("utf8"))
        self.assertEqual(list(state), ["2026-11-27"])
        self.assertFalse(ci.run(self.runtime, now=dt.datetime(2026, 11, 27, 10, 0, tzinfo=KST))["ran"])

    def test_0670_the_report_repeats_while_the_fill_runs_and_freezes_when_it_is_finished(self):
        fill = self.root / "stock" / "offhours_al_v0600.json"
        fill.write_text(json.dumps({"2026-11-27": {"done": ["000001"], "codes_total": 4, "finished": False}}), "utf8")
        first = ci.run(self.runtime, now=SAT)
        self.assertTrue(first["ran"])
        self.assertEqual((first["final"], first["fill"]["done"], first["fill"]["total"]), (False, 1, 4))
        self.assertFalse(ci.run(self.runtime, now=SAT + dt.timedelta(minutes=10))["ran"])         # not yet: every 30 minutes
        second = ci.run(self.runtime, now=SAT + dt.timedelta(minutes=31))
        self.assertTrue(second["ran"])                                                              # the fill is still running: measured again
        fill.write_text(json.dumps({"2026-11-27": {"done": ["000001", "000002", "000003", "000004"], "codes_total": 4, "finished": True}}), "utf8")
        third = ci.run(self.runtime, now=SAT + dt.timedelta(minutes=70))
        self.assertTrue(third["ran"])
        self.assertTrue(third["final"])
        self.assertFalse(ci.run(self.runtime, now=SAT + dt.timedelta(minutes=140))["ran"])        # final: not repeated

    def test_no_combined_store_means_no_report_and_no_error(self):
        (self.root / "stock" / "all_venue_bars_v0440.sqlite3").unlink()
        out = ci.run(self.runtime, now=SAT)
        self.assertEqual((out["ran"], out.get("error")), (False, None))

    def test_the_regular_database_is_not_changed(self):
        with self.store.connect() as db:
            before = db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        ci.run(self.runtime, now=SAT)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bars").fetchone()[0], before)

    def test_installed_in_the_quiet_hours_loop(self):
        import inspect
        import offhours_prework_v0530 as off
        self.assertIn("combined_impact_v0650", inspect.getsource(off._combined_loop))


if __name__ == "__main__":
    unittest.main()
