"""Packaging contract: the shipped (signed debug-type) APK is not debuggable.

Static contract only. It does not build an APK; the Gradle build itself is
verified by CI, and the resulting manifest by `aapt dump badging` in CI/device.
"""
import re
import unittest
from pathlib import Path

BASE = Path(__file__).parents[1]
REPO = BASE.parent
GRADLE = (BASE / "app/build.gradle").read_text(encoding="utf8")
WORKFLOWS = REPO / ".github/workflows"
JAVA = BASE / "app/src/main/java/com/artitrade/mobile"


def debug_block():
    start = GRADLE.index("buildTypes {")
    return GRADLE[start:GRADLE.index("release {", start)]


class NotDebuggableByDefault(unittest.TestCase):
    def test_debuggable_is_only_an_explicit_opt_in(self):
        block = debug_block()
        self.assertRegex(block, r"debuggable\s+System\.getenv\('ARTITRADE_DEBUGGABLE'\)\s*==\s*'1'")
        # No unconditional true anywhere in the build file.
        self.assertNotRegex(GRADLE, r"debuggable\s+true")
        self.assertNotRegex(GRADLE, r"isDebuggable\s*=\s*true")

    def test_emulator_smoke_builds_keep_run_as_working(self):
        block = debug_block()
        self.assertIn("ARTITRADE_EMULATOR_ABI') == 'x86_64'", block)
        self.assertIn("ARTITRADE_EMULATOR_FIXTURE') == '1'", block)
        emulator = (WORKFLOWS / "build-artitrade-android-emulator.yml").read_text(encoding="utf8")
        self.assertIn("ARTITRADE_EMULATOR_ABI: x86_64", emulator)
        self.assertIn("ARTITRADE_EMULATOR_FIXTURE=1", emulator)

    def test_no_manifest_forces_debuggable(self):
        for manifest in (BASE / "app/src").glob("*/AndroidManifest.xml"):
            self.assertNotIn("debuggable", manifest.read_text(encoding="utf8"), manifest)

    def test_current_release_workflows_do_not_need_run_as(self):
        checked = 0
        for wf in WORKFLOWS.glob("artitrade-0[0-9][0-9]-*.yml"):
            text = wf.read_text(encoding="utf8")
            if "assembleUnifiedDebug" not in text:
                continue
            checked += 1
            self.assertNotIn("run-as", text, wf.name)
            self.assertNotIn("ARTITRADE_EMULATOR", text, wf.name)
        self.assertGreater(checked, 0)

    def test_in_place_upgrade_identity_is_unchanged(self):
        unified = GRADLE[GRADLE.index("unified {"):GRADLE.index("signingConfigs")]
        self.assertIn("applicationId 'com.artitrade.unified.s21dex'", unified)
        self.assertRegex(unified, r"versionName\s+'\d+\.\d+\.\d+'")
        self.assertRegex(unified, r"versionCode\s+\d+")
        self.assertIn("signingConfig signingConfigs.ciStable", GRADLE)


class UnusedClassRemoved(unittest.TestCase):
    def test_market_band_delta_view_is_gone_and_unreferenced(self):
        self.assertFalse((JAVA / "MarketBandDeltaView.java").exists())
        for src in list((BASE / "app/src").rglob("*.java")) + list((BASE / "ui_tests").glob("*")):
            if src.is_file() and src.resolve() != Path(__file__).resolve():
                self.assertNotIn("MarketBandDeltaView", src.read_text(encoding="utf8", errors="replace"),
                                 str(src))


if __name__ == "__main__":
    unittest.main()
