"""0.4.8 (#163): the displayed size of ONE 0D book snapshot is taken once (BUY ask and SELL bid), a new snapshot brings new size."""
import datetime as dt
import tempfile
import types
import unittest

import gmo_d1_preplan_v097 as pre
from sim_gmo_d1_v0470 import Sim, at

CLOSE = 5000                      # a 5,000 won stock: the per-stock budget buys about 99 shares, so the order is never the limit


class Base(unittest.TestCase):
    def sim(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name, close=CLOSE, open_=CLOSE)
        self.addCleanup(s.close)
        return s


class OneSnapshot(Base):
    def test_six_prints_on_one_five_share_book_fill_five(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        for i in range(6):
            s.print_(at(9, 0, 19, 100 * i), 4950, 5, ask=4950, bid=4900)
        self.assertEqual(s.filled, 5)                        # 30 before 0.4.8

    def test_twenty_one_share_prints_on_one_book_fill_five(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        for i in range(20):
            s.print_(at(9, 0, 19, 30 * i), 4950, 1, ask=4950, bid=4900)
        self.assertEqual(s.filled, 5)

    def test_a_new_snapshot_with_the_same_values_brings_new_size(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        s.print_(at(9, 0, 18, 300), 4950, 5, ask=4950, bid=4900)
        s.book(at(9, 0, 19), 4950, 4900, 5)                   # identical values, new message
        s.print_(at(9, 0, 19, 300), 4950, 5, ask=4950, bid=4900)
        self.assertEqual(s.filled, 10)

    def test_the_displayed_size_not_the_print_volume_is_still_the_unit(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 50)
        s.print_(at(9, 0, 19), 4950, 2, ask=4950, bid=4900)
        self.assertEqual(s.filled, 50)                        # unchanged on purpose: a print volume cap would starve thin names

    def test_one_print_per_book_is_unchanged(self):
        for variant in ("capped", "original"):
            s = self.sim()
            if variant == "original":
                s.svc.engine.paper_match = s.svc.engine._v0480_original
            for k in range(20):
                s.book(at(9, 0, 5 + 2 * k), 4950, 4900, 5)
                s.print_(at(9, 0, 5 + 2 * k, 400), 4950, 5, ask=4950, bid=4900)
            if variant == "capped":
                capped = s.filled
            else:
                self.assertEqual(capped, s.filled)

    def test_full_budget_in_a_liquid_symbol_is_unchanged(self):
        s = self.sim()
        for sec in range(0, 40):
            when = at(9, 0, 5 + sec)
            s.book(when, 4950, 4900, 200)
            s.print_(when.replace(microsecond=300000), 4950, 50, ask=4950, bid=4900)
        order = s.orders[0]
        self.assertEqual(order.state, "FILLED")


class Sides(unittest.TestCase):
    """SELL side and bookkeeping on a fake engine (no sample position is needed)."""

    def rig(self, ask_size, bid_size):
        epoch = [dt.datetime(2026, 11, 26, 9, 0, 18)]
        orders = {"b": types.SimpleNamespace(code="X", side="BUY", filled=0),
                  "s": types.SimpleNamespace(code="X", side="SELL", filled=0)}
        seen = []

        def paper_match(q):
            seen.append((q.ask_size, q.bid_size))
            orders["b"].filled += q.ask_size
            orders["s"].filled += q.bid_size
        eng = types.SimpleNamespace(orders=orders, paper_match=paper_match)
        svc = types.SimpleNamespace(last_book={"X": (4950, 4900, ask_size, bid_size, epoch[0])})
        pre.consume_book_once(svc, eng)
        return eng, svc, epoch, seen

    def quote(self, ask_size, bid_size):
        return types.SimpleNamespace(code="X", ask_size=ask_size, bid_size=bid_size)

    def test_bid_size_is_taken_once_per_snapshot(self):
        import dataclasses
        from projectgmo.engine import Quote
        eng, svc, epoch, seen = self.rig(7, 4)
        q = Quote("X", "2026-11-26T09:00:19", 1, 4950.0, 4950.0, 4900.0, 7, 4)
        eng.paper_match(q)
        eng.paper_match(q)
        self.assertEqual(seen, [(7, 4), (0, 0)])
        svc.last_book["X"] = (4950, 4900, 7, 4, epoch[0] + dt.timedelta(seconds=1))
        eng.paper_match(q)
        self.assertEqual(seen[-1], (7, 4))

    def test_missing_size_and_missing_book_pass_through(self):
        from projectgmo.engine import Quote
        eng, svc, epoch, seen = self.rig(0, 0)
        eng.paper_match(Quote("X", "2026-11-26T09:00:19", 1, 4950.0, 4950.0, 4900.0, 0, 0))
        svc.last_book.clear()
        eng.paper_match(Quote("X", "2026-11-26T09:00:19", 1, 4950.0, 4950.0, 4900.0, 9, 9))
        self.assertEqual(seen, [(0, 0), (9, 9)])

    def test_installed_once(self):
        eng, svc, epoch, seen = self.rig(3, 3)
        first = eng.paper_match
        pre.consume_book_once(svc, eng)
        self.assertIs(eng.paper_match, first)


if __name__ == "__main__":
    unittest.main()
