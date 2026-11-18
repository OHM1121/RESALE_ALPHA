"""No broker, no APK, no phone. Test one-epoch 8-wallet rollback defense."""
import datetime as dt
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ledger_rollback_guard_v002 import (
    CHECKPOINT, RollbackBlocked, verify_and_checkpoint, verify_json)

MARKER="ARTITRADE_FRESH_EPOCH_V106.json"
DBS=(
 "stock/at/at.sqlite3",
 "stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
 "stock/cw/at_close.sqlite3",
 "stock/gmo/gmo.sqlite3",
 "stock/trend/trend.sqlite3",
 "coin/base/upbit_paper.sqlite3",
 "coin/ma/upbit_ma.sqlite3",
 "coin/trend/trend.sqlite3",
)

class AntiRollbackEightWallets(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)/"files"/"ARTITRADE_PHONE_MAIN_V070"
        self.root.mkdir(parents=True)
        self.start=dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=2)
        self.time1=(self.start+dt.timedelta(minutes=10)).isoformat()
        self.time2=(self.start+dt.timedelta(minutes=11)).isoformat()
        (self.root/MARKER).write_text(json.dumps({
            "schema":"ARTITRADE_FRESH_PAPER_EPOCH_V106",
            "epoch_id":"original-first-paper-epoch-001",
            "state":"COMMITTED",
            "completed_at_utc":self.start.isoformat()
        }),encoding="utf8")
        for rel in DBS:
            db=self.root/rel
            db.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(db) as c:
                c.execute("""CREATE TABLE executions(
                    id INTEGER PRIMARY KEY,ts TEXT,side TEXT,code TEXT,
                    qty INTEGER,price REAL)""")
                c.execute("INSERT INTO executions VALUES(1,?,?,?,?,?)",
                          (self.time1,"BUY",rel,1,100.0))

    def test_all_eight_wallet_checkpoints_are_monotonic(self):
        first=verify_and_checkpoint(self.root)
        self.assertEqual(first["tables_checked"],8)
        self.assertTrue(first["checkpoint_advanced"])
        second=verify_and_checkpoint(self.root,require_existing=True)
        self.assertFalse(second["checkpoint_advanced"])
        self.assertEqual(len(json.loads((self.root.parent/CHECKPOINT)
                                      .read_text())["tables"]),8)
        self.assertEqual(json.loads(verify_json(self.root))["status"],
                         "MONOTONIC_PAPER_EPOCH_OK")

    def test_legitimate_new_buy_sell_advanced_and_preserved(self):
        verify_and_checkpoint(self.root)
        db=self.root/DBS[1]
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO executions VALUES(2,?,?,?,?,?)",
                      (self.time2,"SELL",DBS[1],1,105.0))
        result=verify_and_checkpoint(self.root,require_existing=True)
        self.assertTrue(result["checkpoint_advanced"])
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],2)

    def test_old_backup_copy_replacing_db_blocks_before_trading(self):
        db=self.root/DBS[0]
        snapshot=self.root.parent/"old_copy.sqlite3"
        shutil.copyfile(db,snapshot)
        verify_and_checkpoint(self.root)
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO executions VALUES(2,?,?,?,?,?)",
                      (self.time2,"SELL",DBS[0],1,105.0))
        verify_and_checkpoint(self.root,require_existing=True)
        shutil.copyfile(snapshot,db)
        with self.assertRaisesRegex(RollbackBlocked,"ROLLED_BACK"):
            verify_and_checkpoint(self.root,require_existing=True)

    def test_same_row_count_different_old_execution_blocks(self):
        verify_and_checkpoint(self.root)
        db=self.root/DBS[2]
        with sqlite3.connect(db) as c:
            c.execute("UPDATE executions SET price=12 WHERE id=1")
        with self.assertRaisesRegex(RollbackBlocked,"SAME_SIZE_DIFFERENT"):
            verify_and_checkpoint(self.root,require_existing=True)

    def test_deleted_original_execution_db_is_not_reset(self):
        verify_and_checkpoint(self.root)
        (self.root/DBS[3]).unlink()
        with self.assertRaisesRegex(RollbackBlocked,"DISAPPEARED"):
            verify_and_checkpoint(self.root,require_existing=True)

    def test_missing_independent_checkpoint_blocks_rebaseline(self):
        verify_and_checkpoint(self.root)
        (self.root.parent/CHECKPOINT).unlink()
        with self.assertRaisesRegex(RollbackBlocked,"WITNESS_REMOVED"):
            verify_and_checkpoint(self.root,require_existing=True)

    def test_old_epoch_trade_cannot_be_imported_with_new_filename(self):
        verify_and_checkpoint(self.root)
        db=self.root/"coin"/"base"/"restored_old.sqlite3"
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE fills(id INTEGER PRIMARY KEY,ts TEXT,side TEXT)")
            before=(self.start-dt.timedelta(days=4)).isoformat()
            c.execute("INSERT INTO fills VALUES(1,?,?)",(before,"BUY"))
        with self.assertRaisesRegex(RollbackBlocked,"PREVIOUS_EPOCH_EXECUTION"):
            verify_and_checkpoint(self.root,require_existing=True)

    def test_research_old_backtest_trades_are_not_live_execution_proof(self):
        verify_and_checkpoint(self.root)
        db=self.root/"stock"/"research"/"historic_research.sqlite3"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE fills(id INTEGER PRIMARY KEY,ts TEXT)")
            c.execute("INSERT INTO fills VALUES(1,?)",
                      ((self.start-dt.timedelta(days=200)).isoformat(),))
        self.assertEqual(
            verify_and_checkpoint(self.root,require_existing=True)["status"],
            "MONOTONIC_PAPER_EPOCH_OK")

    def test_marker_replaced_to_different_epoch_blocks(self):
        verify_and_checkpoint(self.root)
        data=json.loads((self.root/MARKER).read_text())
        data["epoch_id"]="OLD_EPOCH"
        (self.root/MARKER).write_text(json.dumps(data))
        with self.assertRaisesRegex(RollbackBlocked,"CHECKPOINT_INVALID_OR_REPLACED"):
            verify_and_checkpoint(self.root,require_existing=True)

    def test_guard_never_alters_live_or_original_trade_rows(self):
        db=self.root/DBS[0]
        with sqlite3.connect(db) as c:
            c.execute("ALTER TABLE executions ADD COLUMN execution_mode TEXT")
            c.execute("UPDATE executions SET execution_mode='PAPER'")
        before=db.read_bytes()
        verify_and_checkpoint(self.root)
        after=db.read_bytes()
        self.assertEqual(before,after)

if __name__=="__main__":unittest.main()
