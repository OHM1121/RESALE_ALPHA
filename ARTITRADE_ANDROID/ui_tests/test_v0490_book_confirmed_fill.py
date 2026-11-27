"""0.4.9 (#164): PAPER D+1 BUY filled from two consecutive fresh 0D snapshots ("the shown quantity is still there")."""
import os
import tempfile
import unittest
from unittest import mock

from sim_gmo_d1_v0470 import Sim, at


class Base(unittest.TestCase):
    def sim(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name, close=5000, open_=5000)
        self.addCleanup(s.close)
        return s

    def logs(self, s):
        return [x for x in s.svc.engine.audit if "PAPER_BOOK_CONFIRMED_FILL_V0490" in str(x)]


class Confirm(Base):
    def test_a_quiet_symbol_fills_when_the_quantity_is_still_there_without_any_print(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        self.assertEqual((len(s.orders), s.filled), (1, 0))      # one snapshot alone never fills
        s.book(at(9, 0, 28), 4950, 4900, 5)
        self.assertEqual(s.filled, 5)
        self.assertEqual(len(self.logs(s)), 1)
        self.assertIn("NOT BROKER FILL", str(self.logs(s)[0]))
        self.assertEqual(s.plan["fill_basis_v0490"], "BOOK_CONFIRMED_TWO_SNAPSHOTS")

    def test_filled_size_is_the_smaller_of_the_two_snapshots(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 50)
        s.book(at(9, 0, 28), 4950, 4900, 8)
        self.assertEqual(s.filled, 8)

    def test_a_quantity_that_vanished_between_snapshots_is_not_bought(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 50)
        s.book(at(9, 0, 25), 4950, 4900, 0)                       # gone (rejected message)
        s.book(at(9, 0, 40), 4950, 4900, 8)                       # reappears: only a new reference
        self.assertEqual(s.filled, 0)
        s.book(at(9, 0, 55), 4950, 4900, 8)                       # and it stays: now confirmed
        self.assertEqual(s.filled, 8)

    def test_an_ask_above_the_limit_in_between_resets_the_reference(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 50)
        s.book(at(9, 0, 25), 5100, 5050, 50)
        s.book(at(9, 0, 40), 4950, 4900, 50)
        self.assertEqual(s.filled, 0)

    def test_snapshots_too_far_apart_do_not_confirm_each_other(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 50)
        s.book(at(9, 3, 25), 4950, 4900, 50)
        self.assertEqual(s.filled, 0)

    def test_price_is_the_higher_ask_with_the_paper_slippage_and_never_above_the_limit(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        s.book(at(9, 0, 28), 4960, 4900, 5)
        fill = [f for f in s.svc.engine.buy_fills if f["code"] == "241560"][0]
        self.assertGreaterEqual(fill["price"], 4960)
        self.assertLessEqual(fill["price"], s.orders[0].limit)

    def test_the_same_snapshot_is_not_taken_again_by_a_print(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        s.book(at(9, 0, 28), 4950, 4900, 5)
        self.assertEqual(s.filled, 5)
        s.print_(at(9, 0, 28, 400), 4950, 50, ask=4950, bid=4900)
        self.assertEqual(s.filled, 5)                             # 0.4.8 rule: one snapshot, one take

    def test_a_liquid_symbol_reaches_the_budget_from_books_alone(self):
        s = self.sim()
        for k in range(40):
            s.book(at(9, 0, 5 + k), 4950, 4900, 200)
        self.assertEqual(s.orders[0].state, "FILLED")
        self.assertLessEqual(s.spent, s.budget + 0.01)

    def test_real_mode_never_uses_it(self):
        s = self.sim()
        with mock.patch.dict(os.environ, {"ARTITRADE_REAL_MODE": "1"}):
            s.book(at(9, 0, 18), 4950, 4900, 5)
            s.book(at(9, 0, 28), 4950, 4900, 5)
        self.assertEqual(s.filled, 0)

    def test_the_print_path_still_fills_as_before(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        s.print_(at(9, 0, 19), 4950, 5, ask=4950, bid=4900)
        self.assertEqual(s.filled, 5)


class Doosan1126(Base):
    def test_the_11_26_symbol_still_waits_for_the_09_14_print(self):
        """Documented limit: the ask hovered at the ceiling and snapshots were minutes apart, so no two valid snapshots confirm."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name)
        self.addCleanup(s.close)
        for h, m, sec, a, b, q in [(8, 59, 58, 48500, 48450, 88), (9, 0, 0, 49000, 48900, 50), (9, 0, 18, 48350, 48150, 9),
                                   (9, 0, 50, 48700, 48200, 43), (9, 2, 7, 48500, 48400, 120), (9, 8, 25, 48600, 48450, 149),
                                   (9, 11, 35, 48250, 48200, 89), (9, 14, 1, 48200, 48150, 90)]:
            s.book(at(h, m, sec), a, b, q)
        self.assertEqual(s.filled, 0)
        s.print_(at(9, 14, 2), 48175, 78, ask=48200, bid=48150)
        self.assertGreater(s.filled, 0)


if __name__ == "__main__":
    unittest.main()
