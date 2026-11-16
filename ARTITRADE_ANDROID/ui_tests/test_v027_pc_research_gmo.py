#!/usr/bin/env python3
"""v0.7.27 regression: exact coin schema and persisted PC GMO evidence."""
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
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).parents[1]
RUNTIME=ROOT/"app/src/main/python/android_runtime_v078.py"
tree=ast.parse(RUNTIME.read_text(encoding="utf8"))
names={"_coin_research","_gmo_original_record","_gmo_fill_identity",
       "_merge_gmo_pc_evidence","_research_source_audit","_research_scope",
       "_research_bands","_research_record","_research_history",
       "_first_existing","_attach_fill_cost",
       "_hub_records_v078"}
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
class Reader:
    @staticmethod
    def _db_rows(db,sql,params=()):
        if not Path(db).is_file():
            return []
        with sqlite3.connect(db) as c:
            c.row_factory=sqlite3.Row
            try:
                return [dict(x) for x in c.execute(sql,params)]
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return []
                raise
ns={"Path":Path,"sqlite3":sqlite3,"json":json,"Counter":Counter,
    "_core":Reader(),"threading":__import__("threading"),"time":__import__("time"),"math":__import__("math")}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(RUNTIME),"exec"),ns)
parts=sorted((ROOT/"app/src/main/assets/strategy_v070").glob("part_*.txt"))
with zipfile.ZipFile(io.BytesIO(base64.b64decode(
        "".join(p.read_text(encoding="ascii") for p in parts)))) as z:
    original=z.read("d_gmo_score_research_paper.py")
mod=types.ModuleType("d_gmo_score_research_paper")
exec(compile(original,"d_gmo_score_research_paper.py","exec"),mod.__dict__)
sys.modules[mod.__name__]=mod

