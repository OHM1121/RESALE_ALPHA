"""v0.8.1 KRX stock MarketTrend RSI35 preparatory scenarios.

Executes original independent TrendWalletV054 PAPER buy to its SQLite ledger;
does not simulate a broker fill in the test or open a second Kiwoom feed.
"""
from __future__ import annotations
import datetime as dt
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from market_trend_preplan_v099 import PreparedStockTrend,_limits,_qty_by_level
from market_trend_v043 import KST,SignalLatch
from market_trend_wallet_v054 import TrendWalletV054

CODE="005930"
CLOSE=dt.datetime(2026,11,17,9,5,tzinfo=KST)
NOW=CLOSE-dt.timedelta(seconds=10)


class Watch:
    def __init__(self):
        self.rows=[{"code":CODE,"frame":"5m","bar_end":CLOSE.isoformat(),
                    "rsi20_live":35.0,"armed":True,"status":"WATCH_PENDING"}]
    def snapshot(self,market):return list(self.rows)
    def discard(self,market,code,frame):
        self.rows=[r for r in self.rows
                   if r["code"]!=code or r["frame"]!=frame]


class LedgerJournal:
    def __init__(self):
        self._latch=SignalLatch(armed=True)
        self.saved=[]
        self.events=[]
    def latch(self,*args):return self._latch
    def save_latch(self,*args):self.saved.append(args)
    def stop_reentry_blocked(self,*args):return False
    def event(self,*args,**kw):self.events.append((args,kw))


class SafeKRXDepth:
    def __init__(self,price=10000,qty=100):
        self._lock=threading.RLock()
        self._items={(CODE,"KRX"):(price,price-10,qty,
                                   dt.datetime.now(KST))}
        self.reserved=[]
    def available(self,code,venue,at,limit,desired):
        book=self._items.get((code,venue))
        if book is None or book[0]>limit:return None,"PRICE_TOO_HIGH"
        if abs((dt.datetime.now(KST)-book[3]).total_seconds())>3:return None,"STALE"
        qty=min(desired,book[2])
        if qty<=0:return None,"NO_DEPTH"
        return (book[0],qty),"OK"
    def reserve(self,code,venue,at,limit,qty):
        book=self._items.get((code,venue))
        if not book or book[0]>limit or qty>book[2]:return False
        self.reserved.append((code,venue,qty))
        return True


