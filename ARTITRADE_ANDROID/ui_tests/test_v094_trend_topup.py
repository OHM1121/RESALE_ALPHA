"""v094 stock and coin market-trend underfunded verified entry only.

The original MT54 SQLite and original TrendPosition are authority. No new
candidate, new position or separate trader; no live broker/paper mix-up.
"""
import datetime as dt
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from market_trend_wallet_v054 import TrendWalletV054
from market_trend_v043 import TrendPosition
from trend_partial_entry_v094 import topup_trend
KST=dt.timezone(dt.timedelta(hours=9))


class Book:
    def __init__(self,now):
        self._lock=threading.RLock()
        self._items={('005930','KRX'):(10020,10010,1000,now)}
        self._reserved={}
        self._bid_depth={('005930','KRX'):(10010,1000,now)}
    def available(self,code,venue,now,limit,desired):
        v=self._items[(code,venue)]
        cap=int(v[2]*.5)-self._reserved.get((code,venue),0)
        if v[0]>limit or (now-v[3]).total_seconds()>3:return None,'BLOCK'
        return (v[0],min(cap,desired),v[2],0),'OK'
    def reserve(self,code,venue,now,ask,qty):
        v=self._items[(code,venue)]
        if ask!=v[0] or qty>int(v[2]*.5)-self._reserved.get((code,venue),0):return False
        self._reserved[(code,venue)]=qty+self._reserved.get((code,venue),0)
        return True


class Runtime:
    def __init__(self,root,now):
        self.root=root;self.live_enabled=lambda market:False
        self.stock_runner=type('Runner',(),{})()
        self.stock_runner.engine=type('Engine',(),{'book':Book(now)})()
        self.coin_runner=type('CoinRunner',(),{})()
        self.coin_runner._lock=threading.RLock()
        self.coin_runner.last_book={'KRW-BTC':{
            'market':'KRW-BTC','ask':'10020','ask_size':'1000',
            'bid':'10010','bid_size':'1000','timestamp':int(now.timestamp()*1000),
            'sr_tick_size':'10'}}


class NativeTrend(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.now=dt.datetime.now(KST)
        self.rt=Runtime(self.temp.name,self.now)
        self.wallet=TrendWalletV054(self.rt)
        self.positions={}
        for market,code in [('stock','005930'),('coin','KRW-BTC')]:
            at=self.now-dt.timedelta(seconds=2)
            p=TrendPosition(market,'trend','5m',code,at,10000,1,
                            '2026-11-17T09:59:00+09:00')
            p.__dict__['execution_mode']='PAPER'
            with self.wallet._connect(market) as c:
                t=c.execute("""INSERT INTO positions(tf,symbol,name,entry_at,entry,qty0,qty,
                    entry_fee,mark,status,execution_mode,broker_order_id,bar_key,payload,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ('5m',code,'TEST',at.isoformat(),10000,1,1,5,10000,
                     'TREND_OPEN','PAPER','',p.bar_key,'{}',at.isoformat()))
                p.__dict__['position_id']=t.lastrowid
                c.execute('UPDATE positions SET payload=? WHERE id=?',
                          (__import__('json').dumps(p.to_dict()),p.position_id))
                c.execute("""INSERT INTO executions(event_key,position_id,tf,symbol,at,side,
                    qty,price,fees,tax,net_pnl,cash_after,execution_mode,broker_order_id,reason)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ('INITIAL:'+code,p.position_id,'5m',code,at.isoformat(),
                     'BUY',1,10000,5,0,0,0,'PAPER','','ORIGINAL_TREND_BUY'))
                c.execute("UPDATE accounts SET cash=cash-10005 WHERE tf='5m'")
            self.positions[market]=p
    def tearDown(self):self.temp.cleanup()
    def execs(self,market):
        with self.wallet._connect(market) as c:return c.execute(
            "SELECT side,qty,price,reason FROM executions ORDER BY id").fetchall()
    def test_stock_up_to_two_ticks_after_true_initial_buy(self):
        pos=self.positions['stock']
        q={'ask':10020,'bid':10010,'ask_size':1000,'bid_size':1000,'trade':10000}
        bought=topup_trend(self.wallet,pos,q,self.now)
        self.assertGreater(bought,0)
        self.assertGreater(pos.original_qty,1)
        self.assertGreater(pos.qty,1)
        self.assertEqual(len(self.execs('stock')),2)
    def test_coin_up_to_two_ticks_after_true_initial_buy(self):
        pos=self.positions['coin']
        q={'ask':10020,'bid':10010,'ask_size':1000,'bid_size':1000,'trade':10000}
        bought=topup_trend(self.wallet,pos,q,self.now)
        self.assertGreater(bought,0)
        self.assertEqual(len(self.execs('coin')),2)
    def test_no_replayed_duplicate_event_or_second_beyond_full_symbol_budget(self):
        pos=self.positions['coin'];q={'ask':10020,'bid':10010,'ask_size':1000,'bid_size':1000}
        self.assertGreater(topup_trend(self.wallet,pos,q,self.now),0)
        self.assertEqual(topup_trend(self.wallet,pos,q,self.now),0)
    def test_stock_third_tick_no_topup(self):
        pos=self.positions['stock'];q={'ask':10030,'bid':10010,'ask_size':1000,'bid_size':1000}
        self.assertEqual(topup_trend(self.wallet,pos,q,self.now),0)
    def test_already_sold_never_topup(self):
        pos=self.positions['stock'];q={'ask':10020,'bid':10010,'ask_size':1000,'bid_size':1000}
        with self.wallet._connect('stock') as c:
            c.execute("""INSERT INTO executions(event_key,position_id,tf,symbol,at,side,
                qty,price,fees,tax,net_pnl,cash_after,execution_mode,broker_order_id,reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('SOLD',pos.position_id,'5m','005930',self.now.isoformat(),
                 'SELL',1,10010,5,0,0,0,'PAPER','','MANUAL'))
        self.assertEqual(topup_trend(self.wallet,pos,q,self.now),0)

if __name__=="__main__":unittest.main()
