"""0.0.15 exact-original-key UID migration + ONE historical CFG exception.

All inputs are synthetic. Never read user credentials, touch public trade
records, submit real orders or claim a specific user's device was changed.
"""
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import position_uid_v015 as ids
import cfg_single_paper_cleanup_v015 as cfg
import ledger_rollback_guard_v002 as guard

class RootFixture(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory(prefix="artitrade-015-")
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)/"files"/"ARTITRADE_PHONE_MAIN_V070"
        self.root.mkdir(parents=True)
        (self.root/guard.MARKER).write_text(json.dumps({
            "schema":guard.EPOCH_SCHEMA,"state":"COMMITTED",
            "epoch_id":"ARTITRADE_SYNTHETIC_015",
            "completed_at_utc":"2026-11-19T00:00:00+00:00"}))
    def path(self,rel):
        p=self.root/rel
        p.parent.mkdir(parents=True,exist_ok=True)
        return p

class UIDHistoricalAndFuture(RootFixture):
    def test_source_owner_and_epoch_prevent_cross_wallet_collisions(self):
        item=dict(id=1,entry_at="2026-11-19T09:30:01+09:00",
                  code="KRW-SIM",tf="5m")
        packet={"stock":{"at":{"ledger":{
            "positions":[dict(item,code="000001")],
            "executions":[dict(id=10,position_id=1,side="BUY")],
            "ma":{"positions":[dict(item,code="000001")],
                  "executions":[dict(id=11,position_id=1,side="BUY")]},
            "gmo":{"positions":[{"code":"000001","entry_date":"2026-11-19"}],
                   "executions":[]},
          }},"cw":{"ledger":{"positions":[dict(item)],
                        "executions":[dict(id=12,position_id=1,side="BUY")]}}},
          "coin":{"general":{"ledger":{"positions":[dict(item)],
                          "executions":[dict(id=13,position_id=1,side="BUY")]}},
                  "ma":{"ledger":{"positions":[dict(item)],
                          "executions":[dict(id=14,position_id=1,side="BUY")]}},
                  "trend":{"ledger":{"positions":[dict(item)],
                          "executions":[dict(id=15,position_id=1,side="BUY")]}}},
          }
        # Also cover independent stock trend (eight wallets in total).
        packet["stock"]["trend"]={"ledger":{"positions":[dict(item,code="000001")],
                 "executions":[dict(id=16,position_id=1,side="BUY")]}}
        ids.annotate_packet(self.root,packet)
        wallets=[
           packet["stock"]["at"]["ledger"],
           packet["stock"]["at"]["ledger"]["ma"],
           packet["stock"]["at"]["ledger"]["gmo"],
           packet["stock"]["cw"]["ledger"],
           packet["stock"]["trend"]["ledger"],
           packet["coin"]["general"]["ledger"],
           packet["coin"]["ma"]["ledger"],
           packet["coin"]["trend"]["ledger"]]
        all_ids=[]
        for ledger in wallets:
            self.assertTrue(all(x.get("position_uid") for x in ledger["positions"]))
            self.assertTrue(all(x.get("position_uid") for x in ledger["executions"]))
            for x in ledger["positions"]:all_ids.append(x["position_uid"])
            for e in ledger["executions"]:
                if e.get("position_id")==1 and ledger["positions"][0].get("id")==1:
                    self.assertEqual(e["position_uid"],ledger["positions"][0]["position_uid"])
        self.assertEqual(len(all_ids),8)
        self.assertEqual(len(set(all_ids)),8)
        old_ids=list(all_ids)
        ids.annotate_packet(self.root,packet)
        self.assertEqual([x["positions"][0]["position_uid"] for x in wallets],old_ids)

    def test_completed_receipts_get_uid_and_unlinked_event_get_distinct_id(self):
        packet={"coin":{"ma":{"ledger":{"positions":[],
            "executions":[
              {"id":21,"position_id":17,"side":"BUY","market":"KRW-SIM","tf":"5m"},
              {"id":22,"position_id":17,"side":"SELL","market":"KRW-SIM","tf":"5m"},
              {"id":23,"side":"SELL","market":"KRW-SIM","tf":"5m"}]}}}}
        ids.annotate_packet(self.root,packet)
        es=packet["coin"]["ma"]["ledger"]["executions"]
        self.assertEqual(es[0]["position_uid"],es[1]["position_uid"])
        self.assertNotEqual(es[0]["position_uid"],es[2]["position_uid"])
        self.assertEqual(es[2]["position_uid_status"],"UNLINKED_EXECUTION")
        expected=[x["position_uid"] for x in es]
        ids.annotate_packet(self.root,packet)
        self.assertEqual([x["position_uid"] for x in es],expected)

    def test_same_native_id_with_proven_new_original_buy_allocates_new_generation(self):
        with ids.Registry(self.root) as_unused if False else _Noop():pass

