"""0.0.19 target visibility source-of-truth regression, no native sells.

Never use historical candle highs as native receipts; no source-ledger write.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

PY=Path(__file__).resolve().parents[1]/"app/src/main/python"
sys.path.insert(0,str(PY))
import staged_exit_preview_v019 as v019


class OriginalWalletPlanTests(unittest.TestCase):
    def test_source_required_no_inferred_initial_qty(self):
        row={"id":1,"status":"OPEN","entry_price":100,"qty":7,
             "tf":"5m","code":"005930","entry_at":"2026-11-19T09:00:00+09:00"}
        self.assertIsNone(v019.preview("stock/at",row))
        self.assertIsNone(v019.preview("stock/ma",row))
        self.assertIsNone(v019.preview("coin/ma",row))
        self.assertIsNone(v019.preview("stock/cw",row))
        self.assertIsNone(v019.preview("stock/trend",row))
        self.assertIsNone(v019.preview("coin/trend",row))
        self.assertIsNone(v019.preview("coin/general",row))
        self.assertIsNone(v019.preview("stock/gmo",row))

    def test_gmo_is_original_20_and_40_not_workbook_30_35(self):
        cfg=SimpleNamespace(first_sell_fraction=.20,second_sell_fraction=.40,
                            second_target_up=.03)
        native=SimpleNamespace(code="005930",entry_price=100,
            total_qty=100,first_profit_target=.07,
            entry_date="2026-11-19",pending_sell_id="")
        rt=SimpleNamespace(stock_runner=SimpleNamespace(
            gmo=SimpleNamespace(engine=SimpleNamespace(
                positions={"005930":native},cfg=cfg))))
        r={"status":"OPEN","code":"005930","entry_price":100,
           "qty":100,"entry_date":"2026-11-19"}
        plan=v019.preview("stock/gmo",r,rt)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["first_qty"],20)
        self.assertEqual(plan["second_qty"],40)
        self.assertAlmostEqual(plan["first_target"],107)
        self.assertAlmostEqual(plan["second_target"],110.21)
        self.assertFalse(plan["actual_native_sell_receipt"])
        self.assertFalse(plan["actual_broker_order"])

    def test_original_persisted_stages_preserve_partial_executions(self):
        at="2026-11-19T09:01:12+09:00"
        row={"id":7,"code":"005930","tf":"5m","entry_at":at,
             "entry_qty":100,"qty":70,"status":"OPEN","entry_price":100}
        stages={
          1:{"policy":"NEW_LEGACY_1118","market":"stock",
              "frame":"5m","symbol":"005930","original_qty":"100",
              "order_qty":"30","limit_price":"102",
              "filled_qty":"30","created_at":at,"status":"FILLED"},
          2:{"policy":"NEW_LEGACY_1118","market":"stock",
              "frame":"5m","symbol":"005930","original_qty":"100",
              "order_qty":"35","limit_price":"104.04",
              "filled_qty":"0","created_at":at,"status":"PAPER_RESTING"}}
        result=v019.preview("stock/at",row,native_stages=stages)
        self.assertEqual(result["first_qty"],30)
        self.assertEqual(result["second_qty"],35)
        self.assertEqual(result["first_filled_qty"],30)
        self.assertEqual(result["second_filled_qty"],0)
        self.assertFalse(result["trade_fill_inferred"])
        self.assertIsNone(v019.preview("stock/at",dict(row,qty=101),native_stages=stages))
        self.assertIsNone(v019.preview("stock/at",dict(row,code="000001"),native_stages=stages))

    def test_read_only_native_sqlite_no_schema_create_and_restarts(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            p=root/"stock"/"ARTITRADE_D.sqlite3"
            p.parent.mkdir(parents=True)
            with sqlite3.connect(p) as c:
                c.executescript("""CREATE TABLE nl1118_resting_sells(
                    position_id INT,stage INT,policy TEXT,market TEXT,
                    symbol TEXT,frame TEXT,limit_price TEXT,original_qty TEXT,
                    order_qty TEXT,filled_qty TEXT,status TEXT,created_at TEXT);
                INSERT INTO nl1118_resting_sells VALUES
                    (11,1,'NEW_LEGACY_1118','stock','005930','5m',
                    '102','100','30','0','PAPER_RESTING','2026-11-19T09:00:00+09:00'),
                    (11,2,'NEW_LEGACY_1118','stock','005930','5m',
                    '104.04','100','35','0','PAPER_RESTING','2026-11-19T09:00:00+09:00');
                """)
            row={"id":11,"code":"005930","tf":"5m",
                 "entry_at":"2026-11-19T09:00:00+09:00",
                 "entry_qty":100,"qty":100,"entry_price":100}
            rt=SimpleNamespace(root=root)
            original=p.read_bytes()
            packet={"stock":{"at":{"ledger":{"positions":[row],
                "executions":[]}}}}
            a=v019.decorate(packet,rt)
            b=v019.decorate(packet,rt)
            for result in (a,b):
                plan=result["stock"]["at"]["ledger"]["positions"][0]["v019_exit_plan"]
                self.assertEqual(plan["first_target"],102)
                self.assertEqual(plan["second_qty"],35)
                self.assertEqual(plan["source"],"ORIGINAL_OWNER_SQLITE_NL1118_RESTING")
                self.assertFalse(plan["actual_broker_order"])
            self.assertEqual(original,p.read_bytes())
            self.assertFalse((root/"coin").exists())

    def test_wrong_owner_or_orphan_stage_not_a_fill(self):
        at="2026-11-19T09:00:00+09:00"
        r={"id":12,"code":"005930","tf":"5m",
           "entry_at":at,"entry_qty":100,"qty":100,"entry_price":100}
        s={1:{"policy":"NEW_LEGACY_1118","market":"stock",
           "frame":"5m","symbol":"005930","original_qty":"100",
           "order_qty":"30","limit_price":"102","filled_qty":"0",
           "created_at":at,"status":"PAPER_RESTING"}}
        self.assertIsNone(v019.preview("coin/ma",r,native_stages=s))
        self.assertIsNone(v019.preview("stock/at",dict(r,entry_at="wrong"),
            native_stages=s))
        result=v019.preview("stock/at",r,native_stages=s)
        self.assertIsNone(result["second_target"])
        self.assertEqual(result["first_qty"],30)


if __name__=="__main__":
    unittest.main()
