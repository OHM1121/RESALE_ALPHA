"""Real isolated original PAPER SQLite tests for all-stock manual exits.

A quote fixture is explicitly marked test-only; all SELL ledger writes are
performed by the original native engines in temporary SQLite databases.
No phone database, app credential, broker order, or fake exchange print.
"""
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import stock_manual_v086 as manual
from d_storage import DStore
from d_engine import PaperEngine
from stock_ma_engine import StockMAService
from at_close_store import AtCloseStore
from at_close_engine import PaperEngine as CloseEngine
from market_trend_wallet_v054 import TrendWalletV054

KST=dt.timezone(dt.timedelta(hours=9))
CODE='005930'

def make_req(owner,id,qty=100,pct=100):
    return dict(market='stock',strategy=owner,code=CODE,
                position_id=id,expected_qty=str(qty),percent=pct,
                request_id=str(uuid4()),user_confirmed=True)

def genuine_bid(qty=1000,price=102):
    now=dt.datetime.now(KST)
    return {'bid':price,'bid_qty':qty,'at':now,'exchange_at':now,
        'source':'KIWOOM_REAL_0D_KRX_PAPER',
        'depth':manual.VerifiedDepth(CODE,price,qty,now)}

class VerifiedBidTests(unittest.TestCase):
    def test_exact_official_ka10004_best_bid_freshness_and_symbol(self):
        now=dt.datetime.now(KST)
        payload={'stk_cd':CODE,'return_code':0,
            'bid_req_base_tm':now.strftime('%H%M%S'),
            'buy_fpr_bid':'-6960','buy_fpr_req':'125',
            'sel_fpr_bid':'6970','sel_fpr_req':'240'}
        class REST:
            def __init__(self,key,secret):pass
            def post(self,tr,path,data):
                self.assert_eq=(tr, path, data)
                return dict(payload)
        runner=SimpleNamespace(api=SimpleNamespace(key='test',secret='test'))
        with patch.object(manual,'_krx_session',return_value=True), \
             patch('d_live.ReadOnlyMarketAPI',REST), \
             patch('at_close_bridge.wait_market_rest_slot',return_value=None):
            result=manual._rest_bid(runner,CODE,now)
        self.assertEqual((result['bid'],result['bid_qty']),(6960,125))
        self.assertEqual(result['source'],'KIWOOM_REST_KA10004_KRX_PAPER')
        payload['bid_req_base_tm']='073003'
        with patch.object(manual,'_krx_session',return_value=True), \
             patch('d_live.ReadOnlyMarketAPI',REST), \
             patch('at_close_bridge.wait_market_rest_slot',return_value=None):
            with self.assertRaisesRegex(ValueError,'KA10004_EXCHANGE_BOOK_STALE'):
                manual._rest_bid(runner,CODE,now)
        payload['bid_req_base_tm']=now.strftime('%H%M%S')
        payload['stk_cd']='000660'
        with patch.object(manual,'_krx_session',return_value=True), \
             patch('d_live.ReadOnlyMarketAPI',REST), \
             patch('at_close_bridge.wait_market_rest_slot',return_value=None):
            with self.assertRaisesRegex(ValueError,'KA10004_SYMBOL_MISMATCH'):
                manual._rest_bid(runner,CODE,now)

    def test_stale_book_and_disconnected_socket_do_not_sell(self):
        now=dt.datetime.now(KST)
        old=now-dt.timedelta(seconds=40)
        raw=SimpleNamespace(_lock=threading.RLock(),
            _items={(CODE,'KRX'):(103,102,100,old)},
            _bid_depth={(CODE,'KRX'):(102,100,old)},_bid_reserved={})
        runner=SimpleNamespace(engine=SimpleNamespace(book=raw),
                               _confirmed_ws_session=True)
        self.assertIsNone(manual._live_ws_bid(runner,CODE,now))
        raw._items[(CODE,'KRX')]=(103,102,100,now)
        raw._bid_depth[(CODE,'KRX')]=(102,100,now)
        runner._confirmed_ws_session=False
        self.assertIsNone(manual._live_ws_bid(runner,CODE,now))
        runner._confirmed_ws_session=True
        self.assertEqual(manual._live_ws_bid(runner,CODE,now)['bid'],102)

    def test_unsupported_closed_market_never_calls_rest(self):
        with patch.object(manual,'_krx_session',return_value=False):
            with self.assertRaisesRegex(ValueError,'MARKET_CLOSED'):
                manual._rest_bid(SimpleNamespace(api=None),CODE,dt.datetime.now(KST))

