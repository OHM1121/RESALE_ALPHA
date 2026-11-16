"""Actual buy target-hit must require actual partial SELL, never market HIGH alone."""
import ast
import types
import unittest
from pathlib import Path

SRC=Path(__file__).parents[1]/"app/src/main/python/android_runtime_v078.py"
tree=ast.parse(SRC.read_text(encoding="utf8"))
names={"_target_summary","_research_scope","_research_bands","_research_record",
       "_stock_at_research","_stock_ma_research","_coin_research"}
functions=[node for node in tree.body
           if isinstance(node,ast.FunctionDef) and node.name in names]
core=types.SimpleNamespace(_db_rows=lambda path,sql,*args:[])
scope={"Path":Path,"_core":core}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(SRC),"exec"),scope)


class ActualOnlyOnSellTests(unittest.TestCase):
    def setUp(self):
        self.positions=[]
        self.candidates=[]
        self.core_calls=[]
        def query(path,sql,*args):
            self.core_calls.append(sql)
            if "FROM candidate_snapshots" in sql or "FROM candidates ORDER BY" in sql:
                return self.candidates
            if "FROM positions" in sql:
                return self.positions
            return []
        core._db_rows=query
    def test_actual_frame_summary_ignores_unfilled_high(self):
        fn=scope["_target_summary"]
        for strategy in ("stock_at","stock_ma"):
            position={"status":"OPEN","qty":2,"peak":200,
                      "target":100,"ref_bodytop":100,"first_sold":0}
            self.assertEqual(fn([position],strategy)["pending"],1)
            self.assertEqual(fn([position],strategy)["hit"],0)
            position["status"]="CLOSED"
            self.assertEqual(fn([position],strategy)["miss"],1)
            position["first_sold"]=1
            self.assertEqual(fn([position],strategy)["hit"],1)
        coin={"status":"OPEN","peak":200,"target":100,"first_sold_qty":"0"}
        self.assertEqual(fn([coin],"coin")["pending"],1)
        coin["first_sold_qty"]="0.4"
        self.assertEqual(fn([coin],"coin")["hit"],1)

    def test_AT_actual_peak_high_is_not_filled_sell(self):
        self.candidates=[{"signal_id":1,"tf":"5m","code":"005930",
            "name":"삼성전자","ref_ts":"2026-11-13T09:00:00+09:00",
            "at":"2026-11-13T09:00:00+09:00","first_target":100,"score":75}]
        position={"tf":"5m","code":"005930","ref_ts":self.candidates[0]["ref_ts"],
                  "status":"OPEN","peak":150,"first_sold":0}
        self.positions=[position]
        result=scope["_stock_at_research"]("/tmp/source")
        self.assertEqual(result["items"][0]["outcome"],"PENDING")
        position["status"]="CLOSED"
        self.assertEqual(scope["_stock_at_research"]("/tmp/source")["items"][0]["outcome"],"MISS")
        position["first_sold"]=1
        self.assertEqual(scope["_stock_at_research"]("/tmp/source")["items"][0]["outcome"],"HIT")

    def test_MA_real_sell_not_high(self):
        self.candidates=[{"id":4,"tf":"5m","code":"005930","name":"삼성전자",
            "ref_ts":"a","at":"b","score":72,"status":"QUALIFIED",
            "target":100,"features":"{}"}]
        position={"id":9,"candidate_id":4,"tf":"5m","code":"005930",
            "status":"OPEN","first_sold":0,"peak":200,"target":100,"entry_at":"b"}
        self.positions=[position]
        self.assertEqual(scope["_stock_ma_research"]("/tmp/source")["items"][0]["outcome"],"PENDING")
        position["first_sold"]=1
        self.assertEqual(scope["_stock_ma_research"]("/tmp/source")["items"][0]["outcome"],"HIT")

    def test_coin_real_sell_not_high(self):
        self.candidates=[{"id":6,"tf":"5m","market":"KRW-BTC","name":"비트코인",
            "ref_ts":"2026-11-13T00:00:00Z","at":"2026-11-13T00:00:00Z",
            "score":82,"limit_price":100,"target":110,"reason":"QUALIFIED","status":"OPEN"}]
        position={"id":7,"tf":"5m","market":"KRW-BTC",
                  "ref_at":"2026-11-13T00:00:00Z",
                  "status":"OPEN","first_sold_qty":"0","peak":150,"target":110}
        self.positions=[position]
        self.assertEqual(scope["_coin_research"]("/tmp/source","base")["items"][0]["outcome"],"PENDING")
        position["first_sold_qty"]="0.2"
        self.assertEqual(scope["_coin_research"]("/tmp/source","base")["items"][0]["outcome"],"HIT")


if __name__=="__main__":unittest.main()
