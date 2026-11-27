"""0.6.5 (owner 2026-11-27): the KRX closing single-price auction 15:20-15:30 - the quote is frozen, nothing is matched until the single price at 15:30; a silent feed is normal there."""
import datetime as dt
import unittest

import krx_session_v041 as krx

try:
    import d_v19_portfolio as pf
except Exception:
    pf = None

KST = dt.timezone(dt.timedelta(hours=9))


def at(h, mi=0, s=0, day=(2026, 11, 27)):
    return dt.datetime(*day, h, mi, s, tzinfo=KST)


class Clock(unittest.TestCase):
    def test_the_regular_session_is_still_open_in_the_auction_but_says_what_it_is(self):
        self.assertEqual(krx.status(at(15, 19, 59)), (True, "장중"))
        self.assertEqual(krx.status(at(15, 20)), (True, "종가 단일가"))
        self.assertEqual(krx.status(at(15, 29, 59)), (True, "종가 단일가"))
        self.assertEqual(krx.status(at(15, 30))[0], False)

    def test_in_closing_auction(self):
        self.assertFalse(krx.in_closing_auction(at(15, 19, 59)))
        self.assertTrue(krx.in_closing_auction(at(15, 20)))
        self.assertTrue(krx.in_closing_auction(at(15, 29, 59)))
        self.assertFalse(krx.in_closing_auction(at(15, 30)))
        self.assertFalse(krx.in_closing_auction(at(15, 25, day=(2026, 11, 28))))          # Saturday
        self.assertFalse(krx.in_closing_auction(at(10)))

    def test_the_continuous_clock_ends_at_1520_and_nxt_is_unchanged(self):
        self.assertTrue(krx.in_regular_clock(at(9), "KRX"))
        self.assertTrue(krx.in_regular_clock(at(15, 19, 59), "KRX"))
        self.assertFalse(krx.in_regular_clock(at(15, 20), "KRX"))
        self.assertFalse(krx.in_regular_clock(at(15, 29), "KRX"))
        self.assertTrue(krx.in_regular_clock(at(8, 10), "NXT"))
        self.assertFalse(krx.in_regular_clock(at(8, 30), "NXT"))

    def test_the_special_session_day_has_its_own_end(self):
        day = (2026, 11, 19)                                    # college-exam day: 10:00-16:30, auction 16:20-16:30
        self.assertTrue(krx.in_regular_clock(at(16, 19, day=day), "KRX"))
        self.assertFalse(krx.in_regular_clock(at(16, 20, day=day), "KRX"))
        self.assertTrue(krx.in_closing_auction(at(16, 25, day=day)))
        self.assertFalse(krx.in_closing_auction(at(15, 25, day=day)))

    def test_the_session_table_used_by_the_scores_and_the_bars_is_unchanged(self):
        self.assertEqual(krx.venue_bounds(at(12), "KRX"), (540, 930))
        self.assertTrue(krx.is_regular_open(at(15, 25)))


@unittest.skipIf(pf is None, "vendor engine not on the path")
class Engine(unittest.TestCase):
    def test_the_engine_does_not_match_in_the_auction_but_in_the_after_hours_session(self):
        self.assertTrue(pf.live_session(at(9), "KRX"))
        self.assertTrue(pf.live_session(at(15, 19, 59), "KRX"))
        self.assertFalse(pf.live_session(at(15, 20), "KRX"))
        self.assertFalse(pf.live_session(at(15, 29, 59), "KRX"))
        self.assertFalse(pf.live_session(at(15, 30), "KRX"))
        self.assertFalse(pf.live_session(at(15, 45), "KRX"))
        self.assertTrue(pf.live_session(at(16, 0), "KRX"))
        self.assertTrue(pf.live_session(at(19, 59), "KRX"))
        self.assertFalse(pf.live_session(at(20, 0), "KRX"))
        self.assertTrue(pf.live_session(at(8, 10), "NXT"))

    def test_the_source_carries_the_patch(self):
        from pathlib import Path
        self.assertIn("bounds[1]-__import__('krx_session_v041').CLOSING_AUCTION_MINUTES", Path(pf.__file__).read_text("utf8"))


if __name__ == "__main__":
    unittest.main()
