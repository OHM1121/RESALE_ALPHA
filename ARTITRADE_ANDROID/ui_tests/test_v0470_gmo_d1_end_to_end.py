"""0.4.7: scripted end-to-end scenarios of the supply-demand D+1 path (real GMOService + Engine + overlays, simulated clock)."""
import tempfile
import unittest

from sim_gmo_d1_v0470 import Sim, at, CODE


class Base(unittest.TestCase):
    def sim(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name, **kw)
        self.addCleanup(s.close)
        return s


class Doosan1126Replay(Base):
    """The 11-26 case: books about every 30 s with the ask <= D close, only a handful of prints per hour."""

    def feed(self, s):
        s.book(at(8, 59, 58), 48500, 48450, 88)
        s.book(at(9, 0, 0), 49000, 48900, 50)               # above the ceiling at the opening cross
        s.book(at(9, 0, 18), 48350, 48150, 9)                # below the D+1 ceiling (D close 48,450)
        s.book(at(9, 0, 50), 48700, 48200, 43)
        s.book(at(9, 2, 7), 48500, 48400, 120)
        s.book(at(9, 8, 25), 48600, 48450, 149)
        s.book(at(9, 11, 35), 48250, 48200, 89)
        s.book(at(9, 14, 1), 48200, 48150, 90)
        s.print_(at(9, 14, 2), 48175, 78, ask=48200, bid=48150)   # one print arrives, within 3 s of the last book

    def test_old_behaviour_never_ordered_until_a_print(self):
        s = self.sim(book_trigger=False)
        self.feed(s)
        # without the book trigger nothing is ordered by any of the eight books; only the single print at 09:14:02 can start it
        self.assertEqual(len(s.orders), 1)
        self.assertEqual(s.orders[0].time[11:19], "09:14:02")

    def test_new_behaviour_orders_from_the_first_valid_book_below_the_ceiling(self):
        s = self.sim()
        s.book(at(8, 59, 58), 48500, 48450, 88)
        self.assertEqual(s.orders, [])                       # before 09:00 nothing is placed
        s.book(at(9, 0, 0), 49000, 48900, 50)
        self.assertEqual(s.orders, [])                       # above the ceiling
        s.book(at(9, 0, 18), 48350, 48150, 9)
        self.assertEqual(len(s.orders), 1)                   # placed at the first valid book below the ceiling
        self.assertEqual(s.filled, 0)                        # a book alone never fills
        self.assertEqual(s.plan["order_trigger"], "FRESH_0D_BOOK_NO_0B_WAIT_V0460")
        self.feed_rest(s)
        self.assertEqual(len(s.orders), 1)                   # one order for the whole day

    def feed_rest(self, s):
        s.book(at(9, 14, 1), 48200, 48150, 90)
        s.print_(at(9, 14, 2), 48175, 78, ask=48200, bid=48150)
        self.assertGreater(s.filled, 0)                      # the print fills against the fresh book
        self.assertLessEqual(s.filled, 90)
        self.assertLessEqual(s.spent, s.budget + 0.01)


class LiquidityUsedOnce(Base):
    def test_one_print_consumes_the_displayed_ask_size_once(self):
        s = self.sim()
        s.book(at(9, 0, 18), 48350, 48150, 3)
        s.print_(at(9, 0, 19), 48350, 5, ask=48350, bid=48150)
        self.assertEqual(s.filled, 3)                        # was 6 before 0.4.7: original matcher + preplan matcher

    def test_a_new_book_snapshot_brings_new_liquidity(self):
        s = self.sim()
        s.book(at(9, 0, 18), 48350, 48150, 3)
        s.print_(at(9, 0, 19), 48350, 5, ask=48350, bid=48150)
        s.book(at(9, 0, 22), 48350, 48150, 4)
        s.print_(at(9, 0, 23), 48350, 5, ask=48350, bid=48150)
        self.assertEqual(s.filled, 7)


class Liquid(Base):
    def test_a_liquid_symbol_reaches_the_full_budget(self):
        s = self.sim()
        for sec in range(0, 40):
            when = at(9, 0, 5 + sec)
            s.book(when, 48400, 48350, 200)
            s.print_(when.replace(microsecond=300000), 48400, 50, ask=48400, bid=48350)
        order = s.orders[0]
        self.assertEqual(order.state, "FILLED", (order.requested, order.filled))
        self.assertEqual(s.filled, order.requested)
        self.assertLessEqual(s.spent, s.budget + 0.01)
        self.assertGreater(s.spent, s.budget - 2 * 48600)    # within two shares of the per-stock budget
        self.assertEqual(len(s.orders), 1)


class Defects(Base):
    def test_krx_zero_depth_defect_is_handled_by_the_fallback(self):
        s = self.sim()
        s.book(at(9, 0, 18), 48350, 48150, 9, krx_specific_zero=True)      # the 11-23 wire format
        self.assertEqual(len(s.orders), 1)

    def test_no_order_without_ask_quantity_or_with_a_stale_book(self):
        s = self.sim()
        s.book(at(9, 0, 18), 48350, 48150, 0)
        self.assertEqual(s.orders, [])
        s.book(at(9, 0, 30), 48350, 48150, 9)
        s.clock.set(at(9, 0, 40))                              # 10 s later: the book is stale for any new decision
        self.assertEqual(len(s.orders), 1)                     # it was valid at 09:00:30
        s2 = self.sim()
        s2.book(at(9, 5, 0), 48350, 48150, 9)
        self.assertEqual(len(s2.orders), 1)

    def test_a_feed_gap_never_duplicates_or_loses_the_order(self):
        s = self.sim()
        s.book(at(9, 0, 18), 48350, 48150, 3)
        s.print_(at(9, 0, 19), 48350, 5, ask=48350, bid=48150)
        first = s.filled
        self.assertEqual(first, 3)                           # only what the book displayed
        s.book(at(9, 20, 0), 48300, 48250, 40)               # 20 minutes without any message
        s.print_(at(9, 20, 1), 48300, 20, ask=48300, bid=48250)
        self.assertEqual(len(s.orders), 1)
        self.assertGreater(s.filled, first)                  # the same order keeps filling after the gap
        self.assertLessEqual(s.spent, s.budget + 0.01)

    def test_no_late_fill_on_another_date_and_the_window_ends(self):
        s = self.sim()
        s.book(at(9, 0, 18), 48350, 48150, 9)
        self.assertEqual(len(s.orders), 1)
        order = s.orders[0]
        from sim_gmo_d1_v0470 import DAY, KST
        import datetime as dt
        next_day = dt.datetime(DAY.year, DAY.month, DAY.day + 1, 9, 5, tzinfo=KST)
        s.book(next_day, 48350, 48150, 100)
        s.print_(next_day, 48350, 50, ask=48350, bid=48150)
        self.assertEqual(s.filled, 0)                          # D+1 only: nothing fills on the next date
        self.assertNotEqual(order.state, "FILLED")


if __name__ == "__main__":
    unittest.main()
