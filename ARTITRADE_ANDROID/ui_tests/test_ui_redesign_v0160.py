"""0.1.6 (#125): dark chart redesign + visual integrated dashboard/holdings.

Source-level boundary tests: the redesign is presentation only, so these pin that
(a) the new visual views exist and are wired into the integrated pages,
(b) no chart builds software layers/blur (speed), and
(c) the integrated pages never write ledger/order data and keep the live-price binding.
"""
import re
import unittest
from pathlib import Path

JAVA = Path(__file__).resolve().parents[1] / "app/src/main/java/com/artitrade/mobile"


def read(name):
    return (JAVA / name).read_text(encoding="utf-8")


class UiRedesignV0160(unittest.TestCase):
    def test_new_views_exist(self):
        v = read("IntegratedVisualViews.java")
        for cls in ("final class SegmentBarView", "final class ReturnGaugeView",
                    "final class SignedAmountBarView", "final class IntegratedPalette"):
            self.assertIn(cls, v)
        self.assertIn("final class ChartStyle", read("ChartStyle.java"))

    def test_integrated_holdings_are_visual_and_grouped(self):
        ui = read("UnifiedVisualActivity.java")
        body = ui[ui.index("private void renderIntegratedPositions()"):ui.index("private void renderHome()")]
        for token in ("SegmentBarView", "SignedAmountBarView", "IntegratedPalette.strategy",
                      "buildHoldingCard(p,holding.scopeMarket,holding.scopeStrategy"):
            self.assertIn(token, body)
        card = ui[ui.index("private LinearLayout buildHoldingCard"):ui.index("private String positionBuyReason")]
        for token in ("ReturnGaugeView", 'panelRow(invested,"매수가"', 'heroStat(resultRow,"평가손익"',
                      "bindFastVisiblePrice(p,scopeMarket,nowValue,dot"):
            self.assertIn(token, card)
        # every strategy of both markets gets its own section
        self.assertIn("for(String[] def:strategyList(m))", body)

    def test_integrated_dashboard_has_comparison_charts(self):
        ui = read("UnifiedVisualActivity.java")
        body = ui[ui.index("private void renderIntegratedHome()"):ui.index("private void renderIntegratedPositions()")]
        self.assertIn("전략별 누적손익 비교", body)
        self.assertIn("손익 구성", body)
        self.assertIn("MetricProgressView winBar", body)
        self.assertIn("HorizontalStrategyBarsView", body)

    def test_unknown_values_never_become_zero(self):
        v = read("IntegratedVisualViews.java")
        self.assertIn("Double.isNaN(pct)", v)  # unknown return draws only the empty track
        ui = read("UnifiedVisualActivity.java")
        body = ui[ui.index("private void renderIntegratedPositions()"):ui.index("private void renderHome()")]
        self.assertIn('knownN+"/"+count+" 평가"', body)
        self.assertIn("unknownN++", body)

    def test_no_slow_rendering_in_charts(self):
        for name in ("ChartStyle.java", "IntegratedVisualViews.java", "ArtitradeVisualViews.java"):
            src = read(name)
            self.assertNotIn("LAYER_TYPE_SOFTWARE", src, name)
            self.assertNotIn("setShadowLayer", src, name)
            self.assertNotIn("BlurMaskFilter", src, name)

    def test_presentation_only_no_ledger_or_order_calls(self):
        for name in ("ChartStyle.java", "IntegratedVisualViews.java"):
            src = re.sub(r"/\*.*?\*/|//[^\n]*", "", read(name), flags=re.S)
            for bad in ("SQLite", "placeOrder", "ledger", "Runtime.", "SharedPreferences"):
                self.assertNotIn(bad, src, name)

    def test_gradient_helpers_used_by_existing_charts(self):
        v = read("ArtitradeVisualViews.java")
        self.assertGreaterEqual(len(re.findall(r"ChartStyle\.barPaint", v)), 4)
        self.assertIn("ChartStyle.areaPaint", v)
        # original pinned strings stay
        self.assertIn("WonFormat.signed(v)", v)
        self.assertIn("WonFormat.won(v)", v)

    def test_footnotes_removed_from_visible_lists(self):
        ui = read("UnifiedVisualActivity.java")
        for body_start, body_end in (("private void buildHoldingCard", "private String positionBuyReason"),
                                     ("private LinearLayout buildTradeGroupCard", "private void renderList")):
            pass
        holding = ui[ui.index("private LinearLayout buildHoldingCard"):ui.index("private String positionBuyReason")]
        group = ui[ui.index("private LinearLayout buildTradeGroupCard"):ui.index("private void renderList")]
        for body in (holding, group):
            for gone in ("고유 거래 ID", "공식 REST", "화면 전용", "원장 손익은 별도", "재확인 중"):
                self.assertNotIn(gone, body)
        # the live state is a coloured dot, not a sentence
        self.assertIn("private TextView quoteDot()", ui)
        self.assertIn("setQuoteDot(item.status", ui)
        self.assertNotIn('"고유 거래 ID"', ui)

    def test_every_strategy_holdings_page_uses_the_visual_card(self):
        ui = read("UnifiedVisualActivity.java")
        body = ui[ui.index("private void renderList("):ui.index("private void openPolicyForPosition")]
        self.assertIn("buildHoldingCard(r,market,source,holdPnl", body)
        self.assertIn('if("positions".equals(kind)){', body)

    def test_execution_history_is_grouped_by_unique_trade_id(self):
        ui = read("UnifiedVisualActivity.java")
        self.assertIn('r.optString("position_uid","")', ui)
        key = ui[ui.index("private String tradeGroupKey"):ui.index("private java.util.ArrayList<TradeGroup> groupExecutions")]
        # uid first, then wallet position id, never a shared fallback that could merge two trades
        self.assertLess(key.index('"uid|"'), key.index('"pid|"'))
        self.assertIn('"row|"+tradeEvidenceKey(item)', key)
        lst = ui[ui.index("private void renderList("):ui.index("private void openPolicyForPosition")]
        self.assertIn("groupExecutions(fullHistory,history)", lst)
        self.assertIn("buildTradeGroupCard(tradeGroups.get(i))", lst)
        # filters decide which trades show, but a trade always shows all of its fills
        grp = ui[ui.index("private java.util.ArrayList<TradeGroup> groupExecutions"):ui.index("private String shortKst")]
        self.assertIn("for(HistoryRow row:full)", grp)
        self.assertIn("for(HistoryRow row:filtered)", grp)
        card = ui[ui.index("private LinearLayout buildTradeGroupCard"):ui.index("private void addTradeFillLine")]
        self.assertIn("TradeCycleView", card)
        self.assertIn("state=sells.isEmpty()?0", card)       # no sell -> still holding, never a fake exit
        self.assertIn('"일부 청산"', card)

    def test_trade_cycle_view_and_smooth_trend_line(self):
        v = read("IntegratedVisualViews.java")
        self.assertIn("final class TradeCycleView", v)
        art = read("ArtitradeVisualViews.java")
        self.assertIn("private Path smooth(", art)
        self.assertIn("cubicTo", art)
        self.assertIn("ChartStyle.ellipsize(p,label", art)   # caption can never overlap the headline
        self.assertIn("valueW", art)                           # bar amounts live in their own column


if __name__ == "__main__":
    unittest.main()
