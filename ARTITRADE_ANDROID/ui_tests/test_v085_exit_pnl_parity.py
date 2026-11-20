"""v085 read-only regression: UI publish after confirmed native PAPER exits.

No broker order, original SQLite rewrite, synthetic fill, or emulator fixture.
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
SERVICE=(ROOT/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text(encoding="utf-8")
UI=(ROOT/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf-8")
RUNTIME=(ROOT/"python/android_runtime_v078.py").read_text(encoding="utf-8")
MANUAL=(ROOT/"python/android_manual_paper_sell_v085.py").read_text(encoding="utf-8")

class ManualExitDashboardParity(unittest.TestCase):
    def test_confirmed_sell_requests_fresh_local_record_after_worker_finishes(self):
        fn=SERVICE[SERVICE.index("private void applyManualPaperSell"):SERVICE.index("private void applyPendingOperation")]
        self.assertIn("manualSellOutstanding.decrementAndGet();",fn)
        self.assertIn('result.optBoolean("ok",false)',fn)
        self.assertIn("uiRecordLastStartedMs=0L;",fn)
        self.assertIn("scheduleUiRecordsIfDue(new LocalHubClient(this));",fn)
        self.assertLess(fn.index("manualSellOutstanding.decrementAndGet();"),
                        fn.index("scheduleUiRecordsIfDue(new LocalHubClient(this));"))
    def test_published_records_remain_view_only_and_derived_from_native_wallets(self):
        self.assertIn("JSONObject localRecords=strategy.hubRecords();",SERVICE)
        self.assertIn("LargeRecordStoreV087.write(this,serialized);",SERVICE)
        self.assertIn("manualSellOutstanding.get()>0)return;",SERVICE)
        self.assertIn("frame.get('realized')",RUNTIME)
        self.assertIn("frame.get('holding')",RUNTIME)
        self.assertIn("wallet.get('realized'",RUNTIME)
    def test_integrated_dashboard_sums_strategy_realized_without_making_fills(self):
        self.assertIn("if(finite(v.realized)){realized+=v.realized;knownR++;}",UI)
        self.assertIn("sum(st.executions,true)",UI)
        self.assertIn("if(reusedLocalRecords&&meaningfulLocalRecords&&!hubGapChanged",UI)
    def test_manual_execution_requires_durable_native_sell_evidence(self):
        self.assertIn("COIN_MANUAL_FILL_NOT_RECORDED",MANUAL)
        # v086 routes stock exits to their original strategy owners. A
        # native PAPER fill is authoritative only after its exact original
        # SELL row exists; a pending intent cannot masquerade as completion.
        STOCK=(ROOT/"python/stock_manual_v086.py").read_text(encoding="utf-8")
        self.assertIn("STOCK_MA_SELL_FILL_NOT_RECORDED",STOCK)
        self.assertIn("STOCK_GMO_MANUAL_INTENT_REJECTED",STOCK)
        self.assertIn("PENDING_REAL_KRX_BOOK",STOCK)
        self.assertIn("STOCK_CLOSE_SELL_FILL_NOT_RECORDED",STOCK)
        self.assertIn("STOCK_AT_MANUAL_SELL_NOT_RECORDED",STOCK)

if __name__=="__main__":unittest.main()
