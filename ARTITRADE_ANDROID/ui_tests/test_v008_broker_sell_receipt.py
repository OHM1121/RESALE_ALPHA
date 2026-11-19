"""0.0.8 read-only broker order evidence cannot become a real order or fill."""
from __future__ import annotations
import json
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))
from broker_sell_receipt_v008 import normalize_response,audit_stock_sells


class StubBroker:
    def __init__(self,status=200,payload=None):
        self.calls=[]
        self.status=status
        self.payload=payload
    def ready(self):return True
    def _post(self,api,path,body,timeout):
        self.calls.append((api,path,dict(body),timeout))
        return SimpleNamespace(status_code=self.status,headers={}),self.payload


class BrokerReceipts(unittest.TestCase):
    def test_explicit_official_ka10075_only(self):
        api=StubBroker(payload={
            "return_code":0,
            "oso":[{"stk_cd":"A005930","ord_no":"0001234",
                    "io_tp_nm":"+매도","oso_qty":"7","ord_qty":"10",
                    "ord_pric":"70200","ord_stt":"접수","stex_tp_txt":"KRX"},
                   {"stk_cd":"005930","ord_no":"0001235",
                    "io_tp_nm":"+매수","oso_qty":"20","ord_pric":"70000"}]})
        rt=SimpleNamespace(kiwoom=api,live_enabled=lambda _:False)
        result=audit_stock_sells(rt)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["orders"]),1)
        self.assertEqual(result["orders"][0]["broker_order_id"],"0001234")
        self.assertEqual(result["orders"][0]["remaining_qty"],7)
        self.assertEqual(result["orders"][0]["state"],"BROKER_UNFILLED_VERIFIED")
        self.assertFalse(result["app_live_enabled"])
        self.assertEqual(result["stages"][0]["status"],"NOT_VERIFIED_AS_BROKER_ORDER")
        self.assertEqual(len(api.calls),1)
        api_id,path,body,timeout=api.calls[0]
        self.assertEqual((api_id,path),("ka10075","/api/dostk/acnt"))
        self.assertEqual(body,{"all_stk_tp":"0","trde_tp":"1","stk_cd":"","stex_tp":"0"})

    def test_empty_response_is_proof_of_no_current_unfilled_only(self):
        out=normalize_response(200,{"return_code":0,"oso":[]},"2026-11-19T10:05:00+09:00")
        self.assertTrue(out["ok"])
        self.assertEqual(out["orders"],[])
        self.assertIsNone(out["broker_orders_registered_by_app"])

    def test_no_rows_not_success(self):
        self.assertFalse(normalize_response(200,{"return_code":0},"x")["ok"])
        self.assertFalse(normalize_response(401,{"oso":[]},"x")["ok"])
        self.assertFalse(normalize_response(200,{"return_code":-1,"oso":[]},"x")["ok"])
        self.assertFalse(normalize_response(200,{"return_code":0,"oso":{}},"x")["ok"])

    def test_pagination_must_not_imply_no_orders(self):
        out=normalize_response(200,{"return_code":0,"oso":[]},"x",has_more=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["has_more"])
        self.assertEqual(out["error"],"PAGINATION_INCOMPLETE_NO_NEGATIVE_ASSERTION")

    def test_keys_missing_must_not_call_broker(self):
        api=StubBroker()
        api.ready=lambda:False
        out=audit_stock_sells(SimpleNamespace(kiwoom=api,live_enabled=lambda _:False))
        self.assertFalse(out["ok"])
        self.assertEqual(api.calls,[])
        self.assertEqual(out["error"],"KIWOOM_KEYS_UNAVAILABLE")

if __name__=="__main__":unittest.main()
