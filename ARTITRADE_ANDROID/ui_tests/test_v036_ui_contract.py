"""v036 stock+coin table and MSERVER UX implementation contract."""
import unittest
from pathlib import Path

BASE=Path(__file__).parents[1]
FILE=BASE/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java"
UI=FILE.read_text(encoding="utf8")

class ComprehensiveUi(unittest.TestCase):
    def test_every_historical_data_view_has_filter_sort_control(self):
        for name in ("renderList","renderResearch","renderScores",
                     "renderPolicyHistory","renderErrors"):
            self.assertIn("private void "+name+"(",UI)
        # Source-independent query model must be applied BEFORE paging.
        for query_key in ('"list/"','"research/"','"scores/"','"policy/"','"errors/"'):
            self.assertIn(query_key,UI)
        self.assertGreaterEqual(UI.count("filterAndSortHistory("),5)
        self.assertGreaterEqual(UI.count("tableOptionsDialog("),5)

    def test_mserver_right_drawer_no_research_row_counts_preload_or_raw_error(self):
        start=UI.index('TextView srvTitle=text("MSERVER"')
        end=UI.index('panel.addView(tools);',start)
        block=UI[start:end]
        for forbidden in (
            "원본 기록 ","PC 수급매매 승계 확인: 원본",
            "화면 표시자료 준비시간","화면용 기록 ",
            "원본 "+'"+pcGmoRows',"HTTP ", "전략검증:",
            "게시 실패 위치:","원인: "+'"+explanation'):
            self.assertNotIn(forbidden,block)
        for state in ("연결","PC 실시간연동","휴대전화 원장 전송","PC 원장 승계"):
            self.assertIn(state,block)
        self.assertIn("logMserverIssue(",UI)

    def test_home_target_label_never_calls_incomplete_100pct_success(self):
        self.assertIn('hit.setMetric("목표확인 비중"',UI)
        self.assertIn('win.setMetric("실현손익 승률"',UI)
        self.assertIn('actual.optInt("pending"',UI)

    def test_research_popup_labels_target_OX_and_ignores_not_bought_candidates(self):
        self.assertIn("ResearchBandSelection.includeInPopup(",UI)
        self.assertIn('case "goal":return "목표달성 O/X"',UI)
        self.assertIn("tableHeaderCell(headers,",UI)

if __name__=="__main__":unittest.main()
