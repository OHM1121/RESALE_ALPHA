"""MarketTrend v099 TOP50 + prepared PAPER RSI35 3-tier BUY regressions.

No orders reach a broker; a real temporary MT54 SQLite wallet is used.
"""
from __future__ import annotations
import datetime as dt
import tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace

from market_trend_preplan_v099 import PreparedCoinTrend,_limits,_qty_by_level
from market_trend_v043 import KST,SignalLatch
from market_trend_wallet_v054 import TrendWalletV054
from market_trend_runtime_v043 import Coordinator

COIN="KRW-CASHCAT"
CLOSE=dt.datetime(2026,11,17,16,50,tzinfo=KST)
NOW=CLOSE-dt.timedelta(minutes=4,seconds=45)

class Watch:
    def __init__(self,armed=True):
        self.rows=[dict(code=COIN,frame="5m",bar_end=CLOSE.isoformat(),
                        rsi20_live=35,armed=armed,status="WATCH_PENDING")]
    def snapshot(self,market):
        return list(self.rows)
    def discard(self,market,code,frame):
        self.rows=[r for r in self.rows if r['code']!=code or r['frame']!=frame]

class Journal:
    def __init__(self):
        self.latch_obj=SignalLatch(armed=True)
        self.saved=[]
        self.events=[]
    def latch(self,*a):return self.latch_obj
    def save_latch(self,*a):self.saved.append(a)
    def stop_reentry_blocked(self,*a):return False
    def event(self,*args,**kwargs):self.events.append((args,kwargs))

class CoinPlannerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.book={"market":COIN,"ask":"60.0","bid":"59.9",
                    "ask_size":"5000","bid_size":"4000",
                    "timestamp":int(time.time()*1000),"sr_tick_size":None}
        runner=SimpleNamespace(_lock=threading.RLock(),last_book={COIN:self.book})
        rt=SimpleNamespace(root=root,coin_runner=runner,live_enabled=lambda m:False)
        self.wallet=TrendWalletV054(rt)
        self.journal=Journal()
        self.watch=Watch()
        self.blocks=[]
        self.co=SimpleNamespace(rt=rt,stop_event=threading.Event(),
            standalone_wallet=self.wallet,watch=self.watch,
            _candidates={"coin":{COIN:("CRYPTO",COIN)}},
            _quotes={"coin":{COIN:(35.,time.monotonic())}},
            _positions={},_ordered=set(),lock=threading.RLock(),
            journal=self.journal,_last_fetch_error={"coin":""},
            _closed_series=lambda *a:[dict(close=100.,end=NOW) for _ in range(30)],
            _quote=lambda *a:{"ask":float(self.book["ask"]),
                               "ask_size":float(self.book["ask_size"]),
                               "trade":35.,"bid":float(self.book["bid"]),
                               "bid_size":float(self.book["bid_size"])},
            _record_entry_block=lambda *a,**kw:self.blocks.append(a))
        self.p=PreparedCoinTrend(self.co)
        self.p.prepare(NOW)
    def tearDown(self):
        self.temp.cleanup()

    def change_book(self,price,size=5000):
        self.book["ask"]=str(price)
        self.book["bid"]=str(float(price)-0.1)
        self.book["ask_size"]=str(size)
        self.book["timestamp"]=int(time.time()*1000)+1
        self.co._quotes["coin"][COIN]=(35.,time.monotonic())

    def test_preflight_three_order_levels_and_maximum_cost(self):
        plans=self.p.snapshot()
        self.assertEqual(len(plans),1)
        self.assertEqual(plans[0]["scenarios"],["60.0","60.1","60.2"])
        self.assertEqual(plans[0]["budget_won"],500000)
        self.assertTrue(all(float(v)>0 for v in plans[0]["precomputed_qty"]))
        self.assertFalse(self.wallet.open_positions("coin"))

    def test_first_30_cross_fills_via_original_sqlite_and_duplicate_block(self):
        self.p.evaluate(NOW)
        held=self.wallet.open_positions("coin")
        self.assertEqual(len(held),1)
        self.assertEqual(held[0].symbol,COIN)
        self.assertEqual(len(self.journal.saved),1)
        self.assertEqual(self.journal.events[0][0][0],
                         "MT54:SIG:coin:"+COIN+":5m:"+CLOSE.isoformat())
        self.p.evaluate(NOW)
        self.assertEqual(len(self.wallet.open_positions("coin")),1)
        self.assertEqual(self.p.snapshot()[0]["scenario"],1)
        self.assertLessEqual(float(held[0].entry)*held[0].qty,500000)

    def test_scenario_three_prepared_and_original_cash_guard(self):
        self.change_book(60.2)
        self.p.evaluate(NOW)
        held=self.wallet.open_positions("coin")
        self.assertEqual(len(held),1)
        self.assertEqual(self.p.snapshot()[0]["scenario"],3)
        self.assertLessEqual(float(held[0].entry)*held[0].qty,500000)
        self.assertAlmostEqual(float(held[0].entry),60.2,places=4)

    def test_above_two_ticks_is_not_a_fill(self):
        self.change_book(60.3)
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("coin"))
        self.assertEqual(self.p.snapshot()[0]["status"],
                         "ABOVE_SCENARIO_3_CEILING")

    def test_missing_real_book_prevents_entering(self):
        self.book["timestamp"]=int((time.time()-9)*1000)
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("coin"))
        self.assertEqual(self.p.snapshot()[0]["status"],"WAIT_FRESH_UPBIT_BOOK")

    def test_previous_closed_rsi_below_30_cannot_buy(self):
        self.co._closed_series=lambda *a:[dict(close=100-i,end=NOW)
                                        for i in range(30)]
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("coin"))
        self.assertEqual(self.p.snapshot()[0]["status"],
                         "PRIOR_CLOSED_RSI_ALREADY_BELOW_30")

    def test_paper_fast_worker_does_not_authorize_live_fok(self):
        self.wallet._is_live=lambda market:True
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("coin"))

    def test_invalid_quote_has_no_trade(self):
        self.co._quotes["coin"]={}
        self.p.evaluate(NOW)
        self.assertFalse(self.wallet.open_positions("coin"))

    def test_strict_top50_universe_source_and_ui(self):
        src=(Path(__file__).resolve().parents[1]/
             "app/src/main/python/market_trend_runtime_v043.py").read_text()
        self.assertIn('len(observed)<50',src)
        self.assertIn('observed[:50]',src)
        ui=(Path(__file__).resolve().parents[1]/
            "app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        self.assertIn("업비트 · KRW TOP50",ui)

    def test_upbit_tick_sizes_limit_exactly_three(self):
        self.assertEqual([str(x) for x in _limits(60,COIN)],
                         ["60","60.1","60.2"])
        self.assertEqual(_limits(60,"KRW-USDT"),())
        self.assertEqual(tuple(str(x) for x in _qty_by_level(10000,500,
                         _limits(60,COIN))),
                         ("166.64166916","166.364671", "166.08858"))

if __name__=="__main__":
    unittest.main()
