"""v107 source-only regression: NO APK, NO LIVE orders, NO phone mutation."""
import datetime as dt
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

import market_trend_v043 as original
from market_trend_closed_entry_v107 import (
    first_closed_cross, first_ten_seconds, verified_closed_rows)
from android_research_bundle import make_bundle


class ClosedRsiTest(unittest.TestCase):
    def setUp(self):
        self.closes = [100 + i for i in range(29)] + [1]
        self.utc = dt.timezone.utc
        self.open_at = dt.datetime(2026, 11, 18, 6, 0, tzinfo=self.utc)

    def test_first_under_30_on_only_latest_completed_bar(self):
        signal = first_closed_cross(self.closes, original.rsi20)
        self.assertIsNotNone(signal)
        self.assertLess(signal["rsi20_closed"], 30)
        self.assertTrue(all(x >= 30 for x in signal["last_10_rsi20"][:-1]))

    def test_no_historical_or_current_bar_double_entry(self):
        self.assertIsNone(first_closed_cross(self.closes + [1], original.rsi20))
        self.assertIsNone(first_closed_cross(self.closes[:-1], original.rsi20))

    def test_next_bar_start_window(self):
        self.assertTrue(first_ten_seconds(self.open_at, self.open_at))
        self.assertTrue(first_ten_seconds(self.open_at+dt.timedelta(seconds=9.999),
                                          self.open_at))
        self.assertFalse(first_ten_seconds(self.open_at+dt.timedelta(seconds=10),
                                           self.open_at))
        self.assertFalse(first_ten_seconds(self.open_at-dt.timedelta(microseconds=1),
                                           self.open_at))

    def test_coin_requires_just_completed_bar(self):
        rows = []
        for i,price in enumerate(self.closes):
            rows.append({"end":self.open_at-dt.timedelta(minutes=5*(29-i)),
                         "close":price})
        verified = verified_closed_rows(rows,self.open_at,"coin","5m")
        self.assertEqual(len(verified),30)
        self.assertIsNotNone(first_closed_cross([r["close"] for r in verified],
                                                original.rsi20))
        missing = rows[:-1]
        self.assertEqual(verified_closed_rows(missing,self.open_at,"coin","5m"),[])

    def test_future_candle_excluded(self):
        rows = [{"end":self.open_at-dt.timedelta(minutes=5*(29-i)),
                 "close":x} for i,x in enumerate(self.closes)]
        rows.append({"end":self.open_at+dt.timedelta(minutes=5),"close":1})
        self.assertEqual(len(verified_closed_rows(rows,self.open_at,"coin","5m")),30)


class ExportTest(unittest.TestCase):
    def test_lossless_research_and_error_zip_deflate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"ledger"
            out=Path(tmp)/"result"
            (root/"coin"/"base").mkdir(parents=True)
            out.mkdir()
            db=root/"coin"/"base"/"upbit_paper.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT)")
                conn.executemany("INSERT INTO events(payload) VALUES(?)",
                                 [("x"*800,) for _ in range(350)])
            with sqlite3.connect(db) as conn:
                expected=conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            for kind in ("research","errors"):
                result=make_bundle(str(root),str(out),"coin",
                                   max_part_bytes=2_000_000,bundle_kind=kind)
                self.assertTrue(result)
                names=sorted(out.glob("ARTITRADE_COIN_*_PART_*.zip"))
                self.assertGreaterEqual(len(names),1)
                checked=False
                for name in names:
                    with zipfile.ZipFile(name) as z:
                        dbs=[x for x in z.namelist() if x.endswith("upbit_paper.sqlite3")]
                        if not dbs:continue
                        checked=True
                        info=z.getinfo(dbs[0])
                        self.assertEqual(info.compress_type,zipfile.ZIP_DEFLATED)
                        self.assertLess(info.compress_size,info.file_size)
                        restored=out/(kind+".sqlite3")
                        restored.write_bytes(z.read(dbs[0]))
                        with sqlite3.connect(restored) as conn:
                            got=conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                        self.assertEqual(got,expected)
                self.assertTrue(checked)


if __name__=="__main__":
    unittest.main()
