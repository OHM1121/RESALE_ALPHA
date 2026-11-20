"""0.0.9 APK native regressions: v010 must fix genuine delayed KRX session."""
import datetime as dt
import threading
import unittest
from projectgmo.engine import Engine,Quote
from projectgmo.selector import DSignal
from d_gmo_runtime import GMOService
from krx_session_v041 import venue_bounds

def mk():
    e=Engine()
    e.signals["000680"]=DSignal("000680","LS","2026-11-18",
        "2026-11-19",2525,2405,2650,2930,-30510,26711,1298,
        [-2000]*20,"SHORT","KOSPI","VERIFIED")
    order=e._order("000680","BUY","D+1_V097_KRX_S1",
                   2405,2405,10,"2026-11-19T15:59:55+09:00")
    return e,order

class NativeSpecialClosing(unittest.TestCase):
    def test_regular_KRX_special_end_is_1630_not_1530(self):
        e,o=mk()
        at="2026-11-19T16:00:00+09:00"
        q=Quote("000680",at,1,2385,0,0,0,0,"KRX",0)
        n=e.paper_match_krx_print_proxy(q,100,
                   buy_missing_book=True,sell_missing_book=False)
        self.assertGreater(n,0)
        self.assertLessEqual(n,10)
        self.assertEqual(o.filled,n)
    def test_special_session_real_0D_paper_book_matches(self):
        e,o=mk()
        q=Quote("000680","2026-11-19T16:05:00+09:00",
                1,2385,2390,2380,20,20,"KRX",0)
        e.paper_match(q)
        self.assertGreater(o.filled,0)
    def test_partial_buy_remains_observed_until_real_1630_close(self):
        e,o=mk()
        service=object.__new__(GMOService)
        service.engine=e;service._lock=threading.RLock()
        at=dt.datetime.fromisoformat("2026-11-19T16:15:00+09:00")
        self.assertIn("000680",service.active_buy_watchlist(at))
        at=dt.datetime.fromisoformat("2026-11-19T16:30:00+09:00")
        self.assertNotIn("000680",service.active_buy_watchlist(at))
    def test_2027_nov19_is_regular_0900_not_annual_fixed_suneung(self):
        t=dt.datetime.fromisoformat("2027-11-19T09:00:00+09:00")
        self.assertEqual(venue_bounds(t,"KRX"),(540,930))
    def test_no_fills_after_session_close(self):
        e,o=mk()
        q=Quote("000680","2026-11-19T16:31:00+09:00",
                1,2385,0,0,0,0,"KRX",0)
        self.assertEqual(e.paper_match_krx_print_proxy(
            q,100,buy_missing_book=True,sell_missing_book=False),0)
        self.assertEqual(o.filled,0)

if __name__=="__main__":unittest.main()
