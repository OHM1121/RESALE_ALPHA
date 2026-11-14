"""EXE observation timestamps are separate from actual SELL fills."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
import research_evidence_v036 as m

class EvidenceTests(unittest.TestCase):
    def test_first_target_observation_is_not_a_sell_fill(self):
        row={"first_target":120,"first_observed_target_after_touch":"20261103135900",
             "observation_minutes":19,"observation_provenance":"KRX_1M"}
        result=m.from_at_forward(row)
        self.assertEqual(result["observed_target_at"],"20261103135900")
        self.assertEqual(result["observed_target_price"],120)
        self.assertEqual(result["observed_target_evidence"],"STOCK_MINUTE_HIGH_NOT_FILL")
        self.assertFalse(result["sell_evidence"])

    def test_missing_observation_marks_explicit_reason_not_target_miss(self):
        a=m.observation_status({"missing_observation_minutes":200,"observation_minutes":1})
        self.assertEqual(a,"DATA_GAP")
        b=m.observation_status({"missing_observation_minutes":0,"observation_minutes":0})
        self.assertEqual(b,"NO_OBSERVATIONS")
        self.assertEqual(m.observation_status({"observation_minutes":10}),"OBSERVING")

    def test_original_coin_upbit_book_bid_observation_after_virtual_entry(self):
        rows=[
          {"source":"UPBIT_BOOK_BID","first_at":"2026-11-03T11:03:00",
           "last_at":"2026-11-03T11:03:59","bid_high":"105"},
          {"source":"UPBIT_BOOK_BID","first_at":"2026-11-03T11:02:00",
           "last_at":"2026-11-03T11:02:40","bid_high":"102"}]
        target=m.coin_shadow_observed_target(rows,104,"2026-11-03T11:00:00")
        self.assertEqual(target["observed_target_price"],104.0)
        self.assertEqual(target["observed_target_at"],"2026-11-03T11:03:59")
        self.assertEqual(target["observed_target_evidence"],"COIN_BOOK_BID_HIGH_NOT_FILL")
        self.assertIsNone(m.coin_shadow_observed_target(rows,110,"2026-11-03T11:00:00"))

    def test_coin_book_target_row_verdict_keeps_virtual_vs_actual_evidence(self):
        a={"bought":False,"entry_observed":True,"outcome":"PENDING","target_hit":None}
        b=m.attach_coin_virtual_goal(a,{"observed_target_at":"2026-11-03T12:00",
            "observed_target_price":104.0,
            "observed_target_evidence":"COIN_BOOK_BID_HIGH_NOT_FILL"})
        self.assertEqual(b["outcome"],"HIT")
        self.assertTrue(b["target_hit"])
        self.assertFalse(b.get("virtual_sell_evidence"))
        c={"bought":True,"entry_observed":True,"outcome":"PENDING","target_hit":None}
        m.attach_coin_virtual_goal(c,{"observed_target_at":"2026-11-03T12:00",
            "observed_target_price":104.0,
            "observed_target_evidence":"COIN_BOOK_BID_HIGH_NOT_FILL"})
        self.assertEqual(c["outcome"],"PENDING")
        self.assertIsNone(c["target_hit"])

    def test_high_target_is_never_a_buy_or_sell_fill(self):
        r=m.from_at_forward({"first_target":100,
            "first_observed_target_after_touch":None,
            "observation_minutes":3,"observed_max_price":120})
        self.assertIsNone(r["observed_target_at"])
        self.assertEqual(r["observed_target_evidence"],"")
if __name__=="__main__":unittest.main()
