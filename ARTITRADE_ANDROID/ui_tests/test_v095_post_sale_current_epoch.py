"""v095 RED: MA v067 and MT54 real SELL episodes feed post-sale high research.

Only real persisted SELLs and observed public prices. No artificial fills.
"""
import datetime as dt
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from post_exit_sources_v053 import scan
from post_exit_high_v053 import Tracker,KST

class FollowupRealSource(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.now=dt.datetime.now(KST).replace(microsecond=0)
        self.sell_time=self.now-dt.timedelta(minutes=9)
        self.entry_time=self.now-dt.timedelta(hours=3)
    def tearDown(self):self.temp.cleanup()

    def ma(self,market,symbol,epoch):
        path=(self.root/market/("MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3" if market=="stock" else "ma_epoch_v067/upbit_paper.sqlite3")) if epoch==67 else (
            self.root/market/("MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3" if market=="stock" else "ma_epoch_v062/upbit_paper.sqlite3"))
        path.parent.mkdir(parents=True,exist_ok=True)
        col="code" if market=="stock" else "market"
        with sqlite3.connect(path) as con:
            con.executescript(f"""CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,{col} TEXT,
              status TEXT,entry REAL,entry_at TEXT);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,side TEXT,
              qty REAL,price REAL,ts TEXT);
            """)
            con.execute(f"INSERT INTO positions VALUES (1,'5m',?,'OPEN',10000,?)",
                        (symbol,self.entry_time.isoformat()))
            con.execute("INSERT INTO executions VALUES (12,1,'SELL',10,10200,?)",
                        (self.sell_time.isoformat(),))
        return path

    def trend(self,market,symbol,full=False):
        path=self.root/market/"trend/market_trend_wallet_v054.sqlite3"
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as con:
            con.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,symbol TEXT,
                status TEXT,entry REAL,entry_at TEXT,execution_mode TEXT);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                side TEXT,event_key TEXT,qty REAL,price REAL,at TEXT);
            """)
            con.execute("INSERT INTO positions VALUES(8,'5m',?,?,?,?,?)",
                (symbol,'CLOSED' if full else 'TREND_OPEN',10000,
                 self.entry_time.isoformat(),'PAPER'))
            con.execute("INSERT INTO executions VALUES(91,8,'SELL',?,0.4,10200,?)",
                (f"MT54:SELL:{market}:8:TP1",self.sell_time.isoformat()))
            if full:
                con.execute("INSERT INTO executions VALUES(92,8,'SELL',?,0.6,10400,?)",
                    (f"MT54:SELL:{market}:8:TP3",(self.sell_time+dt.timedelta(seconds=90)).isoformat()))
        return path

    def test_both_v067_ma_market_partial_sales_included_even_with_v062_dbs(self):
        self.ma('stock','000001',62)
        self.ma('coin','KRW-OLD',62)
        self.ma('stock','005930',67)
        self.ma('coin','KRW-BTC',67)
        rows,issues=scan(self.root,int(self.now.timestamp()))
        found={(x['market'],x['symbol']):x for x in rows if x['strategy']=='ma'}
        self.assertIn(('stock','005930'),found)
        self.assertIn(('coin','KRW-BTC'),found)
        self.assertIn(('stock','000001'),found)
        self.assertIn(('coin','KRW-OLD'),found)
        self.assertEqual(found['stock','005930']['exit_evidence'],'CONFIRMED')
        self.assertIn(':partialfill:1:12',found['stock','005930']['source_ref'])

    def test_trend_both_market_partial_and_full_sales_use_distinct_exec_ids(self):
        self.trend('stock','005930',full=True)
        self.trend('coin','KRW-ETH',full=False)
        rows,issues=scan(self.root,int(self.now.timestamp()))
        stock=[x for x in rows if x['strategy']=='trend' and x['market']=='stock']
        coin=[x for x in rows if x['strategy']=='trend' and x['market']=='coin']
        self.assertEqual(len(stock),2,stock)
        self.assertEqual(len(coin),1,coin)
        self.assertTrue(any(':partialfill:8:91' in x['source_ref'] for x in stock))
        self.assertTrue(any(':position:8:' in x['source_ref'] for x in stock))
        self.assertTrue(any(':partialfill:8:91' in x['source_ref'] for x in coin))

    def test_v067_and_trend_wal_changes_trigger_rescan_and_true_tick_high(self):
        tracker=Tracker(self.root)
        tracker.scan_sources(now=int(self.now.timestamp()))
        a=self.ma('stock','005930',67)
        self.trend('coin','KRW-BTC')
        result=tracker.scan_sources(now=int(self.now.timestamp()))
        self.assertGreaterEqual(result['cases_seen'],2,result)
        self.assertIn('005930',tracker.active_symbols('stock'))
        self.assertIn('KRW-BTC',tracker.active_symbols('coin'))
        next_tick=self.now+dt.timedelta(seconds=1)
        samples=[('stock','005930',10600,next_tick.isoformat(),'KRX_VERIFIED_0B'),
                 ('coin','KRW-BTC',10700,next_tick.isoformat(),'UPBIT_PUBLIC_TICKER')]
        self.assertGreaterEqual(tracker.record_batch(samples),2)
        stock=tracker.summary('stock','ma')['cases']
        trend=tracker.summary('coin','trend')['cases']
        self.assertTrue(stock and stock[0]['peak']==10600,stock)
        self.assertTrue(trend and trend[0]['peak']==10700,trend)

if __name__=='__main__':unittest.main()
