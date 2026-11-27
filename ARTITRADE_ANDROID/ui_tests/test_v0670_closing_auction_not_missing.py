"""0.6.7 (owner 2026-11-28 "단일가 체결은 안 해도 돼. 다만 그 시간을 누락봉으로 치지 말라는 거야"): the KRX closing single-price auction 15:20-15:30 (quote frozen, no trades) is never counted as missing candles / unobserved minutes."""
import datetime as dt
import unittest

KST = dt.timezone(dt.timedelta(hours=9))


def engine(name):
    try:
        return __import__(name)
    except Exception:
        raise unittest.SkipTest("vendor engine not on the path")


class UnobservedMinutes(unittest.TestCase):
    def test_a_night_after_a_complete_day_adds_no_unobserved_minutes(self):
        h = engine("d_candidate_history")
        # last observed minute of a day is 15:19 (the last minute that can trade), next observation is the next morning's first minute
        elapsed = h.elapsed_session_minutes("20261126151900", "20261127090000")
        self.assertEqual(max(0, elapsed - 1), 0)
        # over a weekend as well
        elapsed = h.elapsed_session_minutes("20261127151900", "20261130090000")
        self.assertEqual(max(0, elapsed - 1), 0)

    def test_a_real_gap_inside_the_continuous_session_is_still_counted(self):
        h = engine("d_candidate_history")
        self.assertEqual(max(0, h.elapsed_session_minutes("20261127101000", "20261127101500") - 1), 4)
        self.assertEqual(max(0, h.elapsed_session_minutes("20261127151000", "20261127151900") - 1), 8)

    def test_the_session_is_380_minutes_09_00_to_15_20(self):
        h = engine("d_candidate_history")
        self.assertEqual(h._mins_intraday("20261127153000"), 380)
        self.assertEqual(h._mins_intraday("20261127092000"), 20)
        # a whole weekday in between is 380 minutes
        self.assertEqual(h.elapsed_session_minutes("20261124090000", "20261126090000"), 760)


class MinuteRepair(unittest.TestCase):
    def test_no_repair_is_queued_for_a_minute_of_the_closing_auction(self):
        r = engine("d_minute_repair_fast")
        now = dt.datetime(2026, 11, 27, 15, 32, 0, tzinfo=KST)
        self.assertTrue(r.is_closed_regular_minute("20261127151900", now=dt.datetime(2026, 11, 27, 15, 21, 5, tzinfo=KST)))
        for stamp in ("20261127152000", "20261127152500", "20261127152900"):
            self.assertFalse(r.is_closed_regular_minute(stamp, now=now), stamp)


class ExpectedCandles(unittest.TestCase):
    def test_the_expected_regular_candles_do_not_include_the_auction(self):
        engine("d_engine")
        import offhours_weekend_v0590 as weekend
        day = dt.date(2026, 11, 27)
        five = weekend.expected_slots(day, "5m")
        self.assertEqual(five[0][8:12], "0900")
        self.assertEqual(five[-1][8:12], "1515")                     # the 15:15-15:20 candle is the last one that can contain trades
        self.assertNotIn("20261127152000", five)
        self.assertNotIn("20261127152500", five)
        self.assertEqual(weekend.expected_slots(day, "30m")[-1][8:12], "1500")   # 15:00-15:30 contains the closing price


class AuditsStayHonest(unittest.TestCase):
    def test_the_patch_adds_exactly_these_changes(self):
        from pathlib import Path
        text = (Path(__file__).parent / "patch_v065_post_session_lookback.py").read_text("utf8")
        self.assertIn("min (380 ,", text)
        self.assertIn("when.minute<20", text)



class CombinedFillOwnThread(unittest.TestCase):
    def test_the_fill_has_its_own_thread_and_is_not_in_the_shared_loop(self):
        import inspect
        import offhours_prework_v0530 as off
        self.assertIn("all_venue_complete_v0600", inspect.getsource(off._combined_loop))
        self.assertIn("combined_impact_v0650", inspect.getsource(off._combined_loop))
        shared = inspect.getsource(off._loop)
        self.assertNotIn("combined.run", shared)
        self.assertNotIn("impact.run", shared)
        self.assertIn("_combined_loop", inspect.getsource(off.install))

    def test_the_fill_rounds_are_larger_and_rest_shorter(self):
        import all_venue_complete_v0600 as al
        self.assertGreaterEqual(al.BATCH, 60)
        self.assertLessEqual(al.ROUND_REST, 120.0)
        self.assertLessEqual(al.BATCH * 4 * al.PAUSE, 600)            # one round stays far below the shared request ceiling (about 1 request per second)


if __name__ == "__main__":
    unittest.main()
