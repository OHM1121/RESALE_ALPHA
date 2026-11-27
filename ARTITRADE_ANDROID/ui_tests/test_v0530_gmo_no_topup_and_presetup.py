"""0.5.3: (1) after a sell nothing of the split buy is ever bought again; (2) plans and ladders are set up in the quiet morning hours."""
import tempfile
import unittest
from unittest import mock

import gmo_d1_preplan_v097 as pre
import night_futures_gate_v0260 as night
from sim_gmo_d1_v0470 import Sim, at, CODE


class Base(unittest.TestCase):
    def sim(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name, close=5000, open_=5000, ladder=True, **kw)
        self.addCleanup(s.close)
        return s


class NoTopUpAfterAnExit(Base):
    def partly_bought(self):
        s = self.sim()
        s.book(at(9, 0, 18), 5100, 5050, 50)
        s.print_(at(9, 1, 0), 4990, 7, ask=4990, bid=4985)              # rung 1 only (30 % of the limit)
        got = {o.purpose.rsplit("_", 1)[-1]: o for o in s.orders}
        self.assertEqual((got["L1"].state, got["L2"].filled, got["L3"].filled), ("FILLED", 0, 0))
        return s, got

    def test_an_exit_in_progress_cancels_the_rest_and_the_price_coming_down_buys_nothing(self):
        s, got = self.partly_bought()
        s.svc.engine.positions[CODE].pending_sell_id = "SELL-1"           # an exit has started on the partial position
        before = s.filled
        s.print_(at(9, 5, 0), 4945, 7, ask=4955, bid=4945)                # price reaches rung 2
        self.assertEqual(s.filled, before)
        self.assertEqual((got["L2"].state, got["L3"].state), ("CANCELED", "CANCELED"))
        self.assertEqual(s.plan["status"], "BLOCKED")
        s.print_(at(9, 9, 0), 4800, 7, ask=4810, bid=4800)
        self.assertEqual(s.filled, before)                                  # and never later

    def test_a_partial_take_profit_stops_the_split_buy_too(self):
        s, got = self.partly_bought()
        s.svc.engine.positions[CODE].first_filled_qty = 3
        s.print_(at(9, 5, 0), 4945, 7, ask=4955, bid=4945)
        self.assertEqual((got["L2"].state, got["L3"].state), ("CANCELED", "CANCELED"))
        self.assertEqual(s.filled, got["L1"].filled)

    def test_the_displayed_book_matcher_cannot_fill_a_ladder_order_once_the_exit_started(self):
        s, got = self.partly_bought()
        s.svc.engine.positions[CODE].exit_stage = 1
        s.book(at(9, 6, 0), 4900, 4850, 500)                                # rung 2 and 3 would now be marketable ...
        s.print_(at(9, 6, 0, 300), 4900, 60, ask=4900, bid=4850)
        self.assertEqual(s.filled, got["L1"].filled)                         # ... but the exit came first

    def test_nothing_changes_while_no_exit_has_started(self):
        s, got = self.partly_bought()
        s.print_(at(9, 5, 0), 4945, 7, ask=4955, bid=4945)
        self.assertEqual(got["L2"].state, "FILLED")


class PresetupInTheQuietHours(Base):
    VERIFIED = {"status": "VERIFIED", "change_pct": 10.0}

    def fresh(self):
        s = self.sim()
        s.planner.plans.clear()
        s.planner._ladders.clear()
        return s

    def test_plans_and_the_ladder_are_ready_before_the_preparation_window(self):
        s = self.fresh()
        with mock.patch.object(night, "state", return_value=self.VERIFIED), \
                mock.patch.object(night, "gmo_ceiling", return_value=(5500, self.VERIFIED)):
            made = s.planner.presetup(at(6, 10))
        plan = s.planner.plans[("2026-11-26", CODE, "KRX")]
        self.assertIn(CODE, made)
        self.assertEqual(plan["preset_at"][:19], "2026-11-26T06:10:00")
        self.assertEqual((plan["ladder"]["basis"], plan["ladder"]["condition_price"]), ("EWY_ADJUSTED_D_CLOSE", 5500))
        self.assertEqual(s.orders, [])                                         # nothing is ordered before the open

    def test_nothing_is_set_up_before_06_00_or_without_a_verified_ewy_value(self):
        s = self.fresh()
        with mock.patch.object(night, "state", return_value=self.VERIFIED), \
                mock.patch.object(night, "gmo_ceiling", return_value=(5500, self.VERIFIED)):
            self.assertEqual(s.planner.presetup(at(5, 30)), ())
        with mock.patch.object(night, "state", return_value={"status": "NOT_CHECKED_YET"}):
            self.assertEqual(s.planner.presetup(at(6, 10)), ())
        self.assertEqual(s.planner.plans, {})

    def test_the_normal_preparation_finds_the_ready_plan_and_the_order_comes_after_the_open(self):
        s = self.fresh()
        with mock.patch.object(night, "state", return_value=self.VERIFIED), \
                mock.patch.object(night, "gmo_ceiling", return_value=(5500, self.VERIFIED)):
            s.planner.presetup(at(6, 10))
            ladder = s.planner.plans[("2026-11-26", CODE, "KRX")]["ladder"]
            s.planner.prepare("KRX", at(8, 55))
            self.assertEqual(len(s.planner.plans), 2)                           # KRX and NXT, each once
            self.assertIs(s.planner.plans[("2026-11-26", CODE, "KRX")]["ladder"], ladder)
            s.book(at(9, 0, 18), 5600, 5550, 50)
        self.assertEqual(sorted(o.limit for o in s.orders), [5330, 5440, 5500])

    def test_repeated_calls_and_weekends_do_nothing_more(self):
        s = self.fresh()
        with mock.patch.object(night, "state", return_value=self.VERIFIED), \
                mock.patch.object(night, "gmo_ceiling", return_value=(5500, self.VERIFIED)):
            s.planner.presetup(at(6, 10))
            keys = set(s.planner.plans)
            s.planner.presetup(at(6, 11))
            self.assertEqual(set(s.planner.plans), keys)
            import datetime as dt
            saturday = dt.datetime(2026, 11, 28, 6, 10, tzinfo=at(6, 10).tzinfo)
            self.assertEqual(s.planner.presetup(saturday), ())


if __name__ == "__main__":
    unittest.main()
