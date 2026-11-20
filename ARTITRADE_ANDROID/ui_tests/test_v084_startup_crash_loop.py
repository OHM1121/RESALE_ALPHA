"""Startup must not crash repeatedly because persisted UI navigation has a bad type.

The previous onCreate read SharedPreferences BEFORE the containing try block.
A ClassCastException from one corrupted preference caused an uncaught launcher
fatal on every restart, while the trading SQLite files were unaffected.
"""
import unittest
from pathlib import Path

JAVA=(Path(__file__).resolve().parents[1]/
      "app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java")


class CrashLoopRecovery(unittest.TestCase):
    def test_navigation_restore_is_guarded_without_deleting_trade_data(self):
        source=JAVA.read_text(encoding="utf-8")
        start=source.index("@Override public void onCreate(Bundle state)")
        stop=source.index("private void recordUiEntry(",start)
        block=source[start:stop]
        self.assertIn("startup_navigation_recovered",block)
        self.assertIn("restoreNavigation(state);",block)
        self.assertLess(block.index("try{"),block.index("restoreNavigation(state);"))
        self.assertGreater(block.index("catch(Throwable"),block.index("restoreNavigation(state);"))
        self.assertNotIn("deleteDatabase",block)
        self.assertNotIn("clearApplicationUserData",block)
        self.assertNotIn("clearCache",block)

if __name__=="__main__":unittest.main()
