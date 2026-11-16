"""Startup is preloaded off UI thread before showing native dashboard."""
import unittest
from pathlib import Path

SRC=(Path(__file__).parents[1]/"app/src/main/java/com/artitrade/mobile/"
     "UnifiedVisualActivity.java").read_text(encoding="utf8")

class NativePreloadGate(unittest.TestCase):
    def test_first_shell_only_after_snapshot_prepared(self):
        create=SRC.split('@Override public void onCreate(Bundle state)',1)[1].split('private void restoreNavigation',1)[0]
        self.assertIn('showBoot();',create)
        self.assertIn('refresh(true);',create)
        self.assertNotIn('showShell();\n                    refresh(true);',create)
        self.assertIn('preloadScreenReady',SRC)
        self.assertIn('finishPreload',SRC)

if __name__=='__main__':unittest.main()
