"""EXE observation timestamps are separate from actual SELL fills."""
import sys
from pathlib import Path
import unittest
import sqlite3
import tempfile
import ast
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
import research_evidence_v036 as m

class EvidenceTests(unittest.TestCase):
    def test_home_timeframe_target_rate_is_confirmed_over_all_positions(self):
        source=Path(__file__).parents[1]/"app/src/main/python/android_runtime_v078.py"
        tree=ast.parse(source.read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                and n.name=="_target_summary")
        ns={}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),"exec"),ns)
        holdings=[{"id":1,"status":"OPEN","first_sold":1,"qty":5}]+[
            {"id":i,"status":"OPEN","first_sold":0,"qty":4}
            for i in range(2,11)]
        result=ns["_target_summary"](holdings,"stock_at")
        self.assertEqual(result["hit"],1)
        self.assertEqual(result["pending"],9)
        self.assertEqual(result["eligible"],10)
        self.assertEqual(result["rate_pct"],10.0)
        self.assertEqual(result["decided_rate_pct"],100.0)

    def test_first_target_sell_fill_survives_over_30000_unrelated_fills(self):
        from android_sqlite_reader import db_rows
        source=Path(__file__).parents[1]/"app/src/main/python/android_runtime_v078.py"
        nodes=ast.parse(source.read_text())
        fn=next(n for n in nodes.body if isinstance(n,ast.FunctionDef)
                and n.name=="_research_first_target_fills")
        ns={"_core":SimpleNamespace(_db_rows=db_rows)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),"exec"),ns)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"paper.sqlite3"
            with sqlite3.connect(path) as con:
                con.execute("""CREATE TABLE executions(id INTEGER,position_id INTEGER,
                    side TEXT,reason TEXT,price REAL,ts TEXT)""")
                con.execute("INSERT INTO executions VALUES(1,7,'SELL','FIRST_TARGET_40PCT',120,'20261103105232')")
                con.executemany("INSERT INTO executions VALUES(? ,11,'SELL','OTHER',130,'20261103140000')",
                    ((i,) for i in range(2,30009)))
            lookup=ns["_research_first_target_fills"](path)
            self.assertEqual(lookup[7]["sell_price"],120)
            self.assertEqual(lookup[7]["sell_at"],"20261103105232")

    def test_first_target_observation_is_not_a_sell_fill(self):
        row={"first_target":120,"first_observed_target_after_touch":"20261103135900",
             "observation_minutes":19,"observation_provenance":"KRX_1M"}
        result=m.from_at_forward(row)
        self.assertEqual(result["observed_target_at"],"20261103135900")
        self.assertEqual(result["observed_target_price"],120)
        self.assertEqual(result["observed_target_evidence"],"STOCK_MINUTE_HIGH_NOT_FILL")
        self.assertFalse(result["sell_evidence"])

    def test_missing_observation_marks_explicit_reason_not_target_miss(self):
        a=m.observation_status({"missing_observation_minutes":200,"observation_minutes":1})
        self.assertEqual(a,"DATA_GAP")
        b=m.observation_status({"missing_observation_minutes":0,"observation_minutes":0})
        self.assertEqual(b,"NO_OBSERVATIONS")
        self.assertEqual(m.observation_status({"observation_minutes":10}),"OBSERVING")

    def test_original_coin_upbit_book_bid_observation_after_virtual_entry(self):
        rows=[
          {"source":"UPBIT_BOOK_BID","first_at":"2026-11-03T11:03:00",
           "last_at":"2026-11-03T11:03:59","bid_high":"105"},
          {"source":"UPBIT_BOOK_BID","first_at":"2026-11-03T11:02:00",
           "last_at":"2026-11-03T11:02:40","bid_high":"102"}]
        target=m.coin_shadow_observed_target(rows,104,"2026-11-03T11:00:00")
        self.assertEqual(target["observed_target_price"],104.0)
        self.assertEqual(target["observed_target_at"],"2026-11-03T11:03:59")
        self.assertEqual(target["observed_target_evidence"],"COIN_BOOK_BID_HIGH_NOT_FILL")
        self.assertIsNone(m.coin_shadow_observed_target(rows,110,"2026-11-03T11:00:00"))

    def test_coin_book_target_row_verdict_keeps_virtual_vs_actual_evidence(self):
        a={"bought":False,"entry_observed":True,"outcome":"PENDING","target_hit":None}
        b=m.attach_coin_virtual_goal(a,{"observed_target_at":"2026-11-03T12:00",
            "observed_target_price":104.0,
            "observed_target_evidence":"COIN_BOOK_BID_HIGH_NOT_FILL"})
        self.assertEqual(b["outcome"],"HIT")
        self.assertTrue(b["target_hit"])
        self.assertFalse(b.get("virtual_sell_evidence"))
        c={"bought":True,"entry_observed":True,"outcome":"PENDING","target_hit":None}
        m.attach_coin_virtual_goal(c,{"observed_target_at":"2026-11-03T12:00",
            "observed_target_price":104.0,
            "observed_target_evidence":"COIN_BOOK_BID_HIGH_NOT_FILL"})
        self.assertEqual(c["outcome"],"PENDING")
        self.assertIsNone(c["target_hit"])

    def test_high_target_is_never_a_buy_or_sell_fill(self):
        r=m.from_at_forward({"first_target":100,
            "first_observed_target_after_touch":None,
            "observation_minutes":3,"observed_max_price":120})
        self.assertIsNone(r["observed_target_at"])
        self.assertEqual(r["observed_target_evidence"],"")
if __name__=="__main__":unittest.main()
