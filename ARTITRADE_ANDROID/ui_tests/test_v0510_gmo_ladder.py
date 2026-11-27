"""0.5.1 (#166): supply-demand D+1 entry = three-rung split BUY decided before the open and recorded (owner decision 2026-11-27)."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

import gmo_d1_preplan_v097 as pre
import gmo_exec_link_v0410 as link
import night_futures_gate_v0260 as night
import position_uid_v015 as uid
from sim_gmo_d1_v0470 import Sim, at, CODE

CLOSE = 5000                                  # 5,000 won stock; the per-stock limit buys about 100 shares


class Base(unittest.TestCase):
    def sim(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Sim(tmp.name, close=CLOSE, open_=CLOSE, ladder=True, **kw)
        self.addCleanup(s.close)
        return s

    def rungs(self, s):
        return {o.purpose.rsplit("_", 1)[-1]: o for o in s.orders}

    def logs(self, s, tag):
        return [x for x in s.svc.engine.audit if tag in str(x)]


class LadderMath(unittest.TestCase):
    def test_owner_example_prices_and_shares(self):
        rungs = pre.compute_ladder(5000, 500000, 0.00015)
        self.assertEqual([r["price"] for r in rungs], [5000, 4950, 4850])          # -1 % from rung 1, -2 % from rung 2, tick floored
        self.assertEqual([r["budget_fraction"] for r in rungs], [0.30, 0.35, 0.35])  # 30 + 35 + 40 would be 105 %: the last rung takes what is left
        self.assertLessEqual(sum(r["quantity"] * r["price"] * 1.00015 for r in rungs), 500000)
        self.assertEqual([r["quantity"] for r in rungs], [int(500000 * f / (p * 1.00015)) for f, p in zip((.3, .35, .35), (5000, 4950, 4850))])

    def test_the_total_never_exceeds_the_per_stock_limit(self):
        for condition in (1990, 5500, 12300, 48350, 251000):
            rungs = pre.compute_ladder(condition, 500000, 0.00015)
            self.assertLessEqual(sum(r["quantity"] * r["price"] * 1.00015 for r in rungs), 500000 + 1e-6)
            self.assertAlmostEqual(sum(r["budget_fraction"] for r in rungs), 1.0)


class Recorded(Base):
    def test_the_ladder_is_recorded_in_the_plan_and_in_the_audit_log(self):
        s = self.sim()
        s.planner.retry_ladders(at(8, 50))
        ladder = s.plan["ladder"]
        self.assertEqual(ladder["version"], "LADDER_V0510")
        self.assertEqual(ladder["basis"], "D_CLOSE_STRICT_BELOW_EWY_UNVERIFIED")
        self.assertIn("computed_at", ladder)
        self.assertEqual(len(self.logs(s, "GMO_D1_LADDER_V0510")), 1)
        text = str(self.logs(s, "GMO_D1_LADDER_V0510")[0])
        for r in ladder["rungs"]:
            self.assertIn("L%d=%d/%d" % (r["rung"], r["price"], r["quantity"]), text)

    def test_the_condition_price_follows_the_ewy_ceiling_when_it_is_known(self):
        s = self.sim()
        s.plan.pop("ladder", None)
        s.planner._ladders.clear()
        info = {"status": "VERIFIED", "change_pct": 10.0}
        with mock.patch.object(night, "gmo_ceiling", return_value=(5500, info)):
            ladder = s.planner._ensure_ladder(s.plan, s.svc.engine.signals[CODE], at(7, 55))
        self.assertEqual((ladder["basis"], ladder["condition_price"], ladder["ewy_change_pct"]), ("EWY_ADJUSTED_D_CLOSE", 5500, 10.0))
        self.assertEqual([r["price"] for r in ladder["rungs"]], [5500, 5440, 5330])

    def test_without_the_ewy_value_it_waits_until_the_nxt_open_and_then_uses_the_d_close_rule(self):
        s = self.sim()
        s.plan.pop("ladder", None)
        s.planner._ladders.clear()
        with mock.patch.object(night, "gmo_ceiling", return_value=(None, {"status": "NOT_CHECKED_YET"})):
            self.assertIsNone(s.planner._ensure_ladder(s.plan, s.svc.engine.signals[CODE], at(7, 55)))
            ladder = s.planner._ensure_ladder(s.plan, s.svc.engine.signals[CODE], at(8, 0, 1))
        self.assertEqual(ladder["basis"], "D_CLOSE_STRICT_BELOW_EWY_UNVERIFIED")
        self.assertEqual(ladder["condition_price"], 4995)


class Orders(Base):
    def test_three_resting_orders_at_the_rung_prices_and_no_plus_two_ticks(self):
        s = self.sim()
        s.book(at(9, 0, 18), 5200, 5150, 50)                      # above the condition: the orders still wait at their rungs
        got = self.rungs(s)
        self.assertEqual(sorted(got), ["L1", "L2", "L3"])
        self.assertEqual([got[k].limit for k in ("L1", "L2", "L3")], [4995, 4945, 4845])
        self.assertEqual(s.filled, 0)
        self.assertTrue(all(o.limit <= 4995 for o in s.orders))   # nothing above the condition price

    def test_a_rung_at_or_above_the_ask_is_bought_at_the_ask_and_the_others_wait(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4990, 4900, 50)
        s.print_(at(9, 0, 19), 4990, 5, ask=4990, bid=4900)
        got = self.rungs(s)
        self.assertEqual((got["L1"].filled, got["L2"].filled, got["L3"].filled), (got["L1"].requested, 0, 0))
        self.assertEqual({f["price"] for f in s.svc.engine.buy_fills}, {4990})

    def test_price_coming_down_to_a_rung_buys_it_at_the_rung_price(self):
        s = self.sim()
        s.book(at(9, 0, 18), 5100, 5050, 50)
        s.print_(at(9, 3, 0), 5000, 7, ask=5010, bid=5000)         # above every rung: nothing
        self.assertEqual(s.filled, 0)
        s.print_(at(9, 5, 0), 4945, 7, ask=4955, bid=4945)          # trades at rung 2
        got = self.rungs(s)                                          # the price came down through rung 1 (4,995) to rung 2 (4,945)
        self.assertEqual((got["L1"].filled, got["L2"].filled, got["L3"].filled), (got["L1"].requested, got["L2"].requested, 0))
        self.assertEqual(sorted(f["price"] for f in s.svc.engine.buy_fills), [4945, 4995])        # each at its own rung price
        self.assertIn("PAPER_LADDER_FILL_V0510", str(self.logs(s, "PAPER_LADDER_FILL_V0510")[0]))
        s.print_(at(9, 40, 0), 4800, 7, ask=4810, bid=4800)         # trades through rung 3
        self.assertEqual(self.rungs(s)["L3"].state, "FILLED")

    def test_no_chase_and_no_top_up_when_the_price_runs_away(self):
        s = self.sim()
        s.book(at(9, 0, 18), 5100, 5050, 50)
        for minute in range(1, 40):
            s.print_(at(9, minute, 0), 5200 + minute, 5, ask=5210 + minute, bid=5200 + minute)
        self.assertEqual((s.filled, len(s.orders)), (0, 3))        # the three orders only, no second round

    def test_no_buy_after_d_plus_1(self):
        s = self.sim()
        s.book(at(15, 20, 0), 5100, 5050, 50)
        s.print_(at(15, 40, 0), 4800, 5, ask=4810, bid=4800)       # after the D+1 regular session
        self.assertEqual(s.filled, 0)
        nxt = at(9, 0, 18) + dt.timedelta(days=1)                   # the next day
        s.clock.set(nxt)
        s.print_(nxt, 4700, 5, ask=4710, bid=4700)
        self.assertEqual(s.filled, 0)


class GapDown(Base):
    def test_a_gap_below_every_rung_discards_the_ladder_and_buys_everything_at_the_current_price(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4500, 4450, 500)
        self.assertEqual([o.purpose.rsplit("_", 1)[-1] for o in s.orders], ["GAP"])
        order = s.orders[0]
        self.assertEqual(order.limit, 4500)
        self.assertEqual(order.requested, int(s.budget / (4500 * 1.00015)))      # the whole per-stock amount at the current price
        self.assertEqual(s.plan["ladder_discarded"], "GAP_BELOW_ALL_RUNGS")
        self.assertEqual(len(self.logs(s, "GMO_D1_LADDER_DISCARDED_V0510")), 1)
        s.print_(at(9, 0, 19), 4500, 50, ask=4500, bid=4450)
        self.assertEqual(s.filled, order.requested)
        self.assertEqual(s.plan["status"], "COMPLETED")

    def test_a_gap_between_the_rungs_only_makes_the_rungs_above_it_marketable(self):
        s = self.sim()
        s.book(at(9, 0, 18), 4900, 4850, 500)                      # below rung 1 and rung 2, above rung 3 (4,845)
        got = self.rungs(s)
        self.assertEqual(sorted(got), ["L1", "L2", "L3"])
        s.print_(at(9, 0, 19), 4900, 60, ask=4900, bid=4850)
        self.assertEqual((got["L1"].state, got["L2"].state, got["L3"].filled), ("FILLED", "FILLED", 0))
        self.assertEqual({f["price"] for f in s.svc.engine.buy_fills}, {4900})


class StartBelowRungOne(Base):
    """Owner example 2026-11-27: rung 1 = 4,900, rung 2 = 4,500; the price starts at 4,700."""

    def custom(self, s):
        ladder = s.plan["ladder"]
        ladder["rungs"] = [{"rung": 1, "price": 4900, "budget_fraction": 0.30, "quantity": int(500000 * .30 / (4900 * 1.00015))},
                           {"rung": 2, "price": 4500, "budget_fraction": 0.35, "quantity": int(500000 * .35 / (4500 * 1.00015))},
                           {"rung": 3, "price": 4400, "budget_fraction": 0.35, "quantity": int(500000 * .35 / (4400 * 1.00015))}]

    def test_rung_one_is_bought_now_at_the_current_price_and_the_lower_rungs_stay(self):
        s = self.sim()
        s.planner.retry_ladders(at(8, 50))
        self.custom(s)
        s.book(at(9, 0, 18), 4700, 4650, 500)
        got = self.rungs(s)
        self.assertEqual(got["L1"].limit, 4700)                                          # 30 % of the limit at the current price
        self.assertEqual(got["L1"].requested, int(s.budget * 0.30 / (4700 * 1.00015)))
        self.assertGreater(got["L1"].requested, int(500000 * .30 / (4900 * 1.00015)))    # more shares than at the rung price
        self.assertEqual((got["L2"].limit, got["L3"].limit), (4500, 4400))               # the others stay as set
        self.assertEqual(len(self.logs(s, "GMO_D1_LADDER_RUNG_AT_CURRENT_PRICE_V0510")), 1)
        s.print_(at(9, 0, 19), 4700, 60, ask=4700, bid=4650)
        self.assertEqual((got["L1"].state, got["L2"].filled, got["L3"].filled), ("FILLED", 0, 0))
        self.assertEqual({f["price"] for f in s.svc.engine.buy_fills}, {4700})
        s.print_(at(9, 30, 0), 4500, 5, ask=4510, bid=4500)                              # later the price comes down to rung 2
        self.assertEqual((got["L2"].state, got["L3"].filled), ("FILLED", 0))
        self.assertIn(4500.0, {f["price"] for f in s.svc.engine.buy_fills})

    def test_a_start_above_rung_one_leaves_every_rung_resting(self):
        s = self.sim()
        s.planner.retry_ladders(at(8, 50))
        self.custom(s)
        s.book(at(9, 0, 18), 4950, 4900, 500)
        self.assertEqual([o.limit for o in sorted(s.orders, key=lambda o: o.id)], [4900, 4500, 4400])
        s.print_(at(9, 0, 19), 4950, 60, ask=4950, bid=4900)
        self.assertEqual(s.filled, 0)


class OnePosition(Base):
    def test_three_rungs_make_one_position_with_one_identity(self):
        s = self.sim()
        s.book(at(9, 0, 18), 5100, 5050, 50)
        s.print_(at(9, 1, 0), 4990, 7, ask=4990, bid=4985)
        s.print_(at(9, 5, 0), 4945, 7, ask=4955, bid=4945)
        s.print_(at(9, 9, 0), 4800, 7, ask=4810, bid=4800)
        engine = s.svc.engine
        self.assertEqual(len([c for c in engine.positions if c == CODE]), 1)
        self.assertEqual(len({f["order_id"] for f in engine.buy_fills}), 3)
        rows = [{"id": -9000000 - i, "code": f["code"], "side": "BUY", "ts": f["time"], "qty": f["qty"]} for i, f in enumerate(engine.buy_fills)]
        link.link(rows, engine)
        self.assertEqual({uid._identity_key(r, "execution")[0] for r in rows}, {"entry:%s|2026-11-26" % CODE})
        position = {"id": -1, "code": CODE, "entry_date": "2026-11-26", "tf": "gmo"}
        self.assertEqual(uid._identity_key(position, "position")[0], "entry:%s|2026-11-26" % CODE)
        self.assertEqual(s.plan["status"], "COMPLETED")
        self.assertEqual(engine.positions[CODE].qty, sum(f["qty"] for f in engine.buy_fills))


class Handover(Base):
    def test_the_krx_orders_cover_only_what_the_nxt_session_left_and_wait_for_the_cancel_confirmation(self):
        s = self.sim()
        engine, planner = s.svc.engine, s.planner
        s.planner.retry_ladders(at(8, 50))
        ladder = s.plan["ladder"]
        made = planner._ladder_place(s.plan, engine, ladder, [], "NXT", at(8, 5), 5100, 5100, None)
        self.assertEqual(len(made), 3)
        l1, l2, l3 = made
        engine.fill(l1.id, l1.requested, 4990.0, at(8, 6).isoformat())                 # rung 1 fully bought on NXT
        engine.fill(l2.id, 10, 4945.0, at(8, 20).isoformat())                          # rung 2 partly
        for order in (l2, l3):
            engine._cancel(order, "NXT session ended", at(8, 50).isoformat())
        again = planner._ladder_place(s.plan, engine, ladder, planner._ladder_orders(engine, CODE, "2026-11-26"), "KRX", at(9, 0, 1), 5100, 5100, None)
        self.assertIsNone(again)                                                         # cancel not confirmed yet: no new order
        self.assertEqual(s.plan["status"], "WAIT_CANCEL_CONFIRMATION")
        for order in (l2, l3):
            engine.acknowledge_cancel(order.id, at(8, 50, 2).isoformat())
        again = planner._ladder_place(s.plan, engine, ladder, planner._ladder_orders(engine, CODE, "2026-11-26"), "KRX", at(9, 0, 2), 5100, 5100, None)
        got = {o.purpose.rsplit("_", 1)[-1]: o.requested for o in again}
        self.assertEqual(sorted(got), ["L2", "L3"])                                      # rung 1 is done
        self.assertEqual(got["L2"], ladder["rungs"][1]["quantity"] - 10)                  # only the rest of rung 2
        self.assertEqual(got["L3"], ladder["rungs"][2]["quantity"])


class NxtVenue(Base):
    def test_the_same_ladder_works_in_the_nxt_premarket(self):
        s = self.sim()
        engine, planner = s.svc.engine, s.planner
        key = ("2026-11-26", CODE, "NXT")
        planner.prepare("NXT", at(7, 55))
        plan = planner.plans[key]
        self.assertEqual(plan["status"], "WAIT_NXT_ELIGIBILITY")               # nothing is bought before the venue eligibility is verified
        plan["status"] = "PREPARED"
        s.clock.set(at(8, 0, 10))
        planner.retry_ladders(at(8, 0, 10))
        self.assertEqual(plan["ladder"]["basis"], "D_CLOSE_STRICT_BELOW_EWY_UNVERIFIED")
        tick = lambda last, when: (CODE, "KOSPI", "x", when.strftime("%Y%m%d%H%M%S"), last, 5, when.isoformat(), "NXT", when.isoformat())
        planner._step(plan, at(8, 0, 10), 5100, 5110, 40, "NXT", tick=tick(5100, at(8, 0, 10)), bid=5100)
        got = {o.purpose.rsplit("_", 1)[-1]: o for o in engine.orders.values() if o.purpose.startswith("D+1_V097_NXT_")}
        self.assertEqual(sorted(got), ["L1", "L2", "L3"])
        planner._step(plan, at(8, 5, 0), 4990, 4995, 40, "NXT", tick=tick(4990, at(8, 5, 0)), bid=4990)
        self.assertEqual(got["L1"].filled, got["L1"].requested)                  # price reached rung 1 on the NXT venue
        self.assertEqual({f["price"] for f in engine.buy_fills}, {4995.0})
        planner._step(plan, at(8, 6, 0), 4800, 4805, 40, "NXT", tick=tick(4800, at(8, 6, 0)), bid=4800)
        self.assertEqual((got["L2"].state, got["L3"].state), ("FILLED", "FILLED"))
        self.assertEqual(plan["status"], "COMPLETED")


class Safety(Base):
    def test_real_mode_places_nothing(self):
        s = self.sim()
        with mock.patch.dict(os.environ, {"ARTITRADE_REAL_MODE": "1"}):
            s.book(at(9, 0, 18), 4990, 4900, 50)
        self.assertEqual(s.orders, [])

    def test_one_set_of_orders_per_day_even_with_many_books(self):
        s = self.sim()
        for k in range(10):
            s.book(at(9, 0, 18 + 2 * k), 5100, 5050, 50)
        self.assertEqual(len(s.orders), 3)

    def test_a_restart_does_not_duplicate_the_orders(self):
        s = self.sim()
        s.book(at(9, 0, 18), 5100, 5050, 50)
        before = [o.id for o in s.orders]
        s.planner.plans.clear()
        s.planner._ladders.clear()
        s.planner.prepare("KRX", at(9, 0, 30), recovery=True)
        s.book(at(9, 0, 40), 5100, 5050, 50)
        self.assertEqual([o.id for o in s.orders], before)


if __name__ == "__main__":
    unittest.main()
