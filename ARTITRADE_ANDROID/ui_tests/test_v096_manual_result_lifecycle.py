"""v096 regression: manual PAPER exits must not be hidden behind read-only snapshots.
This static regression supplements real Android emulator tests; it never fakes fills.
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
SERVICE=(ROOT/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text(encoding="utf-8")
UI=(ROOT/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf-8")
BRIDGE=(ROOT/"java/com/artitrade/mobile/StrategyRuntimeBridge.java").read_text(encoding="utf-8")
MANUAL=(ROOT/"python/android_manual_paper_sell_v085.py").read_text(encoding="utf-8")

class ManualPaperSellLifecycle(unittest.TestCase):
    def test_queued_and_running_uuid_stages_durable(self):
        self.assertIn('.put("stage","QUEUED")',SERVICE)
        self.assertIn('.put("stage","ENGINE_LOCK_WAIT")',SERVICE)
        self.assertIn('active.put("stage","ENGINE_EXECUTION_REQUESTED")',SERVICE)
        self.assertIn('finally{\n                manualSellOutstanding.decrementAndGet();',SERVICE)
        self.assertIn('putString("result_"+requestId,result.toString())',SERVICE)
    def test_expensive_ui_snapshots_defer_to_exit(self):
        self.assertIn('manualSellOutstanding.get()>0)return;',SERVICE)
        self.assertIn('worker.setPriority(Thread.MIN_PRIORITY+1)',SERVICE)
    def test_unknown_result_is_not_a_fill_and_not_retried(self):
        self.assertIn('"MANUAL_SELL_RUNNING".equals(state)',UI)
        self.assertIn('SELL 체결을 대조하기 전에는 재주문하지 않습니다',UI)
        self.assertIn('원인: "+result.optString("error"',UI)
        self.assertNotIn('startForegroundService(action)',UI[UI.index('private void showManualPaperResult'):UI.index('private void policyHistoryCell')])
    def test_original_fill_truth_is_unchanged(self):
        self.assertIn("PENDING_REAL_KRX_PRINT",MANUAL)
        self.assertIn("COIN_MANUAL_FILL_NOT_RECORDED",MANUAL)
        self.assertIn('synchronized JSONObject manualPaperSell',BRIDGE)

if __name__=="__main__":
    unittest.main()
