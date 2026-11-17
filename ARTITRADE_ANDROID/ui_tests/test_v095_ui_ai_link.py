"""v095: UI must not hide original causal reason or cache an empty post-sale
research lookup forever. This guards a second regression after the Python
source-join tests.
"""
import unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]
class NativeUI(unittest.TestCase):
    def test_verified_causal_evidence_shown_in_history_not_quote_slogan(self):
        ui=(P/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('e.optString("verified_trade_basis","")',ui)
        self.assertIn('매매조건·체결 근거',ui)
        self.assertIn('tradeEvidenceFetchedAt',ui)
    def test_post_sale_missing_cache_is_retried_after_20_seconds(self):
        ui=(P/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('POST_SALE_REFRESH_MS=20_000L',ui)
        self.assertIn('SystemClock.uptimeMillis()-lastFetched<POST_SALE_REFRESH_MS',ui)
    def test_manual_ai_consumes_both_source_scopes_with_per_trade_numbers(self):
        src=(P/'app/src/main/python/ai_strategy_analysis_v040.py').read_text()
        self.assertIn('verified_trade_basis_v095',src)
        self.assertIn('summary_for_ai(root,market,"ma")',src)
        self.assertIn('summary_for_ai(root,market,"trend")',src)
        summary=(P/'app/src/main/python/ai_comprehensive_v052.py').read_text()
        self.assertIn('V095_EXACT_ORIGINAL_EXECUTION_NOT_LINKED',summary)
        self.assertIn('original_buy["verified_numeric_factors"]',summary)

if __name__=='__main__':unittest.main()
