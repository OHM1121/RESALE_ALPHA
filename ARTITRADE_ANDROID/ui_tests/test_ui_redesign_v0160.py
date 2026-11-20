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
        for token in ("SegmentBarView", "ReturnGaugeView", "SignedAmountBarView",
                      "IntegratedPalette.strategy", "holdingStat(stats1,\"매수가\"",
                      "holdingStat(stats2,\"평가손익\"", "bindFastVisiblePrice(p,holding.scopeMarket,nowValue"):
            self.assertIn(token, body)
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


if __name__ == "__main__":
    unittest.main()
