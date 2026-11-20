"""v091 regression: REAL native PAPER SELL transactions, not fake _sell returns.

The only mocks hide unneeded network/history; trading uses unmodified native
stock_ma_engine.StockMAService._sell / d_upbit.CoinLedger._sell and temporary
SQLite wallets. The user device's broker and original ledger are never touched.
"""
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import ma_v067_policy as policy
import ma_v067_stock_runtime as stock
import ma_v067_coin_runtime as coin
from stock_ma_engine import StockMAService
from d_upbit import CoinLedger

KST=dt.timezone(dt.timedelta(hours=9))
START=dt.datetime(2026,11,17,10,0,tzinfo=KST)


class StockQuote:
    def available_sell(self,*args):return 10000
    def reserve_sell(self,*args):return True


class StockOwner:
    _sell=StockMAService._sell


class CoinOwner:
    lock=threading.RLock()
    _sell=CoinLedger._sell
    def __init__(self,db):self.db=db
    def connect(self):
        conn=sqlite3.connect(self.db)
        conn.row_factory=sqlite3.Row
        return conn
    def _write_fill(self,c,key,pid,tf,market,at,side,qty,bid,fee,pnl,reason,cash):
        c.execute('INSERT INTO executions(event_key,position_id,tf,market,at,side,qty,price,reason) VALUES(?,?,?,?,?,?,?,?,?)',
                  (key,pid,tf,market,at,side,str(qty),str(bid),reason))
    def _mark_closed_followup(self,*args):return None


