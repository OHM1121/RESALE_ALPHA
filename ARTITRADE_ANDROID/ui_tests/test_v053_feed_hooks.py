"""v053 public mark capture: no additional broker-orderbook subscriptions."""
import datetime as dt
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import post_exit_runtime_v053 as hooks


class SourcePriceMemory(dict):
    def __init__(self,values,callback):
        super().__init__(values)
        self._callback=callback
    def __setitem__(self,code,price):
        super().__setitem__(code,price)
        self._callback(code,float(price))


class PublicFeedTrackingV053(unittest.TestCase):
    def test_public_price_memory_records_unheld_research_coin_without_book_subscriptions(self):
        original_calls=[]
        holder=types.SimpleNamespace()
        class StockRunner:
            def __init__(self):
                self.market_map={"005930":("KOSPI","삼성전자")}
            def _new_tick(self,tick):
                original_calls.append(("stock_tick",tick[0]))
            def _pinned_stocks(self):
                return {"005930":("KOSPI","삼성전자")}
        class CoinRunner:
            def __init__(self):
                self._lock=threading.RLock()
                self.tracked=["KRW-XRP"]
                self.held=set()
                self.latest_prices={}
            def _both_watched_codes(self):
                return sorted(self.held)
            def mt_set(self,values):
                self.__dict__["_market_trend_prices"]=SourcePriceMemory(
                    dict(values or {}),lambda code,price:
                        original_calls.append(("trend_observer",code,price)))
            def mt_get(self):
                return self.__dict__["_market_trend_prices"]
            latest_prices=property(mt_get,mt_set)
        class AppRuntime:
            def __init__(self,root):
                self.root=root
                self.stock_runner=StockRunner()
                self.coin_runner=CoinRunner()
                self.errors=[]
                self.saved_order_count=0
            def start(self):
                return '{"ok":true}'
            def stop(self):
                return '{"ok":true}'
            def hub_records(self):
                return json.dumps({"stock":{},"coin":{}})
            def _err(self,*args):
                self.errors.append(args)
        stock=types.ModuleType("d_live")
        stock.LivePaperRunner=StockRunner
        coin=types.ModuleType("d_upbit")
        coin.UpbitPaperRunner=CoinRunner
        core=types.SimpleNamespace(Runtime=AppRuntime,_RUNTIME=None)
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.dict(sys.modules,{"d_live":stock,"d_upbit":coin}):
                hooks.install(core)
                rt=AppRuntime(root)
                core._RUNTIME=rt
                rt.start()
                tr=rt.post_sale_tracker_v053
                captured=[]
                tr.quote=lambda *a,**kw:captured.append(a) or True
                case=tr.register("coin","general","5m","KRW-BTC",
                     "PAPER",100,dt.datetime.now(dt.timezone.utc).isoformat(),
                     "source:virtual:10",exit_evidence="EXPECTED")
                tr.scan_sources()
                rt.coin_runner.latest_prices["KRW-BTC"]="125"
                self.assertEqual(len(captured),1)
                self.assertEqual(captured[0][:3],("coin","KRW-BTC",125.0))
                self.assertEqual(captured[0][4],"UPBIT_PUBLIC_RECEIPT")
                self.assertIn(("trend_observer","KRW-BTC",125.0),original_calls)
                self.assertEqual(rt.coin_runner._both_watched_codes(),[])
                self.assertIn("KRW-BTC",rt.coin_runner.tracked)
                stamp=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()
                rt.stock_runner._new_tick(("005930","KOSPI","삼성전자",
                    "20261115100000",51500,100,stamp,"KRX"))
                self.assertIn(("stock_tick","005930"),original_calls)
                rt.stop()


if __name__=="__main__":
    unittest.main()