class PCResearchGMORegression(unittest.TestCase):
    def test_coin_base_and_ma_use_original_candidates_schema(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for mode in ("base","ma"):
                target=root/"coin"/mode
                target.mkdir(parents=True)
                with sqlite3.connect(target/"upbit_paper.sqlite3") as db:
                    db.executescript("""CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,
                        name TEXT,ref_at TEXT,score REAL,limit_price TEXT,target TEXT,reason TEXT,
                        created_at TEXT,features TEXT);
                        CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,
                        status TEXT,first_sold_qty TEXT);
                        CREATE TABLE coin_shadow_trials(candidate_id INTEGER,state TEXT,entry_at TEXT,
                        entry_price REAL,peak REAL,realized_net REAL,sell_at TEXT);
                        CREATE TABLE coin_shadow_fills(candidate_id INTEGER,side TEXT,reason TEXT);""")
                    db.execute("INSERT INTO candidates VALUES(1,'5m','KRW-BTC','BTC',"
                               "'2026-11-12T09:00:00',65,'50000','55000','TEST',"
                               "'2026-11-12T09:01:00','{}')")
                    db.execute("INSERT INTO positions VALUES(1,'5m','KRW-BTC',"
                               "'2026-11-12T09:00:00','OPEN','0')")
                research=ns["_coin_research"](root,mode)
                self.assertEqual(len(research["items"]),1)
                self.assertEqual(research["items"][0]["score"],65)
                self.assertTrue(research["items"][0]["bought"])
                self.assertEqual(research["items"][0]["at"],"2026-11-12T09:01:00")
                self.assertEqual(research["items"][0]["outcome"],"PENDING")

    def make_sample(self,root):
        stock=root/"stock"
        stock.mkdir()
        data={"cfg":{"seed":2000000},"cash":1800000,"realized":250,
              "signals":{"005930":{"name":"Samsung"}},
              "positions":{"005930":{"name":"Samsung","qty":2,"entry_price":70000,
                                     "entry_date":"20261112","first_filled_qty":1}},
              "orders":{},
              "buy_fills":[{"code":"005930","time":"2026-11-12T09:01:00",
                            "qty":3,"price":70000,"purpose":"BUY_ENTRY"}],
              "fills":[{"code":"005930","time":"2026-11-13T10:00:00","qty":1,
                        "price":72000,"net_profit":250,"purpose":"TP_FIRST_PARTIAL"}]}
        (stock/"PROJECTGMO_V1.0_account_state.json").write_text(
            json.dumps(data),encoding="utf8")
        with sqlite3.connect(stock/"PROJECTGMO_V1.0_research.sqlite3") as db:
            db.executescript("""CREATE TABLE gmo_candidates(code TEXT,event_date TEXT,entry_date TEXT,
                name TEXT,opening_price INTEGER,first_tp_pct REAL,gmo_score INTEGER,
                saved_at TEXT,evidence TEXT);
                CREATE TABLE gmo_daily_ohlc(code TEXT,event_date TEXT,trade_date TEXT,
                day_index INTEGER,high INTEGER,final INTEGER);""")
            evidence={"scoring_version":"GMO V1","gmo_score":65,
                      "score_components":{"base":40,"personal":25,"volume":0,"decline":0}}
            db.execute("INSERT INTO gmo_candidates VALUES(?,?,?,?,?,?,?,?,?)",
                ("005930","20261111","20261112","Samsung",70000,.04,65,
                 "2026-11-11",json.dumps(evidence)))
        return stock

    def test_gmo_partial_phone_snapshot_merges_persisted_pc_paper(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            self.make_sample(root)
            ledger,research=ns["_gmo_original_record"](root)
            self.assertEqual(len(ledger["positions"]),1)
            self.assertEqual(len(ledger["executions"]),2)
            self.assertEqual(ledger["realized_pnl"],250)
            self.assertEqual(research["source_total"],1)
            self.assertEqual(research["items"][0]["outcome"],"HIT")
            only_buy=[e for e in ledger["executions"] if e["side"]=="BUY"]
            live={"positions":[],"pending":[],"executions":only_buy,
                  "accounts":[{"tf":"gmo","realized_pnl":0}],
                  "realized_pnl":0,"unrealized_pnl":None}
            merged=ns["_merge_gmo_pc_evidence"](live,ledger)
            self.assertEqual(len(merged["executions"]),2)
            self.assertEqual(len(merged["positions"]),1)
            self.assertEqual(merged["realized_pnl"],250)
            self.assertEqual(merged["accounts"][0]["realized_pnl"],250)
            merged_again=ns["_merge_gmo_pc_evidence"](merged,ledger)
            self.assertEqual(len(merged_again["executions"]),2)

    def test_gmo_missing_state_never_invents_fills_or_wins(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            stock=self.make_sample(root)
            (stock/"PROJECTGMO_V1.0_account_state.json").unlink()
            ledger,research=ns["_gmo_original_record"](root)
            self.assertEqual(len(ledger["executions"]),0)
            self.assertEqual(research["source_total"],1)
            self.assertEqual(research["load_status"],"ACCOUNT_STATE_MISSING")
            self.assertEqual(research["items"][0]["outcome"],"PENDING")

    def test_fault_isolated_from_other_markets_and_original_gmo(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            self.make_sample(root)
            class Runtime:
                stock_runner=types.SimpleNamespace(gmo=None)
                coin_runner=types.SimpleNamespace(ledger=1,ma_ledger=2)
                def __init__(self):
                    self.root=root
                    self.errors=[]
                def _err(self,k,e):
                    self.errors.append(k)
            def fail(*args):
                raise RuntimeError("AT source read failed")
            ns["_capture_pc_research_evidence_async"]=lambda self:None
            ns["_ORIG_HUB_RECORDS"]=lambda self:json.dumps({"stock":{"at":{"ledger":{}}}})
            ns["_stock_at_v19_full"]=fail
            ns["_stock_ma_full"]=lambda self:{"positions":[]}
            ns["_close_full"]=lambda self:{"positions":[]}
            ns["_coin_full"]=lambda ledger:{"positions":[{"tf":"5m","code":str(ledger)}]}
            ns["_research_history"]=lambda self,out:out.setdefault("research_test",True)
            runtime=Runtime()
            loaded=json.loads(ns["_hub_records_v078"](runtime))
            self.assertIn("stock_at_history_v078",runtime.errors)
            self.assertTrue(loaded.get("research_test"))
            self.assertEqual(len(loaded["coin"]["general"]["ledger"]["positions"]),1)
            self.assertEqual(len(loaded["stock"]["at"]["ledger"]["gmo"]["executions"]),2)

if __name__=="__main__":
    unittest.main()
