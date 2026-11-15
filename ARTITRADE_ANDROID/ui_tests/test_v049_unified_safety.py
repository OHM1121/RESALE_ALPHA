"""v0.7.49 regression: single unified client, safe live gating, backups and KRX session."""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "app/src/main/python"))
import android_ledger_backup as backup
import krx_session_v041 as krx
import market_trend_v043 as mt
import market_trend_runtime_v043 as runtime
import market_trend_wallet_v043 as wallet

JAVA = ROOT / "app/src/main/java/com/artitrade/mobile"
PY = ROOT / "app/src/main/python"


class V049UnifiedSafety(TestCase):
    def test_original_update_identity_and_unified_launcher(self):
        gradle = (ROOT / "app/build.gradle").read_text(encoding="utf-8")
        manifest = (ROOT / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        bridge = (JAVA / "StrategyRuntimeBridge.java").read_text(encoding="utf-8")
        self.assertIn("com.artitrade.unified.s21dex", gradle)
        self.assertIn('launcherActivity: ".UnifiedVisualActivity"', gradle)
        self.assertIn('launcherActivity', manifest)
        self.assertIn('getModule("android_runtime_v078")', bridge)
        self.assertIn("ARTITRADE_PHONE_MAIN_V070", bridge)

    def test_live_requires_verified_lease_and_explicit_rearm(self):
        service = (JAVA / "UnifiedTradingService.java").read_text(encoding="utf-8")
        bridge = (JAVA / "StrategyRuntimeBridge.java").read_text(encoding="utf-8")
        self.assertIn('putBoolean("live_stock",false)', bridge)
        self.assertIn('putBoolean("live_coin",false)', bridge)
        self.assertIn('LIVE_RUNTIME_NOT_READY', bridge)
        self.assertIn('Python did not confirm requested live mode', bridge)
        self.assertIn('Python did not confirm disarmed startup', bridge)
        self.assertIn('PHONE_LEASE_EPOCH_MISSING', service)
        self.assertIn('LIVE_RUNTIME_ARM_FAILED', service)
        self.assertIn('MSERVER_STATUS_UNREACHABLE', service)
        self.assertIn('MSERVER_STATUS_UNVERIFIED', service)
        self.assertIn('private void disarmUnverifiedMarkets(', service)
        self.assertIn('if(status==null||!status.optBoolean("ok"))return 0;', service)
        self.assertIn('}catch(Exception e){return 0;}', service)
        self.assertNotIn('cc?35:0,System.currentTimeMillis()', service)

    def test_backup_validation_accepts_exact_shared_source_journals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "phone"
            root.mkdir()
            output = Path(temp) / "result.zip"
            with sqlite3.connect(root / "market_trend_v043.sqlite3") as db:
                db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY)")
                db.execute("INSERT INTO events VALUES(1)")
            with sqlite3.connect(root / "market_research_v033.sqlite3") as db:
                db.execute("CREATE TABLE scores(v INTEGER)")
                db.execute("INSERT INTO scores VALUES(-10)")
            with sqlite3.connect(root / "phone_buy_evidence_v035.sqlite3") as db:
                db.execute("CREATE TABLE samples(id INTEGER)")
                db.execute("INSERT INTO samples VALUES(99)")
            (root / "ANDROID_OPERATION_V074.json").write_text(
                json.dumps({"stock":{},"coin":{}}),encoding="utf-8")
            result=json.loads(backup.make_backup(str(root),str(output)))
            self.assertTrue(result["ok"])
            verification=backup.validate_backup(str(output))
            self.assertTrue(verification["ok"])
            self.assertEqual(verification["files"],4)

    def test_normal_vs_special_krx_preclose_slots(self):
        normal=dt.datetime(2026,11,18,15,19,48,tzinfo=mt.KST)
        special=dt.datetime(2026,11,19,16,19,48,tzinfo=mt.KST)
        end_normal=runtime._end_clock(normal,"stock","day")
        end_special=runtime._end_clock(special,"stock","day")
        self.assertEqual((end_normal.hour,end_normal.minute),(15,30))
        self.assertEqual((end_special.hour,end_special.minute),(16,30))
        for frame in ("30m","60m","day"):
            self.assertTrue(mt.preclose_due(normal,end_normal,frame,"stock"))
            self.assertTrue(mt.preclose_due(special,end_special,frame,"stock"))
            self.assertTrue(mt.live_rsi_check_due(
                special-dt.timedelta(seconds=3),end_special,frame,"stock"))
            self.assertFalse(mt.preclose_due(
                special+dt.timedelta(minutes=1),end_special,frame,"stock"))
        self.assertEqual(krx.SPECIAL_SESSIONS["2026-11-19"],(600,990))

    def test_shadow_not_broker_pending_and_signal_provenance(self):
        visual=(JAVA/"UnifiedVisualActivity.java").read_text(encoding="utf-8")
        trend=(PY/"market_trend_runtime_v043.py").read_text(encoding="utf-8")
        wallet=(PY/"market_trend_wallet_v043.py").read_text(encoding="utf-8")
        analysis=(PY/"ai_trend_analysis_v047.py").read_text(encoding="utf-8")
        self.assertIn('!candidate.optBoolean("ledger_is_virtual_only",false)',visual)
        self.assertIn('signal_mode=(',trend)
        self.assertIn('"execution_mode"]=fill["mode"]',wallet)
        self.assertIn('"UNKNOWN_FILLED_LEDGER"',analysis)

    def test_trend_live_holding_cannot_be_sold_in_paper_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=mt.TrendJournal(tmp)
            current=dt.datetime(2026,11,13,10,0,tzinfo=mt.KST)
            position=mt.TrendPosition("coin","general","5m","KRW-BTC",
                                      current,1000,1,current.isoformat())
            position.__dict__["position_id"]=7
            position.__dict__["execution_mode"]="LIVE"
            paper=wallet.TrendWallet(
                SimpleNamespace(root=tmp,live_enabled=lambda _:False),journal)
            sold,status=paper.sell(position,1050,current,"MT_TP1",bid_size=10)
            self.assertEqual(sold,0)
            self.assertEqual(status,"LIVE_POSITION_BROKER_DISABLED")
            position.__dict__["execution_mode"]="PAPER"
            live=wallet.TrendWallet(
                SimpleNamespace(root=tmp,live_enabled=lambda _:True),journal)
            sold,status=live.sell(position,1050,current,"MT_TP1",bid_size=10)
            self.assertEqual(sold,0)
            self.assertEqual(status,"PAPER_POSITION_LIVE_MODE")
            del position.__dict__["execution_mode"]
            journal.event("TEST:BUY:7","coin","general","5m","KRW-BTC",
                          "ACTUAL","MT_BUY_FILLED",payload={"position_id":7})
            self.assertEqual(paper._position_fill_mode(position),"LIVE")
