"""0.5.5: a held supply-demand (GMO) position must reach its first take-profit even when the tick consumer is busy; a missed touch leaves evidence.

Real vendor `GMOService` / `Engine` (simulated clock, scripted prints). The case is 하림지주 2026-11-26: 3 sh @ 9,930, first target 10,426.5, prints of 10,520 at 09:15:07.
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
    from projectgmo.engine import Position
    RUNTIME = True
except Exception:  # pragma: no cover - needs the original engine on sys.path (CI provides it)
    RUNTIME = False

CODE = "003380"


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class HeldExit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sim = S.Sim(self.tmp.name)
        self.eng = self.sim.svc.engine
        # entry_price 9,930 (average of the three 1-share fills), 3 shares, score 75 -> first take-profit 5 %
        self.eng.positions[CODE] = Position(CODE, "하림지주", 3, 3, 9930.0, "2026-11-23", "SHORT", 29794.4685, gmo_score=75, first_profit_target=0.05)
        S.MARKET_MAP[CODE] = ("KOSDAQ", "하림지주")
        self.clock = self.sim.clock
        self.patches = [mock.patch.object(d_live, "now_kst", lambda: self.clock.now), mock.patch.object(held, "_now", lambda: self.clock.now)]
        for p in self.patches:
            p.start()
        for key in ("codes", "offered"):
            held._STATE[key] = {}
        held._STATE.update({"day": "", "reported": set(), "held": frozenset()})
        self.issues = []
        self.errors = []
        r = types.SimpleNamespace()
        r.gmo = self.sim.svc
        r._gmo_tick_queue = queue.Queue(800)           # nobody drains it: the busy consumer
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
        r.store = types.SimpleNamespace(issue=lambda *a, **k: self.issues.append((a, k)), write_runtime_error=lambda e, c: self.errors.append((c, repr(e))))
        r.latest_krx_trade = lambda code: d_live.LivePaperRunner.latest_krx_trade(r, code)
        self.runner = r
        self.new_tick = lambda tick: (held.record_ingress(r, tick), d_live.LivePaperRunner._new_tick(r, tick))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.sim.close()
        self.tmp.cleanup()

    def tick(self, when, price, lag=0.0):
        self.clock.set(when)
        exchange = when - S.dt.timedelta(seconds=lag)
        return (CODE, "KOSDAQ", "하림지주", exchange.strftime("%Y%m%d%H%M") + "00", price, 5, when.isoformat(), "KRX", exchange.isoformat(), 0, 0)

    @property
    def pos(self):
        return self.eng.positions[CODE]

    def sells(self):
        return [o for o in self.eng.orders.values() if o.code == CODE and o.side == "SELL"]

    # ---- the vendor engine itself is right
    def test_engine_alone_turns_a_fresh_print_above_target_into_the_first_take_profit(self):
        held._STATE["held"] = frozenset({CODE})
        self.sim.svc.on_live_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.assertTrue(self.pos.first_profit_triggered)
        self.assertEqual([o.purpose for o in self.sells()], ["TP5_FIRST_20PCT"])

    # ---- the consumer is blocked: the print only sits in the queue
    def test_without_the_watchdog_a_busy_consumer_loses_the_touch(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.assertEqual(self.runner._gmo_tick_queue.qsize(), 1, "the print is queued behind the busy consumer")
        self.assertFalse(self.pos.first_profit_triggered)

    def test_watchdog_offers_the_fresh_print_without_waiting_for_the_consumer(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.clock.set(S.at(9, 15, 8, 700))
        self.assertEqual(held.offer_latest(self.runner), 1)
        self.assertTrue(self.pos.first_profit_triggered)
        self.assertEqual([o.purpose for o in self.sells()], ["TP5_FIRST_20PCT"])
        stats = held._STATE["codes"][CODE]
        self.assertEqual((stats["ingress"], stats["watchdog_offered"], stats["ingress_max"]), (1, 1, 10520))

    def test_the_same_print_is_offered_once(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10400))
        self.assertEqual(held.offer_latest(self.runner), 1)
        self.assertEqual(held.offer_latest(self.runner), 0)
        self.assertEqual(held._STATE["codes"][CODE]["watchdog_offered"], 1)

    def test_a_stale_print_is_never_offered_and_the_three_second_gate_is_unchanged(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10520, lag=0.0))
        self.clock.set(S.at(9, 15, 12))                       # 3.7 s later
        self.assertEqual(held.offer_latest(self.runner), 0)
        self.assertFalse(self.pos.first_profit_triggered)
        self.assertEqual(held._STATE["codes"][CODE]["watchdog_stale"], 1)

    def test_a_print_below_the_target_changes_nothing(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10400))
        held.offer_latest(self.runner)
        self.assertFalse(self.pos.first_profit_triggered)
        self.assertEqual(self.sells(), [])

    def test_a_code_that_is_not_held_is_ignored(self):
        del self.eng.positions[CODE]
        held._STATE["held"] = frozenset()
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.assertEqual(held.offer_latest(self.runner), 0)
        self.assertEqual(held._STATE["codes"], {})

    # ---- evidence
    def test_a_touch_that_never_reached_the_engine_is_reported_once(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        self.clock.set(S.at(9, 15, 20))                       # stale for the watchdog as well: nothing processed it
        held.offer_latest(self.runner)
        held.check_touches(self.runner)
        held.check_touches(self.runner)
        self.assertEqual(len(self.issues), 1)
        args, kwargs = self.issues[0]
        self.assertEqual(args[0], "GMO_HELD_TP_TOUCH_UNPROCESSED")
        self.assertEqual(kwargs["details"]["code"], CODE)
        self.assertEqual(kwargs["details"]["counters"]["ingress_max"], 10520)
        self.assertEqual(kwargs["details"]["counters"]["engine_calls"], 0)

    def test_no_report_when_the_exit_started(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10520))
        held.offer_latest(self.runner)
        self.clock.set(S.at(9, 15, 30))
        held.check_touches(self.runner)
        self.assertEqual(self.issues, [])

    def test_state_file_is_written_in_the_gmo_root(self):
        held._STATE["held"] = frozenset({CODE})
        self.new_tick(self.tick(S.at(9, 15, 8, 300), 10400))
        held.write_state(self.runner)
        self.assertTrue((Path(self.sim.svc.root) / held.STATE_FILE).exists())


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class InstallWiring(unittest.TestCase):
    def test_install_wraps_three_vendor_methods_once_and_starts_one_watchdog(self):
        import d_gmo_runtime
        keep = (d_live.LivePaperRunner._new_tick, d_live.LivePaperRunner._start_market_workers, d_gmo_runtime.GMOService.on_live_tick)
        started = []
        try:
            with mock.patch.dict(held._STATE, {"installed": False, "threads": set()}):
                d_live.LivePaperRunner._start_market_workers = lambda self: started.append("native")
                held.install()
                self.assertIsNot(d_live.LivePaperRunner._new_tick, keep[0])
                wrapped = (d_live.LivePaperRunner._new_tick, d_live.LivePaperRunner._start_market_workers, d_gmo_runtime.GMOService.on_live_tick)
                held.install()
                self.assertEqual(wrapped, (d_live.LivePaperRunner._new_tick, d_live.LivePaperRunner._start_market_workers, d_gmo_runtime.GMOService.on_live_tick))
                runner = types.SimpleNamespace(stop_event=threading.Event(), gmo=types.SimpleNamespace(holding_codes=lambda: frozenset()), latest_krx_trade=lambda c: None, store=None)
                with mock.patch.object(held.threading, "Thread") as thread:
                    d_live.LivePaperRunner._start_market_workers(runner)
                    d_live.LivePaperRunner._start_market_workers(runner)
                self.assertEqual(started, ["native", "native"])
                self.assertEqual(thread.call_count, 1)
        finally:
            d_live.LivePaperRunner._new_tick, d_live.LivePaperRunner._start_market_workers, d_gmo_runtime.GMOService.on_live_tick = keep


if __name__ == "__main__":
    unittest.main()
