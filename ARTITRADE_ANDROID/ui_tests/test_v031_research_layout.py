"""v031 layout regression: visible 4-column popup and wide information distribution."""
import unittest
from pathlib import Path

UI=Path(__file__).parents[1]/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java'
MATRIX=Path(__file__).parents[1]/'app/src/main/java/com/artitrade/mobile/ResearchScoreMatrixView.java'

class ResearchLayoutContract(unittest.TestCase):
    def test_all_six_strategy_score_bands_support_exact_doubletap_and_close(self):
        ui=UI.read_text(encoding="utf8")
        matrix=MATRIX.read_text(encoding="utf8")
        self.assertIn('matrix.setOnBandDoubleTapListener',ui)
        self.assertIn('private void showResearchBandPopup(',ui)
        self.assertIn('ResearchBandSelection.matches(',ui)
        self.assertIn('setPositiveButton("닫기"',ui)
        self.assertIn('new GestureDetector(',matrix)
        self.assertIn('onDoubleTap(MotionEvent event)',matrix)
        self.assertIn('new String[]{"5m","30m","60m","day","gmo","close"}',ui)

    def test_research_popup_uses_four_weighted_columns_and_truthful_sources(self):
        ui=UI.read_text(encoding="utf8")
        for item in ('종목명 / 전략','조건충족일 (D일)','매수가 (일자)','매도가 / 목표관측 (일자)'):
            self.assertIn(item,ui)
        for field in ('"sell_price"','"sell_evidence"','"virtual_sell_price"',
                      '"observed_target_price"','"first_observed_target_after_touch"'):
            self.assertIn(field,ui)
        self.assertIn('new android.widget.BaseAdapter()',ui)
        self.assertRegex(ui,r'getView\(int position,\s*View convertView,\s*android\.view\.ViewGroup parent\)')

    def test_research_scores_errors_policy_and_operation_use_weighted_columns(self):
        ui=UI.read_text(encoding="utf8")
        for marker in ('researchEvidenceColumns','scoreCandidateColumns',
                       'errorHistoryColumns','operationFrameColumns'):
            self.assertIn(marker,ui)
        # v033 replaces the old generic policy columns with an explicit
        # five-field, full-width table; do not require a removed local name.
        self.assertIn('policyHistoryCell(headings,"당시 시장점수"',ui)
        self.assertIn('new LinearLayout.LayoutParams(-1,dp(79))',ui)
        self.assertIn('new LinearLayout.LayoutParams(0,-2,',ui)
        self.assertIn('getConfiguration().screenWidthDp',ui)

if __name__=='__main__':unittest.main()
