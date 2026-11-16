"""Static Android source integration tests for standalone MT54 navigation and accounts."""
from pathlib import Path
from unittest import TestCase
ROOT=Path(__file__).parents[1]
UI=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf-8')
SCOPE=(ROOT/'app/src/main/java/com/artitrade/mobile/StrategyDashboardScope.java').read_text('utf-8')

class V054Ui(TestCase):
    def test_unified_release_is_v054_or_newer_with_incremented_versioncode(self):
        gradle=(ROOT/'app/build.gradle').read_text('utf-8')
        self.assertRegex(gradle,r"versionCode (?:6[1-9]|[7-9][0-9])")
        self.assertIn("versionName '0.7.71-WS-ORDERED-RECV-HISTORY'", gradle)
        self.assertIn("applicationId 'com.artitrade.unified.s21dex'",gradle)

    def test_trend_is_primary_strategy_in_both_markets(self):
        self.assertIn('{"gmo","수급매매"},{"trend","마켓트렌드"}',UI)
        self.assertIn('{"general","기준봉"},{"ma","MA"},{"trend","마켓트렌드"}',UI)
        self.assertIn('"close","gmo","trend"',UI)
        self.assertIn('"general","ma","trend"',UI)

    def test_trend_has_four_independent_seed_controls(self):
        self.assertIn('addFrameOperationSection("마켓트렌드", "stock","trend",settings)',UI)
        self.assertIn('addFrameOperationSection("마켓트렌드", "coin","trend",settings)',UI)
        self.assertIn('for(String stg:new String[]{"at","ma","trend"})',UI)
        self.assertIn('for(String stg:new String[]{"general","ma","trend"})',UI)
        self.assertIn('String key=m+"."+stg+"."+tf',UI)

    def test_native_strategies_no_longer_offer_manual_override(self):
        panel=UI.split('private void renderHome(){',1)[1].split('String feedName=',1)[0]
        self.assertNotIn('Button manualButton=',panel)
        self.assertIn('기준봉·MA·마켓트렌드 각각 독립 원장',panel)

    def test_watch_pending_has_distinct_observation_screen(self):
        self.assertIn('renderTrendWatchPending()',UI)
        self.assertIn('market_trend_watch',UI)
        self.assertIn('RSI(20) ≤ 35',UI)
        self.assertIn('실제 주문 대기가 아닌 RSI 관측',UI)
        self.assertIn('WATCH_PENDING',UI)

    def test_segregated_ledger_read_and_ai_path(self):
        self.assertIn('if("trend".equals(s))',UI)
        self.assertIn('"trend".equals(strategy)',UI)
        self.assertIn('return market+"/"+strategy;',UI)
        self.assertIn('"trend".equals(filter)',SCOPE)

if __name__=='__main__':
    import unittest;unittest.main()
