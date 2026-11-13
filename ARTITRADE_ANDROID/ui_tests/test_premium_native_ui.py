"""Native OLED/legibility regression for visual-only release."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).parents[1]
JAVA=ROOT/"app/src/main/java/com/artitrade/mobile"
def read(name):return (JAVA/name).read_text(encoding="utf8")

class PremiumUiRegression(unittest.TestCase):
    def test_black_theme_and_all_clicks_have_reduced_motion(self):
        manifest=(ROOT/"app/src/main/AndroidManifest.xml").read_text()
        theme=(ROOT/"app/src/main/res/values/styles.xml").read_text()
        ui=read("UnifiedVisualActivity.java")
        motion=read("PremiumUi.java")
        self.assertIn('android:theme="@style/ArtitradeBlackTheme"',manifest)
        self.assertIn('<item name="android:windowBackground">#000000</item>',theme)
        self.assertIn('<item name="android:dialogTheme">@style/ArtitradeBlackDialog</item>',theme)
        self.assertIn("static final int BG=Color.BLACK;",motion)
        self.assertIn("PremiumUi.tapMotion(b);",ui)
        self.assertIn("PremiumUi.tapMotion(box);",ui)
        self.assertIn("PremiumUi.tapMotion(tile);",ui)
        self.assertIn("Settings.Global.ANIMATOR_DURATION_SCALE",motion)
        self.assertIn("new RippleDrawable(",motion)
        self.assertIn("return false; // Preserve normal click",motion)

    def test_accessible_text_no_truncated_research_and_s21_density(self):
        ui=read("UnifiedVisualActivity.java")
        charts=read("ArtitradeVisualViews.java")
        matrix=read("ResearchScoreMatrixView.java")
        self.assertIn("t.setTextSize(Math.max(12,sp));",ui)
        self.assertIn("b.setTextSize(13);",ui)
        self.assertIn("selection.setTextSize(13);",ui)
        self.assertIn("boolean sideBySide=getResources().getConfiguration().screenWidthDp>=720",ui)
        self.assertIn("boolean matrixColumns=getResources().getConfiguration().screenWidthDp>=720",ui)
        self.assertIn('new LinearLayout.LayoutParams(0,dp(131),1)',ui)
        self.assertNotIn('new LinearLayout.LayoutParams(0,dp(154),1)',ui)
        self.assertIn("stacked",matrix)
        self.assertIn("11,PremiumUi.MUTED",matrix)
        self.assertIn("PremiumUi.SURFACE",charts)
        self.assertIn("WonFormat.won(v)",charts)
        self.assertIn("WonFormat.signed(v)",charts)
        self.assertNotIn("%.1f만",charts)
        self.assertNotIn("%.1f억",charts)

    def test_research_and_naver_upbit_navigation_unchanged(self):
        ui=read("UnifiedVisualActivity.java")
        self.assertIn('String key=rowMarket+":"',ui)
        self.assertIn("consecutiveSymbolTaps==4",ui)
        self.assertIn("com.android.chrome",ui)
        self.assertIn("https://finance.naver.com/item/main.naver?code=",ui)
        self.assertIn("https://upbit.com/exchange?code=CRIX.UPBIT.",ui)
        for key in ('"stock","at"','"stock","ma"','"stock","gmo"','"stock","close"','"coin","general"','"coin","ma"'):
            self.assertIn(key,ui)
        self.assertIn('private void renderResearch(){',ui)
        self.assertIn("source_total",ui)
        self.assertIn("partial_window",read("UnifiedVisualActivity.java")+
                      (ROOT/"app/src/main/python/android_runtime_v078.py").read_text())

    def test_custom_glyphs_and_preview_are_black_not_blue(self):
        premium=read("PremiumUi.java")
        for icon in ('home','positions','pending','executions','research','buy','sell','settings'):
            self.assertIn('case "'+icon+'":',premium)
        preview=(ROOT/"app/src/main/assets/unified_dashboard.html").read_text()
        self.assertIn("--bg:#000",preview)
        self.assertIn("prefers-reduced-motion",preview)
        self.assertIn("n(st.total,'원')",preview)

if __name__=="__main__":unittest.main()
