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
    def test_gmo_paper_live_quote_scope_and_broker_provenance(self):
        source=(PY/"android_runtime_v078.py").read_text(encoding="utf-8")
        node=next(n for n in ast.parse(source).body
                  if isinstance(n,ast.FunctionDef) and n.name=="_patched_gmo")
        observed=[]
        class Engine:
            def __init__(self):
                self.orders={}
            def paper_match(self,q):observed.append(("ORIGINAL_PAPER",q.code))
            def paper_match_krx_print_proxy(self,q,volume,**kw):
                observed.append(("PAPER_0B",q.code,volume));return 1
            def _order(self,code,side,purpose,ref,limit,qty,at):
                order=types.SimpleNamespace(id="GMO-"+str(len(self.orders)+1),code=code,
                     state="NEW",side=side,purpose=purpose,limit=limit,
                     requested=qty,filled=0,time=at)
                self.orders[order.id]=order
                return order
            def _d1_buy_window_ok(self,order,at):return True
            def mark_submitted(self,oid,at):self.orders[oid].state="OPEN"
            def fill(self,oid,qty,price,at):
                self.orders[oid].filled+=qty
                self.orders[oid].state="FILLED"
            def _log(self,*args):observed.append(("GMO_LOG",args))
        eng=Engine()
        gmo=types.SimpleNamespace(engine=eng)
        rt=types.SimpleNamespace(stock_runner=types.SimpleNamespace(gmo=gmo),
            _live_stock=False,_live={"stock":False},root="unused",
            kiwoom=types.SimpleNamespace(fok=lambda *args: (
              observed.append(("BROKER_FOK",args[0],args[1],args[2])) or
              {"qty":args[3],"price":args[4],"order_id":"OID"})),
            on_live_fill=lambda *args:observed.append(("LIVE_CONFIRMED",args)),
            _err=lambda *args:observed.append(("RUNTIME_ERROR",args)))
        rt.live_enabled=lambda m:rt._live_stock
        def original_live_patch(self):
            eng.paper_match=lambda q:observed.append(("OLD_UNSCOPED_BROKER",q.code))
            eng.paper_match_krx_print_proxy=lambda *a,**k:0
        class NoFill(Exception):pass
        scope={"_ORIG_PATCH_GMO":original_live_patch,
               "_core":types.SimpleNamespace(LiveOrderNoFill=NoFill,_safe_key=lambda x:x),
               "math":__import__("math"),"UnsafePaperExit":RuntimeError,
               "refuse_paper_sell_if_live_open":lambda *a:None,
               "verified_gmo_live_quantity":lambda *a:0}
        exec(compile(ast.Module(body=[node],type_ignores=[]),"<gmo>",'exec'),scope)
        scope["_patched_gmo"](rt)
        q=lambda code,lag=0.5:types.SimpleNamespace(
            code=code,venue="KRX",receive_delay_seconds=lag,
            ask=100,ask_size=10,bid=99,bid_size=10,trading_day_index=1,
            at="2026-11-19T09:00:00+09:00")
        eng.paper_match(q("005930"))
        self.assertEqual(observed[-1],("ORIGINAL_PAPER","005930"))
        self.assertEqual(eng.paper_match_krx_print_proxy(q("005930"),20,
                         buy_missing_book=True,sell_missing_book=False),1)
        # An old PAPER order must not be submitted to a real broker on arm.
        old=eng._order("005930","BUY","D+1_PAPER",100,100,1,q("005930").at)
        rt._live_stock=True
        live=eng._order("005930","BUY","D+1_LIVE",100,100,2,q("005930").at)
        eng.paper_match(q("000660"))
        eng.paper_match(q("005930",lag=10))
        self.assertEqual(len([x for x in observed if x[0]=="BROKER_FOK"]),0)
        eng.paper_match(q("005930"))
        placed=[x for x in observed if x[0]=="BROKER_FOK"]
        self.assertEqual(len(placed),1)
        self.assertEqual(placed[0][2],"BUY")
        self.assertEqual(old.state,"NEW")
        self.assertEqual(live.state,"FILLED")
        self.assertEqual(eng.paper_match_krx_print_proxy(q("005930"),20,
                         buy_missing_book=True,sell_missing_book=False),0)
        # A PAPER position may never trigger a live SELL without GMO broker
        # position provenance even when a valid top-of-book quote exists.
        eng._order("005930","SELL","TP1",95,95,1,q("005930").at)
        eng.paper_match(q("005930"))
        self.assertEqual(len([x for x in observed if x[0]=="BROKER_FOK"]),1)

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

    def test_gmo_broker_fills_not_confused_with_other_stock_strategies(self):
        guard=load_module("v050_guard","android_live_position_guard_v049.py")
        with tempfile.TemporaryDirectory() as tmp:
            broker=Path(tmp)/"broker"
            broker.mkdir()
            database=broker/"live_order_journal.sqlite3"
            with sqlite3.connect(database) as c:
                c.execute("""CREATE TABLE live_orders(
                    event_key TEXT,market TEXT,symbol TEXT,
                    side TEXT,status TEXT,fill_qty TEXT)""")
                c.executemany("INSERT INTO live_orders VALUES(?,?,?,?,?,?)",[
                    ("SQL-AT-1","stock","005930","BUY","FILLED","9"),
                    ("GMO-BUY-1","stock","005930","BUY","FILLED","5"),
                    ("GMO-SELL-1","stock","005930","SELL","FILLED","2"),
                    ("GMO-OLD-2","stock","000660","BUY","FILLED","4")])
            self.assertEqual(guard.verified_gmo_live_quantity(tmp,"A005930"),3)
            self.assertEqual(guard.verified_gmo_live_quantity(tmp,"000660"),4)
            with sqlite3.connect(database) as c:
                c.execute("INSERT INTO live_orders VALUES(?,?,?,?,?,?)",
                    ("GMO-PENDING","stock","005930","SELL","SENT",""))
            with self.assertRaisesRegex(guard.UnsafePaperExit,"GMO_BROKER_ORDER_UNRESOLVED"):
                guard.verified_gmo_live_quantity(tmp,"005930")
