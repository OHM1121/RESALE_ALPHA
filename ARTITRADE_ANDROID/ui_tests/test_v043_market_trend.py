"""v043 RSI20 market-trend math, exits, locks, and research persistence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import datetime as dt
import sys
import unittest

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import market_trend_v043 as m


def at(y=2026,mo=11,d=13,h=10,minute=0,s=0):
    return dt.datetime(y,mo,d,h,minute,s,tzinfo=m.KST)


class MarketTrendV043(TestCase):
    def test_closed_wilder_rsi_and_unfinished_bar_are_distinct(self):
        self.assertIsNone(m.rsi20([100]*20))
        self.assertEqual(m.rsi20([100+i for i in range(21)]),100.0)
        self.assertEqual(m.rsi20([100-i for i in range(21)]),0.0)
        self.assertEqual(m.rsi20([100]*21),50.0)
        self.assertLess(m.rsi20([100+i for i in range(21)],80),100)
        self.assertIsNone(m.rsi20([100]*21,forming_close=-1))

    def test_market_gate_negative_zero_stale_or_failed(self):
        self.assertEqual(m.market_mode(-.1),"MARKET_TREND")
        self.assertEqual(m.market_mode(0),"ORIGINAL")
        self.assertEqual(m.market_mode(None),"PAUSED")
        self.assertEqual(m.market_mode(-20,stale=True),"PAUSED")
        self.assertEqual(m.market_mode(-20,status="API 오류"),"PAUSED")
        self.assertEqual(m.market_mode(-20,market_open=False),"PAUSED")

    def test_new_candidate_existing_oversold_is_unarmed(self):
        latch=m.SignalLatch()
        latch.completed("bar0",27)
        self.assertFalse(latch.offer("bar1",24))
        latch.completed("bar1",32)
        self.assertTrue(latch.offer("bar2",29))
        self.assertFalse(latch.offer("bar2",26))
        self.assertFalse(latch.offer("bar3",27))
        latch.completed("bar3",33)
        self.assertTrue(latch.offer("bar4",30))

    def test_preclose_time_and_krx_auction_exception(self):
        self.assertTrue(m.preclose_due(at(h=10,minute=4,s=48),at(h=10,minute=5),"5m","stock"))
        self.assertFalse(m.preclose_due(at(h=10,minute=4,s=47),at(h=10,minute=5),"5m","stock"))
        self.assertTrue(m.preclose_due(at(h=15,minute=19,s=48),at(h=15,minute=30),"day","stock"))
        self.assertFalse(m.preclose_due(at(h=15,minute=29,s=48),at(h=15,minute=30),"day","stock"))
        self.assertFalse(m.preclose_due(at(h=15,minute=22,s=0),at(h=15,minute=30),"30m","stock"))
        self.assertTrue(m.preclose_due(at(h=23,minute=59,s=48),
                         at(y=2026,mo=11,d=14,h=0),"day","coin"))

    def test_expiry_counts_krx_trading_dates_as_24h(self):
        # 13 November 2026 is a Friday.
        sat=at(2026,11,14,10)
        self.assertEqual(m.hold_deadline(at(), "5m","stock"),at(2026,11,17,10))
        self.assertEqual(m.hold_deadline(at(), "30m","stock"),at(2026,11,18,10))
        self.assertEqual(m.hold_deadline(at(), "60m","stock"),at(2026,11,20,10))
        self.assertEqual(m.hold_deadline(at(), "5m","coin"),at(2026,11,15,10))
        self.assertEqual(m.hold_deadline(at(), "day","coin"),at(2026,11,23,10))
        self.assertGreater(m.hold_deadline(at(), "day","stock"),sat)

    def test_long_frame_first_and_capped_targets(self):
        self.assertEqual(m.choose_frame(["5m","30m","day"]),"day")
        goals=m.candle_targets(10000,10000,9600)
        self.assertAlmostEqual(goals["first"],10300)
        self.assertAlmostEqual(goals["second"],10609)
        self.assertAlmostEqual(m.candle_targets(9700,10000,9600)["first"],10000)
        self.assertIsNone(m.candle_targets(10000,10000,10000))

    def test_sell_stages_stop_and_retouch(self):
        p=m.TrendPosition("coin","general","5m","KRW-BTC",at(),10000,100,"bar0")
        self.assertEqual(p.action(9000,at(h=10,minute=0,s=55),bar_finished=False),
                         ("MT_STOP_9PCT",1.0))
        p.close_entry_bar("bar0",10000,9800,28)
        self.assertIsNone(p.action(10000,at(h=10,minute=5)))
        self.assertEqual(p.action(10200,at(h=10,minute=5)),("MT_TP1",.30))
        p.apply_sell("MT_TP1",30,10200)
        self.assertEqual(p.qty,70)
        self.assertAlmostEqual(p.action(10404,at(h=10,minute=6))[1],.5)
        p.apply_sell("MT_TP2",35,10404)
        self.assertEqual(p.qty,35)
        self.assertIsNone(p.action(11000,at(h=10,minute=7)))
        self.assertEqual(p.action(10500,at(h=10,minute=8))[0],"MT_TP3_TRAIL")

    def test_three_bar_oversold_trigger_and_reset(self):
        p=m.TrendPosition("coin","ma","5m","KRW-BTC",at(),10000,100,"bar0")
        p.close_entry_bar("bar0",10000,9800,28)
        p.close_next_bar("bar1",28);p.close_next_bar("bar2",27)
        self.assertEqual(p.closed_seq,2)
        self.assertEqual(p.action(9999,at(h=10,minute=19,s=50),bar3_preclose=True)[0],
                         "MT_RSI_3_BARS")
        p.close_next_bar("bar3",33)
        self.assertEqual(p.closed_seq,-1)
        p.close_next_bar("bar4",29)
        self.assertEqual(p.closed_seq,0)

    def test_evidence_is_persisted_and_deduped(self):
        with TemporaryDirectory() as root:
            j=m.TrendJournal(root)
            latch=m.SignalLatch(True)
            j.save_latch("coin","KRW-BTC","5m",latch)
            self.assertTrue(m.TrendJournal(root).latch("coin","KRW-BTC","5m").armed)
            j.event("e1","coin","ma","5m","KRW-BTC","VIRTUAL","FUNDS",price=100,
                    rsi20_live=28.3,rsi20_closed=32)
            j.event("e1","coin","ma","5m","KRW-BTC","VIRTUAL","FUNDS",price=100)
            self.assertEqual(len(j.events_recent()),1)
            j.record_watch("coin","KRW-BTC","5m",100,38,"bar1",at=at().timestamp())
            j.record_watch("coin","KRW-BTC","5m",100,42,"bar1",at=at().timestamp())
            with j.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM watch_observations").fetchone()[0],1)


if __name__=="__main__":unittest.main()
