"""0.2.8 regression (owner 2026-11-22, "-3% 이하인 날에만"): the pre-emptive
night-EWY stock exit runs only on SEVERE nights (EWY <= -3.0 %), never on a
mild down night; buy blocks and the GMO ceiling are unchanged (issue #141)."""
import datetime as dt
import unittest

import night_futures_gate_v0260 as night

KST = dt.timezone(dt.timedelta(hours=9))


def holding(mark, stop):
    return {"strategy": "ma", "code": "005930", "position_id": 7, "entry": 10000.0,
            "mark": mark, "stops": [{"price": stop, "label": "직전봉 최저가 손절", "pct": 0}]}


class SevereOnlyPreemptiveExit(unittest.TestCase):
    def test_threshold_is_the_severe_threshold(self):
        self.assertEqual(night.PREEMPT_MAX_PCT, -3.0)
        self.assertEqual(night.PREEMPT_MAX_PCT, night.SEVERE_PCT)

    def test_mild_down_night_never_sells_even_if_stop_is_close(self):
        # -2.99 % would push 10,000 to 9,701 <= stop 9,990, but the night is not severe.
        for pct in (-0.1, -1.0, -2.5, -2.99):
            self.assertEqual(night.preemptive_plan([holding(10000.0, 9990.0)], pct), [], pct)

    def test_severe_night_sells_at_and_below_minus_three(self):
        for pct in (-3.0, -3.01, -4.5):
            plan = night.preemptive_plan([holding(10000.0, 9750.0)], pct)
            self.assertEqual(len(plan), 1, pct)

    def test_severe_night_still_requires_the_stop_to_be_reached(self):
        self.assertEqual(night.preemptive_plan([holding(10000.0, 9600.0)], -3.0), [])

    def test_step_does_not_plan_on_a_mild_night(self):
        night._PREEMPT.clear()
        night.set_state_for_test({"date": "2026-11-23", "status": "VERIFIED",
                                  "change_pct": -2.0, "severe": False})
        now = dt.datetime(2026, 11, 23, 8, 0, 5, tzinfo=KST)
        self.assertEqual(night.preemptive_step(now), 10)
        self.assertNotIn("2026-11-23", night._PREEMPT)

    def test_buy_block_threshold_unchanged(self):
        self.assertEqual(night.SEVERE_PCT, -3.0)
        self.assertEqual(night.BLOCK_SECONDS, 1800)


if __name__ == "__main__":
    unittest.main()
