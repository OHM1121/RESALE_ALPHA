"""v102: display only the installed numeric version in the title bar."""
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf8")

class OnlyVersionLabel(unittest.TestCase):
    def test_header_has_no_market_or_feature_suffix(self):
        start=UI.index("    private String marketVersionLabel(){")
        end=UI.index("    private void showShell(){",start)
        body=UI[start:end]
        self.assertIn('return "v"+version;',body)
        self.assertNotIn('+" · "+',body)
        self.assertNotIn('integratedMode?',body)
        self.assertNotIn('stock".equals(market)',body)
        self.assertIn("version=getPackageManager().getPackageInfo(getPackageName(),0).versionName",body)
    def test_release_version_name_is_strict_numeric(self):
        release="0.9.3"
        self.assertRegex(release,r"^\d+\.\d+\.\d+$")
        self.assertNotRegex(release,r"[- ]")

if __name__=="__main__":unittest.main()
