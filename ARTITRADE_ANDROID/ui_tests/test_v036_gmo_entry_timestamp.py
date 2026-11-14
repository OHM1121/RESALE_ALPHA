"""GMO original JSON buy_fills.time must enrich the matched current holding."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
import gmo_entry_time_v036 as m

class GmoEntryTimeTest(unittest.TestCase):
    def test_current_holding_joins_exact_entry_date_and_first_filled_timestamp(self):
        p={"code":"005930","entry_date":"2026-11-03","entry_price":200}
        buys=[{"code":"005930","time":"2026-11-03T10:52:32+09:00","purpose":"BUY_GMO","price":200,"qty":3},
              {"code":"005930","time":"2026-11-03T10:52:31+09:00","purpose":"BUY_GMO_FIRST","price":200,"qty":4},
              {"code":"005930","time":"2026-11-10T11:00:00+09:00","purpose":"NEW_POSITION","price":210,"qty":2}]
        evidence=m.entry_fill(p,buys)
        self.assertEqual(evidence["entry_at"],"2026-11-03T10:52:31+09:00")
        self.assertEqual(evidence["buy_reason"],"BUY_GMO_FIRST")

    def test_no_proof_returns_missing_without_inventing(self):
        self.assertIsNone(m.entry_fill({"code":"005930","entry_date":"2026-11-05"},
                                        [{"code":"005930","time":"2026-11-03T10:00:00","qty":2}]))
    def test_raw_exe_state_json_has_verified_fill_time(self):
        state={"positions":{"005930":{"qty":7,"entry_date":"2026-11-03"}},
               "buy_fills":[{"code":"005930","time":"2026-11-03T10:52:32+09:00","qty":7,"price":200,"purpose":"GMO_PAPER"}]}
        report=m.enrich_positions([{"code":"005930","qty":7,"tf":"gmo"}],state)
        self.assertEqual(report[0]["entry_at"],"2026-11-03T10:52:32+09:00")
        self.assertEqual(report[0]["buy_reason"],"GMO_PAPER")
if __name__=="__main__":unittest.main()
