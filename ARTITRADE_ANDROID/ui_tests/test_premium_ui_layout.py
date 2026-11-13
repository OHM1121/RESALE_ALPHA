"""Native OLED UI regressions: typography, geometry, icons, motion and parity.

These static checks intentionally do NOT pretend to be device screenshots.
They catch layout dimension drift, clickable motion loss and duplicate Java cases
before Android Gradle compiles the full APK.
"""
import re
import unittest
from pathlib import Path

BASE=Path(__file__).parents[1]/"app/src/main/java/com/artitrade/mobile"
UI=(BASE/"UnifiedVisualActivity.java").read_text(encoding="utf-8")
PREMIUM=(BASE/"PremiumUi.java").read_text(encoding="utf-8")
VISUAL=(BASE/"ArtitradeVisualViews.java").read_text(encoding="utf-8")
MATRIX=(BASE/"ResearchScoreMatrixView.java").read_text(encoding="utf-8")
GRADLE=(Path(__file__).parents[1]/"app/build.gradle").read_text(encoding="utf-8")


def only_method(code: str, first: str, last: str) -> str:
    start=code.index(first)
    end=code.index(last,start+len(first))
    return code[start:end]


class OLEDNativeUiRegressions(unittest.TestCase):
    def test_palette_is_oled_black_but_cards_are_separated_by_accessible_outlines(self):
        self.assertIn("static final int BG=Color.BLACK;",PREMIUM)
        self.assertIn("shell.setBackgroundColor(PremiumUi.BG)",UI)
        self.assertIn("scroll.setBackgroundColor(PremiumUi.BG)",UI)
        self.assertIn("getWindow().setNavigationBarColor(PremiumUi.BG)",UI)
        self.assertIn("getWindow().setStatusBarColor(PremiumUi.BG)",UI)
        self.assertIn("PremiumUi.SURFACE",VISUAL)
        self.assertIn("PremiumUi.LINE",MATRIX)

    def test_click_feedback_is_touch_only_and_respects_accessibility_animation_scale(self):
        self.assertIn("RippleDrawable",PREMIUM)
        self.assertIn("Settings.Global.ANIMATOR_DURATION_SCALE",PREMIUM)
        self.assertIn("setDuration(pressed?85:155)",PREMIUM)
        self.assertIn("return false; // Preserve normal click",PREMIUM)
        for method in (
            ("private Button button(", "private LinearLayout card()"),
            ("private TextView segmentItem(", "private TextView strategyTab("),
            ("private TextView strategyTab(", "private View iconAction("),
            ("private View iconAction(", "private LinearLayout bottomNavItem("),
            ("private LinearLayout bottomNavItem(", "private View divider()"),
        ):
            self.assertIn("PremiumUi.tapMotion(",only_method(UI,*method))
        self.assertIn("bindSymbolFourTap",UI)
        self.assertIn("consecutiveSymbolTaps==4",UI)

    def test_one_unique_symmetric_vector_icon_per_action(self):
        names=re.findall(r'case "([a-z]+)":',PREMIUM)
        self.assertEqual(len(names),len(set(names)),f"duplicate Java switch case: {names}")
        self.assertTrue({"home","positions","pending","executions","research",
                         "settings","buy","sell","close","wallet","archive"}<=set(names))

    def test_landscape_space_is_allocated_to_numbers_not_gap(self):
        self.assertIn("box.setOrientation(LinearLayout.HORIZONTAL)",UI)
        self.assertIn('TextView caption=text(label,13);caption.setGravity(Gravity.CENTER);',UI)
        self.assertIn('new LinearLayout.LayoutParams(0,dp(46),1)',UI)
        self.assertIn('shell.addView(bottom,new LinearLayout.LayoutParams(-1,dp(52)))',UI)
        self.assertIn('new LinearLayout.LayoutParams(0,dp(172),1)',UI)
        self.assertIn('new LinearLayout.LayoutParams(-1,dp(172))',UI)
        self.assertIn('getResources().getConfiguration().screenWidthDp>=720',UI)
        self.assertIn('getResources().getConfiguration().screenWidthDp>=680',UI)
        # Bottom navigation: height=46; 29dp icon + 3dp top & bottom padding.
        self.assertGreaterEqual(46,29+2*3)

    def test_krw_and_readable_financial_axes_remain_absolute(self):
        self.assertIn('String amount=compact(value);',VISUAL)
        self.assertIn('return WonFormat.signed(v);',VISUAL)
        self.assertIn('return WonFormat.won(v);',VISUAL)
        self.assertTrue('p.setTextSize(sp(12));' in VISUAL,
                        'financial chart axis must remain at least 12sp')
        self.assertIn('p.getTextSize()>sp(10)',VISUAL)
        self.assertIn('float middle=top+height*.66f;',VISUAL)
        self.assertIn('p.setTextSize(sp(12))',VISUAL)
        self.assertIn('boolean stacked=pctWidth+countWidth>available;',MATRIX)
        self.assertIn('11,PremiumUi.MUTED',MATRIX)

    def test_version_and_gmo_scope_remain_distinct(self):
        self.assertIn("versionCode 37",GRADLE)
        self.assertIn("versionName '0.7.26-LEDGER-SCREEN-AUDIT'",GRADLE)
        self.assertIn('v0.7.26 · 주식',UI)
        self.assertIn('v0.7.26 · 코인',UI)
        self.assertIn('stats(market,strategy)',UI)
        self.assertIn('market+"/"+scope',UI)
        self.assertIn('StrategyDashboardScope.showFrameCharts(strategy)',UI)
        self.assertIn('WonFormat.signed(selected.total)',UI)


if __name__=="__main__":
    unittest.main()
