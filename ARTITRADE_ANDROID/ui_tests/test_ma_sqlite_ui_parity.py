#!/usr/bin/env python3
"""MA exported UI rows must work with the actual frozen EXE schema (no reason column).

Regression for a previous SELECT candidate.reason crash which made
MA/CLOSE/coin/GMO screens blank in the same hub-record refresh cycle.
"""
import ast
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

SRC=Path(__file__).parents[1]/"app/src/main/python/android_runtime_v078.py"
root=ast.parse(SRC.read_text(encoding="utf-8"))
symbols={"_stock_ma_full","_closed_position_win_rate","_target_summary",
         "_attach_fill_cost","_first_existing"}
selected=[x for x in root.body if isinstance(x,ast.FunctionDef) and x.name in symbols]
scope={"Path":Path,"_FRAMES":("5m","30m","60m","day"),"math":__import__("math")}
scope["_ma_pending_diagnostics"]=lambda self,snap:snap


def db_rows(path,sql,*params):
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        return [dict(row) for row in db.execute(sql,params or ())]

scope["_core"]=types.SimpleNamespace(_db_rows=db_rows)
exec(compile(ast.Module(body=selected,type_ignores=[]),str(SRC),"exec"),scope)


class MAOriginalSQLiteParity(unittest.TestCase):
    def test_display_all_rows_with_no_candidate_reason_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/"stock/STOCK_MA_ENGINE_V1"
            directory.mkdir(parents=True)
            db_path=directory/"ma_paper.sqlite3"
            with sqlite3.connect(db_path) as db:
                db.executescript("""
                    CREATE TABLE candidates(
                        id INTEGER PRIMARY KEY,tf TEXT,code TEXT,name TEXT,
                        market TEXT,ref_ts TEXT,created_at TEXT,
                        limit_price INTEGER,target INTEGER,score REAL,
                        special INTEGER,low INTEGER,close_price INTEGER,
                        high INTEGER,bodytop INTEGER,recovery1 INTEGER,
                        first_fraction REAL,features TEXT,status TEXT);
                    CREATE TABLE positions(
                        id INTEGER PRIMARY KEY,candidate_id INTEGER,tf TEXT,
                        code TEXT,name TEXT,entry_at TEXT,entry_date TEXT,
                        entry INTEGER,qty INTEGER,qty0 INTEGER,first_qty INTEGER,
                        first_sold INTEGER,second_sold INTEGER,recovery INTEGER,
                        peak INTEGER,target INTEGER,status TEXT);
                    CREATE TABLE executions(
                        id INTEGER PRIMARY KEY,tf TEXT,position_id INTEGER,
                        code TEXT,name TEXT,ts TEXT,side TEXT,qty INTEGER,
                        price INTEGER,fees INTEGER,tax INTEGER,
                        net_profit INTEGER,reason TEXT);
                    INSERT INTO candidates
                        (id,tf,code,name,market,ref_ts,created_at,limit_price,
                        target,score,features,status)
                        VALUES(1,'5m','005930','삼성전자','KOSPI','20261113090000',
                            '2026-11-13T09:01:00+09:00',70000,74000,78,
                            '{"v":1}','PENDING');
                    INSERT INTO positions(id,candidate_id,tf,code,name,
                        entry_at,entry_date,entry,qty,qty0,first_qty,
                        first_sold,second_sold,recovery,peak,target,status)
                        VALUES(7,1,'5m','005930','삼성전자',
                            '2026-11-13T09:03:00+09:00','2026-11-13',70000,
                            2,2,1,0,0,0,73000,74000,'OPEN');
                    INSERT INTO executions(id,tf,position_id,code,name,ts,
                        side,qty,price,fees,tax,net_profit,reason)
                        VALUES(12,'5m',7,'005930','삼성전자',
                            '2026-11-13T09:18:00+09:00','SELL',1,72000,
                            1,0,1999,'MA_TRAIL_EXIT');
                """)
            class MA:
                db=db_path
                def snapshot(self):
                    return {
                        "positions":[{"id":7,"tf":"5m","code":"005930","name":"삼성전자",
                                      "entry_price":70000,"qty":2,"paper_pnl":4000}],
                        "pending":[{"signal_id":1,"tf":"5m","code":"005930","name":"삼성전자",
                                    "score":78,"ref_ts":"20261113090000"}],
                        "executions":[],
                        "performance_by_frame":{"5m":{"realized_won":1999,"holding_won":4000}},
                        "wallets":{"5m":{"cash":100000,"seed":2000000,
                                         "max_positions":4}}}
            engine=types.SimpleNamespace(root=str(Path(tmp)),
                    stock_runner=types.SimpleNamespace(ma=MA()))
            output=scope["_stock_ma_full"](engine)
            self.assertEqual(len(output["positions"]),1)
            self.assertEqual(output["positions"][0]["entry_at"],"2026-11-13T09:03:00+09:00")
            self.assertEqual(output["positions"][0]["candidate_id"],1)
            self.assertEqual(output["pending"][0]["created_at"],"2026-11-13T09:01:00+09:00")
            self.assertEqual(output["executions"][0]["reason"],"MA_TRAIL_EXIT")
            self.assertEqual(output["realized_pnl"],1999)
            self.assertEqual(output["unrealized_pnl"],4000)
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],1)
            print("MA_FROZEN_EXE_DB_SCHEMA_AND_UI_TIMESTAMP_PASS")


if __name__=="__main__":
    unittest.main()
