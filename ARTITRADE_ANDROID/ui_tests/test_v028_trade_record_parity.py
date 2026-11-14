#!/usr/bin/env python3
"""v0.7.28: read-only verification against the bundled original EXE ledger.

These tests use synthetic candidates/orders/fills: no brokerage calls and
no trading logic mutation. A price observation cannot become a paper fill.
"""
import ast
import base64
import io
import math
import json
import sqlite3
import tempfile
import unittest
import zipfile
import sys
from pathlib import Path

ROOT=Path(__file__).parents[1]
# Isolated AST reader now imports the verified EXE 1m observation helper.
sys.path.insert(0,str(ROOT/"app/src/main/python"))
RUNTIME=ROOT/"app/src/main/python/android_runtime_v078.py"
NAMES={"_research_first_target_fills","_attach_fill_cost","_first_existing","_stock_at_research","_close_research",
       "_coin_research","_research_scope","_research_bands","_research_record"}
tree=ast.parse(RUNTIME.read_text(encoding="utf-8"))
selected=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in NAMES]
class OriginalSQLite:
    @staticmethod
    def _db_rows(db,sql,params=()):
        if not Path(db).exists():return []
        with sqlite3.connect(db) as c:
            c.row_factory=sqlite3.Row
            try:return [dict(x) for x in c.execute(sql,params)]
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):return []
                raise
ctx={"_core":OriginalSQLite(),"Path":Path,"sqlite3":sqlite3,
     "math":math,"json":json}
exec(compile(ast.Module(body=selected,type_ignores=[]),str(RUNTIME),"exec"),ctx)

