"""No APK / no broker / no SQLite mutation. Strict NEW LEGACY >0 BUY gate."""
from pathlib import Path
import sys
import tempfile
import types
import unittest

import at_market_score_gate_v107 as gate

class MarketGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/"coin"/"base").mkdir(parents=True)
        (self.root/"coin"/"ma").mkdir(parents=True)
        self.score={"stock": -1.0,"coin": -0.1}
        self.snapshots={"stock":{"score":-1.0,"stale":False},
                        "coin":{"score":-0.1,"stale":False}}
        class Worker:
            def __init__(item,values):item.values=values
            def snapshot(item):return item.values
        class L:
            def __enter__(item):return item
            def __exit__(item,*args):return False
        self.worker=Worker(self.snapshots)
        markets=types.ModuleType("market_research_v033")
        markets._WORKERS={str(self.root.resolve()):self.worker}
        markets._WORKERS_LOCK=L()
        self.original_mods={k:sys.modules.get(k) for k in
            ("market_research_v033","d_engine","d_v19_portfolio","d_upbit")}
        sys.modules["market_research_v033"]=markets
        stock=types.ModuleType("d_engine")
        d19=types.ModuleType("d_v19_portfolio")
        coin=types.ModuleType("d_upbit")
        class Base:
            def __init__(item,root):item.store=types.SimpleNamespace(root=root)
            def _buy_if_touch(item,c,tf,minute,at):return "STOCK_LEGACY_BUY"
            def sell(item):return "SELL_NOT_CHANGED"
        class D19(Base):
            def _buy_limit_depth(item,c,row,when,venue,observed_trade_price,observed_trade_qty=0):
                return True,"STOCK_D19_BUY"
        class Coin:
            def __init__(item,path):item.path=path
            def _buy(item,c,order,book,seen,amount_available):return 123.0
            def sell(item):return "COIN_SELL_NOT_CHANGED"
        stock.PaperEngine=Base
        d19.D19PaperEngine=D19
        coin.CoinLedger=Coin
        sys.modules["d_engine"]=stock
        sys.modules["d_v19_portfolio"]=d19
        sys.modules["d_upbit"]=coin
        gate._INSTALLED=False
        gate.install()
        self.old=Base(self.root)
        self.d19=D19(self.root)
        self.base=Coin(self.root/"coin"/"base"/"upbit_paper.sqlite3")
        self.ma=Coin(self.root/"coin"/"ma"/"upbit_paper.sqlite3")

    def tearDown(self):
        gate._INSTALLED=False
        for key,module in self.original_mods.items():
            if module is None:sys.modules.pop(key,None)
            else:sys.modules[key]=module
        self.tmp.cleanup()

    def test_negative_blocks_only_new_reference_buys(self):
        self.assertIsNone(self.old._buy_if_touch(None,"5m",{},None))
        self.assertEqual(self.d19._buy_limit_depth(None,{},None,"KRX",1)[0],False)
        self.assertEqual(self.base._buy(None,{},None,None,None),0)
        self.assertEqual(self.ma._buy(None,{},None,None,None),123.0)

    def test_sell_not_changed(self):
        self.assertEqual(self.old.sell(),"SELL_NOT_CHANGED")
        self.assertEqual(self.ma.sell(),"COIN_SELL_NOT_CHANGED")
        self.assertEqual(self.base.sell(),"COIN_SELL_NOT_CHANGED")

    def test_zero_stock_score_blocks_but_positive_coin_allows_original_buy(self):
        # NEW LEGACY(11.18) authorizes an actual reference BUY ONLY for signed
        # scores > 0. The retired negative-only contract was unsafe.
        self.snapshots["stock"]["score"]=0.0
        self.snapshots["coin"]["score"]=1.0
        self.assertIsNone(self.old._buy_if_touch(None,"5m",{},None))
        self.assertFalse(self.d19._buy_limit_depth(None,{},None,"KRX",1)[0])
        self.assertEqual(self.base._buy(None,{},None,None,None),123.0)
        self.snapshots["stock"]["score"]=0.1
        self.assertEqual(self.old._buy_if_touch(None,"5m",{},None),"STOCK_LEGACY_BUY")
        self.assertEqual(self.d19._buy_limit_depth(None,{},None,"KRX",1),
                         (True,"STOCK_D19_BUY"))

    def test_stale_or_missing_always_blocks_actual_reference_buy(self):
        self.snapshots["stock"].update(score=-10,stale=True)
        self.snapshots["coin"]["score"]=None
        stock=gate.score_evidence(self.root,"stock")
        coin=gate.score_evidence(self.root,"coin")
        self.assertIsNone(stock["score"])
        self.assertFalse(stock["positive"])
        self.assertIsNone(coin["score"])
        self.assertFalse(coin["positive"])
        self.assertFalse(self.d19._buy_limit_depth(None,{},None,"KRX",1)[0])
        self.assertIsNone(self.old._buy_if_touch(None,"5m",{},None))
        self.assertEqual(self.base._buy(None,{},None,None,None),0)
        # Other strategies' native policies remain unchanged.
        self.assertEqual(self.ma._buy(None,{},None,None,None),123.0)


if __name__=="__main__":
    unittest.main()