class NativeManualWallets(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.book=genuine_bid()
        self.rt=SimpleNamespace(root=self.root,live_enabled=lambda market:False)
        self.runner=SimpleNamespace()
        self.rt.stock_runner=self.runner
        self.bidpatch=patch.object(manual,'verified_stock_bid',return_value=self.book)
        self.bidpatch.start();self.addCleanup(self.bidpatch.stop)

    def test_at_manual_no_0b_and_uuid_idempotency_and_realized(self):
        store=DStore(self.root/'stock'/'base')
        engine=PaperEngine(store)
        self.runner.engine=engine
        with store.connect() as c:
            c.execute("""INSERT INTO positions(tf,code,market,name,ref_ts,
                ref_low,ref_high,ref_bodytop,entry_ts,entry_price,entry_qty,
                qty,hold_days,entry_fee,last_price,peak,exit_policy)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('5m',CODE,'KOSPI','fixture','20261117090000',90,120,110,
                 '20261117090100',100,100,100,2,10,100,100,'3LEG31'))
            id=c.execute('SELECT id FROM positions ORDER BY id DESC LIMIT 1').fetchone()[0]
        req=make_req('at',id)
        receipt=manual.manual_stock_at(self.rt,req)
        self.assertEqual(receipt['qty'],100)
        self.assertEqual(receipt['remaining_qty'],0)
        self.assertEqual(manual.manual_stock_at(self.rt,req)['already_executed'],True)
        with store.connect() as c:
            fills=c.execute("SELECT qty,price FROM executions WHERE side='SELL' AND position_id=?",(id,)).fetchall()
            realized=c.execute("SELECT realized_pnl FROM accounts WHERE tf='5m'").fetchone()[0]
        self.assertEqual([(x[0],x[1]) for x in fills],[(100,102)])
        self.assertGreater(realized,0)

    def seed_ma(self,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as c:
            c.executescript("""CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER);
                INSERT INTO accounts VALUES('5m',100000,0);
                CREATE TABLE positions(id INTEGER PRIMARY KEY,candidate_id INTEGER,tf TEXT,
                   code TEXT,name TEXT,entry INTEGER,entry_at TEXT,qty INTEGER,qty0 INTEGER,
                   first_qty INTEGER,first_sold INTEGER,second_sold INTEGER,
                   first_price INTEGER,status TEXT,last_price INTEGER);
                CREATE TABLE executions(id INTEGER PRIMARY KEY,tf TEXT,position_id INTEGER,
                   code TEXT,name TEXT,ts TEXT,side TEXT,qty INTEGER,price INTEGER,
                   fees INTEGER,tax INTEGER,net_profit INTEGER,reason TEXT);
            """)
            c.execute("""INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (11,15,'5m',CODE,'fixture',100,'2026-11-17T09:00:00+09:00',
                 100,100,10,0,0,0,'OPEN',101))
    def test_ma_original_epoch_and_current_ma_ledger_no_second_sale(self):
        new=self.root/'stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3'
        self.seed_ma(new)
        self.runner.ma=SimpleNamespace(db=new,lock=threading.RLock())
        self.runner.ma._sell=StockMAService._sell.__get__(self.runner.ma)
        req=make_req('ma',11,pct=50)
        result=manual.manual_stock_ma(self.rt,req)
        self.assertEqual(result['qty'],50)
        self.assertTrue(manual.manual_stock_ma(self.rt,req)['already_executed'])
        with sqlite3.connect(new) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='SELL'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT qty FROM positions WHERE id=11").fetchone()[0],50)

    def test_fresh_epoch_does_not_touch_historical_ma_v062(self):
        # A full first-launch reset has deleted old MA epochs. Even if an
        # obsolete file is manually injected, the current-only SELL owner
        # must never treat those former holdings as new PAPER positions.
        new=self.root/'stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3'
        old=self.root/'stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3'
        self.seed_ma(old)
        self.runner.ma=SimpleNamespace(db=new,lock=threading.RLock())
        self.runner.ma._sell=StockMAService._sell.__get__(self.runner.ma)
        req=make_req('ma',11)
        with self.assertRaisesRegex(ValueError,'STOCK_MA_POSITION_NOT_OPEN'):
            manual.manual_stock_ma(self.rt,req)
        with sqlite3.connect(old) as c:
            self.assertEqual(c.execute("SELECT status FROM positions WHERE id=11").fetchone()[0],'OPEN')
        self.assertFalse(new.exists(),'historical file cannot create a current owner')

    def test_close_wallet_native_realized_and_uuid_proof(self):
        store=AtCloseStore(self.root/'stock'/'cw')
        eng=CloseEngine(store)
        close=SimpleNamespace(store=store,engine=eng)
        with store.connect() as c:
            c.execute("""INSERT INTO positions(strategy,code,name,status,
                entry_ts,entry_date,deadline_date,entry_price,entry_qty,qty,
                entry_fee,peak_price,trough_price,last_price)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('CLOSE',CODE,'fixture','OPEN','2026-11-17T09:01:00+09:00',
                 '2026-11-17','2026-11-19',100,100,100,10,100,100,100))
            id=c.execute('SELECT MAX(id) FROM positions').fetchone()[0]
        req=make_req('close',id)
        with patch('at_close_bridge.close_runner',return_value=close):
            got=manual.manual_stock_close(self.rt,req)
            already=manual.manual_stock_close(self.rt,req)
        self.assertEqual(got['qty'],100)
        self.assertTrue(already['already_executed'])
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM executions WHERE side='SELL'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT status FROM positions WHERE id=?",(id,)).fetchone()[0],'CLOSED')

    def test_gmo_original_intent_fill_and_uuid_idempotency(self):
        from projectgmo.engine import Engine,Position
        engine=Engine()
        engine.positions[CODE]=Position(CODE,'fixture',100,100,100.0,'2026-11-17','SHORT',10000.0,stage='PROTECTED')
        self.runner.gmo=SimpleNamespace(_lock=threading.RLock(),engine=engine,_persist=lambda force:None)
        req=make_req('gmo',-1,qty=100)
        result=manual.manual_stock_gmo(self.rt,req)
        self.assertEqual((result['qty'],result['status']),(100,'FILLED'))
        self.assertEqual((len(engine.fills),len(engine.positions)),(1,0))
        cash=engine.cash
        self.assertTrue(manual.manual_stock_gmo(self.rt,req)['already_executed'])
        self.assertEqual((len(engine.fills),engine.cash),(1,cash))

    def test_gmo_partial_book_consumes_only_bid_capacity_once(self):
        from projectgmo.engine import Engine,Position
        engine=Engine()
        engine.positions[CODE]=Position(CODE,'fixture',100,100,100.0,'2026-11-17','SHORT',10000.0,stage='PROTECTED')
        self.runner.gmo=SimpleNamespace(_lock=threading.RLock(),engine=engine,_persist=lambda force:None)
        req=make_req('gmo',-1,qty=100)
        with patch.object(manual,'verified_stock_bid',return_value=genuine_bid(qty=25)):
            result=manual.manual_stock_gmo(self.rt,req)
        self.assertEqual((result['qty'],result['status']),(25,'PARTIAL_PENDING'))
        self.assertEqual(engine.positions[CODE].qty,75)
        with patch.object(manual,'verified_stock_bid',side_effect=ValueError('SHOULD_NOT_REQUOTE')):
            repeated=manual.manual_stock_gmo(self.rt,req)
        self.assertTrue(repeated['already_executed'])
        self.assertEqual(len(engine.fills),1)

    def test_stock_trend_original_sqlite_and_repeated_request(self):
        wallet=TrendWalletV054(self.rt)
        self.runner.engine=SimpleNamespace(book=None)
        self.runner.market_map={CODE:('KOSPI','fixture')}
        at=dt.datetime.now(KST)
        pos,why=wallet.buy('stock','30m',CODE,'fixture',100,1000,at,at.isoformat(),35)
        self.assertIsNotNone(pos,why)
        self.rt.market_trend=SimpleNamespace(lock=threading.RLock(),
            _positions={('stock','trend','30m',CODE):pos},standalone_wallet=wallet)
        request=make_req('trend',pos.position_id,qty=pos.qty,pct=50)
        got=manual.manual_stock_trend(self.rt,request)
        self.assertGreater(got['qty'],0)
        self.assertTrue(manual.manual_stock_trend(self.rt,request)['already_executed'])
        with wallet._connect('stock') as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='SELL'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
