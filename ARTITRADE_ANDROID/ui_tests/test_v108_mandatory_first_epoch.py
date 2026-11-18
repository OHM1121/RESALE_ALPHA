"""Mandatory first-boot source-only fresh epoch regression. No phone touched."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from fresh_epoch_reset_v106 import MARKER,reset_once_json,preview,ResetBlocked

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
JAVA=ROOT/"java/com/artitrade/mobile"
PY=ROOT/"python"

class MandatoryPaperFreshStart(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="fresh_8_wallets_")
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"files"/"ARTITRADE_PHONE_MAIN_V070"
        self.path.mkdir(parents=True)
        (self.path/"ANDROID_OPERATION_V074.json").write_text(
            '{"stock":{"at":{"seed":10000000}}}',encoding="utf8")
        (self.path/"stock"/"private_api_secret.json").parent.mkdir(parents=True)
        (self.path/"stock"/"private_api_secret.json").write_text(
            '{"key":"unchanged"}',encoding="utf8")
        names=["stock/ARTITRADE_D.sqlite3",
               "stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
               "stock/close/AT_CLOSE_PAPER.sqlite3",
               "stock/gmo/gmo_paper.sqlite3",
               "stock/trend/market_trend_wallet_v054.sqlite3",
               "coin/base/upbit_paper.sqlite3",
               "coin/ma/ma_paper.sqlite3",
               "coin/trend/market_trend_wallet_v054.sqlite3",
               "execution_entry_plans_v107.sqlite3"]
        for name in names:
            path=self.path/name
            path.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE positions(id INT, execution_mode TEXT)")
                db.execute("INSERT INTO positions VALUES(1,'PAPER')")
                db.execute("CREATE TABLE executions(id INT, side TEXT)")
                db.execute("INSERT INTO executions VALUES(1,'BUY')")
        (self.path/"stock"/"old_pc_backup.zip").write_bytes(b"OLD")
        (self.path/"coin"/"old_ledger.bak").write_bytes(b"OLD")
        self.external=Path(self.tmp.name)/"external_backup.zip"
        self.external.write_bytes(b"USER_EXTERNAL_BACKUP")

    def test_full_first_epoch_discards_all_eight_old_wallets(self):
        result=json.loads(reset_once_json(self.path))
        self.assertEqual(result["status"],"COMMITTED")
        self.assertEqual(preview(self.path)["status"],"ALREADY_COMMITTED")
        self.assertFalse(any(self.path.rglob("*.sqlite3")))
        self.assertFalse(any(self.path.rglob("*.zip")))
        self.assertFalse(any(self.path.rglob("*.bak")))
        self.assertTrue(self.external.exists())
        self.assertTrue((self.path/"ANDROID_OPERATION_V074.json").exists())
        self.assertTrue((self.path/"stock"/"private_api_secret.json").exists())
        self.assertTrue((self.path/MARKER).exists())

    def test_second_start_preserves_new_epoch_buy_and_sell(self):
        json.loads(reset_once_json(self.path))
        db=self.path/"coin"/"base"/"upbit_paper.sqlite3"
        db.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE executions(side TEXT)")
            connection.execute("INSERT INTO executions VALUES('BUY')")
        again=json.loads(reset_once_json(self.path))
        self.assertEqual(again["status"],"ALREADY_COMMITTED")
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],1)

    def test_cold_reset_boot_before_service_and_no_pc_import(self):
        act=(JAVA/"UnifiedVisualActivity.java").read_text()
        svc=(JAVA/"UnifiedTradingService.java").read_text()
        self.assertLess(act.index("FreshEpochGateV106.mandatoryFreshStartPending(this)"),
                        act.index("startRuntimeLater();"))
        self.assertIn("reset_once_json",act)
        self.assertIn("SERVICE_WAS_ACTIVE_REOPEN_APP_FOR_COLD_RESET",act)
        self.assertIn("mandatoryFreshStartPending(this)",svc)
        self.assertNotIn("new LegacyLedgerMigration(this,crashes)",svc)
        self.assertNotIn("_install_legacy_ma_exit_v094(_core)",
                         (PY/"android_runtime_v078.py").read_text())

    def test_live_provenance_refuses_wipe(self):
        with sqlite3.connect(self.path/"stock"/"ARTITRADE_D.sqlite3") as c:
            c.execute("INSERT INTO positions VALUES(2,'LIVE')")
        with self.assertRaises(ResetBlocked):
            reset_once_json(self.path)
        self.assertFalse((self.path/MARKER).exists())

if __name__=="__main__":unittest.main()
