"""Fresh-only 0.0.1 optimization and number-only release policy tests."""
import re
import unittest
from pathlib import Path
from version_policy_v001 import next_app_version

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/"app/src/main"
UI=(MAIN/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text("utf8")
SERVICE=(MAIN/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text("utf8")
TREND=(MAIN/"python/market_trend_runtime_v043.py").read_text("utf8")
RUNTIME=(MAIN/"python/android_runtime_v078.py").read_text("utf8")
GRADLE=(ROOT/"app/build.gradle").read_text("utf8")

class FreshOnlyPerfVersion(unittest.TestCase):
    def test_app_version_name_and_monotonic_android_upgrade_code(self):
        self.assertIn("versionCode 124",GRADLE)
        self.assertIn("versionName '0.0.2'",GRADLE)
        self.assertIn("applicationId 'com.artitrade.unified.s21dex'",GRADLE)
        self.assertIn("resValue 'string', 'app_name', 'ARTITRADE'",GRADLE)
        self.assertIn('return version.matches(',UI)
        self.assertNotIn('return "v"+version;',UI)
        self.assertNotIn('TextView ver=text("  ·  "+installedVersion',UI)
        self.assertNotIn('setContentTitle("ARTITRADE S21+',SERVICE)
    def test_sequence_minor_rollover_no_suffix(self):
        self.assertEqual(next_app_version("0.0.1"),"0.0.2")
        self.assertEqual(next_app_version("0.0.8"),"0.0.9")
        self.assertEqual(next_app_version("0.0.9"),"0.1.0")
        self.assertEqual(next_app_version("0.1.9"),"0.2.0")
        self.assertEqual(next_app_version("0.9.9"),"1.0.0")
        for invalid in ("v0.0.1","0.0.1-BETA","00.0.1","0.1"):
            with self.assertRaises(ValueError):next_app_version(invalid)
    def test_fresh_only_ui_does_not_read_remote_legacy_ledger(self):
        self.assertNotIn('client.record(k[0],k[1],k[2])',UI)
        self.assertNotIn('client.migrationStatus()',UI)
        self.assertIn('"FRESH_PHONE_LEDGER_ONLY"',UI)
        self.assertIn('packet.put("using_hub_fallback",false)',UI)
        self.assertIn('packet.put("mserver_migration",new JSONObject())',UI)
    def test_removed_research_page_no_repeated_sqlite_join_in_ui(self):
        self.assertIn('if not for_ui:\n            try:\n                _research_history(self,out)',RUNTIME)
        self.assertIn('if not for_ui:\n                try:\n                    originals=_gmo_original_record',RUNTIME)
        self.assertIn('from market_research_v033 import offer_records',RUNTIME)
        self.assertIn('from android_research_audit import capture',RUNTIME)
        self.assertIn('getModule("android_ai_evidence_export_v046")',UI)
    def test_trend_5hz_evaluation_memoized_until_real_source_changes(self):
        self.assertIn('self._fresh_closed_signal_cache={}',TREND)
        self.assertIn('source_signature=tuple((x["end"],x["close"])',TREND)
        self.assertIn('cached[0]==source_signature',TREND)
        self.assertIn('first_closed_cross([x["close"] for x in rows]',TREND)
        self.assertIn('self._refresh_due_closed_local_v108',TREND)
    def test_explanatory_screen_notes_removed_but_cautions_retained(self):
        self.assertNotIn('미선정 후보 포함 · 후보 당시 점수와 판정 상태 · 실제 계좌성과와 분리',UI)
        self.assertNotIn('정식 매수후보: 최근 10개 확정봉',UI)
        self.assertIn('실계좌 주문은 실행하지 않습니다.',UI)
        self.assertIn('원장 정책: 새 PAPER만 유지',UI)

if __name__=="__main__":unittest.main()
