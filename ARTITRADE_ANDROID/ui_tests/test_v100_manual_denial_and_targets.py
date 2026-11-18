"""v100: original PAPER exits must disclose denial reason and audit BUY-time
3LEG31 price thresholds. No live broker calls, user wallet writes or SELL fakery.
"""
from __future__ import annotations
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import android_manual_paper_sell_v085 as manual
from stock_at_policy_reconcile_v091 import _stamp

REQ={"market":"stock","strategy":"at","code":"005930",
     "position_id":19,"expected_qty":"10","percent":100,
     "request_id":"00000000-0000-4000-8000-000000001000",
     "user_confirmed":True}

class ManualDenialEvidence(unittest.TestCase):
    def test_native_verified_book_denial_survives_android_json_result(self):
        runtime=SimpleNamespace(started=True,root="/tmp/test-artitrade-v100",
                                live_enabled=lambda market:False)
        bridge=SimpleNamespace(_RUNTIME=runtime)
        with patch.dict(sys.modules,{"android_live_bridge":bridge}):
            with patch("android_live_position_guard_v049.refuse_paper_sell_if_live_open"):
                with patch.object(manual,"_stock_at",
                        side_effect=ValueError("KA10004_EXCHANGE_BOOK_STALE")) as handler:
                    result=json.loads(manual.perform(REQ))
        self.assertEqual(result["ok"],False)
        self.assertEqual(result["error"],"KA10004_EXCHANGE_BOOK_STALE")
        self.assertEqual(result["detail"],"KA10004_EXCHANGE_BOOK_STALE")
        handler.assert_called_once()
        self.assertNotIn("qty",result)
        self.assertNotIn("price",result)

    def test_arbitrary_valueerror_does_not_become_approved_sell(self):
        runtime=SimpleNamespace(started=True,root="/tmp/test-artitrade-v100",
                                live_enabled=lambda market:False)
        with patch.dict(sys.modules,{"android_live_bridge":SimpleNamespace(_RUNTIME=runtime)}):
            with patch("android_live_position_guard_v049.refuse_paper_sell_if_live_open"):
                with patch.object(manual,"_stock_at",side_effect=ValueError("not a durable fill")):
                    result=json.loads(manual.perform(REQ))
        self.assertEqual(result["error"],"INVALID_MANUAL_VALUE")
        self.assertFalse(result["ok"])

class TargetAuditOnly(unittest.TestCase):
    def test_original_first_second_targets_recorded_once_without_sell(self):
        p={"id":101,"tf":"5m","code":"005930","status":"OPEN",
           "qty":100,"entry_qty":100,"first_sold":0,"exit_stage":0,
           "downside_stage":0,"exit_policy":"LEGACY","exit_first_target":0,
           "entry_ts":"20261118100300","entry_price":105,
           "ref_low":100,"ref_bodytop":112,"ref_high":120}
        class Connection:
            def __init__(self):self.statements=[]
            def execute(self,statement,args):
                self.statements.append((statement,args))
                return SimpleNamespace(rowcount=1)
        class Store:
            def __init__(self):self.audits=[]
            def audit(self,*args):self.audits.append(args)
        c=Connection();store=Store()
        self.assertTrue(_stamp(c,store,p,"ORIGINAL_D19_NEW_BUY_ATOMIC"))
        self.assertEqual(len(store.audits),1)
        evidence=store.audits[0][-1]
        self.assertEqual(evidence["entry_price_at_buy"],105)
        self.assertEqual(evidence["first_target_won"],112)
        self.assertEqual(evidence["second_target_threshold_won"],"115.36")
        self.assertFalse(evidence["auto_sell_executed"])
        self.assertEqual(len(c.statements),1)
        self.assertIn("UPDATE positions",c.statements[0][0])
        self.assertEqual(p["qty"],100)

if __name__=="__main__":unittest.main()
