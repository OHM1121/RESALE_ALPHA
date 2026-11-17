"""v082 3 stages: bounded aggressive first order, partial, empty-book recovery."""
from __future__ import annotations

import unittest
from decimal import Decimal as D
from buy_execution_recovery_v082 import Intent


class ThreeStageRecovery(unittest.TestCase):
    def setUp(self):
        self.i=Intent("ma","stock","005930","MA:BAR:1","10000","500000","0.001")
        self.book={"symbol":"005930","exchange_ms":1000000,
                   "ask":10000,"bid":9990,"ask_size":15,
                   "asks":[(10000,15),(10010,12),(10020,30)]}

    def quote(self,book=None,time_ms=1000300):
        return self.i.quote(self.book if book is None else book,time_ms)

    def test_immediate_aggressive_limit_uses_three_verified_ask_levels(self):
        plan=self.quote()
        self.assertEqual(plan["action"],"PLACE")
        self.assertEqual(plan["scenario"],1)
        self.assertEqual(plan["limit"],"10020")
        self.assertEqual(plan["qty"],"49")
        self.i.submit("ORDER-A",plan["qty"],plan["verified_book_ms"])
        self.i.acknowledge("ORDER-A")
        self.assertEqual(self.quote()["action"],"MONITOR")
        self.assertEqual(self.i.status()["reserved_won"],"491470.98")

    def test_partial_cannot_duplicate_until_terminal_broker_acknowledgement(self):
        plan=self.quote()
        self.i.submit("A",plan["qty"],1000000)
        self.i.acknowledge("A")
        self.i.confirm_fill("A","E-1",D("13"),10005)
        self.assertEqual(self.i.status()["state"],"PARTIAL")
        self.assertEqual(self.quote()["action"],"MONITOR")
        self.assertEqual(self.i.terminal("A","CANCELLED_CONFIRMED",False)["state"],"UNCERTAIN")
        self.assertEqual(self.quote()["action"],"BLOCK")

    def test_partial_with_confirmed_cancel_retries_only_remaining_seed(self):
        p=self.quote()
        self.i.submit("A",p["qty"],1000000)
        self.i.acknowledge("A")
        self.i.confirm_fill("A","E-1",13,10005)
        self.i.confirm_fill("A","E-1",13,10005)
        self.assertEqual(self.i.status()["filled_qty"],"13")
        self.i.terminal("A","CANCELLED_CONFIRMED",True)
        book={**self.book,"exchange_ms":1001050,"ask":10010,
              "asks":[(10010,5),(10020,38)]}
        next_plan=self.i.quote(book,1001120)
        self.assertEqual(next_plan["scenario"],2)
        self.assertLess(D(next_plan["qty"]),D(p["qty"]))
        self.i.submit("B",next_plan["qty"],1001050)
        self.assertEqual(self.i.status()["active_order_id"],"B")
        self.assertLessEqual(D(self.i.status()["reserved_won"])+
                             D(self.i.status()["confirmed_spent"]),D("500000"))

    def test_quote_gap_does_not_end_intent_then_recovers_on_new_book(self):
        self.assertEqual(self.i.quote({},1000300)["action"],"WAIT")
        recovered=self.i.quote({**self.book,"exchange_ms":1001300},1001350)
        self.assertEqual(recovered["action"],"PLACE")
        self.assertEqual(recovered["scenario"],3)
        self.assertEqual(recovered["reason"],"RECOVERED_BOOK")

    def test_price_above_cap_and_stale_or_crossed_books_never_place(self):
        for b in ({**self.book,"ask":10100,"exchange_ms":1002100},
                  {**self.book,"bid":20000,"exchange_ms":1002200},
                  {**self.book,"exchange_ms":900000}):
            result=self.i.quote(b,1002450)
            self.assertNotEqual(result["action"],"PLACE")

    def test_live_fill_without_correct_order_and_execution_proof_is_rejected(self):
        plan=self.quote()
        self.i.submit("A",plan["qty"],1000000)
        with self.assertRaises(RuntimeError):
            self.i.confirm_fill("UNKNOWN","E1",2,10000)
        with self.assertRaises(RuntimeError):
            self.i.confirm_fill("A","E2",50,10000)
        with self.assertRaises(RuntimeError):
            self.i.confirm_fill("A","E3",1,10100)
        self.assertEqual(self.i.status()["filled_qty"],"0")

    def test_uncertain_order_does_not_allow_reentry_even_after_quote_normalizes(self):
        p=self.quote()
        self.i.submit("A",p["qty"],1000000)
        self.i.unknown("BROKER_ORDER_STATUS_TIMEOUT")
        self.assertEqual(self.quote()["action"],"BLOCK")
        with self.assertRaises(RuntimeError):
            self.i.submit("B","2",1001300)

    def test_sale_start_blocks_later_topup(self):
        self.i.mark_selling()
        self.assertEqual(self.quote()["action"],"BLOCK")

    def test_coin_floor_and_seed_ceiling_without_synthetic_broker_fill(self):
        i=Intent("trend","coin","KRW-BTC","MT:RSI:1","100000000","200000",
                 "0.0005")
        book={"symbol":"KRW-BTC","exchange_ms":1500000,"ask":100000000,
              "bid":99999000,"ask_size":D("0.5")}
        result=i.quote(book,1500100)
        self.assertEqual(result["action"],"PLACE")
        self.assertLessEqual(D(result["qty"])*D(result["limit"])*D("1.0005"),D("200000"))
        self.assertEqual(i.status()["filled_qty"],"0")


if __name__=="__main__":
    unittest.main()
