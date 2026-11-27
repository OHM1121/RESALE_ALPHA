"""0.5.6: the first take-profit of a held supply-demand position is a RESTING sell at the target price (real-trading process, rule R15).

Real vendor `GMOService` / `Engine`; 하림지주 003380: 3 sh @ 9,930, first target 5 % = 10,426.5 -> resting price 10,430 (tick 10).
"""
from __future__ import annotations

import queue
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import sim_gmo_d1_v0470 as S
    import d_live
    import gmo_held_exit_v0550 as held
    import gmo_resting_tp_v0560 as rest
    from projectgmo.engine import Engine, Position
    RUNTIME = True
except Exception:  # pragma: no cover - needs the original engine on sys.path (CI provides it)
    RUNTIME = False

CODE = "003380"


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class RestingTakeProfit(unittest.TestCase):
    def setUp(self):
        self.native = Engine._trigger_first_tp
        Engine._trigger_first_tp = rest.trigger_resting
        self.tmp = tempfile.TemporaryDirectory()
        self.sim = S.Sim(self.tmp.name)
        self.eng = self.sim.svc.engine
        self.eng.positions[CODE] = Position(CODE, "하림지주", 3, 3, 9930.0, "2026-11-23", "SHORT", 29794.4685, gmo_score=75, first_profit_target=0.05)
        self.eng.buy_fills.append({"time": "2026-11-23T10:44:08+09:00", "code": CODE, "order_id": "x", "qty": 3, "price": 9930, "fee": 4, "paid": 29794.4685, "purpose": "D+1"})
        S.MARKET_MAP[CODE] = ("KOSDAQ", "하림지주")
        self.clock = self.sim.clock
        self.patches = [mock.patch.object(d_live, "now_kst", lambda: self.clock.now), mock.patch.object(held, "_now", lambda: self.clock.now)]
        for p in self.patches:
            p.start()
        held._STATE["codes"], held._STATE["offered"], held._STATE["touches"] = {}, {}, {}
        held._STATE.update({"day": "", "reported": set(), "held": frozenset({CODE})})
        r = types.SimpleNamespace()
        r.gmo = self.sim.svc
        r._gmo_tick_queue = queue.Queue(800)
        r._tick_queue = queue.Queue(800)
        r.nxt_snapshot = types.SimpleNamespace(allows=lambda c, d: False)
        r._tracked_codes = {CODE}
        r._quote_lock = threading.Lock()
        r._latest_trade_by_venue = {}
        r._latest_krx_trades = {}
        r._invalid_codes = set()
        r._correction_inflight = set()
        r._last_krx_minute_by_code = {}
        r._paper_interest_held = set()
        r._paper_interest_limits = {}
        r._paper_interest_ready = False
        r.ma = types.SimpleNamespace(watched_codes=lambda: set())
        r.minute_bars = {}
        r._queue_fast_minute_repair = lambda *a, **k: None
        r._queue_pipeline_event = lambda *a, **k: None
        r.stop_event = threading.Event()
        self.errors = []
        r.store = types.SimpleNamespace(issue=lambda *a, **k: None, write_runtime_error=lambda e, c: self.errors.append((c, repr(e))))
        r.latest_krx_trade = lambda code: d_live.LivePaperRunner.latest_krx_trade(r, code)
        self.runner = r

    def tearDown(self):
        Engine._trigger_first_tp = self.native
        for p in self.patches:
            p.stop()
        self.sim.close()
        self.tmp.cleanup()

    def tick(self, when, price, lag=0.0):
        self.clock.set(when)
        exchange = when - S.dt.timedelta(seconds=lag)
        return (CODE, "KOSDAQ", "하림지주", exchange.strftime("%Y%m%d%H%M") + "00", price, 5, when.isoformat(), "KRX", exchange.isoformat(), 0, 0)

    def ingress(self, tick):
        held.record_ingress(self.runner, tick)
        d_live.LivePaperRunner._new_tick(self.runner, tick)

    @property
    def pos(self):
        return self.eng.positions[CODE]

    def sells(self):
        return [o for o in self.eng.orders.values() if o.code == CODE and o.side == "SELL"]

    def test_resting_price_is_the_target_rounded_up_to_a_valid_tick(self):
        self.assertEqual(rest.resting_price(self.pos), 10430)

    def test_vendor_path_fills_at_the_resting_price_not_at_the_bid_minus_ticks(self):
        self.sim.svc.on_live_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        orders = self.sells()
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0].purpose, orders[0].limit, orders[0].state), ("TP5_FIRST_20PCT", 10430, "FILLED"))
        fills = [f for f in self.eng.fills if f["code"] == CODE]
        self.assertEqual([(f["qty"], f["price"]) for f in fills], [(1, 10430)])
        self.assertEqual(self.pos.stage, "PROTECTED")
        self.assertEqual(self.pos.qty, 2)

    def test_a_stale_print_that_never_reached_the_consumer_still_fills_the_resting_order(self):
        """The 하림지주 case: the print sits behind a busy consumer and is 12 s old when anybody looks; the resting order has filled at the exchange."""
        self.ingress(self.tick(S.at(9, 15, 8, 300), 10520))
        self.assertEqual(self.runner._gmo_tick_queue.qsize(), 1)
        self.clock.set(S.at(9, 15, 20))
        self.assertEqual(held.apply_touches(self.runner), 1)
        fills = [f for f in self.eng.fills if f["code"] == CODE]
        self.assertEqual([(f["qty"], f["price"], f["purpose"]) for f in fills], [(1, 10430, "TP5_FIRST_20PCT")])
        self.assertTrue(fills[0]["time"].startswith("2026-11-26T09:15:08"))     # the print's own time, not the late look
        self.assertEqual(held._STATE["codes"][CODE]["resting_applied"], 1)

    def test_a_gap_print_far_above_still_fills_at_the_resting_limit(self):
        self.ingress(self.tick(S.at(9, 15, 8), 11200))
        held.apply_touches(self.runner)
        self.assertEqual([f["price"] for f in self.eng.fills if f["code"] == CODE], [10430])

    def test_a_print_below_the_resting_price_changes_nothing(self):
        self.ingress(self.tick(S.at(9, 15, 8), 10420))
        self.assertEqual(held.apply_touches(self.runner), 0)
        self.sim.svc.on_live_tick(self.tick(S.at(9, 15, 9), 10420))
        self.assertEqual(self.sells(), [])
        self.assertEqual(self.pos.stage, "NORMAL")

    def test_a_print_older_than_the_order_cannot_fill_it(self):
        self.eng.buy_fills.append({"time": "2026-11-26T09:15:30+09:00", "code": CODE, "order_id": "y", "qty": 0, "price": 9930, "fee": 0, "paid": 0, "purpose": "D+1"})
        self.ingress(self.tick(S.at(9, 15, 8), 10520))
        self.assertEqual(held.apply_touches(self.runner), 0)
        self.assertEqual(self.sells(), [])

    def test_applied_once(self):
        self.ingress(self.tick(S.at(9, 15, 8), 10520))
        held.apply_touches(self.runner)
        self.ingress(self.tick(S.at(9, 15, 9), 10560))
        self.assertEqual(held.apply_touches(self.runner), 0)
        self.assertEqual(len(self.sells()), 1)

    def test_the_remaining_split_buy_orders_are_cancelled_at_the_touch(self):
        buy = self.eng._order(CODE, "BUY", "D+1_V097_KRX_L2", 10000, 9900, 5, "2026-11-26T09:00:00+09:00")
        self.ingress(self.tick(S.at(9, 15, 8), 10520))
        held.apply_touches(self.runner)
        self.assertIn(self.eng.orders[buy.id].state, ("CANCEL_REQUESTED", "CANCELED"))

    def test_later_exit_rules_still_work_after_the_first_fill(self):
        self.sim.svc.on_live_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.assertEqual(self.pos.stage, "PROTECTED")
        self.sim.svc.on_live_tick(self.tick(S.at(9, 16, 5), 10100))              # entry +1.7 % <= +2 % floor
        reasons = [o.purpose for o in self.sells()]
        self.assertIn("ENTRY_PLUS2_FLOOR", reasons)

    def test_cash_and_realized_follow_the_engine_fill(self):
        cash = self.eng.cash
        self.sim.svc.on_live_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.assertGreater(self.eng.cash, cash)
        self.assertGreater(self.eng.realized, 0)

    def test_blocked_position_is_not_sold(self):
        self.pos.blocked = "OPERATOR"
        self.ingress(self.tick(S.at(9, 15, 8), 10520))
        held.apply_touches(self.runner)
        self.assertEqual(self.sells(), [])


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class CatchUpFromCandles(RestingTakeProfit):
    """A touch the app could not see (restart, feed gap, phone off) is read from the finished candles (하림지주 11-25 15:15 5m high 10,440)."""

    def setUp(self):
        super().setUp()
        import sqlite3
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)")
        self.runner.store.connect = lambda: _Conn(self.conn)

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def bar(self, ts, high, frame="5m", source="FIX15_VERIFIED_KIWOOM_REST"):
        self.conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (CODE, "KOSDAQ", frame, ts, high - 10, high, high - 20, high - 10, 100, source))

    def test_official_candle_after_the_order_fills_the_resting_sell_at_its_limit_and_candle_end(self):
        self.bar("20261123104500", 9990)
        self.bar("20261125151500", 10440)               # the first candle that reached 10,430
        self.bar("20261126091500", 10600)
        self.assertEqual(rest.catch_up(self.runner), 1)
        fills = [f for f in self.eng.fills if f["code"] == CODE]
        self.assertEqual([(f["qty"], f["price"], f["purpose"]) for f in fills], [(1, 10430, "TP5_FIRST_20PCT")])
        self.assertTrue(fills[0]["time"].startswith("2026-11-25T15:20:00"))
        self.assertEqual(self.pos.stage, "PROTECTED")
        self.assertEqual(self.pos.protected_peak, 10600)
        self.assertEqual(self.pos.qty, 2)
        self.assertTrue(any(e["kind"] == "RESTING_TP_CATCHUP_V0560" for e in self.eng.audit))

    def test_a_candle_that_contains_the_order_moment_is_not_evidence(self):
        self.bar("20261123104000", 10500)               # 10:40-10:45 contains the 10:44:08 buy fill
        self.assertEqual(rest.catch_up(self.runner), 0)
        self.assertEqual(self.sells(), [])

    def test_a_candle_below_the_resting_price_is_not_evidence(self):
        self.bar("20261125151500", 10420)
        self.assertEqual(rest.catch_up(self.runner), 0)

    def test_once_only(self):
        self.bar("20261125151500", 10440)
        self.assertEqual(rest.catch_up(self.runner), 1)
        self.assertEqual(rest.catch_up(self.runner), 0)
        self.assertEqual(len([o for o in self.sells()]), 1)

    def test_no_known_order_time_means_no_catch_up(self):
        self.eng.buy_fills.clear()
        self.bar("20261125151500", 10440)
        self.assertEqual(rest.catch_up(self.runner), 0)

    def test_one_minute_live_candle_also_counts(self):
        self.bar("20261126091500", 10520, frame="1m", source="LIVE_WS_0B")
        self.assertEqual(rest.catch_up(self.runner), 1)
        self.assertTrue([f for f in self.eng.fills if f["code"] == CODE][0]["time"].startswith("2026-11-26T09:16:00"))


class _Conn:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class InstallWiring(unittest.TestCase):
    def test_install_replaces_the_vendor_method_once(self):
        keep = Engine._trigger_first_tp
        try:
            with mock.patch.dict(rest._STATE, {"installed": False, "native": None}):
                rest.install()
                self.assertIs(Engine._trigger_first_tp, rest.trigger_resting)
                rest.install()
                self.assertIs(Engine._trigger_first_tp, rest.trigger_resting)
        finally:
            Engine._trigger_first_tp = keep


if __name__ == "__main__":
    unittest.main()
