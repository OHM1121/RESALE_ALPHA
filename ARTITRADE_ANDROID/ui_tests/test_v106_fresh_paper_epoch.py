"""v106 source-only fresh PAPER epoch tests. Never run against a phone.

All destructive tests run strictly inside TemporaryDirectory/files/ARTITRADE_PHONE_MAIN_V070.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fresh_epoch_reset_v106 import (
    CONFIRM, MARKER, ResetBlocked, preview, reset
)

ANDROID = Path(__file__).resolve().parents[1] / "app/src/main"
JAVA = ANDROID / "java/com/artitrade/mobile"


class FreshEpochV106(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="artitrade_v106_safe_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "files" / "ARTITRADE_PHONE_MAIN_V070"
        self.root.mkdir(parents=True)
        (self.root / "ANDROID_OPERATION_V074.json").write_text(
            json.dumps({"stock": {"at": {"seed": 9000000}}, "coin": {}}))
        self.create_db("stock/ARTITRADE_D.sqlite3", "PAPER")
        self.create_db("stock/close/AT_CLOSE_PAPER.sqlite3", "PAPER")
        self.create_db("coin/base/upbit_paper.sqlite3", "PAPER")
        self.create_db("coin/trend/market_trend_wallet_v054.sqlite3", "PAPER")
        self.create_db("market_trend_v043.sqlite3", "PAPER")
        (self.root / "recovery_scenarios_v092.json").write_text('{"scenario":"OLD"}')
        (self.root / "coin/base/upbit_paper.sqlite3.pre_pc_import_20261118").write_bytes(b"old")
        (self.root / "stock/ARTITRADE_D.sqlite3-wal").write_bytes(b"old-wal")
        (self.root / "stock/private_token.json").write_text("do-not-touch")

    def create_db(self, name, mode):
        db = self.root / name
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE positions(id INTEGER PRIMARY KEY, execution_mode TEXT, broker_order_id TEXT)")
            c.execute("INSERT INTO positions(execution_mode,broker_order_id) VALUES(?,?)", (mode, ""))
            c.execute("CREATE TABLE executions(side TEXT,execution_mode TEXT)")
            c.execute("INSERT INTO executions VALUES('BUY',?)", (mode,))
        return db

    def execute(self, **kwargs):
        params = {"confirmation": CONFIRM, "engine_stopped": True,
                  "live_orders_disarmed": True}
        params.update(kwargs)
        return reset(self.root, **params)

    def test_preview_is_read_only(self):
        prior = sorted((x.relative_to(self.root).as_posix() for x in self.root.rglob("*") if x.is_file()))
        p = preview(self.root)
        self.assertEqual(p["status"], "PREVIEW_ONLY")
        self.assertGreaterEqual(p["delete_count"], 7)
        self.assertIn("ANDROID_OPERATION_V074.json", p["files_preserved"])
        after = sorted((x.relative_to(self.root).as_posix() for x in self.root.rglob("*") if x.is_file()))
        self.assertEqual(prior, after)
        self.assertFalse((self.root / MARKER).exists())

    def test_user_must_explicitly_approve_offline_disarmed_reset(self):
        for param in (
            {"confirmation": "yes"},
            {"engine_stopped": False},
            {"live_orders_disarmed": False},
        ):
            with self.subTest(param=param), self.assertRaises(ResetBlocked):
                self.execute(**param)
            self.assertTrue((self.root / "stock/ARTITRADE_D.sqlite3").exists())
            self.assertFalse((self.root / MARKER).exists())

    def test_actual_reset_only_erases_paper_history_and_research(self):
        cfg = (self.root / "ANDROID_OPERATION_V074.json").read_text()
        result = self.execute()
        self.assertEqual(result["status"], "COMMITTED")
        self.assertGreater(result["removed_files"], 5)
        self.assertTrue(result["pc_auto_import_blocked"])
        self.assertFalse((self.root / "stock/ARTITRADE_D.sqlite3").exists())
        self.assertFalse((self.root / "stock/ARTITRADE_D.sqlite3-wal").exists())
        self.assertFalse((self.root / "coin/base/upbit_paper.sqlite3").exists())
        self.assertFalse((self.root / "coin/base/upbit_paper.sqlite3.pre_pc_import_20261118").exists())
        self.assertFalse((self.root / "market_trend_v043.sqlite3").exists())
        self.assertFalse((self.root / "recovery_scenarios_v092.json").exists())
        self.assertEqual((self.root / "ANDROID_OPERATION_V074.json").read_text(), cfg)
        self.assertEqual((self.root / "stock/private_token.json").read_text(), "do-not-touch")
        m = json.loads((self.root / MARKER).read_text())
        self.assertEqual(m["state"], "COMMITTED")
        self.assertEqual(m["mode"], "PAPER_ONLY")

    def test_repeated_reset_cannot_erase_newly_accumulated_records(self):
        first = self.execute()
        db = self.create_db("coin/base/upbit_paper.sqlite3", "PAPER")
        second = self.execute()
        self.assertEqual(second["status"], "ALREADY_COMMITTED")
        self.assertEqual(first["epoch_id"], second["epoch_id"])
        self.assertTrue(db.is_file())

    def test_live_execution_is_never_erased(self):
        with sqlite3.connect(self.root / "stock/ARTITRADE_D.sqlite3") as c:
            c.execute("UPDATE positions SET execution_mode='LIVE'")
        with self.assertRaisesRegex(ResetBlocked, "REAL_EXECUTION_MODE_PRESENT"):
            self.execute()
        self.assertFalse((self.root / MARKER).exists())
        self.assertTrue((self.root / "coin/base/upbit_paper.sqlite3").exists())

    def test_broker_reference_is_never_erased(self):
        with sqlite3.connect(self.root / "coin/base/upbit_paper.sqlite3") as c:
            c.execute("UPDATE positions SET broker_order_id='external-order-id'")
        with self.assertRaisesRegex(ResetBlocked, "BROKER_ORDER_PROVENANCE_PRESENT"):
            self.execute()
        self.assertFalse((self.root / MARKER).exists())

    def test_unreadable_sqlite_never_authorizes_wipe(self):
        self.create_db("coin/ma/upbit_paper.sqlite3", "PAPER")
        (self.root / "coin/ma/upbit_paper.sqlite3").write_bytes(b"not-SQLite")
        with self.assertRaisesRegex(ResetBlocked, "UNVERIFIABLE_SQLITE"):
            self.execute()
        self.assertFalse((self.root / MARKER).exists())

    def test_pending_marker_must_stay_until_reset_finishes(self):
        value = {"schema": "ARTITRADE_FRESH_PAPER_EPOCH_V106",
                 "state": "PREPARING", "epoch_id": "v106-incomplete"}
        (self.root / MARKER).write_text(json.dumps(value))
        result = self.execute()
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(result["epoch_id"], "v106-incomplete")

    def test_unknown_phone_path_never_reset(self):
        other = Path(self.temp.name) / "elsewhere"
        other.mkdir()
        with self.assertRaisesRegex(ResetBlocked, "EXACT_ANDROID_PRIVATE"):
            reset(other, confirmation=CONFIRM, engine_stopped=True,
                  live_orders_disarmed=True)

    def test_java_gates_auto_import_and_clears_derived_only(self):
        java = (JAVA / "FreshEpochGateV106.java").read_text()
        svc = (JAVA / "UnifiedTradingService.java").read_text()
        self.assertIn("NO_PC_IMPORT", java)
        self.assertIn("FRESH_EPOCH_BLOCKED", java)
        self.assertIn('"unified_live_orders"', java)
        self.assertIn('"artitrade_local_records_v075"', java)
        self.assertIn('"artitrade_manual_sale_v085"', java)
        self.assertIn("LargeRecordStoreV087.store(c).delete()", java)
        self.assertNotIn('"unified_runtime"', java)
        self.assertNotIn('"unified_operation_v074"', java)
        self.assertIn("FreshEpochGateV106.migrationGate(this)", svc)
        self.assertNotIn("new LegacyLedgerMigration(this,crashes)", svc)
        self.assertNotIn("importer.finishRuntimeVerification", svc)
        self.assertFalse((JAVA / "LegacyLedgerMigration.java").exists())
        self.assertLess(svc.index("FreshEpochGateV106.clearDerivedCachesIfNeeded(this);"),
                        svc.index("JSONObject started=strategy.start();"))
        self.assertIn("if(FreshEpochGateV106.hasMarker(this))return;", svc)
        self.assertIn('"FRESH_EPOCH_BLOCKED".equals(epochState)', svc)
        self.assertIn("NO_OLD_PC_LEDGER_IN_FRESH_EPOCH", svc)


if __name__ == "__main__":
    unittest.main()
