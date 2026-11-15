"""v057 regression: resume from screen-off without re-entering long preload."""
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf-8')
SERVICE=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedTradingService.java').read_text('utf-8')

class V057ScreenResume(unittest.TestCase):
    def test_recreated_activity_uses_heartbeat_fast_path(self):
        create=UI.split('@Override public void onCreate(Bundle state){',1)[1].split('private void restoreNavigation(',1)[0]
        self.assertIn('shouldRestoreShellImmediately(state)',create)
        self.assertIn('showShell();',create)
        self.assertIn('showBoot();',create)
        self.assertIn('main.postDelayed(this::preloadWatchdog',create)
        self.assertIn('isRecentBackgroundHeartbeat()',UI)
        self.assertIn('last_heartbeat_ms',UI)

    def test_service_is_foreground_and_can_be_restarted_idempotently(self):
        self.assertIn('return START_STICKY;',SERVICE)
        self.assertIn('if(running.compareAndSet(false,true))',SERVICE)
        self.assertIn('ensureBackgroundServiceOnResume()',UI)
        self.assertIn('background_health_last_retry',UI)
        self.assertIn('onResume(){',UI)

    def test_truthful_health_in_drawer_not_brand_header(self):
        shell=UI.split('private void showShell(){',1)[1].split('private String etaText(',1)[0]
        self.assertNotIn('brandBox.addView(statusText',shell)
        self.assertIn('new LinearLayout.LayoutParams(-1,dp(26))',shell)
        self.assertIn('new LinearLayout.LayoutParams(-1,dp(26))',shell)
        self.assertIn('new LinearLayout.LayoutParams(-1,dp(27))',shell)
        self.assertIn('readableBackgroundHealth()',UI)
        self.assertIn('statusText=text(',UI)

    def test_ai_result_hides_raw_sources_but_keeps_strategy_findings(self):
        ai=UI.split('private void renderAiAnalysis(){',1)[1].split('private void renderResearch(){',1)[0]
        self.assertNotIn('addApprovedEvidenceCard(report);',ai)
        self.assertNotIn('addComprehensiveV052Card(report);',ai)
        self.assertNotIn('addPostSaleV053Card(report);',ai)
        self.assertIn('aiFrameCard(content,item)',ai)
        self.assertIn('market_trend_rsi20',UI)
        back=(ROOT/'app/src/main/python/ai_strategy_analysis_v040.py').read_text('utf-8')
        for essential in ('approved_metrics_v048','comprehensive_v052','post_sale_tracking_v053'):
            self.assertIn(essential,back)

    def test_failed_evidence_lookup_is_rate_bounded_and_never_reposts_forever(self):
        self.assertIn('tradeEvidenceCache.put(key,new JSONObject());',UI)
        self.assertIn('new LinearLayout.LayoutParams(0,dp(24),1)',UI)

    def test_detailed_transaction_history_has_horizontal_scrolling(self):
        ui=UI
        self.assertIn('renderExecutionEvidenceStrip(',ui)
        self.assertIn('HorizontalScrollView details=new HorizontalScrollView(this)',ui)
        policy=ui.split('private void renderPolicyHistory(){',1)[1].split('private float policyColumnWeight(',1)[0]
        self.assertIn('HorizontalScrollView wideTable=new HorizontalScrollView(this)',policy)
        self.assertIn('readTradeEvidence(',ui)
        self.assertIn('queryTradeHistoryEvidence(',ui)
        self.assertIn('post_sale_peak',ui)
        self.assertIn('ai_source_count',ui)

if __name__=='__main__':unittest.main()
