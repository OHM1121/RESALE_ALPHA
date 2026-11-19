"""Read-only v008 special-session and original NEW LEGACY MarketTrend proof tests.

All quotes/candles are deterministic fixtures; no broker/PAPER orders are placed.
"""
from __future__ import annotations
import datetime as dt
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]/"app/src/main/python"
sys.path.insert(0,str(ROOT))
from market_trend_1118_runtime import RsiWatch,_confirmed_recent,_mark_rsi_watch
from new_legacy_1118_policy import bollinger,RSI_TRACK_HOURS

KST=dt.timezone(dt.timedelta(hours=9))

class TrendSourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.at=dt.datetime(2026,11,19,8,41,27,tzinfo=KST)
        end=dt.datetime(2026,11,19,8,30,0,tzinfo=KST)
        self.bars=[{"end":end-dt.timedelta(minutes=30*(21-i)),
                    "close":float(100+i),"source":"UPBIT_CLOSED"}
                   for i in range(22)]

    def test_complete_coin_30m_source_is_accepted(self):
        rows=_confirmed_recent(self.bars,self.at,"coin","30m")
        self.assertEqual(rows[-1]["end"].strftime("%H:%M"),"08:30")
        self.assertGreaterEqual(len(rows),21)

    def test_special_krx_first_complete_30m_is_required(self):
        now=dt.datetime(2026,11,19,10,41,27,tzinfo=KST)
        end=dt.datetime(2026,11,19,10,30,tzinfo=KST)
        rows=[{"end":end-dt.timedelta(minutes=30*(20-i)),
               "close":100-i*.1,"source":"LOCAL_KRX_VERIFIED"}
              for i in range(21)]
        self.assertEqual(_confirmed_recent(rows,now,"stock","30m")[-1]["end"],end)
        self.assertEqual(_confirmed_recent(rows[:-1],now,"stock","30m"),[])

    def test_missing_newest_completed_bar_must_block_entry(self):
        # ONDO-like 08:41 current price cannot replace missing 08:30 bar.
        self.assertEqual(_confirmed_recent(self.bars[:-1],self.at,"coin","30m"),[])

    def test_middle_gap_cannot_be_silently_ignored(self):
        self.assertEqual(_confirmed_recent(self.bars[:12]+self.bars[13:],self.at,"coin","30m"),[])

    def test_unverified_or_bogus_source_cannot_authorize_order(self):
        rows=[dict(x) for x in self.bars]
        rows[-1]["source"]="CACHED_DISPLAY"
        self.assertEqual(_confirmed_recent(rows,self.at,"coin","30m"),[])

    def test_bollinger_mid_slope_limit_is_strict(self):
        falling=[100*.99**i for i in range(21)]
        x=bollinger(falling)
        self.assertLess(x["angle_deg"],-20)
        self.assertFalse(x["angle_allowed"])

    def test_legacy_watch_migrates_in_place_without_authorizing_buy(self):
        now=self.at
        with tempfile.TemporaryDirectory() as root:
            db=Path(root)/"market_trend_rsi_watch_v1118.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE rsi_watch(
                    market TEXT NOT NULL,symbol TEXT NOT NULL,frame TEXT NOT NULL,
                    observed_at TEXT NOT NULL,expires_at TEXT NOT NULL,
                    first_rsi20 REAL NOT NULL,status TEXT NOT NULL,
                    purchased_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(market,symbol,frame))""")
                conn.execute("INSERT INTO rsi_watch VALUES(?,?,?,?,?,?,?,?)",
                             ("coin","KRW-TEST","30m",
                              (now-dt.timedelta(minutes=30)).isoformat(),
                              (now+dt.timedelta(hours=23)).isoformat(),
                              29.7,"WATCHING",""))
            store=RsiWatch(root)
            key=("coin","KRW-TEST","30m")
            existing=store.load()[key]
            self.assertEqual(existing["status"],"WATCHING")
            self.assertIsNone(existing["evidence"])
            evidence={"market":"coin","symbol":"KRW-TEST","frame":"30m",
                      "observed_at":now.isoformat(),"rsi20":29.8,
                      "history_end":"2026-11-19T08:30:00+09:00",
                      "source":"UPBIT_CLOSED",
                      "closed21_sha256":"test-fixture"}
            owner=SimpleNamespace(_nl1118_watches={key:existing},_nl1118_watch_db=store)
            renewed=_mark_rsi_watch(owner,"coin",key,existing,29.8,now,evidence=evidence)
            self.assertEqual(renewed["observed_at"],now.isoformat())
            self.assertEqual(renewed["expires_at"],
                             (now+dt.timedelta(hours=RSI_TRACK_HOURS)).isoformat())
            self.assertEqual(store.load()[key]["evidence"],evidence)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM rsi_watch").fetchone()[0],1)

if __name__=="__main__":
    unittest.main()
