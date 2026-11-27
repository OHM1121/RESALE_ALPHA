"""0.4.6 (#161): the D+1 supply-demand PAPER order is placed from a fresh valid 0D book (no 0B print needed); fills unchanged."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_v097_gmo_preplan import ServiceFixture
from gmo_d1_preplan_v097 import Preplanner, KST

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


class BookTrigger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = ServiceFixture(self.tmp.name)
        self.ready = dt.datetime(2026, 11, 17, 8, 58, 0, tzinfo=KST)
        self.now = dt.datetime(2026, 11, 17, 9, 0, 5, tzinfo=KST)
        self.p = Preplanner(self.svc, clock=lambda: self.now)
        self.p.prepare("KRX", self.ready)
        self.key = ("2026-11-17", "005930", "KRX")
        self.svc.opens[("005930", "2026-11-17")] = 10000
        self.svc.last_book = {}

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, ask=10000, bid=9990, ask_qty=9, bid_qty=30, age=0.4):
        self.svc.last_book["005930"] = (ask, bid, ask_qty, bid_qty, self.now - dt.timedelta(seconds=age))

    def test_a_fresh_valid_book_places_the_order_and_no_fill(self):
        self.book()
        cash = self.svc.engine.cash
        self.assertTrue(self.p.on_book("005930", self.now))
        plan = self.p.plans[self.key]
        orders = list(self.svc.engine.orders.values())
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].side, "BUY")
        self.assertTrue(orders[0].purpose.startswith("D+1_V097_KRX_S"))
        self.assertEqual(plan["status"], "ORDER_PENDING")
        self.assertEqual(plan["order_trigger"], "FRESH_0D_BOOK_NO_0B_WAIT_V0460")
        self.assertEqual(self.svc.engine.buy_fills, [])                      # the order only: no fill from a book alone
        self.assertEqual(self.svc.engine.cash, cash)
        self.assertGreater(self.svc.persist_calls, 0)

    def test_no_second_order_and_the_tick_path_still_fills(self):
        from projectgmo.engine import Quote
        self.book()
        self.assertTrue(self.p.on_book("005930", self.now))
        self.assertFalse(self.p.on_book("005930", self.now))                  # already has an order
        plan = self.p.plans[self.key]
        t1 = self.now + dt.timedelta(milliseconds=300)
        q = Quote("005930", t1.isoformat(), 1, 10000, 10000, 9990, 9, 30, "KRX", 0)
        self.p._step(plan, t1, 10000, 10000, 9, "KRX", q=q)                   # a real fresh print + book fills as before
        self.assertEqual(len(self.svc.engine.orders), 1)
        self.assertEqual(self.svc.engine.buy_fills[0]["qty"], 9)

    def test_not_without_a_fresh_valid_book_or_above_the_ceiling(self):
        self.assertFalse(self.p.on_book("005930", self.now))                  # no book at all
        self.book(age=5.0)
        self.assertFalse(self.p.on_book("005930", self.now))                  # stale
        self.book(ask_qty=0)
        self.assertFalse(self.p.on_book("005930", self.now))                  # no ask quantity
        self.book(ask=9980, bid=9990)
        self.assertFalse(self.p.on_book("005930", self.now))                  # crossed book
        self.book(ask=10100, bid=10090)
        self.assertFalse(self.p.on_book("005930", self.now))                  # above open + 2 ticks
        self.assertEqual(self.p.plans[self.key]["status"], "WAIT_BELOW_ALLOWED_CEILING")
        self.assertEqual(self.svc.engine.orders, {})

    def test_not_for_a_plan_prepared_after_the_open_or_in_real_mode_or_outside_the_session(self):
        self.book()
        late = Preplanner(self.svc, clock=lambda: self.now)
        late.prepare("KRX", self.now)                                           # prepared after 09:00, not a late recovery
        self.assertFalse(late.on_book("005930", self.now))
        with mock.patch.dict(os.environ, {"ARTITRADE_REAL_MODE": "1"}):
            self.assertFalse(self.p.on_book("005930", self.now))
        self.assertFalse(self.p.on_book("005930", dt.datetime(2026, 11, 17, 7, 0, tzinfo=KST)))
        self.assertEqual(self.svc.engine.orders, {})

    def test_wiring(self):
        text = (PY / "gmo_d1_preplan_v097.py").read_text("utf8")
        self.assertIn("service_class.on_book_message=book_message", text)
        self.assertIn("place_only", text)
        self.assertIn("not place_only", text)                                   # the matcher is skipped for a book-only call


if __name__ == "__main__":
    unittest.main()
