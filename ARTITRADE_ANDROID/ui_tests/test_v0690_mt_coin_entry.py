"""0.6.9 (owner 2026-11-28 "마켓트렌드 신규 매수가 없다"): MarketTrend coin entries were lost to a late just-closed candle, a print consumed before the candle came, and two fixed 10 s / 3 s tests."""
from __future__ import annotations

import datetime as dt
import inspect
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fresh_rule_v0680 as fr
import mt_coin_boundary_v0690 as bd

try:
    import market_trend_v043 as mt
    import market_trend_1118_runtime as nl
    import market_trend_runtime_v043 as runtime
    import new_legacy_1118_policy as pol
    from test_nl1120_trend_close_above_mid import _Journal, _Wallet, bars_for, ramp, T0
    RUNTIME = True
except Exception:  # pragma: no cover
    RUNTIME = False

KST = dt.timezone(dt.timedelta(hours=9))


class Helpers(unittest.TestCase):
    def test_armed_pairs_are_the_watching_ones_of_the_market(self):
        watches = {("coin", "A", "30m"): {"status": "WATCHING"}, ("coin", "B", "60m"): {"status": "READY"},
                   ("stock", "C", "30m"): {"status": "WATCHING"}, ("coin", "D", "day"): {"status": "BOUGHT"}}
        self.assertEqual(bd.armed_pairs(watches, "coin"), {("A", "30m")})
        self.assertEqual(bd.armed_pairs(None, "coin"), set())

    def test_history_is_behind_when_the_just_closed_candle_is_missing(self):
        finish = dt.datetime(2026, 11, 28, 10, 30, tzinfo=KST)
        rows = [{"end": dt.datetime(2026, 11, 28, 10, 0, tzinfo=KST)}]
        self.assertFalse(bd.history_is_behind(rows, finish, 1800))                                    # 10:00 is the newest completed bar of the 10:00-10:30 bar
        self.assertTrue(bd.history_is_behind(rows, finish + dt.timedelta(minutes=30), 1800))          # one bar later it is missing
        self.assertTrue(bd.history_is_behind([], finish, 1800))
        self.assertTrue(bd.history_is_behind([{"x": 1}], finish, 1800))

    def test_a_pair_behind_its_candle_asks_again_quickly_the_important_ones_first(self):
        self.assertEqual(bd.retry_after(True, False, True), 45.0)               # up to date: as before
        self.assertEqual(bd.retry_after(False, False, False), 12.0)             # incomplete: as before
        self.assertEqual(bd.retry_after(True, True, True), bd.REST_GAP_AFTER_BOUNDARY)
        self.assertEqual(bd.retry_after(True, True, False), bd.REST_GAP_OTHER_STALE)
        self.assertLess(bd.REST_GAP_AFTER_BOUNDARY, bd.REST_GAP_OTHER_STALE)

    def test_lap_order_held_then_armed_then_incomplete_then_rest(self):
        held, armed, missing = {("H", "30m")}, {("A", "30m")}, {("M", "60m")}
        rank = {"30m": 0, "60m": 1, "day": 2}.__getitem__
        pairs = [("Z", "30m"), ("M", "60m"), ("A", "30m"), ("H", "30m"), ("B", "30m")]
        pairs.sort(key=lambda p: bd.order_key(p, held, armed, missing, rank))
        self.assertEqual(pairs, [("H", "30m"), ("A", "30m"), ("M", "60m"), ("B", "30m"), ("Z", "30m")])

    def test_the_lap_token_changes_at_a_candle_boundary(self):
        finish = lambda now, frame: (now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0) + dt.timedelta(minutes=30))
        a = bd.lap_token(dt.datetime(2026, 11, 28, 10, 29, 59, tzinfo=KST), finish, ("30m",))
        b = bd.lap_token(dt.datetime(2026, 11, 28, 10, 30, 1, tzinfo=KST), finish, ("30m",))
        c = bd.lap_token(dt.datetime(2026, 11, 28, 10, 31, 0, tzinfo=KST), finish, ("30m",))
        self.assertNotEqual(a, b)
        self.assertEqual(b, c)


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class Entry(unittest.TestCase):
    def setUp(self):
        fr._LAST["coin"] = fr._LAST["stock"] = 0.0

    def make(self, root, now, stamp):
        co = SimpleNamespace()
        co.rt = SimpleNamespace(root=root, coin_runner=SimpleNamespace(preload_gate=None))
        co.lock = threading.RLock()
        co.stop_event = threading.Event()
        co._quotes = {"coin": {"KRW-TEST": (100.0, stamp)}, "stock": {}}
        co._candidates = {"coin": {"KRW-TEST": ("CRYPTO", "TEST")}, "stock": {}}
        co._history = {}
        co._decision_clock = lambda: now
        co.mode = lambda market: "ORIGINAL"
        co._positions = {}
        co.journal = _Journal()
        co.standalone_wallet = _Wallet()
        co._quote = lambda market, code: {"trade": co._quotes[market][code][0], "bid": 99.0, "ask": 101.0, "ask_size": 1000.0, "bid_size": 1000.0}
        co._last_fetch_error = {}
        return co

    def arm(self, co, t1):
        co._history[("coin", "KRW-TEST", "30m")] = bars_for(ramp(0.3), T0)
        with patch.object(nl, "_ready_market", return_value=True), patch.object(mt, "rsi20", return_value=25.0):
            nl._entry_window(co, "coin", t1)

    def test_a_quiet_coin_is_evaluated_when_the_candle_arrives_even_without_a_new_print(self):
        with tempfile.TemporaryDirectory() as root:
            t1 = T0 + dt.timedelta(minutes=5)
            t2 = T0 + dt.timedelta(minutes=30, seconds=5)
            co = self.make(root, t1, time.monotonic())
            self.arm(co, t1)
            self.assertEqual(co._nl1118_watches[("coin", "KRW-TEST", "30m")]["status"], "WATCHING")
            # 10:30:05 - the print of the coin arrives, but the history still ends at 10:00 (the 10:30 candle is not fetched yet)
            stamp = time.monotonic() + 0.001
            co._quotes["coin"]["KRW-TEST"] = (103.0, stamp)
            co._decision_clock = lambda: t2
            with patch.object(nl, "_ready_market", return_value=True), patch.object(mt, "rsi20", return_value=25.0):
                nl._entry_window(co, "coin", t2)
                self.assertEqual(len(co.standalone_wallet.buys), 0)
                self.assertNotEqual(co._nl1118_quote_stamps.get(("coin", "KRW-TEST")), stamp, "a print that could not be evaluated must stay unconsumed")
                # 10:30:50 - the candle arrives; NO new print came (quiet coin)
                co._history[("coin", "KRW-TEST", "30m")] = bars_for(ramp(0.3), T0 + dt.timedelta(minutes=30))
                nl._entry_window(co, "coin", t2 + dt.timedelta(seconds=45))
            self.assertEqual(len(co.standalone_wallet.buys), 1)
            self.assertEqual(co.standalone_wallet.buys[0][2], "KRW-TEST")

    def test_an_evaluated_print_is_still_consumed_once(self):
        with tempfile.TemporaryDirectory() as root:
            t1 = T0 + dt.timedelta(minutes=5)
            stamp = time.monotonic()
            co = self.make(root, t1, stamp)
            self.arm(co, t1)
            self.assertEqual(co._nl1118_quote_stamps[("coin", "KRW-TEST")], stamp)

    def test_a_print_older_than_10_seconds_counts_while_the_line_is_alive(self):
        with tempfile.TemporaryDirectory() as root:
            t1 = T0 + dt.timedelta(minutes=5)
            co = self.make(root, t1, time.monotonic() - 40.0)
            co._history[("coin", "KRW-TEST", "30m")] = bars_for(ramp(0.3), T0)
            with patch.object(nl, "_ready_market", return_value=True), patch.object(mt, "rsi20", return_value=25.0):
                nl._entry_window(co, "coin", t1)
                self.assertEqual(len(co._nl1118_watches), 0, "dead line: the old 10 s rule")
                fr.touch("coin")
                nl._entry_window(co, "coin", t1)
                self.assertEqual(len(co._nl1118_watches), 1, "line alive: the 40 s old print of a quiet coin counts")

    def test_the_two_fixed_tests_are_gone_from_the_entry_module(self):
        source = inspect.getsource(nl._entry_window)
        self.assertNotIn('>(10 if market=="coin" else 3)', source)
        self.assertGreaterEqual(source.count("fresh_rule_v0680"), 2)


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class Prefetch(unittest.TestCase):
    def test_the_lap_uses_the_boundary_rules(self):
        source = inspect.getsource(runtime.Coordinator._prefetch)
        for needle in ("mt_coin_boundary_v0690", "armed_pairs", "order_key", "lap_token", "history_is_behind", "retry_after"):
            self.assertIn(needle, source)
        self.assertIn('market=="coin"', source)                      # the stock lap is untouched

    def test_the_stock_retry_times_are_unchanged(self):
        source = inspect.getsource(runtime.Coordinator._prefetch)
        self.assertIn("retry_after=45 if complete else 12", source)


if __name__ == "__main__":
    unittest.main()
