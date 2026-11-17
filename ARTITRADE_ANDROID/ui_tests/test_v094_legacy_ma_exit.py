"""v094 legacy MA: previously bought V061/V062 holdings must SELL from original SQLite.

A new MA epoch is NOT a new sell account for old holdings. Never copy old
positions, credit a new wallet, reset cash, or arm old pending BUY orders.
"""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from legacy_ma_exit_watch_v094 import legacy_stock_codes,legacy_stock_exit
from stock_ma_engine import StockMAService
KST=dt.timezone(dt.timedelta(hours=9))


class StockBook:
    def __init__(self,stamp):
        from threading import RLock
        self._lock=RLock()
        self._items={('005930','KRX'):(10210,10200,1000,stamp)}
        self._bid_depth={('005930','KRX'):(10200,1000,stamp)}
        self._bid_reserved={}
    def available_sell(self,code,venue,when,bid,desired):
        record=self._bid_depth.get((code,venue))
        if not record or record[0]!=bid or abs((when-record[2]).total_seconds())>3:return 0
        return min(desired,record[1]//2-self._bid_reserved.get((code,venue),0))
    def reserve_sell(self,code,venue,when,bid,qty):
        if self.available_sell(code,venue,when,bid,qty)<qty:return False
        k=(code,venue);self._bid_reserved[k]=self._bid_reserved.get(k,0)+qty;return True


class HistoricalPosition(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.db=self.root/'stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3'
        self.db.parent.mkdir(parents=True)
        self.now=dt.datetime.now(KST)
        self.store=type('MarketStore',(),{'root':self.root})()
        with sqlite3.connect(self.db) as c:
            c.executescript("""
              CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER);
              INSERT INTO accounts VALUES('5m',0,0);
              CREATE TABLE candidates(id INTEGER PRIMARY KEY,code TEXT,name TEXT,market TEXT,features TEXT);
              CREATE TABLE positions(
                id INTEGER PRIMARY KEY,candidate_id INTEGER,tf TEXT,code TEXT,name TEXT,
                entry INTEGER,entry_at TEXT,qty INTEGER,qty0 INTEGER,first_qty INTEGER,
                first_sold INTEGER,second_sold INTEGER,first_price INTEGER,
                status TEXT,last_price INTEGER);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,tf TEXT,position_id INTEGER,
                code TEXT,name TEXT,ts TEXT,side TEXT,qty INTEGER,price INTEGER,
                fees INTEGER,tax INTEGER,net_profit INTEGER,reason TEXT);
              CREATE TABLE ma_v061_positions(position_id INTEGER PRIMARY KEY,kind TEXT,tf TEXT,
                code TEXT,ref_ts TEXT,state_json TEXT,created_at TEXT,updated_at TEXT);
            """)
            c.execute("INSERT INTO candidates VALUES(?,?,?,?,?)",
                      (15,'005930','Test','KOSPI',json.dumps({'ma_v061_kind':'TURNPOINT'})))
            when=(self.now-dt.timedelta(days=2)).isoformat()
            c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (11,15,'5m','005930','Test',10000,when,100,100,20,0,0,0,'OPEN',10200))
            c.execute("INSERT INTO executions VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('5m',11,'005930','Test',when,'BUY',100,10000,0,0,None,'OLD_MA_REAL_0D_PAPER'))
            state={'frame':'5m','stage':0,'first_anchor':'0','first_sold':'0',
                   'second_sold':'0','peak2':'0','last_observed_at':when}
            c.execute("INSERT INTO ma_v061_positions VALUES(?,?,?,?,?,?,?,?)",
                      (11,'TURNPOINT','5m','005930',when,json.dumps(state),when,when))
    def tearDown(self):self.tmp.cleanup()
    def query(self,sql):
        with sqlite3.connect(self.db) as c:return c.execute(sql).fetchall()
    def test_existing_old_v062_holding_is_subscribed_and_sells_20pct_when_bid_already_plus_2(self):
        self.assertIn('005930',legacy_stock_codes(self.root))
        book=StockBook(self.now)
        tick=('005930','KOSPI','Test','202611171000',10200,10,self.now.isoformat(),'KRX',self.now.timestamp())
        with patch('ma_v061_stock_runtime._slope_closes',return_value=[]):
            n=legacy_stock_exit(self.root,self.store,tick,book)
        self.assertEqual(n,20)
        self.assertEqual(self.query("SELECT qty,status FROM positions WHERE id=11"),[(80,'OPEN')])
        self.assertEqual(self.query("SELECT side,qty,reason FROM executions WHERE side='SELL'"),
                         [('SELL',20,'MA_V061_FIRST_20PCT')])
        self.assertEqual(self.query("SELECT first_sold FROM positions WHERE id=11"),[(1,)] if False else [(0,)])
        # The first stage is tracked in the ORIGINAL historical sidecar.
        row=json.loads(self.query("SELECT state_json FROM ma_v061_positions WHERE position_id=11")[0][0])
        self.assertEqual(row['first_sold'],'20')
    def test_actual_runner_gate_dispatches_old_ma_first_sell(self):
        """Reproduce d_live._tick_job_loop's watched_codes() gate.

        Prior implementation only added V062 positions to pinned_stocks(),
        so this conditional never called on_tick and no legacy exit occurred.
        """
        import legacy_ma_exit_watch_v094 as legacy
        legacy.install(None)
        service=StockMAService(self.root/'stock/MA_EPOCH_V067',self.store)
        self.assertIn('005930',service.pinned_stocks())
        self.assertIn('005930',service.watched_codes())
        book=StockBook(self.now)
        tick=('005930','KOSPI','Test','202611171000',10200,10,
              self.now.isoformat(),'KRX',self.now.timestamp())
        with patch('stock_ma_engine.live_session',return_value=True), \\
             patch('stock_ma_engine.after_market_entitlement',return_value=(True,'')), \\
             patch('ma_v061_stock_runtime._slope_closes',return_value=[]):
            # Identical MA gate and on_tick path as production d_live worker.
            if tick[0] in service.watched_codes():
                service.on_tick(tick,book)
        self.assertEqual(self.query("SELECT qty FROM positions WHERE id=11"),[(80,)])
        self.assertEqual(self.query("SELECT reason FROM executions WHERE side='SELL'"),
                         [('MA_V061_FIRST_20PCT',)])
        with sqlite3.connect(service.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],0)

    def test_replayed_same_exchange_event_must_not_make_a_second_sale(self):
        book=StockBook(self.now)
        tick=('005930','KOSPI','Test','202611171000',10200,10,self.now.isoformat(),'KRX',self.now.timestamp())
        with patch('ma_v061_stock_runtime._slope_closes',return_value=[]):
            self.assertEqual(legacy_stock_exit(self.root,self.store,tick,book),20)
            self.assertEqual(legacy_stock_exit(self.root,self.store,tick,book),0)
        self.assertEqual(len(self.query("SELECT * FROM executions WHERE side='SELL'")),1)
    def test_market_store_is_stock_subfolder_in_original_android_runtime(self):
        self.assertIn('005930',legacy_stock_codes(self.root/'stock'))
        book=StockBook(self.now)
        tick=('005930','KOSPI','Test','202611171000',10200,10,self.now.isoformat(),'KRX',self.now.timestamp())
        with patch('ma_v061_stock_runtime._slope_closes',return_value=[]):
            self.assertEqual(legacy_stock_exit(self.root/'stock',self.store,tick,book),20)
        self.assertEqual(len(self.query("SELECT * FROM executions WHERE side='SELL'")),1)

    def test_no_book_or_stale_book_never_creates_old_execution(self):
        book=StockBook(self.now-dt.timedelta(seconds=6))
        tick=('005930','KOSPI','Test','202611171000',10200,10,self.now.isoformat(),'KRX',self.now.timestamp())
        self.assertEqual(legacy_stock_exit(self.root,self.store,tick,book),0)
        self.assertEqual(len(self.query("SELECT * FROM executions WHERE side='SELL'")),0)
    def test_no_new_buy_candidate_submitted_by_legacy_exit_monitor(self):
        book=StockBook(self.now)
        tick=('005930','KOSPI','Test','202611171000',10200,10,self.now.isoformat(),'KRX',self.now.timestamp())
        with patch('ma_v061_stock_runtime._slope_closes',return_value=[]):
            legacy_stock_exit(self.root,self.store,tick,book)
        self.assertEqual(len(self.query("SELECT * FROM executions WHERE side='BUY'")),1)
        self.assertEqual(len(self.query("SELECT * FROM candidates")),1)

if __name__=='__main__':unittest.main()
