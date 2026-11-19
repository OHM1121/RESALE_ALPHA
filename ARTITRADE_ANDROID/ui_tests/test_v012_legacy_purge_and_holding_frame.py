"""Selectively purge old PAPER trades, never GMO/CW, never NEW LEGACY fills."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import legacy_purge_v012 as purge
import ledger_rollback_guard_v002 as guard

def db(path,sql):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(str(path)) as c:c.executescript(sql)

def coin_fixture(path):
    db(path, """CREATE TABLE wallets(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER,seed INTEGER);
      INSERT INTO wallets VALUES('5m',1989995,0,2000000);
      CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,entry_at TEXT,
        entry TEXT,qty0 TEXT,qty TEXT,status TEXT);
      INSERT INTO positions VALUES(1,'5m','KRW-OLD','old_ref','2026-11-19T15:02:00+09:00',
        '100','100','100','OPEN');
      CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,tf TEXT,market TEXT,
        at TEXT,side TEXT,qty TEXT,price TEXT,fees INTEGER,tax INTEGER,net_pnl INTEGER,
        reason TEXT,cash_after INTEGER);
      INSERT INTO executions VALUES(1,1,'5m','KRW-OLD','2026-11-19T15:02:00+09:00',
        'BUY','100','100',5,0,0,'OLD_V26',1989995);
      CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,features TEXT);
      INSERT INTO candidates VALUES(1,'5m','KRW-OLD','old_ref','{"old":true}');
      CREATE TABLE orders(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,status TEXT);
      INSERT INTO orders VALUES(1,'5m','KRW-OLD','old_ref','BOUGHT');
      CREATE TABLE nl1118_reference_positions(
        position_id INTEGER PRIMARY KEY,policy TEXT,market TEXT,frame TEXT,symbol TEXT,
        intent_json TEXT,updated_at TEXT);
    """)

def add_new_coin(path):
    with sqlite3.connect(str(path)) as c:
        c.execute("""INSERT INTO positions VALUES(2,'5m','KRW-NEW','new_ref',
            '2026-11-19T15:03:00+09:00','100','10','10','OPEN')""")
        c.execute("""INSERT INTO executions VALUES(2,2,'5m','KRW-NEW',
            '2026-11-19T15:03:00+09:00','BUY','10','100',1,0,0,
            'NL1118_BUY',1988994)""")
        c.execute("UPDATE wallets SET cash=1988994 WHERE tf='5m'")
        c.execute("""INSERT INTO nl1118_reference_positions VALUES(
            2,'NEW_LEGACY_1118','coin','5m','KRW-NEW','{}','now')""")

def read(path,sql):
    with sqlite3.connect(str(path)) as c:
        return c.execute(sql).fetchall()

class PurgeOneWallet(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory(prefix='artitrade-v012-')
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)
    def test_coin_reference_old_removed_new_survives_and_money_rebased(self):
        path=self.root/'coin'/'base'/'upbit_paper.sqlite3'
        coin_fixture(path);add_new_coin(path)
        result=purge._apply_one(path,'coin_at',self.root)
        self.assertEqual(result['removed'],1)
        self.assertEqual(result['fills'],1)
        self.assertEqual(read(path,'SELECT id FROM positions'),[(2,)])
        self.assertEqual(read(path,'SELECT id,cash_after FROM executions'),[(2,1998999)])
        self.assertEqual(read(path,'SELECT cash,realized FROM wallets'),[(1998999,0)])
        self.assertEqual(read(path,'SELECT id FROM candidates'),[])
        self.assertEqual(read(path,'SELECT id FROM orders'),[])
        self.assertEqual(purge._apply_one(path,'coin_at',self.root)['already'],True)

    def test_stock_ma_legacy_candidate_removed_new_original_unchanged(self):
        path=self.root/'stock'/'MA_EPOCH_V067'/'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3'
        db(path, """CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER,seed INTEGER);
            INSERT INTO accounts VALUES('5m',1989995,0,2000000);
            CREATE TABLE positions(id INTEGER PRIMARY KEY,candidate_id INTEGER,tf TEXT,code TEXT,
                entry TEXT,qty0 INTEGER,qty INTEGER,status TEXT);
            INSERT INTO positions VALUES(1,10,'5m','OLD',100,100,100,'OPEN');
            INSERT INTO positions VALUES(2,11,'5m','NEW',100,10,10,'OPEN');
            CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,tf TEXT,
                code TEXT,side TEXT,qty INTEGER,price INTEGER,fees INTEGER,tax INTEGER,
                net_profit INTEGER);
            INSERT INTO executions VALUES(1,1,'5m','OLD','BUY',100,100,5,0,NULL);
            CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,ref_ts TEXT);
            INSERT INTO candidates VALUES(10,'5m','OLD','oldref');
            INSERT INTO candidates VALUES(11,'5m','NEW','newref');
            CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY,kind TEXT,state_json TEXT);
            INSERT INTO ma_v067_positions VALUES(1,'REVERSAL_BEAR','{}');
            INSERT INTO ma_v067_positions VALUES(2,'REVERSAL_UP',
                '{"policy_source":"NEW LEGACY(11.18).xlsx"}');
        """)
        result=purge._apply_one(path,'stock_ma',self.root)
        self.assertEqual(result['removed'],1)
        self.assertEqual(read(path,'SELECT id FROM positions'),[(2,)])
        self.assertEqual(read(path,'SELECT id FROM candidates'),[(11,)])
        self.assertEqual(read(path,'SELECT cash FROM accounts'),[(2000000,)])

    def test_old_coin_ma_archive_clears_hidden_positions_and_research(self):
        path=self.root/'coin'/'ma'/'upbit_paper.sqlite3'
        coin_fixture(path)
        res=purge._apply_one(path,'old_coin_ma',self.root)
        self.assertEqual(res['removed'],1)
        self.assertEqual(read(path,'SELECT COUNT(*) FROM executions'),[(0,)])
        self.assertEqual(read(path,'SELECT COUNT(*) FROM positions'),[(0,)])
        self.assertEqual(read(path,'SELECT COUNT(*) FROM candidates'),[(0,)])
        self.assertEqual(read(path,'SELECT cash,realized FROM wallets'),[(2000000,0)])

    def test_stock_at_v19_old_paper_cash_restored(self):
        path=self.root/'stock'/'ARTITRADE_D.sqlite3'
        db(path, """CREATE TABLE v19_accounts(tf TEXT PRIMARY KEY,cash INTEGER,
            realized_pnl INTEGER,seed INTEGER);
            INSERT INTO v19_accounts VALUES('30m',1989995,0,2000000);
            CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized_pnl INTEGER);
            INSERT INTO accounts VALUES('30m',2000000,0);
            CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,
                ref_ts TEXT,entry_qty INTEGER,entry_price INTEGER,entry_fee INTEGER,
                sell_proceeds INTEGER,sell_costs INTEGER,qty INTEGER,status TEXT);
            INSERT INTO positions VALUES(1,'30m','OLD','old_ref',100,100,5,0,0,100,'OPEN');
            CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,tf TEXT,
                side TEXT,qty INTEGER,price INTEGER,gross INTEGER,fees INTEGER,
                tax INTEGER,cash_after INTEGER);
            INSERT INTO executions VALUES(1,1,'30m','BUY',100,100,10000,5,0,1989995);
            CREATE TABLE v19_members(position_id INTEGER,signal_id INTEGER);
            INSERT INTO v19_members VALUES(1,22);
            CREATE TABLE signals(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,ref_ts TEXT);
            INSERT INTO signals VALUES(22,'30m','OLD','old_ref');
            CREATE TABLE v19_orders(signal_id INTEGER,status TEXT);
            INSERT INTO v19_orders VALUES(22,'BOUGHT');
        """)
        r=purge._apply_one(path,'stock_at',self.root)
        self.assertEqual(r['removed'],1)
        self.assertEqual(read(path,'SELECT COUNT(*) FROM positions'),[(0,)])
        self.assertEqual(read(path,'SELECT cash,realized_pnl FROM v19_accounts'),[(2000000,0)])
        self.assertEqual(read(path,'SELECT id FROM signals'),[])

    def test_trend_live_position_never_automatically_purged(self):
        path=self.root/'stock'/'trend'/'market_trend_wallet_v054.sqlite3'
        db(path, """CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER,seed INTEGER);
            INSERT INTO accounts VALUES('30m',1000000,0,2000000);
            CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,symbol TEXT,entry TEXT,
                qty0 REAL,qty REAL,status TEXT,execution_mode TEXT,broker_order_id TEXT,
                payload TEXT);
            INSERT INTO positions VALUES(1,'30m','005930','100',10,10,'TREND_OPEN',
                'LIVE','123456','{}');
            CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                tf TEXT,side TEXT,qty REAL,price REAL,fees INTEGER,tax INTEGER,net_pnl INTEGER);
            INSERT INTO executions VALUES(1,1,'30m','BUY',10,100,1,0,0);
        """)
        r=purge._apply_one(path,'stock_trend',self.root)
        self.assertEqual(r['skipped'],1)
        self.assertEqual(read(path,'SELECT id FROM positions'),[(1,)])
        self.assertEqual(read(path,'SELECT COUNT(*) FROM executions'),[(1,)])

    def test_gmo_and_close_never_targeted(self):
        self.assertTrue(all('/gmo/' not in p and '/close/' not in p
            and '/cw/' not in p for _,p in purge.SPECS))
        self.assertEqual([x[0] for x in purge.SPECS[:6]],
            ['stock_at','coin_at','stock_ma','coin_ma','stock_trend','coin_trend'])

class AuthorizedRollback(unittest.TestCase):
    def test_user_authorized_migration_updates_guard_only_for_targeted_wallets(self):
        with tempfile.TemporaryDirectory() as temp:
            files=Path(temp)/'files'
            root=files/'ARTITRADE_PHONE_MAIN_V070'
            root.mkdir(parents=True)
            start='2026-11-18T00:00:00+00:00'
            (root/guard.MARKER).write_text(json.dumps(dict(
                schema=guard.EPOCH_SCHEMA,epoch_id='SIMULATED_V012_EPOCH',
                state='COMMITTED',completed_at_utc=start)))
            path=root/'coin'/'base'/'upbit_paper.sqlite3'
            coin_fixture(path)
            # Leave actual unrelated GMO/CW records at byte-identical offsets.
            close=root/'stock'/'close'/'close_paper.sqlite3'
            db(close,"""CREATE TABLE executions(id INTEGER PRIMARY KEY,
                  side TEXT,qty REAL,price REAL,ts TEXT);
                  INSERT INTO executions VALUES(1,'BUY',100,10,'2026-11-19T15:02:00+09:00');""")
            before=hashlib.sha256(close.read_bytes()).hexdigest()
            guard.verify_and_checkpoint(str(root),require_existing=False)
            result=purge.run_once(str(root))
            self.assertEqual(result['status'],'DONE')
            self.assertEqual(read(path,'SELECT COUNT(*) FROM positions'),[(0,)])
            self.assertEqual(read(path,'SELECT cash FROM wallets'),[(2000000,)])
            self.assertEqual(before,hashlib.sha256(close.read_bytes()).hexdigest())
            self.assertEqual(purge.run_once(str(root))['status'],'ALREADY_CLEANED')
            self.assertTrue(guard.verify_and_checkpoint(str(root),require_existing=True)['ok'])

if __name__=='__main__':unittest.main(verbosity=2)