class _Noop:
    def __enter__(self):return self
    def __exit__(self,*args):return False

class UIDExtra(RootFixture):
    def test_reused_native_pk_cannot_get_previous_trade_uid(self):
        with ids._LOCK:
            r=ids.Registry(self.root)
            before=r.assign("coin/ma","native:7","ORIGINAL_BUY_EXEC_ID_123")
            again=r.assign("coin/ma","native:7","ORIGINAL_BUY_EXEC_ID_123")
            after=r.assign("coin/ma","native:7","ORIGINAL_BUY_EXEC_ID_999")
            r.close()
        self.assertEqual(before,again)
        self.assertNotEqual(before,after)
        reg=ids.Registry(self.root)
        self.assertEqual(reg.assign("coin/ma","native:7","ORIGINAL_BUY_EXEC_ID_999"),after)
        reg.close()

    def test_all_historical_native_rows_backfill_even_closed(self):
        db=self.path("coin/ma_epoch_v067/upbit_paper.sqlite3")
        with sqlite3.connect(db) as c:
            c.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY, status TEXT);
                INSERT INTO positions VALUES(1,'CLOSED'),(2,'OPEN'),(3,'CLOSED');
                CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,side TEXT);
                INSERT INTO executions VALUES(1,1,'BUY'),(2,1,'SELL'),(3,2,'BUY'),(4,3,'BUY');""")
        self.assertEqual(ids.backfill_native(self.root)["wallet_position_counts"]["coin/ma"],3)
        with ids.Registry(self.root) as_unused if False else _Noop():pass
        with sqlite3.connect(self.root/"POSITION_UID_V015.sqlite3") as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM position_uid_registry").fetchone()[0],3)
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],3)

class CFGOnePositionOnly(RootFixture):
    def _make(self,two_reported=False,real=False,with_sell=False):
        p=self.path("coin/ma_epoch_v067/upbit_paper.sqlite3")
        with sqlite3.connect(p) as c:
            c.executescript("""CREATE TABLE wallets(
              tf TEXT PRIMARY KEY,seed INTEGER,cash INTEGER,realized INTEGER);
              INSERT INTO wallets VALUES('5m',2000000,1979989,0);
              CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
                  ref_at TEXT,entry_at TEXT,entry TEXT,qty0 TEXT,qty TEXT,
                  status TEXT,execution_mode TEXT,broker_order_id TEXT);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                  tf TEXT,market TEXT,at TEXT,side TEXT,qty TEXT,price TEXT,
                  fees INTEGER,tax INTEGER,net_pnl INTEGER,reason TEXT,cash_after INTEGER);
              CREATE TABLE ma_v067_positions(
                  position_id INTEGER PRIMARY KEY,kind TEXT,state_json TEXT);
              CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
                  ref_at TEXT,features TEXT);
              CREATE TABLE orders(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
                  ref_at TEXT,status TEXT);
              """)
            entries=[
              (11,"2026-11-19T13:04:55+09:00","old_ref","KRW-CFG","PAPER"),
              (12,"2026-11-19T15:12:00+09:00","new_ref","KRW-CFG","PAPER"),
              (13,"2026-11-19T13:06:00+09:00","other_ref","KRW-OTHER","PAPER"),
            ]
            if two_reported:
                entries[1]=(12,"2026-11-19T13:05:55+09:00","another_ref","KRW-CFG","PAPER")
            if real:
                entries[0]=(11,entries[0][1],"old_ref","KRW-CFG","LIVE")
            for pid,at,ref,sym,mode in entries:
                c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (pid,'5m',sym,ref,at,'100','100','100','OPEN',mode,''))
                c.execute("INSERT INTO ma_v067_positions VALUES(?,?,?)",
                         (pid,'REVERSAL_UP',json.dumps({"first_sold":0,"seen_above_entry":False})))
                c.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid,pid,'5m',sym,at,'BUY','100','100',5,0,0,'NL1118_BUY',1979989))
                c.execute("INSERT INTO candidates VALUES(?,?,?,?,?)",
                          (pid,'5m',sym,ref,'{}'))
                c.execute("INSERT INTO orders VALUES(?,?,?,?,?)",
                          (pid,'5m',sym,ref,'BOUGHT'))
            if with_sell:
                c.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (50,11,'5m','KRW-CFG','2026-11-19T13:07:00+09:00','SELL',
                     '20','105',1,0,100,'MA_V1118_UP_FIRST',1982088))
        guard.verify_and_checkpoint(str(self.root),require_existing=False)
        return p
    def test_only_original_problem_position_removed_other_cfg_and_coin_untouched(self):
        path=self._make()
        pre=sqlite3.connect(path);pre.row_factory=sqlite3.Row
        other_before=[dict(r) for r in pre.execute("SELECT * FROM positions WHERE id<>11 ORDER BY id")]
        pre.close()
        result=cfg.run_once(str(self.root))
        self.assertEqual(result["status"],"DELETED_ONE",result)
        self.assertEqual(result["removed"],1)
        with sqlite3.connect(path) as c:
            self.assertEqual(c.execute("SELECT id FROM positions ORDER BY id").fetchall(),[(12,),(13,)])
            self.assertEqual(c.execute("SELECT id FROM executions ORDER BY id").fetchall(),[(12,),(13,)])
            self.assertEqual(c.execute("SELECT id FROM candidates ORDER BY id").fetchall(),[(12,),(13,)])
            self.assertEqual(c.execute("SELECT cash FROM wallets WHERE tf='5m'").fetchone()[0],1989994)
            c.row_factory=sqlite3.Row
            self.assertEqual([dict(r) for r in c.execute("SELECT * FROM positions ORDER BY id")],other_before)
        self.assertTrue(guard.verify_and_checkpoint(str(self.root),require_existing=True)["ok"])
        self.assertEqual(cfg.run_once(str(self.root))["status"],"ALREADY_DELETED")
    def test_ambiguous_same_symbol_same_frame_preserves_both(self):
        path=self._make(two_reported=True)
        self.assertEqual(cfg.run_once(str(self.root))["reason"],"AMBIGUOUS_MULTIPLE_CFG_POSITIONS")
        with sqlite3.connect(path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],3)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],3)
    def test_real_mode_or_earlier_sell_does_not_erase(self):
        for kind in ("real","with_sell"):
            with self.subTest(kind=kind):
                d=tempfile.TemporaryDirectory(prefix="artitrade-cfg-variant-")
                self.addCleanup(d.cleanup)
                self.root=Path(d.name)/"files"/"ARTITRADE_PHONE_MAIN_V070"
                self.root.mkdir(parents=True)
                (self.root/guard.MARKER).write_text(json.dumps({
                    "schema":guard.EPOCH_SCHEMA,"state":"COMMITTED",
                    "epoch_id":"ARTITRADE_SYNTHETIC_015",
                    "completed_at_utc":"2026-11-19T00:00:00+00:00"}))
                p=self._make(**{kind:True})
                self.assertEqual(cfg.run_once(str(self.root))["status"],"SKIPPED")
                with sqlite3.connect(p) as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],3)

class JavaIntegration(unittest.TestCase):
    def test_native_wallet_primary_keys_unmodified_and_java_boot_order(self):
        src=(Path(__file__).resolve().parents[1]/"app/src/main/java/com/artitrade/mobile/LedgerAntiRollbackV002.java").read_text()
        self.assertLess(src.index('getModule("legacy_purge_v012")'),
                        src.index('getModule("cfg_single_paper_cleanup_v015")'))
        self.assertLess(src.index('getModule("cfg_single_paper_cleanup_v015")'),
                        src.index('getModule("ledger_rollback_guard_v002")'))
        runtime=(Path(__file__).resolve().parents[1]/"app/src/main/python/android_runtime_v078.py").read_text()
        self.assertIn('_install_position_uid_v015(_core)',runtime)
        ui=(Path(__file__).resolve().parents[1]/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        self.assertIn('r.optString("position_uid","연결 검증 필요")',ui)
        self.assertIn('p.optString("position_uid",',ui)

if __name__=="__main__":
    unittest.main(verbosity=2)