class OriginalExeRecordParity(unittest.TestCase):
    def test_exe_qualified_unselected_schema_and_forward_observer_shipped(self):
        parts=sorted((ROOT/"app/src/main/assets/strategy_v070").glob("part_*.txt"))
        self.assertEqual(len(parts),22)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(
                "".join(x.read_text(encoding="ascii") for x in parts)))) as archive:
            source=archive.read("d_candidate_history.py").decode("utf-8")
            for field in ("candidate_snapshots","candidate_cohort_decisions",
                          "candidate_forward","candidate_milestones","observe_live_batch",
                          "unobserved_session_minutes"):
                self.assertIn(field,source)
            close=archive.read("at_close_store.py").decode("utf-8")
            for field in ("research_candidates","research_followups",
                          "research_scan_skips","research_bars"):
                self.assertIn(field,close)

    def test_sell_cost_and_realized_return_are_per_actual_filled_quantity(self):
        sell={"side":"SELL","qty":3,"price":1400,"net_profit":450}
        p={"entry_price":1000,"qty":8}
        row=ctx["_attach_fill_cost"](sell,p)
        self.assertEqual(row["buy_amount"],3000)
        self.assertEqual(row["buy_price"],1000)
        self.assertAlmostEqual(row["realized_pct"],15.0)
        self.assertEqual(row["net_profit"],450)
        loss=ctx["_attach_fill_cost"](
            {"side":"SELL","qty":"0.5","price":"900","net_pnl":-50},
            {"entry":"1000"})
        self.assertEqual(loss["buy_amount"],500)
        self.assertAlmostEqual(loss["realized_pct"],-10.0)
        missing=ctx["_attach_fill_cost"](
            {"side":"SELL","qty":5,"price":950,"net_profit":-200},{})
        self.assertNotIn("buy_amount",missing)
        self.assertNotIn("realized_pct",missing)

    def test_unselected_stock_candidate_keeps_original_rank_and_live_future_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"stock"/"ARTITRADE_D.sqlite3"
            db.parent.mkdir()
            with sqlite3.connect(db) as c:
                c.executescript("""
                CREATE TABLE candidate_snapshots(signal_id INTEGER,tf TEXT,code TEXT,name TEXT,
                    signal_ts TEXT,ref_ts TEXT,diverse_11_score REAL,limit_price INTEGER,first_target INTEGER);
                CREATE TABLE candidate_cohort_decisions(signal_id INTEGER,
                    decision_at_evaluation TEXT,reason_at_evaluation TEXT,
                    origin_10_rank INTEGER,diverse_11_rank INTEGER);
                CREATE TABLE candidate_forward(signal_id INTEGER,minute_count INTEGER,
                    unobserved_session_minutes INTEGER,last_close INTEGER,
                    min_price INTEGER,max_price INTEGER,first_limit_touch_ts TEXT,
                    first_target_after_touch_ts TEXT,observation_basis TEXT,last_ts TEXT);
                """)
                c.execute("INSERT INTO candidate_snapshots VALUES(1,'5m','012345','TEST',"
                          "'20261114090000','20261114085900',48,1000,1100)")
                c.execute("INSERT INTO candidate_cohort_decisions VALUES(1,'NOT_SELECTED',"
                          "'LOWER_ORIGIN_RANK_WITHIN_COHORT',3,2)")
                c.execute("INSERT INTO candidate_forward VALUES(1,21,2,1060,940,1120,"
                          "'20261114101000','20261114112500',"
                          "'KRX_OBSERVED_1M_NOT_VERIFIED_EXCHANGE_FILL','20261114112500')")
            record=ctx["_stock_at_research"](tmp)["items"][0]
            self.assertFalse(record["bought"])
            self.assertEqual(record["origin_rank"],3)
            self.assertIsNone(record["observed_max_price"])
            self.assertEqual(record["observed_max_unverified"],1120)
            self.assertEqual(record["forward_high_status"],"UNVERIFIED_NO_SOURCE_BAR")
            self.assertEqual(record["observation_minutes"],21)
            self.assertEqual(record["missing_observation_minutes"],2)
            self.assertEqual(record["entry_status"],"UNSELECTED")
            self.assertEqual(record["outcome"],"PENDING")
            self.assertIsNone(record["target_hit"])
            self.assertIn("LOWER_ORIGIN_RANK",record["unselected_reason"])

    def test_close_rejected_candidate_post_observation_never_claims_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"stock"/"close"/"AT_CLOSE_PAPER.sqlite3"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as c:
                c.executescript("""
                CREATE TABLE signals(id INTEGER,ts TEXT,strategy TEXT,code TEXT,name TEXT,
                    decision TEXT,entry_result TEXT,feature_json TEXT,reason_json TEXT);
                CREATE TABLE research_candidates(id INTEGER,ts TEXT,strategy TEXT,code TEXT,
                    name TEXT,decision TEXT,near_signal INTEGER,feature_json TEXT,reason_json TEXT);
                CREATE TABLE research_followups(id INTEGER PRIMARY KEY,source_signal_id INTEGER,
                    source_candidate_id INTEGER,horizon TEXT,return_pct REAL,
                    mfe_pct REAL,mae_pct REAL,status TEXT,observed_price INTEGER,observed_ts TEXT);
                """)
                c.execute("INSERT INTO research_candidates VALUES(1,'2026-11-14T15:00','CLOSE',"
                          "'009999','CLOSE TEST','REJECT',1,'{}','DELAYED_BOOK')")
                c.execute("INSERT INTO research_followups VALUES(NULL,NULL,1,'1D',3.7,5.2,-2.1,"
                          "'DONE',1037,'2026-11-15T15:00')")
            item=ctx["_close_research"](tmp)["items"][0]
            self.assertEqual(item["entry_status"],"RESEARCH_UNSELECTED")
            self.assertTrue(item["near_signal"])
            self.assertFalse(item["bought"])
            self.assertEqual(item["observed_return_pct"],3.7)
            self.assertEqual(item["observed_mfe_pct"],5.2)
            self.assertIsNone(item["target_hit"])

    def test_screen_off_service_and_geometric_visuals_are_real(self):
        base=ROOT/"app/src/main"
        service=(base/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text()
        manifest=(base/"AndroidManifest.xml").read_text()
        native=(base/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        track=(base/"java/com/artitrade/mobile/TradeGeometryView.java").read_text()
        rangeview=(base/"java/com/artitrade/mobile/TradeRangeView.java").read_text()
        for keyword in ("START_STICKY","PARTIAL_WAKE_LOCK",
                        "WIFI_MODE_FULL_HIGH_PERF","wifiLock.acquire()",
                        "wifiLock.release()","artitrade_background_health_v1"):
            self.assertIn(keyword,service)
        self.assertIn("android.permission.CHANGE_WIFI_STATE",manifest)
        self.assertIn("android.permission.ACCESS_WIFI_STATE",manifest)
        self.assertIn("TradeGeometryView",native)
        self.assertIn("TradeRangeView",native)
        self.assertIn("canvas.drawRoundRect",track)
        self.assertIn("c.drawRoundRect",rangeview)
        self.assertIn('tradeMetricTile(figures,"매수금액"',native)

if __name__=="__main__":
    unittest.main()
