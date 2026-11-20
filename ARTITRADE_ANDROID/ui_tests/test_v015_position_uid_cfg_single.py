"""Strict 0.0.15 provenance/one-position-only PAPER regression.

All wallets, quotes and fill rows below are SYNTHETIC, never user device data.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import cfg_single_paper_cleanup_v015 as cfg
import position_uid_v015 as uid
import ledger_rollback_guard_v002 as guard

KST=dt.timezone(dt.timedelta(hours=9))
ENTRY="2026-11-19T14:00:00+09:00"
AFTER="2026-11-19T15:30:00+09:00"
EPOCH="ARTITRADE_SYNTHETIC_V015"

def make_root(path):
    root=path/"files"/"ARTITRADE_PHONE_MAIN_V070"
    root.mkdir(parents=True)
    (root/guard.MARKER).write_text(json.dumps({
        "schema":guard.EPOCH_SCHEMA,"epoch_id":EPOCH,
        "state":"COMMITTED","completed_at_utc":"2026-11-18T00:00:00+00:00"
    }),encoding="utf8")
    return root

def setup_cfg(root,second=True,another_early=False,real=False):
    db=root/cfg._REL
    db.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(str(db)) as c:
        c.executescript("""CREATE TABLE wallets(tf TEXT PRIMARY KEY,
         cash INTEGER,realized INTEGER,seed INTEGER);
         CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
           ref_at TEXT,entry_at TEXT,entry TEXT,qty0 TEXT,qty TEXT,
           status TEXT, execution_mode TEXT,broker_order_id TEXT);
         CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
          tf TEXT,market TEXT,at TEXT,side TEXT,qty TEXT,price TEXT,fees INTEGER,
          tax INTEGER,net_pnl INTEGER,reason TEXT,cash_after INTEGER);
         CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY,
          kind TEXT,state_json TEXT);
         CREATE TABLE candidates(id INTEGER PRIMARY KEY,
          tf TEXT,market TEXT,ref_at TEXT,features TEXT);
         CREATE TABLE orders(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
          ref_at TEXT,status TEXT);
         """)
        c.execute("INSERT INTO positions VALUES(1,'5m','KRW-CFG','ref-1',?,'100','10','10','OPEN',?,?)",
                (ENTRY,"LIVE" if real else "PAPER",""))
        c.execute("INSERT INTO executions VALUES(1,1,'5m','KRW-CFG',?,'BUY','10','100',1,0,0,'MA_V1118_UP_BUY',1998999)",
                (ENTRY,))
        c.execute("INSERT INTO ma_v067_positions VALUES(1,'REVERSAL_UP',?)",
                (json.dumps({"first_sold":0,"seen_above_entry":False}),))
        c.execute("INSERT INTO candidates VALUES(1,'5m','KRW-CFG','ref-1','{}')")
        c.execute("INSERT INTO orders VALUES(1,'5m','KRW-CFG','ref-1','BOUGHT')")
        cash=1998999
        if second:
            c.execute("INSERT INTO positions VALUES(2,'5m','KRW-CFG','ref-2',?,'100','5','5','OPEN','PAPER','')",
                     (AFTER,))
            c.execute("INSERT INTO executions VALUES(2,2,'5m','KRW-CFG',?,'BUY','5','100',1,0,0,'MA_V1118_UP_BUY',1998498)",
                     (AFTER,))
            c.execute("INSERT INTO ma_v067_positions VALUES(2,'REVERSAL_UP','{}')")
            c.execute("INSERT INTO candidates VALUES(2,'5m','KRW-CFG','ref-2','{}')")
            c.execute("INSERT INTO orders VALUES(2,'5m','KRW-CFG','ref-2','BOUGHT')")
            cash=1998498
        if another_early:
            c.execute("INSERT INTO positions VALUES(4,'5m','KRW-CFG','ref-4',?,'100','2','2','OPEN','PAPER','')",
                     ("2026-11-19T14:05:00+09:00",))
            c.execute("INSERT INTO executions VALUES(4,4,'5m','KRW-CFG',?,'BUY','2','100',1,0,0,'MA_V1118_UP_BUY',?)",
                     ("2026-11-19T14:05:00+09:00",cash-201))
            c.execute("INSERT INTO ma_v067_positions VALUES(4,'REVERSAL_UP','{}')")
            cash-=201
        c.execute("INSERT INTO wallets VALUES('5m',?,0,2000000)",(cash,))
    return db

def rows(path,sql):
    with sqlite3.connect(str(path)) as c:return c.execute(sql).fetchall()

class StablePositionUID(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="artitrade-015-uid-")
        self.addCleanup(self.tmp.cleanup)
        self.root=make_root(Path(self.tmp.name))

    def sample_packet(self):
        stock=dict(at=dict(ledger=dict(
            positions=[dict(id=1,code="005930",entry_at=ENTRY,tf="5m")],
            executions=[dict(id=1,position_id=1,side="BUY"),dict(id=2,position_id=1,side="SELL")],
            ma=dict(positions=[dict(id=1,code="005930",entry_at=ENTRY,tf="5m")],
                    executions=[dict(id=1,position_id=1,side="BUY")]),
            gmo=dict(positions=[dict(code="005930",entry_date="2026-11-19")],
                     executions=[dict(id=91,code="005930",entry_date="2026-11-19",side="BUY")])
        )),cw=dict(ledger=dict(
            positions=[dict(id=1,code="005930",entry_at=ENTRY)],
            executions=[dict(id=1,position_id=1,side="BUY")])) ,
            trend=dict(ledger=dict(
            positions=[dict(id=1,code="005930",entry_at=ENTRY)],
            executions=[dict(id=1,position_id=1,side="BUY")])))
        coin=dict(general=dict(ledger=dict(
            positions=[dict(id=1,market="KRW-SIM",entry_at=ENTRY)],
            executions=[dict(id=1,position_id=1,side="BUY")])),
            ma=dict(ledger=dict(
            positions=[dict(id=1,market="KRW-SIM",entry_at=ENTRY)],
            executions=[dict(id=1,position_id=1,side="BUY")])),
            trend=dict(ledger=dict(
            positions=[dict(id=1,market="KRW-SIM",entry_at=ENTRY)],
            executions=[dict(id=1,position_id=1,side="BUY")])))
        return {"stock":stock,"coin":coin}

    def test_all_eight_wallets_distinct_and_reboot_stable(self):
        packet=self.sample_packet()
        uid.annotate_packet(self.root,packet)
        def snapshot(p):
            vals={}
            for owner,ledger in uid._walk(p):
                for pos in (ledger.get("positions") or []):
                    vals[owner]=pos["position_uid"]
            return vals
        first=snapshot(packet)
        self.assertEqual(len(first),8)
        self.assertEqual(len(set(first.values())),8)
        self.assertTrue(all(str(x).count("-")==1 for x in first.values()))
        self.assertEqual(
            packet["stock"]["at"]["ledger"]["executions"][0]["position_uid"],
            first["stock/at"])
        self.assertEqual(
            packet["stock"]["at"]["ledger"]["executions"][1]["position_uid"],
            first["stock/at"])
        second=self.sample_packet()
        uid.annotate_packet(self.root,second)
        self.assertEqual(first,snapshot(second))

    def test_reused_native_id_with_different_immutable_buy_receipt_gets_new_uid(self):
        with uid._LOCK:
            with_uid=uid.Registry(self.root)
            try:
                a=with_uid.assign("coin/ma","native:1","buy-receipt-1")
                b=with_uid.assign("coin/ma","native:1","buy-receipt-1")
                c=with_uid.assign("coin/ma","native:1","buy-receipt-2")
                self.assertEqual(a,b)
                self.assertNotEqual(b,c)
            finally:with_uid.close()
            other=uid.Registry(self.root)
            try:self.assertEqual(c,other.assign("coin/ma","native:1","buy-receipt-2"))
            finally:other.close()

    def test_unlinked_fill_is_flagged_not_linked_to_another_same_symbol(self):
        p={"coin":{"ma":{"ledger":{
            "positions":[dict(id=2,market="KRW-CFG",entry_at=ENTRY)],
            "executions":[dict(id=100,side="SELL",market="KRW-CFG",at=ENTRY)]
        }}}}
        uid.annotate_packet(self.root,p)
        ledger=p["coin"]["ma"]["ledger"]
        self.assertEqual(ledger["executions"][0]["position_uid_status"],"UNLINKED_EXECUTION")
        self.assertNotEqual(ledger["executions"][0]["position_uid"],
                            ledger["positions"][0]["position_uid"])

    def test_historical_closed_positions_backfilled_without_changing_original_source(self):
        db=self.root/"coin/ma_epoch_v067/upbit_paper.sqlite3"
        db.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(db) as c:
            c.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY,status TEXT);
                INSERT INTO positions VALUES(1,'CLOSED');
                INSERT INTO positions VALUES(2,'OPEN');
                CREATE TABLE executions(id INTEGER PRIMARY KEY,
                    position_id INTEGER,side TEXT);
                INSERT INTO executions VALUES(8,1,'BUY');
                INSERT INTO executions VALUES(9,2,'BUY');""")
        original=hashlib.sha256(db.read_bytes()).hexdigest()
        counts=uid.backfill_native(self.root)
        self.assertEqual(counts["wallet_position_counts"]["coin/ma"],2)
        # Read-only WAL scan leaves the original source rows in place.
        self.assertEqual(rows(db,"SELECT id,status FROM positions"),
                         [(1,"CLOSED"),(2,"OPEN")])
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(),original)

