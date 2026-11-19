"""0.0.17 read-only source-integrity regression (no user device records)."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/"app/src/main/python"
JAVA=ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java"

class StockPreloadAndPositiveScoreRegression(unittest.TestCase):
    def test_screen_does_not_reuse_dynamic_health_check_percent(self):
        src=JAVA.read_text("utf8")
        begin=src.split("private void updateVerifiedStartupProgress(",1)[1].split(
            "private void finishPreload(",1)[0]
        self.assertNotIn("startupProgress.setProgress(bounded)",begin)
        self.assertIn("updateOriginalStockLoadingCounter(",src)
        self.assertIn("startupStockLoaded=Math.max(startupStockLoaded,observedDone)",src)
        self.assertIn("observedDone>observedTotal",src)
        self.assertIn("startupStockLoaded==startupStockTotal",src)
        self.assertIn('startupStockLoaded+" / "+startupStockTotal+"종목"',src)
        self.assertNotIn('startupPercent=text("5%',src)

    def test_stock_counter_only_from_original_native_symbol_progress(self):
        src=(PY/"android_runtime_v078.py").read_text("utf8")
        self.assertIn("'preload_stock_counter_source'",src)
        self.assertIn("ORIGINAL_NATIVE_PER_SYMBOL_PROGRESS",src)
        self.assertIn("native_total>=100",src)
        self.assertIn("0<=native_done<=native_total",src)
        self.assertNotIn("preload_stock_total': stock_subs",src)

    def test_reference_buy_requires_fresh_strict_positive_at_order_time(self):
        src=(PY/"at_market_score_gate_v107.py").read_text("utf8")
        self.assertIn('"positive":value>0',src)
        self.assertIn('if root is None or not score_evidence(root,"stock")["positive"]:',src)
        self.assertIn('if not score_evidence(root,"coin")["positive"]:',src)
        self.assertIn("STALE_SCORE_NO_DECISION",src)
        self.assertIn("return native_d19_stock(",src)
        self.assertIn("return native_coin_base(",src)
        self.assertNotIn("_NEGATIVE_ONLY",src)

if __name__=="__main__":
    unittest.main()
