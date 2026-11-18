"""v104: crash-log driven fix for the 256MB Android Java UI-JSON OOM.

Uses a *copy* of synthetic original market records. No real exchange
credentials, order execution, user DB rewrite or simulated fills.
"""
import ast
import copy
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PY=(ROOT/"app/src/main/python/android_runtime_v078.py").read_text("utf8")
SERVICE=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedTradingService.java").read_text("utf8")
BRIDGE=(ROOT/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java").read_text("utf8")
ACTIVITY=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text("utf8")
VIEW_STORE=(ROOT/"app/src/main/java/com/artitrade/mobile/LargeRecordStoreV087.java").read_text("utf8")

nodes=ast.parse(PY)
functions=[x for x in nodes.body if isinstance(x,ast.FunctionDef)
           and x.name in ("_ui_view_only_v104","_serialize_hub_v104")]
assert len(functions)==2
ns={"json":json}
exec(compile(ast.Module(body=functions,type_ignores=[]),filename="android_runtime_v078.py",mode="exec"),ns)
project=ns["_ui_view_only_v104"]
serialize=ns["_serialize_hub_v104"]

class DataPreservingOOMFix(unittest.TestCase):
    def test_massive_original_never_stringifies_as_unbounded_ui(self):
        large_reason="ORIGINAL_EVIDENCE_"+"R"*2400
        rows=[{"id":i,"code":"005930","reason_json":large_reason,
               "score":52,"paper":True,"status":"OPEN" if i<32 else "CLOSED"}
              for i in range(900)]
        original={"stock":{"at":{"ledger":{"positions":rows,"executions":rows},
                                "research":{"items":rows,"score_items":rows}},
                           "cw":{"ledger":{"positions":rows,"executions":rows},
                                 "research":{"items":rows}}},
                  "coin":{"general":{"ledger":{"positions":rows},
                                     "research":{"items":rows}}}}
        first=original["stock"]["at"]["ledger"]["positions"][0]
        before=json.dumps(original,ensure_ascii=False)
        self.assertGreater(len(before),5_000_000)
        payload=serialize(original,for_ui=True)
        self.assertLess(len(payload),5_000_000)
        view=json.loads(payload)
        self.assertEqual(view["stock"]["at"]["ledger"]["positions"][0]["id"],0)
        self.assertLessEqual(len(view["stock"]["at"]["ledger"]["positions"]),512)
        self.assertLessEqual(len(view["stock"]["at"]["ledger"]["executions"]),300)
        self.assertLessEqual(len(view["stock"]["at"]["research"]["items"]),128)
        self.assertEqual(view["ui_snapshot_v104"]["full_original_records_in_sqlite_unchanged"],True)
        self.assertTrue(view["ui_snapshot_v104"]["omitted_count_by_path"])
        self.assertEqual(len(original["stock"]["at"]["ledger"]["positions"]),900)
        self.assertEqual(first["reason_json"],large_reason)
        self.assertEqual(serialize(original,for_ui=False),before)

    def test_nested_lists_and_arbitrary_strings_are_bounded(self):
        original={"stock":{"at":{"ledger":{"positions":[{"id":1,"qty":2,
               "status":"OPEN","details":"abc"*10000}]}}}}
        view=json.loads(serialize(original,True))
        self.assertEqual(view["stock"]["at"]["ledger"]["positions"][0]["qty"],2)
        self.assertLess(len(view["stock"]["at"]["ledger"]["positions"][0]["details"]),1500)
        self.assertEqual(len(original["stock"]["at"]["ledger"]["positions"][0]["details"]),30000)

    def test_legacy_java_oom_paths_are_removed_but_full_pc_sync_is_kept(self):
        self.assertIn('module.callAttr("hub_records_ui")',BRIDGE)
        self.assertIn('JSONObject localRecords=strategy.hubRecordsUi();',SERVICE)
        self.assertIn('JSONObject records=strategy.hubRecordsUi();',SERVICE)
        self.assertNotIn('.putString("records",records.toString())',SERVICE)
        self.assertIn('LargeRecordStoreV087.write(this,records.toString());',SERVICE)
        self.assertIn('JSONObject originalForSync=strategy.hubRecords();',SERVICE)
        self.assertIn('publishPhoneLedgers(hub,originalForSync);',SERVICE)
        self.assertIn('MAX_UI_RECORD_BYTES=12_000_000L',VIEW_STORE)
        self.assertIn('UI_RECORD_PREVIOUS_OVERSIZE_REBUILD_REQUIRED',VIEW_STORE)
        self.assertIn('}else if(raw.length()>6_000_000){',ACTIVITY)
        self.assertIn("return \"v\"+version;",ACTIVITY)

    def test_view_does_not_alter_market_order_or_policy_routines(self):
        self.assertIn('hub_records_ui = hub_records_ui_v104',PY)
        self.assertIn('hub_records = _core.hub_records',PY)
        self.assertIn('return _serialize_hub_v104(out,for_ui)',PY)
        self.assertIn("from unified_recovery_v092 import install",PY)
        self.assertIn("from stock_at_policy_reconcile_v091 import install",PY)


if __name__=="__main__":
    unittest.main()