class StockPlans(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.depth=SafeKRXDepth()
        self.runner=SimpleNamespace(market_map={CODE:("KOSPI","TEST")},
            engine=SimpleNamespace(book=self.depth))
        self.rt=SimpleNamespace(root=Path(self.tmp.name),
            stock_runner=self.runner,coin_runner=None,live_enabled=lambda m:False)
        self.wallet=TrendWalletV054(self.rt)
        self.journal=LedgerJournal()
        self.watch=Watch()
        self.blocked=[]
        self.quote={"ask":10000.0,"ask_size":100.0,"bid":9990.0,
                    "bid_size":50.0,"trade":9990.0}
        self.co=SimpleNamespace(
            rt=self.rt,stop_event=threading.Event(),
            standalone_wallet=self.wallet,watch=self.watch,
            _candidates={"stock":{CODE:("KOSPI","TEST")},"coin":{}},
            _quotes={"stock":{CODE:(9990.0,time.monotonic())},"coin":{}},
            _positions={},_ordered=set(),lock=threading.RLock(),
            _decision_clock=lambda:NOW,
            journal=self.journal,_last_fetch_error={"stock":"","coin":""},
            _closed_series=lambda *a:[{"close":10000.0,"end":NOW}
                                      for _ in range(30)],
            _quote=lambda *a:self.quote,
            _record_entry_block=lambda *a,**kw:self.blocked.append(a))
        self.p=PreparedStockTrend(self.co)
        self.p.prepare(NOW)

    def tearDown(self):self.tmp.cleanup()

    def level(self,price,qty=100):
        self.depth._items[(CODE,"KRX")]=(price,price-10,qty,dt.datetime.now(KST))
        self.quote["ask"]=float(price)
        self.quote["bid"]=float(price-10)
        self.quote["ask_size"]=float(qty)
        self.co._quotes["stock"][CODE]=(9990.0,time.monotonic())

    def test_stock_rsi35_prepares_three_krx_levels_with_integer_quantities(self):
        plans=self.p.snapshot()
        self.assertEqual(len(plans),1)
        p=plans[0]
        self.assertEqual(p["scenarios"],["10000","10010","10020"])
        self.assertEqual(p["budget_won"],500000)
        self.assertEqual(p["precomputed_qty"],["49","49","49"])
        self.assertEqual(len(self.wallet.open_positions("stock")),0)
        self.assertEqual(self.depth.reserved,[])

    def test_first_cross_at_original_close_window_uses_authoritative_sqlite(self):
        self.p.evaluate(NOW)
        positions=self.wallet.open_positions("stock")
        self.assertEqual(len(positions),1)
        self.assertEqual(positions[0].symbol,CODE)
        self.assertEqual(positions[0].execution_mode,"PAPER")
        self.assertEqual(self.p.snapshot()[0]["scenario"],1)
        self.assertEqual(len(self.journal.saved),1)
        self.assertEqual(self.journal.events[0][0][0],
                         "MT54:SIG:stock:"+CODE+":5m:"+CLOSE.isoformat())
        self.assertEqual(self.journal.events[0][0][6],
                         "MT081_STOCK_PREPARED_S1_BOUGHT")
        self.assertEqual(len(self.depth.reserved),1)
        self.assertLessEqual(positions[0].entry*positions[0].qty,500000)
        self.p.evaluate(NOW)
        self.assertEqual(len(self.wallet.open_positions("stock")),1)

    def test_third_limit_still_under_max_seed(self):
        self.level(10020)
        self.p.evaluate(NOW)
        held=self.wallet.open_positions("stock")
        self.assertEqual(len(held),1)
        self.assertEqual(self.p.snapshot()[0]["scenario"],3)
        self.assertEqual(held[0].entry,10020)
        self.assertLessEqual(held[0].entry*held[0].qty,500000)
        self.assertEqual(len(self.depth.reserved),1)

    def test_above_two_krx_ticks_no_buy(self):
        self.level(10030)
        self.p.evaluate(NOW)
        self.assertEqual(len(self.wallet.open_positions("stock")),0)
        self.assertEqual(self.p.snapshot()[0]["status"],
                         "ABOVE_SCENARIO_3_CEILING")
        self.assertEqual(self.depth.reserved,[])

    def test_missing_krx_book_no_fill(self):
        self.depth._items.clear()
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("stock"))
        self.assertEqual(self.p.snapshot()[0]["status"],"WAIT_FRESH_BOOK")

    def test_live_mode_uses_original_broker_only_no_preplan(self):
        self.wallet._is_live=lambda market:True
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("stock"))
        self.assertEqual(self.depth.reserved,[])

    def test_previous_closed_rsi_under_30_blocks_entry(self):
        self.co._closed_series=lambda *a:[{"close":10000.-i*10,"end":NOW}
                                          for i in range(30)]
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("stock"))
        self.assertEqual(self.p.snapshot()[0]["status"],
                         "PRIOR_CLOSED_RSI_ALREADY_BELOW_30")

    def test_only_original_12_to_7_seconds_stock_submission_window(self):
        self.p.evaluate(NOW-dt.timedelta(seconds=5))
        self.assertEqual(self.depth.reserved,[])
        self.assertFalse(self.wallet.open_positions("stock"))
        self.p.evaluate(NOW+dt.timedelta(seconds=5))
        self.assertEqual(self.depth.reserved,[])
        self.assertFalse(self.wallet.open_positions("stock"))

    def test_krx_tick_ladder_is_by_symbol_price_not_fixed_ten_won(self):
        self.assertEqual([str(p) for p in _limits(3000,CODE,market="stock")],
                         ["3000","3005","3010"])
        self.assertEqual([str(p) for p in _limits(30000,CODE,market="stock")],
                         ["30000","30050","30100"])
        self.assertEqual([str(p) for p in _limits(100000,CODE,market="stock")],
                         ["100000","100100","100200"])
        qtys=_qty_by_level(500000,100,_limits(30000,CODE,market="stock"),"stock")
        self.assertTrue(all(q==q.to_integral_value() for q in qtys))
        self.assertLessEqual(float(qtys[-1])*30100,500000)

    def test_non_krx_venue_market_map_is_not_valid_entry(self):
        self.runner.market_map[CODE]=("UNKNOWN","TEST")
        self.assertIsNone(self.p._quote_mark(CODE))
        self.p.evaluate(NOW)
        self.assertEqual(self.depth.reserved,[])
        self.assertFalse(self.wallet.open_positions("stock"))


if __name__=="__main__":
    unittest.main()
