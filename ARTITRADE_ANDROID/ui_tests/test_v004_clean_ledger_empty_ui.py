"""No old PC UI promotion, distinguish zero PAPER and failed data loads."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
UI=(ROOT/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text("utf8")
PY=(ROOT/"python/android_runtime_v078.py").read_text("utf8")
SERVICE=(ROOT/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text("utf8")
D19=(ROOT/"python/stock_at_policy_reconcile_v091.py").read_text("utf8")

class CleanLedgerUi(unittest.TestCase):
    def test_zero_realized_and_unrealized_requires_verified_empty_wallet(self):
        self.assertIn("private boolean ledgerSourceReady(JSONObject ledger)",UI)
        self.assertIn("if(!st.sourceReady)return;",UI)
        self.assertIn("if(st.flowSold==0&&st.executions.length()==0&&",UI)
        self.assertIn("!finite(st.realized))st.realized=0.0;",UI)
        self.assertIn("if(st.positions.length()==0&&!finite(st.unrealized))",UI)
        self.assertIn("st.total=st.realized+st.unrealized;",UI)
    def test_rolled_back_and_loading_states_distinct(self):
        self.assertNotIn("일부 원장 미수신",UI)
        self.assertNotIn("손익 미수신",UI)
        self.assertIn('anyError?"원장 조회 오류":"원장 확인 중"',UI)
        self.assertIn("원장 무결성 오류 · 매매 중단",UI)
    def test_never_reimport_old_pc_to_mask_missing_new_gmo(self):
        self.assertNotIn("mergeMissingNested(",UI)
        self.assertNotIn("missingNestedBucket(",UI)
        self.assertNotIn("recoveryCandidate(",UI)
        self.assertIn("return localRecord(snapshot,key);",UI)
        self.assertIn("NATIVE_GMO_ENGINE_START_PENDING",PY)
        self.assertNotIn("'PC_SOURCE_NOT_PRESENT'",PY)
        self.assertNotIn("checkPendingPCArchive(",SERVICE)
    def test_new_d19_policy_preserved_without_old_legacy_scan(self):
        self.assertIn('if epoch.get("state")=="COMMITTED":',D19)
        self.assertIn('D19PaperEngine._buy_limit_depth=buy_with_original_threeleg',D19)
        self.assertIn("'ORIGINAL_D19_NEW_BUY_ATOMIC'",D19)

if __name__=="__main__":unittest.main()
