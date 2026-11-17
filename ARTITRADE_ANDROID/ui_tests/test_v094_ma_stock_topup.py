"""v094 original MA stock position: underfilled, confirmed BUY, same 15s window.

No invented fills; real SQLite positions/accounts/executions, real v067 state.
"""
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from ma_v067_stock_runtime import _topup_partial_stock
from ma_v067_policy import initial_up_exit_state
KST=dt.timezone(dt.timedelta(hours=9))


class Book:
    def __init__(self,seen,ask=10020,qty=100):
        self._lock=threading.RLock()
        self._items={('005930','KRX'):(ask,ask-10,qty,seen)}
        self._reserved={}
    def available(self,code,venue,when,limit,desired):
        with self._lock:
            r=self._items[(code,venue)]
            if (r[0]>limit or r[2]<=0 or abs((when-r[3]).total_seconds())>3):
                return None,'STALE_OR_TOO_HIGH'
            n=min(desired,int(r[2]//2)-self._reserved.get((code,venue),0))
            return ((r[0],n,r[2],0),'OK') if n>0 else (None,'NO_DEPTH')
    def reserve(self,code,venue,when,ask,qty):
        with self._lock:
            q=self._items[(code,venue)]
            if ask!=q[0] or self._reserved.get((code,venue),0)+qty>q[2]//2:return False
            self._reserved[(code,venue)]=self._reserved.get((code,venue),0)+qty
            return True


class Owner:
    lock=threading.RLock()
    def __init__(self,db):self.db=db


class RealMAStockTopup(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'ma.sqlite'
        self.now=dt.datetime.now(KST)
        self.book=Book(self.now)
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE accounts(tf TEXT PRIMARY KEY,seed INTEGER,cash INTEGER,realized INTEGER,max_positions INTEGER);
                INSERT INTO accounts VALUES('5m',100000,100000,0,1);
                CREATE TABLE positions(
                  id INTEGER PRIMARY KEY,tf TEXT,code TEXT,name TEXT,
                  entry INTEGER,entry_at TEXT,qty INTEGER,qty0 INTEGER,
                  first_qty INTEGER,first_sold INTEGER,second_sold INTEGER,
                  status TEXT,last_price INTEGER,target INTEGER);
                CREATE TABLE executions(
                  id INTEGER PRIMARY KEY,tf TEXT,position_id INTEGER,
                  code TEXT,name TEXT,ts TEXT,side TEXT,qty INTEGER,
                  price INTEGER,fees INTEGER,tax INTEGER,
                  net_profit INTEGER,reason TEXT);
                CREATE TABLE ma_v067_positions(
                  position_id INTEGER PRIMARY KEY,kind TEXT,frame TEXT,
                  code TEXT,state_json TEXT,created_at TEXT,updated_at TEXT);
            """)
            at=(self.now-dt.timedelta(seconds=2)).isoformat()
            c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (11,'5m','005930','Test',10000,at,1,1,1,0,0,'OPEN',10000,10200))
            c.execute("INSERT INTO executions VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('5m',11,'005930','Test',at,'BUY',1,10000,0,0,None,'MA_KRX_FRESH_BOOK_PAPER'))
            state=initial_up_exit_state('5m')
            state.update(entry_at=at,rounded_first_goal='1',rounded_second_goal='0')
            c.execute("INSERT INTO ma_v067_positions VALUES(?,?,?,?,?,?,?)",
                      (11,'REVERSAL_UP','5m','005930',json.dumps(state),at,at))
            c.execute("UPDATE accounts SET cash=90000")
        self.owner=Owner(self.db)
        self.tick=('005930','KOSPI','Test','',10020,100,self.now.isoformat(),'KRX',str(self.now.timestamp()))
    def tearDown(self):self.tmp.cleanup()
    def rows(self):
        c=sqlite3.connect(self.db);c.row_factory=sqlite3.Row
        with c:
            p=dict(c.execute('SELECT * FROM positions WHERE id=11').fetchone())
            a=dict(c.execute('SELECT * FROM accounts WHERE tf=?',('5m',)).fetchone())
            buys=[dict(x) for x in c.execute("SELECT * FROM executions WHERE side='BUY' ORDER BY id")]
            st=json.loads(c.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=11").fetchone()[0])
        c.close()
        return p,a,buys,st
    def test_genuine_fresh_book_topups_under_seed_at_first_fill_plus_two_ticks(self):
        n=_topup_partial_stock(self.owner,self.tick,self.book)
        self.assertGreater(n,0)
        p,a,buys,st=self.rows()
        self.assertEqual(len(buys),2)
        self.assertEqual(buys[-1]['reason'],'MA_V094_PARTIAL_TWO_TICKS')
        self.assertLessEqual(sum(x['qty']*x['price']+x['fees'] for x in buys),100000)
        self.assertEqual(p['qty'],p['qty0'])
        self.assertGreater(p['qty0'],1)
        self.assertEqual(st['rounded_first_goal'],str(p['first_qty']))
    def test_same_exchange_second_never_rebuys_same_book(self):
        self.assertGreater(_topup_partial_stock(self.owner,self.tick,self.book),0)
        self.assertEqual(_topup_partial_stock(self.owner,self.tick,self.book),0)
        self.assertEqual(len(self.rows()[2]),2)
    def test_third_tick_above_first_fill_price_never_topups(self):
        self.book=Book(self.now,ask=10030)
        self.assertEqual(_topup_partial_stock(self.owner,self.tick,self.book),0)
        self.assertEqual(len(self.rows()[2]),1)
    def test_no_topup_after_a_verified_sale(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO executions VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('5m',11,'005930','Test',self.now.isoformat(),'SELL',1,10200,0,0,None,'MANUAL'))
        self.assertEqual(_topup_partial_stock(self.owner,self.tick,self.book),0)
        self.assertEqual(len(self.rows()[2]),1)
    def test_after_original_15_sec_window_no_extra_buy(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE positions SET entry_at=?",( (self.now-dt.timedelta(seconds=18)).isoformat(),))
        self.assertEqual(_topup_partial_stock(self.owner,self.tick,self.book),0)
    def test_full_allocation_never_topups(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE accounts SET seed=10000,cash=90000")
        self.assertEqual(_topup_partial_stock(self.owner,self.tick,self.book),0)
    def test_stale_book_cannot_topup(self):
        self.book=Book(self.now-dt.timedelta(seconds=5))
        self.assertEqual(_topup_partial_stock(self.owner,self.tick,self.book),0)

if __name__=='__main__':unittest.main()
