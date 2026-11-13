"""End-to-end SQLite evidence persistence and full research ZIP (no 3,000 row cap)."""
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).parents[1]/"app/src/main/python"
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+".py"))
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

audit=module("android_research_audit")
backup=module("android_ledger_backup")

class PersistenceAndExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        base=Path(self.temp.name)
        self.root=base/"phone";self.root.mkdir()
        self.zip=base/"all_evidence.zip"
        self.dbs={}
        for rel in audit.SOURCES:
            db=self.root/rel
            db.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE candidates(id INTEGER PRIMARY KEY,code TEXT,
                    score INTEGER,reason TEXT,status TEXT,feature_json TEXT)""")
                conn.execute("""CREATE TABLE executions(id INTEGER PRIMARY KEY,
                    side TEXT,ts TEXT,reason TEXT,price INTEGER,net_pnl INTEGER)""")
                conn.execute("""CREATE TABLE stock_shadow_trials(
                    candidate_id INTEGER PRIMARY KEY,state TEXT,entry_at TEXT,
                    peak INTEGER,realized_net INTEGER)""")
                count=3205 if rel.endswith("ARTITRADE_D.sqlite3") else 2
                conn.executemany("INSERT INTO candidates VALUES(?,?,?,?,?,?)",[
                    (i,"005930" if rel.startswith("stock/") else "KRW-BTC",
                     80,"SCORE>=80","OBSERVED",
                     json.dumps({"volume":i,"entry_condition":"reference"}))
                    for i in range(1,count+1)])
                conn.execute("""INSERT INTO executions VALUES(
                    1,'SELL','2026-11-13T00:00:00Z','STOP_LOSS',100000,-8500)""")
                conn.execute("""INSERT INTO stock_shadow_trials VALUES(
                    1,'OPEN','2026-11-13T00:00:00Z',110000,0)""")
            self.dbs[rel]=db
        (self.root/"stock/PROJECTGMO_V1.0_account_state.json").write_text(
            json.dumps({"positions":{"005930":{"qty":2,"stage":"HOLD"}},
                        "buy_fills":[{"code":"005930","time":"2026-11-13T09:00:00+09:00",
                                      "qty":2,"price":100000}],
                        "fills":[{"code":"005930","time":"2026-11-13T09:20:00+09:00",
                                  "qty":1,"price":105000,"purpose":"TP_FIRST_PARTIAL"}]},
                       ensure_ascii=False),encoding="utf8")
        (self.root/"ANDROID_OPERATION_V074.json").write_text(
            '{"stock":{"gmo":{"seed":2000000,"max_positions":4}}}')
    def tearDown(self):
        self.temp.cleanup()

    def query_audit(self,query,args=()):
        with sqlite3.connect(self.root/audit.AUDIT_REL) as con:
            return con.execute(query,args).fetchone()[0]

    def test_append_only_real_and_virtual_versions_keep_earlier_decisions(self):
        first=audit.capture(str(self.root))
        self.assertTrue(first["ok"],first)
        self.assertGreater(first["added_observations"],3205)
        self.assertEqual(self.query_audit(
            "SELECT COUNT(*) FROM observations WHERE source_file=? AND source_table='candidates'",
            ("stock/ARTITRADE_D.sqlite3",)),3205)
        self.assertEqual(self.query_audit(
            "SELECT COUNT(*) FROM observations WHERE record_kind='VIRTUAL'"),6)
        self.assertEqual(self.query_audit(
            "SELECT COUNT(*) FROM observations WHERE record_kind='ACTUAL'"),9)
        self.assertEqual(audit.capture(str(self.root))["added_observations"],0)
        db=self.dbs["coin/base/upbit_paper.sqlite3"]
        with sqlite3.connect(db) as c:
            c.execute("UPDATE candidates SET status='FAILED',reason='NO_BID' WHERE id=1")
            c.execute("UPDATE stock_shadow_trials SET state='CLOSED',realized_net=-1200 WHERE candidate_id=1")
            c.execute("""INSERT INTO executions VALUES(
                2,'BUY','2026-11-13T00:30:00Z','BUY_SIGNAL',99000,0)""")
        result=audit.capture(str(self.root))
        self.assertEqual(result["added_observations"],3)
        self.assertEqual(self.query_audit(
            "SELECT COUNT(*) FROM observations WHERE source_file=? AND source_table='candidates' AND record_key='id:1'",
            ("coin/base/upbit_paper.sqlite3",)),2)
        with sqlite3.connect(self.root/audit.AUDIT_REL) as c:
            rows=c.execute("SELECT DISTINCT record_kind FROM observations").fetchall()
            stamp=c.execute("SELECT observed_at_kst FROM observations LIMIT 1").fetchone()[0]
        self.assertIn(("ACTUAL",),rows)
        self.assertIn(("VIRTUAL",),rows)
        self.assertIn(("SIGNAL_AND_RESEARCH",),rows)
        self.assertRegex(stamp,r"\+09:00$")

    def test_all_3205_records_and_phone_audit_are_in_research_export(self):
        audit.capture(str(self.root))
        report={"analysis/research_summary.json":json.dumps({
            "strategy":"coin_ma","visible_preview_rows":3000,
            "original_candidate_rows":3205,"actual_vs_virtual_separate":True},
            ensure_ascii=False)}
        raw=backup.make_backup(str(self.root),str(self.zip),json.dumps(report,ensure_ascii=False))
        info=json.loads(raw)
        self.assertTrue(info["ok"])
        self.assertGreaterEqual(info["report_count"],3)
        validated=backup.validate_backup(str(self.zip))
        self.assertEqual(validated["reports"],info["report_count"])
        with zipfile.ZipFile(self.zip) as z:
            manifest=json.loads(z.read("manifest.json"))
            self.assertIn(audit.AUDIT_REL,manifest["strategy_files"])
            self.assertIn("ANDROID_OPERATION_V074.json",manifest["strategy_files"])
            self.assertIn("coin/ma/upbit_paper.sqlite3",manifest["strategy_files"])
            self.assertIn("analysis/research_summary.json",manifest["report_files"])
            catalog=json.loads(z.read("analysis/source_table_catalog.json"))
            indexed=[r for r in catalog["tables"] if r["source"]=="stock/ARTITRADE_D.sqlite3"
                     and r["table"]=="candidates"]
            self.assertEqual(indexed[0]["records"],3205)
            self.assertGreater(catalog["actual_trade_rows"],0)
            self.assertGreater(catalog["virtual_research_rows"],0)
            original=Path(self.temp.name)/"untruncated.sqlite3"
            original.write_bytes(z.read("stock/ARTITRADE_D.sqlite3"))
            with sqlite3.connect(original) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],3205)
                self.assertEqual(c.execute("SELECT reason FROM executions").fetchone()[0],"STOP_LOSS")
            saved=Path(self.temp.name)/"observed.sqlite3"
            saved.write_bytes(z.read(audit.AUDIT_REL))
            with sqlite3.connect(saved) as c:
                self.assertGreater(c.execute("SELECT COUNT(*) FROM observations").fetchone()[0],3205)
        # Export must NOT change the source EXE table.
        with sqlite3.connect(self.dbs["stock/ARTITRADE_D.sqlite3"]) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],3205)

    def test_analysis_manifest_corruption_is_rejected(self):
        audit.capture(str(self.root))
        backup.make_backup(str(self.root),str(self.zip),
                           '{"analysis/research_summary.json":"actual vs virtual"}')
        bad=Path(self.temp.name)/"tampered.zip"
        with zipfile.ZipFile(self.zip) as z, zipfile.ZipFile(bad,"w") as altered:
            for name in z.namelist():
                content=z.read(name)
                if name=="analysis/research_summary.json":
                    content=b"modified after backup"
                altered.writestr(name,content)
        with self.assertRaisesRegex(ValueError,"SHA mismatch"):
            backup.validate_backup(str(bad))


if __name__=="__main__":
    unittest.main()
