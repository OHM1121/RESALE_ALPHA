"""Read-only HLB BioStep KOSDAQ 60m hold-duration diagnostic."""
import sys
import unittest
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
from at_hold_diagnostic_v035 import diagnose_at_position

class KRXHoldDiagnostic(unittest.TestCase):
    def setUp(self):
        self.base={"code":"278650","market":"KOSDAQ","name":"HLB바이오스텝",
            "tf":"60m","entry_ts":"20261103105232","hold_days":10,
            "status":"OPEN","qty":5,"first_sold":0,"hold_profit_trail":0}

    def test_nov14_is_only_nine_sessions_not_expired(self):
        a=diagnose_at_position(self.base,datetime(2026,11,14,12,0))
        self.assertEqual(a["sessions_elapsed"],9)
        self.assertEqual(a["hold_days"],10)
        self.assertEqual(a["maturity_date"],"2026-11-16")
        self.assertEqual(a["status"],"NOT_MATURED")
        self.assertIn("9/10",a["exit_scenario"])

    def test_10th_day_maturity_does_not_justify_fabricated_sell(self):
        result=diagnose_at_position(self.base,datetime(2026,11,16,15,20))
        self.assertEqual(result["sessions_elapsed"],10)
        self.assertEqual(result["status"],"MATURE_AWAITING_VERIFIED_TICK")
        self.assertFalse(result["sold"])
        self.assertIn("0B",result["exit_scenario"])

    def test_kospi_60m_policy_is_fourteen_sessions(self):
        data=dict(self.base,market="KOSPI",hold_days=14)
        result=diagnose_at_position(data,datetime(2026,11,14,10))
        self.assertEqual(result["maturity_date"],"2026-11-20")

    def test_early_partial_sale_and_profit_trail_override_regular_deadline(self):
        first=diagnose_at_position(dict(self.base,first_sold=1),
                     datetime(2026,11,17,10))
        trail=diagnose_at_position(dict(self.base,hold_profit_trail=1),
                     datetime(2026,11,17,10))
        self.assertEqual(first["status"],"PARTIAL_EXIT_TRAILING")
        self.assertEqual(trail["status"],"PROFIT_TRAILING")
        self.assertFalse(first["sold"])
        self.assertFalse(trail["sold"])

    def test_missing_policy_is_not_assumed(self):
        result=diagnose_at_position(dict(self.base,hold_days=None),
                     datetime(2026,11,14,10))
        self.assertIsNone(result["maturity_date"])
        self.assertEqual(result["status"],"MISSING_HOLD_POLICY")

if __name__=="__main__":unittest.main()
