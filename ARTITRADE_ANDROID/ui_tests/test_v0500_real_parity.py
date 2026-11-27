"""0.5.0 (#165): paper fills follow the real-trading process - displayed size fully takeable, fills at the displayed price."""
import datetime as dt
import tempfile
import unittest

import d_v19_portfolio as portfolio
import real_parity_v0500 as parity
from sim_gmo_d1_v0470 import Sim, at

NOW = dt.datetime(2026, 11, 26, 9, 0, 20)


def book_with(ask_qty=10, bid_qty=10):
    parity.install_book_depth()
    b = portfolio.Book()
    b._items[("X", "KRX")] = (4950, 4900, ask_qty, NOW)
    b._bid_depth[("X", "KRX")] = (4900, bid_qty, NOW)
    return b


class StockBookDepth(unittest.TestCase):
    def test_the_whole_displayed_ask_is_takeable_not_half(self):
        b = book_with(ask_qty=10)
        quote, why = b.available("X", "KRX", NOW, 4950, 10)
        self.assertIsNotNone(quote, why)
        self.assertEqual(quote[1], 10)                        # was 5 (50 %)

    def test_nothing_beyond_the_displayed_size_is_invented(self):
        b = book_with(ask_qty=10)
        self.assertEqual(b.available("X", "KRX", NOW, 4950, 50)[0][1], 10)

    def test_one_snapshot_is_shared_by_every_wallet(self):
        b = book_with(ask_qty=10)
        self.assertTrue(b.reserve("X", "KRX", NOW, 4950, 6))
        self.assertEqual(b.available("X", "KRX", NOW, 4950, 10)[0][1], 4)
        self.assertFalse(b.reserve("X", "KRX", NOW, 4950, 5))  # a second wallet cannot take what the first one took
        self.assertTrue(b.reserve("X", "KRX", NOW, 4950, 4))
        self.assertIsNone(b.available("X", "KRX", NOW, 4950, 1)[0])

    def test_the_price_jump_case_does_not_fill(self):
        """Owner's example: the ask moved above the order limit, nothing is left at our price -> no fill."""
        b = book_with(ask_qty=10)
        b._items[("X", "KRX")] = (5050, 5000, 10, NOW)
        quote, why = b.available("X", "KRX", NOW, 4950, 10)
        self.assertIsNone(quote)
        self.assertEqual(why, "BEST_ASK_ABOVE_LIMIT")

    def test_a_stale_book_is_still_not_used(self):
        b = book_with()
        self.assertEqual(b.available("X", "KRX", NOW + dt.timedelta(seconds=4), 4950, 1)[1], "STALE_ORDERBOOK")

    def test_the_whole_displayed_bid_is_sellable(self):
        b = book_with(bid_qty=10)
        self.assertEqual(b.available_sell("X", "KRX", NOW, 4900, 10), 10)     # was 5
        self.assertEqual(b.available_sell("X", "KRX", NOW, 4900, 30), 10)
        self.assertEqual(b.available_sell("X", "KRX", NOW, 4890, 5), 0)       # another price level is not this snapshot

    def test_installed_once(self):
        parity.install_book_depth()
        self.assertFalse(parity.install_book_depth())


class GmoFillPrice(unittest.TestCase):
    def sim(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name, close=5000, open_=5000)
        self.addCleanup(s.close)
        return s

    def test_slippage_is_zero_and_the_fill_is_at_the_displayed_ask(self):
        s = self.sim()
        self.assertEqual((s.svc.engine.cfg.paper_buy_slip, s.svc.engine.cfg.paper_sell_slip), (0.0, 0.0))
        s.book(at(9, 0, 18), 4950, 4900, 5)
        s.print_(at(9, 0, 19), 4950, 5, ask=4950, bid=4900)
        fills = [f for f in s.svc.engine.buy_fills if f["code"] == "241560"]
        self.assertEqual([f["price"] for f in fills], [4950])                 # was 4960 (one tick + 0.05 % above the ask)

    def test_book_confirmed_fill_is_at_the_higher_of_the_two_displayed_asks(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 5)
        s.book(at(9, 0, 28), 4960, 4900, 5)
        fills = [f for f in s.svc.engine.buy_fills if f["code"] == "241560"]
        self.assertEqual([f["price"] for f in fills], [4960])

    def test_the_order_limit_and_fees_are_unchanged(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4950, 4900, 500)
        s.print_(at(9, 0, 19), 4950, 500, ask=4950, bid=4900)
        self.assertLessEqual(s.spent, s.budget + 0.01)
        self.assertGreater(s.spent, s.filled * 4950)                          # the buy fee is still charged
        self.assertLessEqual(max(f["price"] for f in s.svc.engine.buy_fills), s.orders[0].limit)

    def test_slip_replacement_is_idempotent(self):
        s = self.sim()
        self.assertFalse(parity.real_price_cfg(s.svc.engine))


if __name__ == "__main__":
    unittest.main()
