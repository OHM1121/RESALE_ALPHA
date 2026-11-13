#!/usr/bin/env python3
"""GMO files are part of the same PC archive; recovery is read-only."""
import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

RUNTIME=Path(__file__).parents[1]/"app/src/main/python/android_runtime_v078.py"
nodes=ast.parse(RUNTIME.read_text(encoding="utf8"))
selected={"_gmo_original_record","_research_source_audit","_research_record",
          "_research_bands","_research_scope"}
functions=[n for n in nodes.body if isinstance(n,ast.FunctionDef) and n.name in selected]
scope={"Path":Path,"sqlite3":sqlite3,"json":json}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(RUNTIME),"exec"),scope)


class GMOMigrationTest(unittest.TestCase):
    def test_original_json_sqlite_visible_without_live_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"stock"
            root.mkdir()
            state={
                "cfg":{"seed":2000000},"cash":1600000,"realized":10000,
                "signals":{"005930":{"name":"삼성전자"}},
                "positions":{"005930":{"name":"삼성전자","qty":2,"entry_price":70000,
                                      "first_filled_qty":1,"stage":"NORMAL"}},
                "orders":{"B1":{"side":"BUY","code":"005930","state":"PENDING",
                                  "requested":3,"filled":1,"limit":70000}},
                "buy_fills":[{"code":"005930","time":"2026-11-12","qty":2,"price":70000}],
                "fills":[{"code":"005930","time":"2026-11-13","qty":1,
                          "price":80000,"net_profit":9500,"purpose":"FIRST_TARGET"}],
            }
            (root/"PROJECTGMO_V1.0_account_state.json").write_text(
                json.dumps(state,ensure_ascii=False),encoding="utf8")
            with sqlite3.connect(root/"PROJECTGMO_V1.0_research.sqlite3") as db:
                db.executescript("""
                    CREATE TABLE gmo_candidates(
                      code TEXT,event_date TEXT,entry_date TEXT,name TEXT,
                      opening_price INTEGER,first_tp_pct REAL,gmo_score INTEGER,
                      saved_at TEXT,evidence TEXT);
                    CREATE TABLE gmo_daily_ohlc(
                      code TEXT,event_date TEXT,day_index INTEGER,high INTEGER,final INTEGER);
                    INSERT INTO gmo_candidates VALUES (
                      '005930','20261111','20261112','삼성전자',70000,.04,65,
                      '2026-11-11','{"scoring_version":"v1"}');
                    INSERT INTO gmo_daily_ohlc VALUES ('005930','20261111',1,80000,1);
                """)
            ledger,research=scope["_gmo_original_record"](Path(temp))
            self.assertEqual(len(ledger["positions"]),1)
            self.assertEqual(len(ledger["pending"]),1)
            self.assertEqual(len(ledger["executions"]),2)
            self.assertEqual(ledger["realized_pnl"],10000)
            self.assertIsNone(ledger["unrealized_pnl"])
            self.assertEqual(research["source_total"],1)
            self.assertEqual(research["items"][0]["outcome"],"HIT")
            self.assertEqual(research["items"][0]["score"],65)
    def test_missing_gmo_is_never_fabricated(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(scope["_gmo_original_record"](Path(temp)))

if __name__=="__main__":
    unittest.main()
