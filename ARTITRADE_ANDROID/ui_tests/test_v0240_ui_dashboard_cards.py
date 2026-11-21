"""0.2.4: sign-coloured line chart, collapsible dashboard/holdings sections, confirmed-vs-holding profit, categorised holding card."""
import re
import unittest
from pathlib import Path

JAVA = Path(__file__).resolve().parents[1] / 'app/src/main/java/com/artitrade/mobile'


def read(name):
    return (JAVA / name).read_text(encoding='utf-8')


def strip_comments(src):
    return re.sub(r'/\*.*?\*/|//[^\n]*', '', src, flags=re.S)


class LineChart(unittest.TestCase):
    def test_line_colour_follows_the_sign_with_zero_green(self):
        v = read('ArtitradeVisualViews.java')
        spark = v[v.index('final class PnlSparklineView'):v.index('final class MetricProgressView')]
        self.assertIn('last<0?ChartStyle.DOWN:ChartStyle.UP', spark)       # zero and positive: green
        # one curve drawn twice, clipped at the zero line: green above, red below
        self.assertEqual(len(re.findall(r'c\.clipRect\(', spark)), 2)
        self.assertIn('ChartStyle.areaPaintDown', spark)
        self.assertIn('boolean upper=side==0;', spark)
        self.assertIn('int col=upper?ChartStyle.UP:ChartStyle.DOWN;', spark)

    def test_every_line_chart_in_the_program_is_this_view(self):
        # PnlSparklineView is the only time-series line chart; all dashboards build it.
        for name in ('AiEvidenceVisualV046.java', 'AiAnalysisVisualV040.java', 'TradeGeometryView.java',
                     'TradeRangeView.java', 'MarketRegimeGraphicView.java'):
            self.assertNotIn('drawPath', read(name), name)


class Views(unittest.TestCase):
    def test_new_views_exist_and_are_presentation_only(self):
        v = read('IntegratedVisualViews.java')
        for cls in ('final class PnlSplitView', 'final class PriceLadderView'):
            self.assertIn(cls, v)
        code = strip_comments(v)
        for bad in ('SQLite', 'placeOrder', 'ledger', 'Runtime.', 'SharedPreferences', 'LAYER_TYPE_SOFTWARE'):
            self.assertNotIn(bad, code)

    def test_confirmed_and_holding_look_different_without_colour(self):
        v = read('IntegratedVisualViews.java')
        split = v[v.index('final class PnlSplitView'):v.index('final class PriceLadderView')]
        self.assertIn('DashPathEffect', split)            # holding = dashed outline
        self.assertIn('"확정"', split)
        self.assertIn('"보유"', split)

    def test_price_ladder_marks_stop_entry_target_and_current(self):
        v = read('IntegratedVisualViews.java')
        ladder = v[v.index('final class PriceLadderView'):]
        for token in ('"손절"', '"매수"', '"목표"', '"현재 "', 'openEnd('):
            self.assertIn(token, ladder)


class Dashboard(unittest.TestCase):
    ui = read('UnifiedVisualActivity.java')

    def test_profit_kinds_are_named_in_words(self):
        home = self.ui[self.ui.index('private void renderIntegratedHome()'):self.ui.index('private TextView quoteDot()')]
        for text in ('확정 손익', '보유 손익', '매도 완료 · 금액 확정', '보유 중 · 시세 따라 변동', '확정 / 보유 손익'):
            self.assertIn(text, home)
        self.assertNotIn('"실현 ("', home)
        self.assertIn('PnlSplitView split=new PnlSplitView(this);', home)      # per strategy tile

    def test_markets_fold_and_keep_their_profit_bars(self):
        home = self.ui[self.ui.index('private void renderIntegratedHome()'):self.ui.index('private TextView quoteDot()')]
        self.assertIn('collapsibleHeader(groupKey', home)
        self.assertIn('if(collapsed)continue;', home)
        head = self.ui[self.ui.index('private LinearLayout collapsibleHeader('):self.ui.index('private TextView quoteDot()')]
        self.assertIn('PnlSplitView split=new PnlSplitView(this);', head)       # bar is part of the header
        self.assertIn('setGroupsCollapsed(', head)

    def test_fold_state_is_saved_and_survives_refresh(self):
        self.assertIn('"collapsed_groups_v1"', self.ui)
        self.assertNotRegex(self.ui, r'collapsedGroups\.clear\(\)')

    def test_holdings_fold_per_strategy_with_bar_still_visible(self):
        body = self.ui[self.ui.index('private void renderIntegratedPositions()'):self.ui.index('private void renderHome()')]
        self.assertIn('final String foldKey="pos/"+key;', body)
        bar = body.index('content.addView(subBar')
        skip = body.index('if(folded)continue;')
        self.assertLess(bar, skip)                      # the profit bar is added BEFORE the fold decision
        self.assertIn('전략 모두 접기', body)
        self.assertIn('모두 펼치기', body)
        self.assertIn('보유 손익 합계', body)


class HoldingCard(unittest.TestCase):
    ui = read('UnifiedVisualActivity.java')
    card = ui[ui.index('private LinearLayout buildHoldingCard'):ui.index('private String positionBuyReason')]

    def test_categories_group_related_figures(self):
        card = self.card
        # exit levels (profit target + stop) share ONE panel
        exits = card[card.index('infoPanel("청산 기준"'):card.index('infoPanel("투입"')]
        self.assertIn('▲ 1차 수익목표', exits)
        self.assertIn('▼ 손절가', exits)
        for title in ('infoPanel("투입"', 'infoPanel("시점"'):
            self.assertIn(title, card)
        self.assertIn('손익비 1 : ', card)

    def test_price_map_is_drawn_from_stop_entry_target_current(self):
        self.assertIn('ladder.set(stopPrice,entry,targetPrice,current);', self.card)
        self.assertIn('new PriceLadderView(this)', self.card)

    def test_result_comes_first(self):
        card = self.card
        self.assertLess(card.index('heroStat(resultRow,"평가손익"'), card.index('new PriceLadderView(this)'))
        self.assertLess(card.index('new PriceLadderView(this)'), card.index('infoPanel("청산 기준"'))

    def test_live_price_binding_and_actions_kept(self):
        self.assertIn('bindFastVisiblePrice(p,scopeMarket,nowValue,dot,"","");', self.card)
        self.assertIn('"강제매도"', self.card)
        self.assertIn('expandedBuyReasons', self.card)


if __name__ == '__main__':
    unittest.main()
