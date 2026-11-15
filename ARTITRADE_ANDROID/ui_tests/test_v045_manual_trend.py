"""v045 per-market manual market-trend override, without order execution."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import threading
import sys

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import market_trend_v043 as mt
import market_trend_runtime_v043 as runtime

PY=(ROOT/"app/src/main/python/market_trend_runtime_v043.py").read_text(encoding="utf-8")
UI=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf-8")
SERVICE=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedTradingService.java").read_text(encoding="utf-8")
BRIDGE=(ROOT/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java").read_text(encoding="utf-8")
GRADLE=(ROOT/"app/build.gradle").read_text(encoding="utf-8")


class V045ManualTrend(TestCase):
    def test_explicit_override_vs_automatic_score_and_failure(self):
        self.assertEqual(mt.market_mode(20),"ORIGINAL")
        self.assertEqual(mt.effective_market_mode(20,manual_trend=True),"MARKET_TREND")
        self.assertEqual(mt.effective_market_mode(-20,manual_trend=False),"MARKET_TREND")
        self.assertEqual(mt.effective_market_mode(20,manual_trend=False),"ORIGINAL")
        self.assertEqual(mt.effective_market_mode(None,stale=True),"PAUSED")
        self.assertEqual(mt.effective_market_mode(None,stale=True,
                          manual_trend=True),"MARKET_TREND")
        self.assertEqual(mt.effective_market_mode(20,market_open=False),"PAUSED")
        self.assertEqual(mt.effective_market_mode(20,market_open=False,
                          manual_trend=True),"MARKET_TREND")
        with self.assertRaises(TypeError):
            mt.effective_market_mode(20,manual_trend="false")

    def test_per_market_persistence_across_restart_without_ledger_mutation(self):
        with TemporaryDirectory() as root:
            j=mt.TrendJournal(root)
            self.assertFalse(j.manual_trend("stock"))
            self.assertFalse(j.manual_trend("coin"))
            j.set_manual_trend("stock",True)
            j.set_manual_trend("coin",False)
            again=mt.TrendJournal(root)
            self.assertTrue(again.manual_trend("stock"))
            self.assertFalse(again.manual_trend("coin"))
            again.set_manual_trend("stock",False)
            again.set_manual_trend("coin",True)
            last=mt.TrendJournal(root)
            self.assertFalse(last.manual_trend("stock"))
            self.assertTrue(last.manual_trend("coin"))
            with last.connect() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM manual_trend_mode").fetchone()[0],2)
                self.assertEqual(c.execute("SELECT count(*) FROM position_state").fetchone()[0],0)
                self.assertEqual(c.execute("SELECT count(*) FROM stop_reentry_blocks").fetchone()[0],0)
            with self.assertRaises(ValueError):
                last.set_manual_trend("gmo",True)
            with self.assertRaises(ValueError):
                last.set_manual_trend("stock","true")

    def test_runtime_changes_one_market_and_auto_restores(self):
        with TemporaryDirectory() as root:
            c=object.__new__(runtime.Coordinator)
            c.journal=mt.TrendJournal(root)
            c.lock=threading.RLock()
            c.manual_trend={"stock":False,"coin":False}
            c.modes={"stock":"PAUSED","coin":"PAUSED"}
            c.scores={"stock":None,"coin":None}
            c.mode_details={"stock":"","coin":""}
            c._last_fetch_error={}
            c._candidates={"stock":{},"coin":{}}
            c._read_score=lambda market:{"score":20,"stale":False,
                "status":"정상","market_open":True}
            c._cancel_native_pending=lambda market:None
            c._update_score("stock")
            c._update_score("coin")
            self.assertEqual(c.mode("stock"),"ORIGINAL")
            self.assertEqual(c.mode("coin"),"ORIGINAL")
            result=c.set_manual_trend("coin",True)
            self.assertTrue(result["ok"])
            self.assertEqual(c.mode("coin"),"MARKET_TREND")
            self.assertEqual(c.mode("stock"),"ORIGINAL")
            self.assertTrue(mt.TrendJournal(root).manual_trend("coin"))
            c.set_manual_trend("coin",False)
            self.assertEqual(c.mode("coin"),"ORIGINAL")
            c._read_score=lambda market:{"score":-20,"stale":False,
                "status":"정상","market_open":True}
            c._update_score("coin")
            self.assertEqual(c.mode("coin"),"MARKET_TREND")

    def test_android_command_ui_and_trading_guards(self):
        self.assertIn('SET_TREND_MANUAL',SERVICE)
        self.assertIn('setTrendManual(String market,boolean enabled)',BRIDGE)
        self.assertIn('module.callAttr("set_trend_manual",market,enabled)',BRIDGE)
        self.assertIn('마켓트렌드 수동 ON',UI)
        self.assertIn('수동 해제 · 자동 복귀',UI)
        self.assertIn('manual_trend',PY)
        self.assertIn('if not is_regular_open():return',PY)
        self.assertIn('self.journal.stop_reentry_blocked(market,code,now)',PY)
        self.assertIn('PENDING_BROKER_RECONCILIATION_REQUIRED',PY)
        self.assertRegex(GRADLE,r"versionCode\s+(?:5[2-9]|[6-9][0-9])")
        self.assertIn("com.artitrade.unified.s21dex",GRADLE)
