"""v0.7.51: frozen GMO PAPER fills remain collectable research even when AT is LIVE."""
import ast
import base64
import importlib
import io
import json
import math
import sqlite3
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).parents[1]
PYSRC=ROOT/"app/src/main/python"
sys.path.insert(0,str(PYSRC))
from android_live_position_guard_v049 import (
    UnsafePaperExit, verified_gmo_live_quantity)


def _load_frozen_core(folder):
    parts=[ROOT/"app/src/main/assets/strategy_v070"/("part_%02d.txt"%i)
           for i in range(22)]
    assert all(f.is_file() for f in parts),"original GMO core assets missing"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(
            "".join(f.read_text(encoding="ascii") for f in parts)))) as frozen:
        for name in frozen.namelist():
            if name=="projectgmo/" or name.startswith("projectgmo/") or name=="d_gmo_dashboard.py":
                frozen.extract(name,folder)
    sys.path.insert(0,str(folder))
    from projectgmo.engine import Engine, Quote
    from projectgmo.selector import DSignal
    from d_gmo_dashboard import aggregate_candidates
    return Engine,Quote,DSignal,aggregate_candidates


def _patched_gmo_for_test(engine,root,live=False):
    source=(PYSRC/"android_runtime_v078.py").read_text(encoding="utf-8")
    method=next(n for n in ast.parse(source).body
                if isinstance(n,ast.FunctionDef) and n.name=="_patched_gmo")
    errors=[]
    rt=types.SimpleNamespace(
        stock_runner=types.SimpleNamespace(gmo=types.SimpleNamespace(engine=engine)),
        root=root,_stock_live=live,
        _err=lambda stage,error:errors.append((stage,str(error))))
    rt.live_enabled=lambda market:rt._stock_live
    def original_broker_overlay(runtime):
        # Same overwrite as the original v071: PAPER path is disabled.
        engine.paper_match=lambda quote:None
        engine.paper_match_krx_print_proxy=lambda *a,**k:None
    scope={
        "_ORIG_PATCH_GMO":original_broker_overlay,
        "_core":types.SimpleNamespace(
            LiveOrderNoFill=type("NoFill",(Exception,),{}),_safe_key=lambda key:key),
        "verified_gmo_live_quantity":verified_gmo_live_quantity,
        "UnsafePaperExit":UnsafePaperExit,
        "math":math
    }
    exec(compile(ast.Module(body=[method],type_ignores=[]),"<gmo-v051>","exec"),scope)
    scope["_patched_gmo"](rt)
    return rt,errors


def _broker_db(root,market_order):
    broker=root/"broker"
    broker.mkdir(parents=True)
    with sqlite3.connect(broker/"live_order_journal.sqlite3") as conn:
        conn.execute("""CREATE TABLE live_orders(
            event_key TEXT PRIMARY KEY,market TEXT,symbol TEXT,
            side TEXT,status TEXT,fill_qty TEXT)""")
        if market_order:
            conn.execute("INSERT INTO live_orders VALUES(?,?,?,?,?,?)",market_order)


class V051GmoPaperResearch(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        Engine,Quote,DSignal,aggregate=_load_frozen_core(self.root/"frozen")
        self.Engine,self.Quote,self.DSignal,self.aggregate=Engine,Quote,DSignal,aggregate
        self.code="005930"
        self.at="2026-11-13T10:03:00+09:00"
        self.engine=Engine()
        self.engine.signals[self.code]=DSignal(
            self.code,"PAPER_TEST","2026-11-12","2026-11-13",
            1100,1000,1100,1100,-10000,5000,5000,[-100]*20,"SHORT","KOSPI")
        self.engine.signal_scores[self.code]=40

    def tearDown(self):
        self.tmp.cleanup()

    def quote(self,price=1000,asksize=100,bidsize=100):
        return self.Quote(self.code,self.at,1,price,price,price-5,
                          asksize,bidsize,"KRX",0.5)

    def test_other_strategy_live_holding_does_not_block_gmo_paper_and_research(self):
        _broker_db(self.root,("SQL-stock_at-live","stock",self.code,"BUY","FILLED","10"))
        _,errors=_patched_gmo_for_test(self.engine,self.root)
        order=self.engine._order(self.code,"BUY","D+1_PAPER",1000,1010,10,self.at)
        self.engine.paper_match(self.quote())
        self.assertEqual(order.state,"FILLED")
        self.assertEqual(len(self.engine.buy_fills),1)
        self.assertEqual(self.engine.positions[self.code].qty,10)
        self.assertFalse(errors)
        self.engine._order(self.code,"SELL","TP_FIRST_PARTIAL",1040,1030,2,self.at)
        self.engine.paper_match(self.quote(price=1100))
        self.assertEqual(len(self.engine.fills),1)
        self.assertEqual(self.engine.positions[self.code].qty,8)
        self.assertGreater(self.engine.realized,0)
        # Research is read-only over the SAME ENGINE buy/sell fills.
        candidate={"code":self.code,"name":"PAPER_TEST",
                  "event_date":"2026-11-12","entry_date":"2026-11-13",
                  "opening_price":1000,"first_tp_pct":0.04,
                  "evidence":"{}","gmo_score":40}
        report=self.aggregate([candidate],[],self.engine.buy_fills,
                              self.engine.fills,settings=self.engine.cfg)
        self.assertEqual(report["bought"]["actual_buy_events"],1)
        self.assertEqual(report["bought"]["realized_profit_won"],
                         round(self.engine.realized))
        saved=self.engine.export_state()
        restored=self.Engine.from_state(saved)
        self.assertEqual(len(restored.buy_fills),1)
        self.assertEqual(len(restored.fills),1)
        self.assertEqual(restored.positions[self.code].qty,8)

    def test_paper_0b_trade_proxy_survives_unrelated_at_broker_holding(self):
        _broker_db(self.root,("SQL-stock_at-live","stock",self.code,"BUY","FILLED","10"))
        _,errors=_patched_gmo_for_test(self.engine,self.root)
        self.engine._order(self.code,"BUY","D+1_PAPER",1000,1010,10,self.at)
        filled=self.engine.paper_match_krx_print_proxy(
            self.quote(asksize=0,bidsize=0),100,
            buy_missing_book=True,sell_missing_book=False)
        self.assertGreater(filled,0)
        self.assertEqual(len(self.engine.buy_fills),1)
        self.assertFalse(errors)

    def test_gmo_live_holding_or_unresolved_order_blocks_cross_mode_paper_fill(self):
        _broker_db(self.root,("GMO-live-buy","stock",self.code,"BUY","FILLED","10"))
        _,errors=_patched_gmo_for_test(self.engine,self.root)
        order=self.engine._order(self.code,"BUY","D+1_PAPER",1000,1010,10,self.at)
        self.engine.paper_match(self.quote())
        self.assertEqual(order.filled,0)
        self.assertEqual(len(self.engine.buy_fills),0)
        self.assertTrue(any(stage=="gmo_paper_broker_conflict" for stage,_ in errors))
        with sqlite3.connect(self.root/"broker/live_order_journal.sqlite3") as conn:
            conn.execute("UPDATE live_orders SET status='SENT' WHERE event_key='GMO-live-buy'")
        self.engine.paper_match(self.quote())
        self.assertEqual(len(self.engine.buy_fills),0)


if __name__=="__main__":
    unittest.main()
