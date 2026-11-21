"""0.2.5: strategy dashboard cleanup, merged foldable time-frame cards, clear fold state, MarketTrend win rate."""
import re
import unittest
from pathlib import Path

JAVA = Path(__file__).resolve().parents[1] / 'app/src/main/java/com/artitrade/mobile'
UI = (JAVA / 'UnifiedVisualActivity.java').read_text(encoding='utf-8')


def block(start, end):
    return UI[UI.index(start):UI.index(end)]


HOME = block('private void renderHome()', 'private void addDashboardKpi(')


class Removed(unittest.TestCase):
    def test_hit_rate_donut_and_flow_bar_are_gone(self):
        self.assertNotIn('목표확인 비중', HOME)
        self.assertNotIn('"매매 흐름"', HOME)
        self.assertNotIn('flowBar', HOME)

    def test_win_rate_donut_stays_alone(self):
        self.assertIn('win.setMetric("실현손익 승률"', HOME)
        self.assertEqual(HOME.count('new DonutMetricView('), 1)

    def test_hero_figures_have_no_clipped_caption_and_normal_height(self):
        self.assertNotIn('매도 완료 · 금액 확정', UI)
        self.assertNotIn('보유 중 · 시세 따라 변동', UI)
        self.assertIn('pnl.addView(split,new LinearLayout.LayoutParams(-1,dp(55)));', HOME)


class MergedFrames(unittest.TestCase):
    def test_performance_and_trend_are_one_section(self):
        self.assertIn('시간봉별 성과 · 추세', HOME)
        self.assertNotIn('"시간봉별 성과",17', HOME)
        self.assertNotIn('시간봉별 실현손익 추세', HOME)
        self.assertNotIn('TimeframeAccountFlowView', HOME)
        self.assertIn('for(String tf:frames)addFrameCard(tf,frameScale);', HOME)

    def test_every_frame_card_keeps_its_bars_when_folded(self):
        card = block('private void addFrameCard(', 'private void addDashboardKpi(')
        bars = card.index('PnlSplitView split=new PnlSplitView(this);')
        fold = card.index('if(!folded){')
        self.assertLess(bars, fold)                         # bars are added before the fold decision
        self.assertIn('PnlSparklineView chart=new PnlSparklineView(this);', card[fold:])   # trend only when open
        self.assertIn('StrategyDashboardScope.trend(market,strategy,tf)', card)
        self.assertIn('"tf/"+market+"/"+strategy+"/"+tf', card)

    def test_fold_all_and_unfold_all(self):
        self.assertIn('시간봉 모두 접기', HOME)
        self.assertIn('모두 펼치기', HOME)

    def test_every_frame_of_every_strategy_is_listed(self):
        self.assertIn('new String[]{"30m","60m","day"}', HOME)          # MarketTrend
        self.assertIn('new String[]{"5m","30m","60m","day"}', HOME)     # the others


class FoldState(unittest.TestCase):
    def test_open_and_folded_look_different(self):
        pill = block('private TextView foldPill(', 'private LinearLayout collapsibleHeader(')
        self.assertIn('"펼치기  ▾"', pill)
        self.assertIn('"접기  ▴"', pill)
        self.assertIn('bg(accent,0,14)', pill)                       # open = filled pill
        style = block('private void foldContainerStyle(', 'private LinearLayout collapsibleHeader(')
        self.assertIn('withAlpha(accent,190)', style)                # open = accent border
        self.assertIn('PremiumUi.LINE', style)                       # folded = plain border

    def test_every_foldable_header_uses_it(self):
        self.assertGreaterEqual(UI.count('foldPill(collapsed,accent)') + UI.count('foldPill(folded,accent)'), 3)
        self.assertGreaterEqual(UI.count('foldContainerStyle('), 4)  # definition + 3 users


class TrendWinRate(unittest.TestCase):
    def test_trend_path_computes_the_win_rate_before_returning(self):
        trend = UI[UI.index('int bought=0,sold=0,knownPnl=0,winSells=0;'):]
        trend = trend[:trend.index('return st;')]
        self.assertIn('if(knownPnl>0)st.win=winSells*100.0/knownPnl;', trend)
        self.assertIn('"net_profit","net_realized","net_pnl","pnl","profit"', trend)


if __name__ == '__main__':
    unittest.main()
