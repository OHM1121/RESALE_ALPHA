"""v082 FOK: a broker-confirmed partial cannot be downgraded to zero."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_fill_reconcile_v082 import verified_find_fill


class KiwoomEvidence(unittest.TestCase):
    def _broker(self,rows):
        class Broker:
            calls=[]
            def _post(self,api,path,body,timeout):
                self.calls.append((api,path,dict(body),timeout))
                result=rows.pop(0) if len(rows)>1 else rows[0]
                return SimpleNamespace(status_code=200),result
        return Broker()

    def test_partial_three_of_ten_is_not_returned_as_zero(self):
        broker=self._broker([{"return_code":0,"acnt_ord_cntr_prst_array":[
            {"ord_no":"0000354","cntr_qty":"+3","cntr_uv":"+10100"}]}])
        with patch('kiwoom_fill_reconcile_v082.time.sleep',return_value=None):
            qty,price=verified_find_fill(broker,"354","005930","BUY",10)
        self.assertEqual((qty,price),(3,10100))
        self.assertEqual(broker.calls[0][2]["qry_tp"],"0")

    def test_full_order_only_after_exact_confirmed_fill(self):
        broker=self._broker([{"return_code":0,"acnt_ord_cntr_prst_array":[
            {"ord_no":"0000354","cntr_qty":"10","cntr_uv":"10500"}]}])
        self.assertEqual(verified_find_fill(broker,"354","005930","BUY",10),
                         (10,10500))
        self.assertEqual(len(broker.calls),1)

    def test_missing_order_id_does_not_mean_zero_filled(self):
        from android_live_bridge import LiveOrderAmbiguous
        broker=self._broker([{"return_code":0,"acnt_ord_cntr_prst_array":[
            {"ord_no":"0000111","cntr_qty":"0","cntr_uv":"0"}]}])
        with patch('kiwoom_fill_reconcile_v082.time.sleep',return_value=None):
            with self.assertRaises(LiveOrderAmbiguous):
                verified_find_fill(broker,"354","005930","BUY",10)

    def test_zero_from_existing_order_is_not_authority_to_rebuy(self):
        broker=self._broker([{"return_code":0,"acnt_ord_cntr_prst_array":[
            {"ord_no":"0000354","cntr_qty":"0","cntr_uv":"0"}]}])
        with patch('kiwoom_fill_reconcile_v082.time.sleep',return_value=None):
            self.assertEqual(verified_find_fill(broker,"354","005930","BUY",10),(0,0))
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/
            "app/src/main/python/android_runtime_v078.py").read_text()
        self.assertIn("BROKER_RECONCILIATION_REQUIRED",text)
        self.assertNotIn('engine._cancel(order,"BROKER_FOK_NO_FILL",quote.at)',text)

    def test_excess_confirmed_fill_halts_instead_of_crediting_invalid_qty(self):
        from android_live_bridge import LiveOrderAmbiguous
        broker=self._broker([{"return_code":0,"acnt_ord_cntr_prst_array":[
            {"ord_no":"0000354","cntr_qty":"11","cntr_uv":"10500"}]}])
        with self.assertRaises(LiveOrderAmbiguous):
            verified_find_fill(broker,"354","005930","BUY",10)


if __name__=="__main__":
    unittest.main()
