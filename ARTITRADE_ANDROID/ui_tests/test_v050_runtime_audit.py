"""v050: preserve intended PAPER/LIVE operation, settings and research provenance."""
import ast
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from unittest import TestCase, mock

ROOT=Path(__file__).parents[1]
PY=ROOT/"app/src/main/python"
sys.path.insert(0,str(PY))


def load_module(name,file):
    spec=importlib.util.spec_from_file_location(name,PY/file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class V050Audit(TestCase):
    def test_gmo_paper_and_live_are_routed_independently(self):
        source=(PY/"android_runtime_v078.py").read_text(encoding="utf-8")
        node=next(n for n in ast.parse(source).body
                  if isinstance(n,ast.FunctionDef) and n.name=="_patched_gmo")
        observed=[]
        class Engine:
            def paper_match(self,q):observed.append(("ORIGINAL_PAPER",q))
            def paper_match_krx_print_proxy(self,q,volume,**kw):
                observed.append(("PAPER_0B",q,volume));return 1
        eng=Engine()
        gmo=types.SimpleNamespace(engine=eng)
        rt=types.SimpleNamespace(
            stock_runner=types.SimpleNamespace(gmo=gmo),
            _live_stock=False,
            live_enabled=lambda m:rt._live_stock)
        def original_live_patch(self):
            # Reproduce v071 bridge replacement, not the original EXE's
            # paper matcher: only a LIVE broker request gets through.
            eng.paper_match=lambda q:observed.append(("BROKER_FOK",q)) if self._live_stock else None
            eng.paper_match_krx_print_proxy=lambda *a,**k:0
        scope={"_ORIG_PATCH_GMO":original_live_patch}
        exec(compile(ast.Module(body=[node],type_ignores=[]),"<gmo>",'exec'),scope)
        scope["_patched_gmo"](rt)
        eng.paper_match("signal-1")
        self.assertEqual(observed[-1],("ORIGINAL_PAPER","signal-1"))
        self.assertEqual(eng.paper_match_krx_print_proxy("tick",20,buy_missing_book=True,sell_missing_book=False),1)
        rt._live_stock=True
        eng.paper_match("signal-2")
        self.assertEqual(observed[-1],("BROKER_FOK","signal-2"))
        self.assertEqual(eng.paper_match_krx_print_proxy("tick",20,buy_missing_book=True,sell_missing_book=False),0)

    def test_corrupt_settings_are_preserved_and_not_replaced_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=types.ModuleType("android_live_bridge")
            fake._RUNTIME=types.SimpleNamespace(root=tmp)
            with mock.patch.dict(sys.modules,{"android_live_bridge":fake}):
                operation=load_module("v050_operation", "android_operation_bridge.py")
            path=Path(tmp)/"ANDROID_OPERATION_V074.json"
            original='{"stock":{"at":123},"coin":'
            path.write_text(original,encoding="utf-8")
            reply=json.loads(operation.apply_saved())
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["error"],"OPERATION_SETTINGS_INVALID_PRESERVED")
            self.assertEqual(path.read_text(encoding="utf-8"),original)
            with self.assertRaisesRegex(ValueError,"OPERATION_SETTINGS_INVALID_PRESERVED"):
                operation._load(fake._RUNTIME)

    def test_failed_operating_account_change_not_saved_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=types.ModuleType("android_live_bridge")
            fake._RUNTIME=types.SimpleNamespace(root=tmp)
            with mock.patch.dict(sys.modules,{"android_live_bridge":fake}):
                op=load_module("v050_operation_failure","android_operation_bridge.py")
            current=op._defaults()
            op._save(fake._RUNTIME,current)
            baseline=(Path(tmp)/"ANDROID_OPERATION_V074.json").read_bytes()
            changed=op._defaults()
            changed["stock"]["at"]["frames"]["5m"]["seed"]=3_000_000
            with mock.patch.object(op,"_apply_runtime",return_value={
                "ok":False,"strategies":{"stock.at":{"applied":False,"error":"NO_CASH"}}}):
                result=json.loads(op.apply(json.dumps(changed)))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"],"OPERATION_PARTIAL_APPLY_NOT_PERSISTED")
            self.assertEqual((Path(tmp)/"ANDROID_OPERATION_V074.json").read_bytes(),baseline)

    def test_research_audit_labels_all_unproven_ledgers_and_skips_unchanged(self):
        audit=load_module("v050_audit", "android_research_audit.py")
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            stock=base/"stock"
            stock.mkdir()
            source=stock/"ARTITRADE_D.sqlite3"
            with sqlite3.connect(source) as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("CREATE TABLE executions(id INTEGER PRIMARY KEY,code TEXT,side TEXT)")
                c.execute("INSERT INTO executions VALUES(1,'005930','BUY')")
            state=stock/"PROJECTGMO_V1.0_account_state.json"
            state.write_text(json.dumps({"fills":[{"code":"005930","side":"BUY"}],"positions":{}}),
                             encoding="utf-8")
            first=audit.capture(base)
            self.assertTrue(first["ok"])
            self.assertGreaterEqual(first["scanned_sources"],2)
            with sqlite3.connect(base/audit.AUDIT_REL) as db:
                kinds={x[0] for x in db.execute("SELECT DISTINCT record_kind FROM observations")}
                self.assertIn("UNVERIFIED_LEDGER",kinds)
                self.assertNotIn("ACTUAL",kinds)
                old_count=db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            second=audit.capture(base)
            self.assertEqual(second["added_observations"],0)
            self.assertEqual(second["scanned_sources"],0)
            self.assertGreaterEqual(second["skipped_unchanged_sources"],2)
            with sqlite3.connect(source) as c:
                c.execute("INSERT INTO executions VALUES(2,'005930','SELL')")
            third=audit.capture(base)
            self.assertEqual(third["scanned_sources"],1)
            self.assertEqual(third["added_observations"],1)
            with sqlite3.connect(base/audit.AUDIT_REL) as db:
                count=db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
                self.assertEqual(count,old_count+1)
                db.execute("UPDATE observations SET record_kind='ACTUAL' WHERE record_kind='UNVERIFIED_LEDGER'")
            audit.capture(base)
            with sqlite3.connect(base/audit.AUDIT_REL) as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM observations WHERE record_kind='ACTUAL'").fetchone()[0],0)
