#!/usr/bin/env python3
import ast, sqlite3, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]/"app"/"src"/"main"/"python"/"android_runtime_v078.py"
nodes=ast.parse(ROOT.read_text(encoding="utf-8"))
target=next(n for n in nodes.body if isinstance(n,ast.FunctionDef) and n.name=="_research_source_audit")
scope={"Path":Path,"sqlite3":sqlite3}
exec(compile(ast.Module(body=[target],type_ignores=[]),str(ROOT),"exec"),scope)
audit=scope["_research_source_audit"]

class PCResearchSourceAuditTest(unittest.TestCase):
    def test_sqlite_total_over_3000_and_evidence_tables_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"paper.sqlite3"
            with sqlite3.connect(db) as c:
                c.executescript("CREATE TABLE candidate_snapshots (signal_id INTEGER PRIMARY KEY,reason TEXT);"
                                "CREATE TABLE candidate_events(id INTEGER PRIMARY KEY,notes TEXT);")
                c.executemany("INSERT INTO candidate_snapshots(signal_id,reason) VALUES(?,?)",
                              [(i,"entered") for i in range(3500)])
                c.execute("INSERT INTO candidate_events VALUES(1,'why stock candidate was not selected')")
            state=audit(db,"candidate_snapshots",("candidate_events","candidate_milestones"))
            self.assertEqual(state["source_total"],3500)
            self.assertEqual(state["evidence_counts"]["candidate_events"],1)
            self.assertNotIn("candidate_milestones",state["evidence_counts"])
            self.assertEqual(state["source_status"],"OK")
    def test_absent_db_must_not_be_claimed_as_successful_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=audit(Path(tmp)/"missing.sqlite3","candidate_snapshots",())
            self.assertEqual(state["source_total"],-1)
            self.assertEqual(state["source_status"],"DB_MISSING")
    def test_db_without_research_primary_is_not_reported_as_zero_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"empty.sqlite3"
            sqlite3.connect(db).close()
            state=audit(db,"candidate_snapshots",())
            self.assertEqual(state["source_total"],-1)
            self.assertEqual(state["source_status"],"TABLE_MISSING")

if __name__=="__main__":
    unittest.main()
