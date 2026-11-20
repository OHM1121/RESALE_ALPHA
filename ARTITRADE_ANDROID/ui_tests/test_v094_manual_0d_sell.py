"""v094: user-confirmed MA manual forced PAPER SELL uses actual fresh 0D BID.

There may be no 0B last-trade print during a quiet, deeply liquid market.
It must never invent a 0B trade nor book size. A confirmed user manual
sell can consume displayed, independently reserved KRX top-BID depth;
original native StockMAService._sell remains the ledger authority.
"""
import datetime as dt
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import android_manual_paper_sell_v085 as manual
from stock_ma_engine import StockMAService
KST=dt.timezone(dt.timedelta(hours=9))


class StockBook:
    def __init__(self,ts,bid_qty=15):
        self._lock=threading.RLock()
        self._items={('005930','KRX'):(10010,10000,150,ts)}
        self._bid_depth={('005930','KRX'):(10000,bid_qty,ts)}
        self._bid_reserved={}


class Service:
    lock=threading.RLock()
    _sell=StockMAService._sell
    def __init__(self,path):self.db=path


class Runtime:
    def __init__(self,store,book):
        self.stock_runner=type("Runner",(),{})()
        self.stock_runner.engine=type("Engine",(),{})()
        self.stock_runner.engine.book=book
        self.stock_runner.ma=Service(store)
        self.stock_runner._confirmed_ws_session=True
        self.root=Path(store).parent
        # Deliberately NO current trade print: the real 0D is independent.
        self.stock_runner.latest_krx_trade=lambda code:None


def seed(db):
    with sqlite3.connect(db) as c:
        c.executescript("""
            CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER);
            INSERT INTO accounts VALUES('5m',0,0);
            CREATE TABLE positions(
                id INTEGER PRIMARY KEY,tf TEXT,code TEXT,name TEXT,entry INTEGER,
                entry_at TEXT,qty INTEGER,qty0 INTEGER,status TEXT,last_price INTEGER);
            INSERT INTO positions VALUES(41,'5m','005930','TEST',9000,'2026-11-17T09:00:00+09:00',10,10,'OPEN',10000);
            CREATE TABLE executions(id INTEGER PRIMARY KEY,tf TEXT,position_id INTEGER,
                code TEXT,name TEXT,ts TEXT,side TEXT,qty INTEGER,price INTEGER,
                fees INTEGER,tax INTEGER,net_profit INTEGER,reason TEXT);
        """)


class ManualForcedSell(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'ma.sqlite'
        seed(self.db)
        self.now=dt.datetime.now(KST)
        self.book=StockBook(self.now)
        self.rt=Runtime(self.db,self.book)
        # Deterministic genuine KRX-session fixture; real runtime still
        # enforces official exchange date and clock eligibility.
        self.session=patch('stock_manual_v086._krx_session',return_value=True)
        self.session.start()
        self.req={'code':'005930','position_id':41,'expected_qty':'10',
                  'percent':100,'request_id':'9c3dca87-2b1c-4b23-8d78-4ed7e5fa0e14'}
    def tearDown(self):
        self.session.stop()
        self.tmp.cleanup()
    def rows(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT qty,status FROM positions WHERE id=41").fetchone(),c.execute(
                "SELECT side,qty,price,reason FROM executions WHERE position_id=41").fetchall()
    def test_fresh_0d_bid_sufficient_no_recent_trade_sells_all_10_with_real_book(self):
        self.assertIsNone(self.rt.stock_runner.latest_krx_trade('005930'))
        result=manual._stock_ma(self.rt,self.req)
        self.assertEqual(result['qty'],10)
        self.assertEqual(result['price'],10000)
        position,execs=self.rows()
        self.assertEqual(position,(0,'CLOSED'))
        self.assertEqual([(x[0],x[1],x[2]) for x in execs],[('SELL',10,10000)])

    def test_same_uuid_replay_never_sells_again_even_when_closed(self):
        manual._stock_ma(self.rt,self.req)
        again=manual._stock_ma(self.rt,self.req)
        self.assertTrue(again['already_executed'])
        self.assertEqual(len(self.rows()[1]),1)

    def test_stale_0d_never_forges_a_sell(self):
        self.book=StockBook(self.now-dt.timedelta(seconds=5))
        self.rt.stock_runner.engine.book=self.book
        with self.assertRaises(ValueError):
            manual._stock_ma(self.rt,self.req)
        self.assertEqual(self.rows()[1],[])

    def test_partial_depth_is_exact_never_more_than_displayed(self):
        self.book=StockBook(self.now,bid_qty=5)
        self.rt.stock_runner.engine.book=self.book
        result=manual._stock_ma(self.rt,self.req)
        self.assertEqual(result['qty'],5)
        self.assertEqual(self.rows()[0],(5,'OPEN'))
        self.assertEqual(self.rows()[1][0][1],5)

    def test_wrong_symbol_cannot_sell_foreign_position(self):
        bad=dict(self.req,code='000660')
        with self.assertRaises(ValueError):
            manual._stock_ma(self.rt,bad)
        self.assertEqual(self.rows()[1],[])


if __name__=="__main__":unittest.main()
