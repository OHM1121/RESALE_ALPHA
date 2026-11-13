#!/usr/bin/env python3
"""Original PC GMO trading versus virtual research must match frozen EXE code."""
import ast
import base64
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).parents[1]
RUNTIME=ROOT/"app/src/main/python/android_runtime_v078.py"
nodes=ast.parse(RUNTIME.read_text(encoding="utf8"))
selected={"_gmo_original_record","_research_source_audit","_research_record",
          "_research_bands","_research_scope"}
functions=[n for n in nodes.body if isinstance(n,ast.FunctionDef) and n.name in selected]
scope={"Path":Path,"sqlite3":sqlite3,"json":json}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(RUNTIME),"exec"),scope)

# Don't mock the trading research policy: load the original bundled EXE module
# from the exact 22-part asset archive that will ship inside this APK.
parts=sorted((ROOT/"app/src/main/assets/strategy_v070").glob("part_*.txt"))
if len(parts)!=22:
    raise RuntimeError("The frozen EXE strategy asset must have exactly 22 parts")
with zipfile.ZipFile(io.BytesIO(base64.b64decode(
        "".join(p.read_text(encoding="ascii") for p in parts)))) as source_zip:
    original=source_zip.read("d_gmo_score_research_paper.py")
module=types.ModuleType("d_gmo_score_research_paper")
exec(compile(original,"d_gmo_score_research_paper.py","exec"),module.__dict__)
sys.modules[module.__name__]=module


def candidate(db,code,score=65,entry_date="20261112",event_date="20261111",
              opening=70000,valid=True):
    evidence={"scoring_version":"GMO V1", "gmo_score":score,
              "score_components":{"base":40,"personal":score-40,
                                  "volume":0,"decline":0}} if valid else {}
    db.execute("INSERT INTO gmo_candidates VALUES(?,?,?,?,?,?,?,?,?)",
               (code,event_date,entry_date,code,opening,.04,score,
                "2026-11-11",json.dumps(evidence)))


def observation(db,code,day,high,final=1,event_date="20261111"):
    db.execute("INSERT INTO gmo_daily_ohlc VALUES(?,?,?,?,?,?)",
               (code,event_date,f"2026-11-{11+day:02d}",day,high,final))


class GMOMigrationTest(unittest.TestCase):
    def make_sample(self,temp):
        root=Path(temp)/"stock";root.mkdir()
        state={
            "cfg":{"seed":2000000},"cash":1600000,"realized":10000,
            "signals":{"005930":{"name":"삼성전자"}},
            "positions":{"005930":{"name":"삼성전자","qty":1,"entry_price":70000,
                                   "first_filled_qty":1,"stage":"NORMAL"}},
            "orders":{"B1":{"side":"BUY","code":"005930","state":"PENDING",
                            "requested":3,"filled":1,"limit":70000}},
            "buy_fills":[
                {"code":"005930","time":"2026-11-12T09:00:00","qty":2,"price":70000},
                {"code":"000003","time":"2026-11-12T09:01:00","qty":2,"price":70000},
                {"code":"000004","time":"2026-11-12T09:02:00","qty":2,"price":70000},
            ],
            "fills":[
                {"code":"005930","time":"2026-11-13T10:00:00","qty":1,
                 "price":80000,"net_profit":9500,"purpose":"TP_FIRST_PARTIAL"},
                {"code":"000003","time":"2026-11-13T10:05:00","qty":2,
                 "price":68000,"net_profit":-4300,"purpose":"STOP_LOSS"},
            ],
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
                    code TEXT,event_date TEXT,trade_date TEXT,day_index INTEGER,
                    high INTEGER,final INTEGER);
            """)
            for code in ("005930","000001","000002","000003","000004","000005"):
                candidate(db,code,valid=(code!="000005"),
                          opening=0 if code=="000005" else 70000)
            observation(db,"005930",1,81000)
            observation(db,"000001",1,74000) # 5.7% high, virtual HIT.
            for day in range(1,16):
                observation(db,"000002",day,71500) # full 15-day MISS.
            observation(db,"000003",1,81000) # high touch != actual SELL success
            observation(db,"000004",1,81000) # unsold actual holding, pending.
            observation(db,"000005",1,81000) # unverifiable original score.
        return scope["_gmo_original_record"](Path(temp))

    def test_original_json_sqlite_visible_without_live_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger,research=self.make_sample(tmp)
            self.assertEqual(len(ledger["positions"]),1)
            self.assertEqual(len(ledger["pending"]),1)
            self.assertEqual(len(ledger["executions"]),5)
            self.assertEqual(ledger["realized_pnl"],10000)
            self.assertIsNone(ledger["unrealized_pnl"])
            self.assertEqual(research["source_total"],6)
            rows={r["code"]:r for r in research["items"]}
            self.assertEqual(rows["005930"]["outcome"],"HIT")  # a real TP SELL
            self.assertEqual(rows["005930"]["score"],65)
            self.assertEqual(rows["000001"]["outcome"],"HIT")  # virtual 5% high
            self.assertEqual(rows["000002"]["outcome"],"MISS") # 15 final days
            self.assertEqual(rows["000003"]["outcome"],"MISS") # real STOP SELL
            self.assertEqual(rows["000004"]["outcome"],"PENDING") # real unsold
            self.assertIsNone(rows["000005"]["score"]) # no valid score proof
            self.assertEqual(research["actual_summary"]["evaluated"],2)
            self.assertEqual(research["actual_summary"]["hit"],1)
            self.assertEqual(research["virtual_summary"]["evaluated"],2)
            self.assertEqual(research["virtual_summary"]["hit"],1)

    def test_missing_gmo_is_never_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(scope["_gmo_original_record"](Path(tmp)))


if __name__=="__main__":unittest.main()
