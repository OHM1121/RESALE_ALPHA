"""v043 original-wallet SQL regression tests; no broker/network calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import datetime as dt
import sqlite3
import sys
import types
import unittest

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import market_trend_v043 as mt
import market_trend_wallet_v043 as wallet


class StubSettings:
    def snapshot(self):
        return {"frames":{tf:{"allocated_krw":2_000_000,"max_positions":4}
                           for tf in mt.FRAMES}}


class StubLedger:
    def __init__(self):self.settings=StubSettings()


class FakeRuntime:
    def __init__(self,root):
        self.root=Path(root)
        self.coin_ledger=StubLedger()
        self.coin_ma_ledger=StubLedger()
        self.halts=[]
    def live_enabled(self,market):return False
    def halt_market(self,market,stage,error):
        self.halts.append((market,stage,str(error)))


def db(root,market,strategy):
    relative={"stock/at":"stock/ARTITRADE_D.sqlite3",
              "stock/ma":"stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
              "coin/general":"coin/base/upbit_paper.sqlite3",
              "coin/ma":"coin/ma/upbit_paper.sqlite3"}[market+"/"+strategy]
    path=Path(root)/relative;path.parent.mkdir(parents=True,exist_ok=True)
    return sqlite3.connect(str(path))


def populate(root):
    with db(root,"stock","at") as c:
        c.executescript("""
        CREATE TABLE v19_accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized_pnl INTEGER DEFAULT 0,seed INTEGER,last_update TEXT);
        CREATE TABLE pending(tf TEXT PRIMARY KEY,code TEXT);
        CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT,code TEXT,market TEXT,name TEXT,
        ref_ts TEXT,ref_low INTEGER,ref_high INTEGER,ref_bodytop INTEGER,entry_ts TEXT,entry_price INTEGER,
        entry_qty INTEGER,qty INTEGER,first_sell_qty INTEGER,first_sold INTEGER DEFAULT 0,
        recovery_mode TEXT DEFAULT '',recovery_price INTEGER DEFAULT 0,trailing INTEGER DEFAULT 0,
        peak INTEGER,hold_days INTEGER,hold_profit_trail INTEGER DEFAULT 0,
        hold_profit_trail_since TEXT DEFAULT '',entry_fee INTEGER,sell_proceeds INTEGER DEFAULT 0,
        sell_costs INTEGER DEFAULT 0,last_price INTEGER,closed_ts TEXT DEFAULT '',status TEXT DEFAULT 'OPEN');
        CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,
        tf TEXT,code TEXT,name TEXT,position_id INTEGER,ts TEXT,side TEXT,qty INTEGER,price INTEGER,
        gross INTEGER,fees INTEGER,tax INTEGER,cash_after INTEGER,reason TEXT,fill_method TEXT,seen_at TEXT);
        """)
        for tf in mt.FRAMES:
            c.execute("INSERT INTO v19_accounts VALUES(?,?,?,?,?)",(tf,2_000_000,0,2_000_000,""))
    with db(root,"stock","ma") as c:
        c.executescript("""
        CREATE TABLE accounts(tf TEXT PRIMARY KEY,seed INTEGER,cash INTEGER,realized INTEGER,max_positions INTEGER);
        CREATE TABLE candidates(id INTEGER PRIMARY KEY,code TEXT,status TEXT);
        CREATE TABLE positions(id INTEGER PRIMARY KEY,candidate_id INTEGER,tf TEXT,code TEXT,name TEXT,
        entry_at TEXT,entry_date TEXT,entry INTEGER,qty INTEGER,qty0 INTEGER,first_qty INTEGER,
        first_sold INTEGER DEFAULT 0,second_sold INTEGER DEFAULT 0,first_price INTEGER DEFAULT 0,
        midpoint_last_bid INTEGER DEFAULT 0,midpoint_armed INTEGER DEFAULT 0,target INTEGER,low INTEGER,
        ref_close INTEGER,bodytop INTEGER,recovery1 INTEGER,peak INTEGER,midpoint INTEGER,
        last_price INTEGER,recovery INTEGER DEFAULT 0,hold_sessions INTEGER,hold_trail INTEGER DEFAULT 0,
        status TEXT DEFAULT 'OPEN');
        CREATE TABLE executions(id INTEGER PRIMARY KEY,tf TEXT,position_id INTEGER,code TEXT,name TEXT,
        ts TEXT,side TEXT,qty INTEGER,price INTEGER,fees INTEGER,tax INTEGER,net_profit INTEGER,reason TEXT);
        """)
        for tf in mt.FRAMES:
            c.execute("INSERT INTO accounts VALUES(?,?,?,?,?)",(tf,2_000_000,2_000_000,0,4))
    for strategy in ("general","ma"):
        with db(root,"coin",strategy) as c:
            c.executescript("""
            CREATE TABLE wallets(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER DEFAULT 0,seed INTEGER);
            CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT,market TEXT,name TEXT,
            ref_at TEXT,entry_at TEXT,entry TEXT,qty0 TEXT,qty TEXT,entry_fee INTEGER,
            target TEXT,peak TEXT,mark TEXT,first_qty TEXT,first_sold_qty TEXT DEFAULT '0',
            trailing INTEGER DEFAULT 0,recovery INTEGER DEFAULT 0,status TEXT DEFAULT 'OPEN',
            hold_until TEXT DEFAULT '',UNIQUE(tf,market,ref_at));
            CREATE TABLE orders(id INTEGER PRIMARY KEY,market TEXT,status TEXT);
            CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,
            position_id INTEGER,tf TEXT,market TEXT,at TEXT,side TEXT,qty TEXT,price TEXT,
            fees INTEGER,tax INTEGER DEFAULT 0,net_pnl INTEGER DEFAULT 0,reason TEXT,cash_after INTEGER);
            """)
            for tf in mt.FRAMES:
                c.execute("INSERT INTO wallets VALUES(?,?,?,?)",(tf,2_000_000,0,2_000_000))


class WalletV043(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        populate(self.temp.name)
        self.rt=FakeRuntime(self.temp.name)
        self.j=mt.TrendJournal(self.temp.name)
        self.w=wallet.TrendWallet(self.rt,self.j)
        fake=types.ModuleType("android_operation_bridge")
        fake._load=lambda rt:{
           "stock":{"at":{"frames":{tf:{"seed":2_000_000,"max_positions":4}
                                      for tf in mt.FRAMES}}}}
        self.module_guard=mock.patch.dict(sys.modules,{"android_operation_bridge":fake})
        self.module_guard.start()
        self.now=dt.datetime(2026,11,13,10,4,48,tzinfo=mt.KST)

    def tearDown(self):
        self.module_guard.stop()
        self.temp.cleanup()

    def _buy(self,market,strategy,tf,symbol,price=10000):
        ask_size=5000 if market=="stock" else 10
        return self.w.buy(market,strategy,tf,symbol,"TEST",price,ask_size,
                  self.now,"2026-11-13T10:05:00+09:00",28)

    def test_stock_at_original_account_cash_and_fill(self):
        pos,state=self._buy("stock","at","5m","005930")
        self.assertEqual(state,"BOUGHT")
        self.assertGreater(pos.qty,0)
        with db(self.temp.name,"stock","at") as c:
            self.assertLess(c.execute("SELECT cash FROM v19_accounts WHERE tf='5m'").fetchone()[0],2_000_000)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions WHERE status='TREND_OPEN'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='BUY'").fetchone()[0],1)
        self.assertEqual(self._buy("stock","ma","30m","005930")[1],"ALREADY_OWNED_OR_PENDING")
        self.assertEqual(len(self.j.open_positions()),1)

    def test_stock_ma_original_account_cash_and_partial_exit(self):
        pos,state=self._buy("stock","ma","5m","005930")
        self.assertEqual(state,"BOUGHT")
        pos.close_entry_bar("bar0",10000,9800,28)
        pos.__dict__["bar_finalized"]=True
        qty,status=self.w.sell(pos,10200,self.now+dt.timedelta(seconds=60),"MT_TP1")
        self.assertGreater(qty,0)
        self.assertEqual(status,"SOLD")
        self.assertLessEqual(abs(pos.qty-pos.original_qty*.70),1.0)
        with db(self.temp.name,"stock","ma") as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='SELL'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions WHERE status='TREND_OPEN'").fetchone()[0],1)

    def test_coin_general_original_account_and_stop(self):
        pos,state=self._buy("coin","general","5m","KRW-BTC")
        self.assertEqual(state,"BOUGHT")
        qty,status=self.w.sell(pos,9000,self.now+dt.timedelta(seconds=1),"MT_STOP_9PCT")
        self.assertGreater(qty,0)
        self.assertEqual(status,"SOLD")
        self.assertEqual(pos.qty,0.0)
        with db(self.temp.name,"coin","general") as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions WHERE status='CLOSED'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='SELL'").fetchone()[0],1)

    def test_coin_ma_is_separate_wallet_and_no_duplication(self):
        pos,state=self._buy("coin","ma","30m","KRW-ETH")
        self.assertEqual(state,"BOUGHT")
        self.assertEqual(self._buy("coin","general","60m","KRW-ETH")[1],"ALREADY_OWNED_OR_PENDING")
        with db(self.temp.name,"coin","ma") as c:
            self.assertLess(c.execute("SELECT cash FROM wallets WHERE tf='30m'").fetchone()[0],2_000_000)


if __name__=="__main__":unittest.main()
