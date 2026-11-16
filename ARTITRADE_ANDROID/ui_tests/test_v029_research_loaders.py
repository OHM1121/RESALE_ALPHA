"""Synthetic EXE-layout fixture tests for five research loaders (no trade execution)."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

BASE=Path(__file__).parents[1]/"app/src/main/python"
SRC=BASE/"android_runtime_v078.py"
import sys
sys.path.insert(0,str(BASE))
from android_sqlite_reader import db_rows

NAMES={"_research_first_target_fills","_research_scope","_research_bands","_research_record",
       "_stock_at_research","_stock_ma_research","_coin_research","_close_research"}
tree=ast.parse(SRC.read_text(encoding="utf8"))
nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in NAMES]
ns={"Path":Path,"_core":SimpleNamespace(_db_rows=db_rows)}
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SRC),"exec"),ns)


class FiveStrategyResearchLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def connect(self,rel):
        path=self.root/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        return sqlite3.connect(path)

    def test_stock_at_preserves_unselected_and_observed_not_filled(self):
        with self.connect("stock/ARTITRADE_D.sqlite3") as c:
            c.executescript("""
                CREATE TABLE candidate_snapshots(signal_id INTEGER,tf TEXT,code TEXT,name TEXT,
                    signal_ts TEXT,ref_ts TEXT,diverse_11_score REAL,limit_price INTEGER,first_target INTEGER);
                CREATE TABLE candidate_cohort_decisions(signal_id INTEGER,decision_at_evaluation TEXT,
                    reason_at_evaluation TEXT,origin_10_rank INTEGER,diverse_11_rank INTEGER);
                CREATE TABLE candidate_forward(signal_id INTEGER,minute_count INTEGER,
                    unobserved_session_minutes INTEGER,last_close INTEGER,min_price INTEGER,
                    max_price INTEGER,first_limit_touch_ts TEXT,first_target_after_touch_ts TEXT,
                    observation_basis TEXT,last_ts TEXT);
                CREATE TABLE positions(tf TEXT,code TEXT,ref_ts TEXT,status TEXT,
                    peak INTEGER,entry_ts TEXT,first_sold INTEGER);
            """)
            c.execute("INSERT INTO candidate_snapshots VALUES(1,'5m','000001','AT',"
                      "'20261114090000','20261114085900',52,1000,1100)")
            c.execute("INSERT INTO candidate_cohort_decisions VALUES(1,'NOT_SELECTED','LOW_RANK',3,2)")
            c.execute("INSERT INTO candidate_forward VALUES(1,12,1,1080,940,1150,"
                      "'20261114100100','20261114102200','OBSERVED_ONLY','20261114102200')")
        row=ns["_stock_at_research"](self.root)["items"][0]
        self.assertFalse(row["bought"])
        self.assertIsNone(row["observed_max_price"])
        self.assertEqual(row["observed_max_unverified"],1150)
        self.assertEqual(row["forward_high_status"],"UNVERIFIED_NO_SOURCE_BAR")
        self.assertEqual(row["outcome"],"PENDING")
        self.assertIsNone(row["target_hit"])

    def test_stock_ma_uses_exe_research_table_even_without_candidate(self):
        with self.connect("stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3") as c:
            c.executescript("""
                CREATE TABLE candidates(id INTEGER,tf TEXT,code TEXT,name TEXT,
                    ref_ts TEXT,created_at TEXT,score REAL,status TEXT,target INTEGER,features TEXT);
                CREATE TABLE positions(id INTEGER,candidate_id INTEGER,tf TEXT,code TEXT,name TEXT,
                    status TEXT,first_sold INTEGER,peak INTEGER,target INTEGER,entry_at TEXT);
                CREATE TABLE research(id INTEGER,ts TEXT,tf TEXT,code TEXT,ref_ts TEXT,
                    verdict TEXT,detail TEXT);
            """)
            c.execute("INSERT INTO research VALUES(1,'2026-11-14T10:00','5m','000222',"
                      "'20261114100000','REJECT','condition not met')")
        result=ns["_stock_ma_research"](self.root)
        self.assertEqual(len(result["items"]),1)
        self.assertEqual(result["items"][0]["code"],"000222")
        self.assertEqual(result["items"][0]["entry_status"],"RESEARCH_UNSELECTED")
        self.assertFalse(result["items"][0]["bought"])

    def test_stock_close_old_followup_without_mfe_column_is_still_visible(self):
        with self.connect("stock/close/AT_CLOSE_PAPER.sqlite3") as c:
            c.executescript("""
                CREATE TABLE signals(id INTEGER,ts TEXT,strategy TEXT,code TEXT,name TEXT,
                    decision TEXT,entry_result TEXT,feature_json TEXT,reason_json TEXT);
                CREATE TABLE positions(id INTEGER,signal_id INTEGER,status TEXT,first_sold INTEGER);
                CREATE TABLE research_candidates(id INTEGER,ts TEXT,strategy TEXT,code TEXT,
                    name TEXT,decision TEXT,near_signal INTEGER,feature_json TEXT,reason_json TEXT);
                CREATE TABLE research_followups(id INTEGER,source_signal_id INTEGER,
                    source_candidate_id INTEGER,horizon TEXT,return_pct REAL,
                    status TEXT,observed_price INTEGER,observed_ts TEXT);
            """)
            c.execute("INSERT INTO research_candidates VALUES(1,'2026-11-14T15:00','CLOSE',"
                      "'009999','CLOSE','REJECT',1,'{}','HOURLY_DELAY')")
            c.execute("INSERT INTO research_followups VALUES(1,NULL,1,'1D',2.5,'DONE',1025,'2026-11-15T15:00')")
        row=ns["_close_research"](self.root)["items"][0]
        self.assertFalse(row["bought"])
        self.assertEqual(row["observed_return_pct"],2.5)
        self.assertIsNone(row["observed_mfe_pct"])

    def test_both_coin_research_files_are_independent(self):
        for which,market in (("base","KRW-BTC"),("ma","KRW-ETH")):
            with self.connect("coin/"+which+"/upbit_paper.sqlite3") as c:
                c.executescript("""
                    CREATE TABLE candidates(id INTEGER,tf TEXT,market TEXT,name TEXT,
                        ref_at TEXT,created_at TEXT,score REAL,limit_price TEXT,target TEXT,reason TEXT);
                    CREATE TABLE positions(tf TEXT,market TEXT,ref_at TEXT,status TEXT,first_sold_qty TEXT);
                    CREATE TABLE candidate_decisions(candidate_id INTEGER,decision TEXT,reason TEXT,
                        competing_score REAL,at TEXT);
                """)
                c.execute("INSERT INTO candidates VALUES(1,'5m',?,?,"
                          "'20261114090100','2026-11-14T09:01',75,'100','110','RULE_PASS')",
                          (market,market))
                c.execute("INSERT INTO candidate_decisions VALUES(1,'NOT_SELECTED','LOWER_SCORE',"
                          "87,'2026-11-14T09:02')")
            rows=ns["_coin_research"](self.root,which)["items"]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["market"],market)
            self.assertFalse(rows[0]["bought"])
            self.assertEqual(rows[0]["competing_score"],87)
            self.assertIsNone(rows[0]["target_hit"])


if __name__=="__main__":
    unittest.main()
