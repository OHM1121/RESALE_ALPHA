"""0.0.2 NEW LEGACY: fresh-ledger entry and Top50 executable regressions.

No real orders/API calls. One fake wallet records attempted genuine proof; every
test uses synthetic quotes and temporary SQLite only.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import market_trend_v043 as mt
import market_trend_runtime_v043 as native
import market_trend_1118_runtime as nl
import market_research_v033 as score


class _Wallet:
    def __init__(self):
        self.buys = []

    def stop_reentry_blocked(self, *args):
        return False

    def owned(self, *args):
        return False

    def buy(self, market, frame, code, name, ask, ask_size, at, barid, rsi):
        self.buys.append((market,frame,code,ask,ask_size,barid,rsi))
        return SimpleNamespace(execution_mode="PAPER", entry=ask,
                               qty=1.0, frame=frame), "BOUGHT"


class _Journal:
    def __init__(self):
        self.events = []
    def stop_reentry_blocked(self, *args):
        return False
    def event(self,*args,**kwargs):
        self.events.append((args,kwargs))


class NewLegacyV002RestoredBuy(unittest.TestCase):
    def test_independent_trend_buy_not_blocked_by_original_at_ma_mode(self):
        with tempfile.TemporaryDirectory() as root:
            now = dt.datetime(2026,11,18,10,5,0,tzinfo=mt.KST)
            end = now.replace(minute=0,second=0,microsecond=0)
            bars = [{"close":120.0-0.05*i,
                     "end":end-dt.timedelta(minutes=(20-i)*30),
                     "source":"UPBIT_CLOSED"} for i in range(21)]
            co=SimpleNamespace()
            co.rt=SimpleNamespace(root=root,coin_runner=SimpleNamespace(preload_gate=None))
            co.lock=threading.RLock()
            co.stop_event=threading.Event()
            co._quotes={"coin":{"KRW-TEST":(120.0,time.monotonic())},"stock":{}}
            co._candidates={"coin":{"KRW-TEST":("CRYPTO","TEST")},"stock":{}}
            co._history={("coin","KRW-TEST","30m"):bars}
            co._decision_clock=lambda:now
            co.mode=lambda market:"ORIGINAL"  # native MT54 independent mode!
            co._positions={}
            co.journal=_Journal()
            co.standalone_wallet=_Wallet()
            co._quote=lambda market,code:{"trade":co._quotes[market][code][0],
                                         "bid":117.0,"ask":119.0,
                                         "ask_size":1000.0,"bid_size":1000.0}
            co._last_fetch_error={}
            with patch.object(nl,"_ready_market",return_value=True), \
                 patch.object(mt,"rsi20",return_value=25.0):
                # First genuine observed price arms the 24h WATCH; it does
                # not authorize a BUY without a second middle-line touch.
                nl._entry_window(co,"coin",now)
                self.assertEqual(len(co.standalone_wallet.buys),0)
                self.assertEqual(len(co._nl1118_watches),1)
                co._quotes["coin"]["KRW-TEST"]=(118.0,time.monotonic()+0.001)
                nl._entry_window(co,"coin",now)
            detail={
                "watch":co._nl1118_watches,
                "quote_stamps":co._nl1118_quote_stamps,
                "previous":co._nl1118_previous_trade,
                "last_error":co._last_fetch_error,
                "bollinger":nl.bollinger(
                    [r["close"] for r in bars][-20:]+[118.0]),
                "cross":nl.actual_middle_touch(120.,118.,
                    nl.bollinger([r["close"] for r in bars][-20:]+[118.0])["middle"]),
                "history_valid":len(nl._confirmed_recent(bars,now,"coin","30m")),
            }
            self.assertEqual(len(co.standalone_wallet.buys),1,
                             "native ORIGINAL mode must NOT veto RSI+BB; "+repr(detail))
            market,frame,code,price,qty,barid,rsi=co.standalone_wallet.buys[0]
            self.assertEqual((market,frame,code),("coin","30m","KRW-TEST"))
            self.assertEqual(price,119.0)
            self.assertEqual(rsi,25.0)
            self.assertEqual(len(co.journal.events),1)
            self.assertEqual(co._nl1118_watches[("coin","KRW-TEST","30m")]["status"],"BOUGHT")

    def test_top50_never_intersects_old_top35_native_markets(self):
        coins=[{"market":f"KRW-{i:03}","acc_trade_price_24h":float(200-i)}
               for i in range(80)]
        runner=SimpleNamespace(client=SimpleNamespace(tickers=lambda:coins),
                               markets={f"KRW-{i:03}":1 for i in range(35)})
        co=native.Coordinator.__new__(native.Coordinator)
        co.rt=SimpleNamespace(coin_runner=runner,stock_runner=None)
        co.lock=threading.RLock()
        co._last_rank={"stock":0.0,"coin":0.0}
        co._candidates={"stock":{},"coin":{}}
        co._last_fetch_error={}
        co._record_entry_block=lambda *a,**k:None
        co._refresh_rank("coin")
        self.assertEqual(len(co._candidates["coin"]),50)
        self.assertIn("KRW-049",co._candidates["coin"])
        self.assertNotIn("KRW-050",co._candidates["coin"])
        self.assertEqual(co._last_fetch_error["coin"],"")

    def test_partial_universe_is_visible_but_not_claimed_top50(self):
        # Do not invent 15 missing coins or rebrand TOP35 as TOP50.
        coins=[{"market":f"KRW-{i:03}","acc_trade_price_24h":float(200-i)}
               for i in range(35)]
        co=native.Coordinator.__new__(native.Coordinator)
        co.rt=SimpleNamespace(coin_runner=SimpleNamespace(
            client=SimpleNamespace(tickers=lambda:coins),markets={}),
            stock_runner=None)
        co.lock=threading.RLock()
        co._last_rank={"stock":0.0,"coin":0.0}
        co._candidates={"stock":{},"coin":{}}
        co._last_fetch_error={}
        co._record_entry_block=lambda *a,**k:None
        co._refresh_rank("coin")
        self.assertEqual(len(co._candidates["coin"]),35)
        self.assertIn("TOP50_PARTIAL_VERIFIED_35_OF_50",co._last_fetch_error["coin"])

    def test_worker_recovers_after_empty_new_score_db_and_failure(self):
        class Event:
            down=False
            def is_set(self):return self.down
        class Thread:
            alive=True
            def is_alive(self):return self.alive
        class Worker:
            def __init__(self,root):
                self._stop=Event()
                self._thread=Thread()
        with tempfile.TemporaryDirectory() as path:
            key=str(Path(path).resolve())
            with score._WORKERS_LOCK:
                old=score._WORKERS.pop(key,None)
            try:
                with patch.object(score,"_MarketWorker",Worker):
                    a=score.ensure_worker(path)
                    self.assertIs(a,score.ensure_worker(path))
                    a._stop.down=True
                    b=score.ensure_worker(path)
                    self.assertIsNot(a,b)
                    b._thread.alive=False
                    c=score.ensure_worker(path)
                    self.assertIsNot(c,b)
            finally:
                with score._WORKERS_LOCK:
                    score._WORKERS.pop(key,None)
                    if old is not None:score._WORKERS[key]=old


if __name__=="__main__":
    unittest.main()