class CFGSingleException(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="artitrade-015-cfg-")
        self.addCleanup(self.tmp.cleanup)
        self.root=make_root(Path(self.tmp.name))

    def test_only_one_reported_position_is_removed_other_cfg_survives(self):
        db=setup_cfg(self.root,second=True)
        result=cfg._apply_one(db,self.root)
        self.assertEqual(result["removed"],1)
        self.assertEqual(result["position_id"],1)
        self.assertEqual(rows(db,"SELECT id,market FROM positions"),[(2,"KRW-CFG")])
        self.assertEqual(rows(db,"SELECT id,position_id FROM executions"),[(2,2)])
        self.assertEqual(rows(db,"SELECT id FROM candidates"),[(2,)])
        self.assertEqual(rows(db,"SELECT id FROM orders"),[(2,)])
        self.assertEqual(rows(db,"SELECT cash,realized FROM wallets"),[(1999499,0)])
        self.assertEqual(rows(db,"SELECT cash_after FROM executions WHERE id=2"),[(1999499,)])

    def test_multiple_matching_cfg_early_positions_are_ambiguity_not_purge(self):
        db=setup_cfg(self.root,second=True,another_early=True)
        original=rows(db,"SELECT id FROM positions")
        result=cfg._apply_one(db,self.root)
        self.assertEqual(result["removed"],0)
        self.assertIn("AMBIGUOUS",result["reason"])
        self.assertEqual(rows(db,"SELECT id FROM positions"),original)

    def test_live_cfg_is_protected(self):
        db=setup_cfg(self.root,second=False,real=True)
        result=cfg._apply_one(db,self.root)
        self.assertEqual(result["removed"],0)
        self.assertEqual(rows(db,"SELECT COUNT(*) FROM executions"),[(1,)])

    def test_verified_witness_rebase_repeated_boot_no_additional_delete(self):
        db=setup_cfg(self.root,second=True)
        initial=guard.verify_and_checkpoint(self.root,require_existing=False)
        self.assertEqual(initial["status"],"MONOTONIC_PAPER_EPOCH_OK")
        first=cfg.run_once(str(self.root))
        self.assertEqual(first["status"],"DELETED_ONE")
        self.assertEqual(first["removed"],1)
        self.assertEqual(cfg.run_once(str(self.root))["status"],"ALREADY_DELETED")
        self.assertEqual(guard.verify_and_checkpoint(
            self.root,require_existing=True)["status"],"MONOTONIC_PAPER_EPOCH_OK")
        self.assertEqual(rows(db,"SELECT id FROM positions"),[(2,)])

    def test_no_unknown_phone_position_does_not_create_a_fake_fill(self):
        db=setup_cfg(self.root,second=False,real=True)
        guard.verify_and_checkpoint(self.root,require_existing=False)
        result=cfg.run_once(str(self.root))
        self.assertEqual(result["status"],"SKIPPED")
        self.assertFalse((self.root.parent/"ARTITRADE_CFG_V015_DONE.json").exists())
        self.assertEqual(rows(db,"SELECT COUNT(*) FROM executions"),[(1,)])

if __name__=="__main__":unittest.main(verbosity=2)