class NativeFillThreeStage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='v091-stage-')
        self.dir=Path(self.tmp.name)
        self.coin_db=self.dir/'coin.sqlite3'
        self.stock_db=self.dir/'stock.sqlite3'
        with sqlite3.connect(self.stock_db) as c:
            c.executescript("""
              CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER);
              INSERT INTO accounts VALUES('5m',0,0);
              CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,name TEXT,
                entry INTEGER,entry_at TEXT,qty INTEGER,qty0 INTEGER,first_sold INTEGER DEFAULT 0,
                second_sold INTEGER DEFAULT 0,status TEXT,last_price INTEGER);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                tf TEXT,code TEXT,name TEXT,ts TEXT,side TEXT,qty INTEGER,price INTEGER,
                fees INTEGER,tax INTEGER,net_profit INTEGER,reason TEXT);
              CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY,
                kind TEXT,frame TEXT,code TEXT,state_json TEXT,created_at TEXT,updated_at TEXT);
            """)
        with sqlite3.connect(self.coin_db) as c:
            c.executescript("""
              CREATE TABLE wallets(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER);
              INSERT INTO wallets VALUES('5m',0,0);
              CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
                entry TEXT,entry_fee TEXT,qty TEXT,qty0 TEXT,first_qty TEXT,
                first_sold_qty TEXT,mark TEXT,status TEXT);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,event_key TEXT UNIQUE,
                position_id INTEGER,tf TEXT,market TEXT,at TEXT,side TEXT,qty TEXT,
                price TEXT,reason TEXT);
              CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY,
                kind TEXT,frame TEXT,code TEXT,state_json TEXT,created_at TEXT,updated_at TEXT);
            """)
    def tearDown(self):self.tmp.cleanup()

    def seed_stock(self,kind,qty=100):
        state=(policy.initial_up_exit_state('5m') if kind=='REVERSAL_UP'
               else policy.initial_bear_exit_state('5m',120))
        with sqlite3.connect(self.stock_db) as c:
            c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                 (11,'5m','005930','KRX_TEST',100,START.isoformat(),qty,qty,0,0,'OPEN',100))
            c.execute('INSERT INTO ma_v067_positions VALUES(?,?,?,?,?,?,?)',
                 (11,kind,'5m','005930',json.dumps(state),START.isoformat(),START.isoformat()))
        return StockOwner()

    def stock_tick(self,owner,bid,index):
        when=START+dt.timedelta(seconds=index)
        with sqlite3.connect(self.stock_db) as c:
            c.row_factory=sqlite3.Row
            p=c.execute('SELECT * FROM positions WHERE id=11').fetchone()
            if p['status']=='OPEN':
                with patch.object(stock,'_latest_0b',return_value=None):
                    stock._exit_policy(owner,c,p,bid,when,StockQuote(),lambda *a:None)

    def stock_rows(self):
        with sqlite3.connect(self.stock_db) as c:
            c.row_factory=sqlite3.Row
            return ([dict(x) for x in c.execute("SELECT * FROM positions")],
                    [dict(x) for x in c.execute("SELECT * FROM executions ORDER BY id")],
                    json.loads(c.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=11").fetchone()[0]))

    def seed_coin(self,kind,qty='100'):
        state=(policy.initial_up_exit_state('5m') if kind=='REVERSAL_UP'
               else policy.initial_bear_exit_state('5m',120))
        with sqlite3.connect(self.coin_db) as c:
            c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (11,'5m','KRW-TEST','100','0',qty,qty,'30' if kind=='REVERSAL_BEAR' else '10','0','100','OPEN'))
            c.execute('INSERT INTO ma_v067_positions VALUES(?,?,?,?,?,?,?)',
                 (11,kind,'5m','KRW-TEST',json.dumps(state),START.isoformat(),START.isoformat()))
        return CoinOwner(self.coin_db)

    def coin_tick(self,owner,bid,index):
        when=START+dt.timedelta(seconds=index)
        book={'market':'KRW-TEST','bid':str(bid),'bid_size':'1000',
              'timestamp':int(when.timestamp()*1000)}
        with patch.object(coin,'_recent_trade',return_value=None),\
             patch.object(coin,'_prior_verified_closes',return_value=[]):
            coin._process_v067_exits(owner,book,when)

    def coin_rows(self):
        with sqlite3.connect(self.coin_db) as c:
            c.row_factory=sqlite3.Row
            return ([dict(x) for x in c.execute("SELECT * FROM positions")],
                    [dict(x) for x in c.execute("SELECT * FROM executions ORDER BY id")],
                    json.loads(c.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=11").fetchone()[0]))

    def test_stock_up_original_paper_buy_position_exits_10_45_45(self):
        owner=self.seed_stock('REVERSAL_UP')
        self.stock_tick(owner,101,0)
        self.assertEqual(self.stock_rows()[1],[])
        self.stock_tick(owner,102,1)
        p,executions,state=self.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[10])
        self.assertEqual(state['first_sold'],'10')
        self.assertEqual(p[0]['first_sold'],1,'native position first_sold must reflect confirmed v067 first fill')
        self.stock_tick(owner,105,2)
        p,executions,state=self.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[10,45])
        self.assertEqual(state['second_sold'],'45')
        self.assertEqual(p[0]['second_sold'],1,'native position second_sold must reflect confirmed v067 second fill')
        self.stock_tick(owner,110,3)
        self.stock_tick(owner,106,4)
        p,executions,state=self.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[10,45,45])
        self.assertEqual([x['reason'] for x in executions],[
             'MA_V065_UP_FIRST_10PCT',
             'MA_V065_UP_SECOND_HALF_REMAINING',
             'MA_V065_UP_PEAK_MINUS_3'])
        self.assertEqual(p[0]['qty'],0)
        self.assertEqual(p[0]['status'],'CLOSED')

    def test_stock_bear_original_paper_exits_30_35_35(self):
        owner=self.seed_stock('REVERSAL_BEAR')
        self.stock_tick(owner,120,1)
        p,executions,state=self.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[30])
        self.assertEqual(p[0]['first_sold'],1)
        self.stock_tick(owner,123,2)
        p,executions,state=self.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[30,35])
        self.assertEqual(p[0]['second_sold'],1)
        self.stock_tick(owner,121,3)
        p,executions,state=self.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[30,35,35])
        self.assertEqual(p[0]['status'],'CLOSED')

    def test_coin_up_original_paper_exits_10_45_45_and_native_first_state(self):
        owner=self.seed_coin('REVERSAL_UP')
        self.coin_tick(owner,101,0)
        self.assertEqual(self.coin_rows()[1],[])
        self.coin_tick(owner,102,1)
        p,execs,state=self.coin_rows()
        self.assertEqual([Decimal(x['qty']) for x in execs],[Decimal('10')])
        self.assertEqual(Decimal(state['first_sold']),Decimal('10'))
        self.assertEqual(Decimal(p[0]['first_sold_qty']),Decimal('10'),
            'native positions.first_sold_qty must match verified v067 executions')
        self.coin_tick(owner,105,2)
        p,execs,state=self.coin_rows()
        self.assertEqual([Decimal(x['qty']) for x in execs],[Decimal('10'),Decimal('45')])
        self.assertEqual(Decimal(state['second_sold']),Decimal('45'))
        self.coin_tick(owner,110,3)
        self.coin_tick(owner,106,4)
        p,execs,state=self.coin_rows()
        self.assertEqual([Decimal(x['qty']) for x in execs],[Decimal('10'),Decimal('45'),Decimal('45')])
        self.assertEqual([x['reason'] for x in execs],[
            'MA_V065_UP_FIRST_10PCT','MA_V065_UP_SECOND_HALF_REMAINING','MA_V065_UP_PEAK_MINUS_3'])
        self.assertEqual(p[0]['status'],'CLOSED')

    def test_coin_bear_original_paper_exits_30_35_35(self):
        owner=self.seed_coin('REVERSAL_BEAR')
        self.coin_tick(owner,120,1)
        p,execs,state=self.coin_rows()
        self.assertEqual([Decimal(x['qty']) for x in execs],[Decimal('30')])
        self.assertEqual(Decimal(p[0]['first_sold_qty']),Decimal('30'))
        self.coin_tick(owner,123,2)
        self.coin_tick(owner,121,3)
        p,execs,state=self.coin_rows()
        self.assertEqual([Decimal(x['qty']) for x in execs],[Decimal('30'),Decimal('35'),Decimal('35')])
        self.assertEqual(p[0]['status'],'CLOSED')

    def test_no_first_profit_without_actual_bid_at_target_or_orderbook_depth(self):
        for kind in ('REVERSAL_UP','REVERSAL_BEAR'):
            with self.subTest(kind=kind):
                initial=policy.initial_up_exit_state('5m') if kind=='REVERSAL_UP' else policy.initial_bear_exit_state('5m',120)
                _,qty,reason=(policy.up_exit(initial,101,100,100,100,START) if kind=='REVERSAL_UP' else policy.bear_exit(initial,119,100,100,100,START))
                self.assertEqual(qty,0)
                self.assertEqual(reason,'')

if __name__=="__main__":unittest.main()
